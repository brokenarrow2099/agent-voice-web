from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
import fcntl
import json
import os
from pathlib import Path
import socket
import ssl
import subprocess
import tempfile
import time
from typing import Literal
from urllib import request


ProbeKind = Literal["http", "https", "tcp"]
Event = dict[str, object]


@dataclass(frozen=True)
class Component:
    name: str
    unit: str
    kind: ProbeKind
    endpoint: str


@dataclass(frozen=True)
class ProbeResult:
    healthy: bool
    latency_ms: int


class Watchdog:
    def __init__(
        self,
        components: Iterable[Component],
        state_path: Path,
        probe: Callable[[Component], ProbeResult],
        unit_state: Callable[[str], str],
        restart: Callable[[str], None],
        now: Callable[[], float],
        emit: Callable[[Event], None],
        *,
        threshold: int = 2,
        cooldown_seconds: int = 600,
    ) -> None:
        self.components = tuple(components)
        self.state_path = state_path
        self.probe = probe
        self.unit_state = unit_state
        self.restart = restart
        self.now = now
        self.emit = emit
        self.threshold = threshold
        self.cooldown_seconds = cooldown_seconds

    def run_once(self) -> int:
        state = self._load_state()
        component_states = state.setdefault("components", {})

        for component in self.components:
            saved = component_states.setdefault(
                component.name,
                {"failures": 0, "last_restart": 0.0},
            )
            if self.unit_state(component.unit) == "activating":
                saved["failures"] = 0
                self._emit(component, "activating", 0, "skip", 0)
                continue

            try:
                result = self.probe(component)
            except Exception:
                result = ProbeResult(False, 0)

            if result.healthy:
                saved["failures"] = 0
                self._emit(component, "healthy", 0, "none", result.latency_ms)
                continue

            failures = min(int(saved.get("failures", 0)) + 1, self.threshold)
            saved["failures"] = failures
            action = "none"
            if failures >= self.threshold:
                current_time = self.now()
                last_restart = float(saved.get("last_restart", 0.0))
                if last_restart and current_time - last_restart < self.cooldown_seconds:
                    action = "cooldown"
                else:
                    self.restart(component.unit)
                    saved["last_restart"] = current_time
                    saved["failures"] = 0
                    failures = 0
                    action = "restart"
            self._emit(
                component,
                "unhealthy",
                failures,
                action,
                result.latency_ms,
            )

        self._save_state(state)
        return 0

    def _load_state(self) -> dict[str, object]:
        try:
            loaded = json.loads(self.state_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            return {"components": {}}
        if not isinstance(loaded, dict) or not isinstance(
            loaded.get("components"), dict
        ):
            return {"components": {}}
        return loaded

    def _save_state(self, state: dict[str, object]) -> None:
        self.state_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        temporary_name = ""
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self.state_path.parent,
                prefix=".watchdog-",
                delete=False,
            ) as temporary:
                temporary_name = temporary.name
                os.fchmod(temporary.fileno(), 0o600)
                json.dump(state, temporary, separators=(",", ":"), sort_keys=True)
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, self.state_path)
        finally:
            if temporary_name:
                try:
                    os.unlink(temporary_name)
                except FileNotFoundError:
                    pass

    def _emit(
        self,
        component: Component,
        status: str,
        failures: int,
        action: str,
        latency_ms: int,
    ) -> None:
        self.emit(
            {
                "component": component.name,
                "status": status,
                "failures": failures,
                "action": action,
                "latency_ms": latency_ms,
            }
        )


def default_components() -> tuple[Component, ...]:
    return (
        Component(
            "sglang",
            "sglang-qwen36.service",
            "http",
            "http://127.0.0.1:8060/health",
        ),
        Component(
            "searxng",
            "searxng.service",
            "http",
            "http://127.0.0.1:8081/healthz",
        ),
        Component("sing-box", "sing-box.service", "tcp", "127.0.0.1:10809"),
        Component(
            "qwen3-tts",
            "qwen3-tts.service",
            "http",
            "http://127.0.0.1:8766/health",
        ),
        Component(
            "speaker-verifier",
            "speaker-verifier.service",
            "http",
            "http://127.0.0.1:8767/health",
        ),
        Component(
            "gateway",
            "claude-voice.service",
            "https",
            "https://127.0.0.1:8443/health/ready",
        ),
    )


def probe_component(component: Component) -> ProbeResult:
    started = time.perf_counter()
    healthy = False
    try:
        if component.kind == "tcp":
            host, separator, port = component.endpoint.rpartition(":")
            if not separator:
                raise ValueError("TCP endpoint must use host:port")
            with socket.create_connection((host, int(port)), timeout=3):
                healthy = True
        else:
            context = ssl._create_unverified_context() if component.kind == "https" else None
            with request.urlopen(component.endpoint, timeout=3, context=context) as response:
                healthy = response.status == 200
    except (OSError, ValueError):
        healthy = False
    elapsed = max(0, round((time.perf_counter() - started) * 1000))
    return ProbeResult(healthy, elapsed)


def systemd_unit_state(unit: str) -> str:
    result = subprocess.run(
        ["systemctl", "--user", "show", unit, "--property=ActiveState", "--value"],
        check=False,
        capture_output=True,
        text=True,
        timeout=5,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def restart_user_unit(unit: str) -> None:
    subprocess.run(
        ["systemctl", "--user", "restart", unit],
        check=False,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        timeout=120,
    )


def emit_json(event: Event) -> None:
    print(json.dumps(event, ensure_ascii=True, separators=(",", ":"), sort_keys=True))


def main() -> int:
    state_dir = Path.home() / ".local" / "state" / "claude-voice"
    state_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
    lock_path = state_dir / "watchdog.lock"
    lock_descriptor = os.open(lock_path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        try:
            fcntl.flock(lock_descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        return Watchdog(
            default_components(),
            state_dir / "watchdog.json",
            probe_component,
            systemd_unit_state,
            restart_user_unit,
            time.time,
            emit_json,
        ).run_once()
    finally:
        os.close(lock_descriptor)
