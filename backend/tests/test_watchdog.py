from __future__ import annotations

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from agent_voice_watchdog import (  # noqa: E402
    Component,
    ProbeResult,
    Watchdog,
    default_components,
)


def component() -> Component:
    return Component(
        "tts",
        "qwen3-tts.service",
        "http",
        "http://127.0.0.1:8766/health",
    )


def read_state(path: Path) -> dict[str, object]:
    return json.loads(path.read_text())


def test_second_failure_restarts_only_failed_component(tmp_path: Path):
    restarted: list[str] = []
    watchdog = Watchdog(
        components=(component(),),
        state_path=tmp_path / "watchdog.json",
        probe=lambda _: ProbeResult(False, 12),
        unit_state=lambda _: "active",
        restart=restarted.append,
        now=lambda: 1000.0,
        emit=lambda _: None,
    )

    watchdog.run_once()
    assert restarted == []

    watchdog.run_once()
    assert restarted == ["qwen3-tts.service"]


def test_success_resets_failure_count(tmp_path: Path):
    results = iter((ProbeResult(False, 2), ProbeResult(True, 3)))
    state_path = tmp_path / "watchdog.json"
    watchdog = Watchdog(
        (component(),),
        state_path,
        lambda _: next(results),
        lambda _: "active",
        lambda _: None,
        lambda: 1.0,
        lambda _: None,
    )

    watchdog.run_once()
    watchdog.run_once()

    state = read_state(state_path)
    assert state["components"]["tts"]["failures"] == 0


def test_activating_unit_does_not_accumulate_failure(tmp_path: Path):
    state_path = tmp_path / "watchdog.json"
    watchdog = Watchdog(
        (component(),),
        state_path,
        lambda _: ProbeResult(False, 1),
        lambda _: "activating",
        lambda _: None,
        lambda: 1.0,
        lambda _: None,
    )

    watchdog.run_once()

    state = read_state(state_path)
    assert state["components"]["tts"]["failures"] == 0


def test_cooldown_blocks_repeat_restart(tmp_path: Path):
    restarted: list[str] = []
    now = 1000.0
    watchdog = Watchdog(
        (component(),),
        tmp_path / "watchdog.json",
        lambda _: ProbeResult(False, 1),
        lambda _: "active",
        restarted.append,
        lambda: now,
        lambda _: None,
    )

    watchdog.run_once()
    watchdog.run_once()
    watchdog.run_once()
    watchdog.run_once()

    assert restarted == ["qwen3-tts.service"]


def test_events_and_state_contain_only_bounded_metadata(tmp_path: Path):
    events: list[dict[str, object]] = []
    state_path = tmp_path / "watchdog.json"
    watchdog = Watchdog(
        (component(),),
        state_path,
        lambda _: ProbeResult(False, 1),
        lambda _: "active",
        lambda _: None,
        lambda: 1.0,
        events.append,
    )

    watchdog.run_once()

    assert set(events[0]) == {
        "action",
        "component",
        "failures",
        "latency_ms",
        "status",
    }
    assert set(read_state(state_path)) == {"components"}


def test_default_components_cover_the_full_local_chain():
    components = {item.name: item for item in default_components()}

    assert set(components) == {
        "gateway",
        "qwen3-tts",
        "searxng",
        "sglang",
        "sing-box",
        "speaker-verifier",
    }
    assert components["sglang"].endpoint == "http://127.0.0.1:8060/health"
    assert components["gateway"].kind == "https"
    assert components["sing-box"].kind == "tcp"
