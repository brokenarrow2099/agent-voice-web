from __future__ import annotations

from contextlib import asynccontextmanager
from dataclasses import dataclass

from fastapi import FastAPI, Request, WebSocket
from fastapi.responses import FileResponse, JSONResponse

from voice_app.asr import ASRService
from voice_app.auth import PairingAuth, install_pairing_routes
from voice_app.claude import ClaudeRunner
from voice_app.config import Settings, get_settings
from voice_app.coordinator import TurnCoordinator
from voice_app.health import HealthChecker
from voice_app.latency import configure_latency_logging
from voice_app.session_store import SessionStore
from voice_app.tts import TTSClient
from voice_app.speaker import SpeakerAuthorizations, SpeakerGate, SpeakerProfileStore, SpeakerVerifierClient
from voice_app.speaker_routes import install_speaker_routes


@dataclass(slots=True)
class AppServices:
    asr: object
    tts: object
    sessions: object
    health: object
    coordinator: object
    speaker: object | None = None
    speaker_verifier: object | None = None
    authorizations: object | None = None


def build_services(settings: Settings) -> AppServices:
    asr = ASRService(settings)
    tts = TTSClient(settings)
    sessions = SessionStore(settings.database_path)
    claude = ClaudeRunner(settings)
    speaker_verifier = SpeakerVerifierClient(
        settings.speaker_url,
        timeout_seconds=settings.speaker_verify_timeout_seconds,
    )
    speaker = SpeakerGate(
        speaker_verifier,
        SpeakerProfileStore(settings.speaker_profile_path),
        settings.speaker_threshold,
        settings.speaker_min_enrollment_similarity,
    )
    authorizations = SpeakerAuthorizations(
        ttl_seconds=settings.speaker_authorization_ttl_seconds
    )
    health = HealthChecker(settings, asr, tts, speaker_verifier)
    coordinator = TurnCoordinator(
        asr=asr,
        claude=claude,
        tts=tts,
        sessions=sessions,
        authorizations=authorizations,
        sentence_queue_size=settings.sentence_queue_size,
        max_frame_bytes=settings.max_ws_frame_bytes,
    )
    return AppServices(
        asr=asr,
        tts=tts,
        sessions=sessions,
        health=health,
        coordinator=coordinator,
        speaker=speaker,
        speaker_verifier=speaker_verifier,
        authorizations=authorizations,
    )


def create_app(settings: Settings | None = None, *, services: AppServices | None = None) -> FastAPI:
    configure_latency_logging()
    config = settings or get_settings()
    runtime = services or build_services(config)
    auth = PairingAuth(config)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        await runtime.sessions.open()
        await runtime.asr.load()
        try:
            yield
        finally:
            close_health = getattr(runtime.health, "close", None)
            if close_health is not None:
                await close_health()
            await runtime.tts.close()
            if runtime.speaker_verifier is not None:
                await runtime.speaker_verifier.close()
            await runtime.sessions.close()

    app = FastAPI(title="Claude Voice", version="0.1.0", lifespan=lifespan)
    app.state.settings = config
    app.state.services = runtime
    app.state.auth = auth
    install_pairing_routes(app, auth)
    if runtime.speaker is not None and runtime.authorizations is not None:
        install_speaker_routes(
            app,
            auth,
            runtime.speaker,
            runtime.authorizations,
            runtime.coordinator,
            runtime.sessions,
            max_audio_bytes=config.asr_sample_rate * 2 * config.speaker_max_audio_seconds,
            default_threshold=config.speaker_threshold,
        )

    @app.get("/health/live")
    async def live() -> dict[str, bool]:
        return {"alive": True}

    @app.get("/health/ready")
    async def ready() -> JSONResponse:
        report = await runtime.health.readiness()
        return JSONResponse(report, status_code=200 if report["ready"] else 503)

    @app.get("/api/session")
    async def session_metadata(request: Request) -> dict[str, str]:
        return {"client_id": auth.require_request(request)}

    @app.websocket("/ws/voice")
    async def voice_socket(websocket: WebSocket) -> None:
        client_id = auth.authenticate_websocket(websocket)
        if client_id is None:
            await websocket.close(code=4401, reason="pairing required")
            return
        await runtime.coordinator.handle_socket(websocket, client_id)

    @app.get("/{requested_path:path}")
    async def frontend(request: Request, requested_path: str) -> FileResponse:
        auth.require_request(request)
        root = config.frontend_dist.resolve()
        candidate = (root / requested_path).resolve()
        if requested_path and candidate.is_relative_to(root) and candidate.is_file():
            return FileResponse(candidate)
        index = root / "index.html"
        if not index.is_file():
            return JSONResponse(
                {"detail": "前端尚未构建"}, status_code=503
            )  # type: ignore[return-value]
        return FileResponse(index)

    return app


app = create_app()
