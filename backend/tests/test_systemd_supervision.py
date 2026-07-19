from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def read(path: str) -> str:
    return (ROOT / path).read_text()


def test_target_aggregates_existing_units_without_restart_propagation():
    target = read("deploy/agent-voice.target")

    for unit in (
        "sing-box.service",
        "searxng.service",
        "sglang-qwen36.service",
        "qwen3-tts.service",
        "speaker-verifier.service",
        "claude-voice.service",
        "claude-voice-bootstrap.service",
        "agent-voice-watchdog.timer",
    ):
        assert unit in target
    assert "PartOf=" not in target
    assert "WantedBy=default.target" in target


def test_searxng_manager_is_scoped_and_non_destructive():
    script = read("scripts/manage-searxng.sh")

    assert "docker compose" in script
    assert 'up -d searxng' in script
    assert 'stop searxng' in script
    for destructive in ("down", "docker rm", "volume rm", "system prune"):
        assert destructive not in script


def test_searxng_unit_is_a_remain_after_exit_wrapper():
    unit = read("deploy/searxng.service")

    assert "Type=oneshot" in unit
    assert "RemainAfterExit=yes" in unit
    assert "manage-searxng.sh start" in unit
    assert "manage-searxng.sh stop" in unit


def test_watchdog_timer_has_bounded_schedule():
    timer = read("deploy/agent-voice-watchdog.timer")

    assert "OnBootSec=3min" in timer
    assert "OnUnitActiveSec=60s" in timer
    assert "RandomizedDelaySec=10s" in timer
    assert "Persistent=true" in timer


def test_watchdog_unit_can_write_only_its_state_directory():
    unit = read("deploy/agent-voice-watchdog.service")

    assert "Type=oneshot" in unit
    assert "UMask=0077" in unit
    assert "NoNewPrivileges=true" in unit
    assert "ProtectSystem=strict" in unit
    assert "ProtectHome=read-only" in unit
    assert "ReadWritePaths=%h/.local/state/claude-voice" in unit


def test_gateway_orders_after_local_dependencies_without_part_of():
    gateway = read("deploy/claude-voice.service")

    for unit in (
        "sing-box.service",
        "searxng.service",
        "sglang-qwen36.service",
        "qwen3-tts.service",
        "speaker-verifier.service",
    ):
        assert f"Wants={unit}" in gateway
        assert f"After={unit}" in gateway
    assert "PartOf=" not in gateway


def test_installer_wires_supervision_units_idempotently():
    installer = read("scripts/install-user-services.sh")

    for unit in (
        "agent-voice.target",
        "searxng.service",
        "agent-voice-watchdog.service",
        "agent-voice-watchdog.timer",
    ):
        assert f'"$PROJECT_ROOT/deploy/{unit}"' in installer
        assert f'"$SERVICE_DIR/{unit}"' in installer
        assert f'"$SERVICE_DIR/{unit}"' in installer[installer.index("systemd-analyze") :]
    assert "systemctl --user enable searxng.service agent-voice.target" in installer
    assert "agent-voice-watchdog.timer" in installer
    assert "systemctl --user start agent-voice.target agent-voice-watchdog.timer" in installer


def test_runbook_documents_status_logs_and_non_cascading_rollback():
    runbook = read("docs/systemd-supervision.md")

    for command in (
        "systemctl --user start agent-voice.target",
        "systemctl --user status agent-voice.target",
        "systemctl --user status agent-voice-watchdog.timer",
        "journalctl --user -u agent-voice-watchdog.service -f",
        "systemctl --user disable --now agent-voice-watchdog.timer",
    ):
        assert command in runbook
    assert "sglang-qwen36.service" in runbook
    assert "不会停止成员服务" in runbook
