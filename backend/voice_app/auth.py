from __future__ import annotations

import base64
import hashlib
import hmac
import re
import secrets
import time
import uuid

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import RedirectResponse
from starlette.websockets import WebSocket

from voice_app.config import Settings


_TOKEN_PATTERN = re.compile(r"(?i)([?&]token=)[^&\s]+")


class PairingAuth:
    def __init__(self, settings: Settings) -> None:
        self.settings = settings

    def pairing_matches(self, candidate: str) -> bool:
        configured = self.settings.pairing_token
        return bool(configured) and secrets.compare_digest(candidate, configured)

    def create_cookie(self) -> str:
        self._require_secrets()
        client_id = str(uuid.uuid4())
        expires = int(time.time()) + self.settings.cookie_max_age
        body = f"{client_id}.{expires}"
        signature = hmac.new(
            self.settings.cookie_secret.encode(), body.encode(), hashlib.sha256
        ).digest()
        encoded = base64.urlsafe_b64encode(signature).decode().rstrip("=")
        return f"{body}.{encoded}"

    def verify_cookie(self, value: str | None) -> str | None:
        if not value or not self.settings.cookie_secret:
            return None
        try:
            client_id, expires_raw, signature = value.split(".", 2)
            uuid.UUID(client_id)
            expires = int(expires_raw)
        except (ValueError, TypeError):
            return None
        if expires < int(time.time()):
            return None
        body = f"{client_id}.{expires}"
        expected = base64.urlsafe_b64encode(
            hmac.new(self.settings.cookie_secret.encode(), body.encode(), hashlib.sha256).digest()
        ).decode().rstrip("=")
        if not secrets.compare_digest(signature, expected):
            return None
        return client_id

    def require_request(self, request: Request) -> str:
        client_id = self.verify_cookie(request.cookies.get(self.settings.cookie_name))
        if client_id is None:
            raise HTTPException(status_code=401, detail="请先使用配对链接访问")
        return client_id

    def authenticate_websocket(self, websocket: WebSocket) -> str | None:
        return self.verify_cookie(websocket.cookies.get(self.settings.cookie_name))

    def set_cookie(self, response: Response) -> None:
        response.set_cookie(
            self.settings.cookie_name,
            self.create_cookie(),
            max_age=self.settings.cookie_max_age,
            secure=True,
            httponly=True,
            samesite="strict",
            path="/",
        )

    def _require_secrets(self) -> None:
        if not self.settings.pairing_token or len(self.settings.cookie_secret) < 32:
            raise RuntimeError("配对令牌和至少 32 字节的 Cookie 密钥尚未配置")


def install_pairing_routes(app: FastAPI, auth: PairingAuth) -> None:
    @app.get("/pair")
    async def pair(token: str = "") -> Response:
        if not auth.pairing_matches(token):
            raise HTTPException(status_code=401, detail="配对令牌无效")
        response = RedirectResponse(url="/", status_code=303)
        auth.set_cookie(response)
        return response


def redact_secrets(value: str) -> str:
    return _TOKEN_PATTERN.sub(r"\1[REDACTED]", value)
