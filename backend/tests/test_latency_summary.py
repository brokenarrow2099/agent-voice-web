from __future__ import annotations

import importlib.util
import io
from pathlib import Path


SCRIPT = Path(__file__).parents[2] / "scripts" / "summarize-voice-latency.py"


def load_module():
    spec = importlib.util.spec_from_file_location("latency_summary", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_parser_ignores_unknown_malformed_and_secret_like_fields() -> None:
    module = load_module()
    lines = [
        "voice[1]: event=turn_backend asr_ms=20 model_first_text_ms=80 token=private",
        "voice[1]: event=turn_backend asr_ms=40 model_first_text_ms=bad SecretKey=hidden",
        "voice[1]: event=turn_client commit_to_first_audio_ms=1200 transcript=private",
        "voice[1]: event=turn_backend asr_ms=-1 unknown_ms=77",
    ]

    samples = module.parse_lines(lines)

    assert samples == {
        "asr_ms": [20.0, 40.0],
        "model_first_text_ms": [80.0],
        "commit_to_first_audio_ms": [1200.0],
    }


def test_event_filter_and_nearest_rank_summary() -> None:
    module = load_module()
    lines = [
        f"event=turn_backend asr_ms={value} model_total_ms={value * 2}"
        for value in range(1, 11)
    ]
    lines.append("event=turn_client asr_ms=999")

    samples = module.parse_lines(lines, event="turn_backend")
    result = module.summarize(samples)

    assert result["asr_ms"] == {
        "count": 10,
        "median": 5.5,
        "p90": 9.0,
        "max": 10.0,
    }


def test_render_never_echoes_raw_journal_content() -> None:
    module = load_module()
    output = io.StringIO()
    module.render(
        module.parse_lines(["event=turn_backend asr_ms=25 transcript=DO_NOT_PRINT"]),
        output,
    )

    rendered = output.getvalue()
    assert "asr_ms" in rendered
    assert "25.0" in rendered
    assert "DO_NOT_PRINT" not in rendered
    assert "transcript" not in rendered
