from __future__ import annotations

import logging

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from voice_app.auth import PairingAuth, install_pairing_routes, redact_secrets
from voice_app.config import Settings


def settings(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VOICE_PAIRING_TOKEN", "pair-token-very-secret")
    monkeypatch.setenv("VOICE_COOKIE_SECRET", "cookie-signing-secret-at-least-32-bytes")
    return Settings(_env_file=None)


def make_app(config):
    app = FastAPI()
    auth = PairingAuth(config)
    install_pairing_routes(app, auth)

    @app.get("/private")
    async def private(request: Request):
        return {"client_id": auth.require_request(request)}

    return app, auth


def test_valid_pairing_token_sets_secure_cookie_and_redirects(tmp_path, monkeypatch):
    app, _ = make_app(settings(tmp_path, monkeypatch))
    client = TestClient(app, base_url="https://voice.local")
    response = client.get("/pair?token=pair-token-very-secret", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/"
    cookie = response.headers["set-cookie"]
    assert "Secure" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie
    assert "pair-token-very-secret" not in cookie
    assert client.get("/private").status_code == 200


def test_missing_or_invalid_cookie_is_unauthorized(tmp_path, monkeypatch):
    app, _ = make_app(settings(tmp_path, monkeypatch))
    client = TestClient(app, base_url="https://voice.local")
    assert client.get("/private").status_code == 401
    client.cookies.set("claude_voice_session", "tampered")
    assert client.get("/private").status_code == 401
    assert client.get("/pair?token=wrong").status_code == 401


def test_pairing_refuses_unconfigured_secrets(tmp_path, monkeypatch):
    monkeypatch.setenv("VOICE_DATA_DIR", str(tmp_path))
    config = Settings(_env_file=None)
    auth = PairingAuth(config)
    with pytest.raises(RuntimeError, match="配置"):
        auth.create_cookie()


def test_secret_redaction_never_logs_query_token(caplog):
    caplog.set_level(logging.INFO)
    value = redact_secrets("GET /pair?token=pair-token-very-secret&next=/")
    logging.info("%s", value)
    assert "pair-token-very-secret" not in caplog.text
    assert "token=%5BREDACTED%5D" in caplog.text or "token=[REDACTED]" in caplog.text
