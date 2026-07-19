from __future__ import annotations

from fastapi.testclient import TestClient

from voice_app.app import AppServices, create_app
from voice_app.config import Settings


class ASR:
    is_loaded = True

    async def load(self):
        pass


class TTS:
    async def health(self):
        return True

    async def close(self):
        pass


class Store:
    async def open(self):
        pass

    async def close(self):
        pass


class Health:
    async def readiness(self):
        return {"ready": True, "dependencies": {"asr": True, "tts": True, "sglang": True, "claude": True}}


class Coordinator:
    async def handle_socket(self, websocket, client_id):
        await websocket.accept()
        await websocket.send_json({"type": "session.ready", "generation_id": 0, "client_id": client_id})
        await websocket.close()


def settings(tmp_path, monkeypatch):
    frontend = tmp_path / "dist"
    frontend.mkdir()
    (frontend / "index.html").write_text("<html>voice app</html>")
    (frontend / "app.js").write_text("console.log('voice')")
    monkeypatch.setenv("VOICE_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("VOICE_FRONTEND_DIST", str(frontend))
    monkeypatch.setenv("VOICE_PAIRING_TOKEN", "pair-token-very-secret")
    monkeypatch.setenv("VOICE_COOKIE_SECRET", "cookie-signing-secret-at-least-32-bytes")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "local-key")
    return Settings(_env_file=None)


def client(tmp_path, monkeypatch):
    config = settings(tmp_path, monkeypatch)
    services = AppServices(asr=ASR(), tts=TTS(), sessions=Store(), health=Health(), coordinator=Coordinator())
    app = create_app(config, services=services)
    return TestClient(app, base_url="https://voice.local"), config


def pair(client, token="pair-token-very-secret"):
    return client.get(f"/pair?token={token}", follow_redirects=False)


def test_liveness_and_dependency_readiness(tmp_path, monkeypatch):
    web, _ = client(tmp_path, monkeypatch)
    with web:
        assert web.get("/health/live").json() == {"alive": True}
        response = web.get("/health/ready")
        assert response.status_code == 200 and response.json()["ready"] is True


def test_static_spa_requires_pairing_and_falls_back(tmp_path, monkeypatch):
    web, _ = client(tmp_path, monkeypatch)
    with web:
        assert web.get("/").status_code == 401
        pair(web)
        assert "voice app" in web.get("/").text
        assert "voice app" in web.get("/conversation/one").text
        assert "console.log" in web.get("/app.js").text


def test_websocket_auth_and_close_code(tmp_path, monkeypatch):
    web, _ = client(tmp_path, monkeypatch)
    with web:
        try:
            with web.websocket_connect("/ws/voice"):
                raise AssertionError("unauthorized socket connected")
        except Exception as exc:
            assert getattr(exc, "code", None) == 4401
        pair(web)
        with web.websocket_connect("wss://voice.local/ws/voice") as socket:
            assert socket.receive_json()["type"] == "session.ready"


def test_readiness_failure_returns_503_with_dependency_details(tmp_path, monkeypatch):
    config = settings(tmp_path, monkeypatch)

    class NotReady:
        async def readiness(self):
            return {"ready": False, "dependencies": {"tts": False}}

    services = AppServices(asr=ASR(), tts=TTS(), sessions=Store(), health=NotReady(), coordinator=Coordinator())
    web = TestClient(create_app(config, services=services), base_url="https://voice.local")
    with web:
        response = web.get("/health/ready")
        assert response.status_code == 503
        assert response.json()["dependencies"]["tts"] is False
