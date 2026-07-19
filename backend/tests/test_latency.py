import io
import logging

from voice_app.latency import TurnMetrics, configure_latency_logging, safe_client_label


class Clock:
    def __init__(self) -> None:
        self.value = 10.0

    def __call__(self) -> float:
        return self.value

    def advance(self, milliseconds: float) -> None:
        self.value += milliseconds / 1000


def test_turn_metrics_measure_known_stages_without_wall_clock_values() -> None:
    clock = Clock()
    metrics = TurnMetrics(turn_id=2, generation_id=4, audio_ms=1000.0, clock=clock)
    metrics.start_asr()
    clock.advance(25)
    metrics.finish_asr()
    metrics.start_model()
    clock.advance(40)
    metrics.mark_model_first_text()
    clock.advance(10)
    metrics.mark_first_sentence()
    metrics.start_tts()
    clock.advance(15)
    metrics.mark_tts_first_audio()
    metrics.increment_sentence_count()

    snapshot = metrics.public_snapshot(final=False)
    assert snapshot["asr_ms"] == 25.0
    assert snapshot["model_first_text_ms"] == 40.0
    assert snapshot["first_sentence_ms"] == 50.0
    assert snapshot["tts_first_audio_ms"] == 15.0
    assert snapshot["response_first_audio_ms"] == 90.0
    assert snapshot["sentence_count"] == 1
    assert "started_at" not in snapshot


def test_client_label_is_stable_short_and_not_the_raw_uuid() -> None:
    client_id = "6f902600-775f-4b66-83e5-e172c5f27e95"
    label = safe_client_label(client_id)
    assert label == safe_client_label(client_id)
    assert len(label) == 10
    assert label not in client_id


def test_latency_logger_uses_uvicorn_error_handlers_in_production() -> None:
    latency_logger = logging.getLogger("voice_app.latency")
    uvicorn_parent_logger = logging.getLogger("uvicorn")
    uvicorn_logger = logging.getLogger("uvicorn.error")
    previous_latency = (list(latency_logger.handlers), latency_logger.level, latency_logger.propagate)
    previous_uvicorn_parent = list(uvicorn_parent_logger.handlers)
    previous_uvicorn = list(uvicorn_logger.handlers)
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    try:
        uvicorn_parent_logger.handlers = [handler]
        uvicorn_logger.handlers = []
        configure_latency_logging()
        latency_logger.info("event=turn_backend asr_ms=25.0")
        assert "event=turn_backend asr_ms=25.0" in output.getvalue()
        assert latency_logger.propagate is False
    finally:
        latency_logger.handlers, latency_logger.level, latency_logger.propagate = previous_latency
        uvicorn_parent_logger.handlers = previous_uvicorn_parent
        uvicorn_logger.handlers = previous_uvicorn
