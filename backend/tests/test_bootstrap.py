from pathlib import Path

from fastapi.testclient import TestClient

from voice_app import bootstrap
from voice_app.config import Settings


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        ca_cert_path=tmp_path / "ca.crt",
        pairing_token="must-not-leak",
        cookie_secret="must-not-leak-either",
    )


def test_bootstrap_explains_ios_trust_without_exposing_pairing_token(
    tmp_path: Path, monkeypatch
):
    monkeypatch.setattr(bootstrap, "settings", make_settings(tmp_path))
    response = TestClient(bootstrap.app).get("/")
    assert response.status_code == 200
    assert "证书信任设置" in response.text
    assert "https://192.0.2.10:8443/" in response.text
    assert "must-not-leak" not in response.text


def test_bootstrap_serves_ca_and_redirects_to_https(tmp_path: Path, monkeypatch):
    settings = make_settings(tmp_path)
    settings.ca_cert_path.write_text("test-ca")
    monkeypatch.setattr(bootstrap, "settings", settings)
    client = TestClient(bootstrap.app)

    ca = client.get("/ca.crt")
    assert ca.status_code == 200
    assert ca.content == b"test-ca"
    assert "claude-voice-local-ca.crt" in ca.headers["content-disposition"]

    redirect = client.get("/open", follow_redirects=False)
    assert redirect.status_code == 307
    assert redirect.headers["location"] == "https://192.0.2.10:8443/"


def test_bootstrap_returns_503_before_certificate_exists(tmp_path: Path, monkeypatch):
    monkeypatch.setattr(bootstrap, "settings", make_settings(tmp_path))
    response = TestClient(bootstrap.app).get("/ca.crt")
    assert response.status_code == 503
