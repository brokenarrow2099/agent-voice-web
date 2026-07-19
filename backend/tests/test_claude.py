from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from voice_app.claude import ClaudeRunner, ClaudeTimeoutError, parse_stream_line
from voice_app.config import Settings


FIXTURE = Path(__file__).parent / "fixtures/claude_stream.jsonl"


def test_parse_realistic_stream_fixture():
    events = [parse_stream_line(line) for line in FIXTURE.read_text().splitlines()]
    events = [item for item in events if item is not None]

    assert events[0].kind == "session"
    assert events[0].session_id == "11111111-1111-4111-8111-111111111111"
    assert [item.text for item in events if item.kind == "text_delta"] == ["先检查一下。"]
    assert [item.tool_name for item in events if item.kind == "tool_start"] == ["Bash"]
    assert not any("secret" in item.text for item in events)
    assert events[-1].kind == "result"
    assert events[-1].text == "先检查一下。已经完成。"


def test_malformed_or_irrelevant_stream_lines_are_ignored():
    assert parse_stream_line("not json") is None
    assert parse_stream_line(json.dumps({"type": "rate_limit_event"})) is None


class FakeStdout:
    def __init__(self, lines: list[str], block: bool = False):
        self.lines = [f"{line}\n".encode() for line in lines]
        self.block = block

    async def readline(self):
        if self.lines:
            return self.lines.pop(0)
        if self.block:
            await asyncio.Future()
        return b""


class FakeStderr:
    def __init__(self, text: str = ""):
        self.text = text

    async def read(self):
        return self.text.encode()


class FakeProcess:
    _next_pid = 41000

    def __init__(self, lines=None, returncode=0, stderr="", block=False):
        self.stdout = FakeStdout(lines or [], block=block)
        self.stderr = FakeStderr(stderr)
        self.returncode = None
        self.final_returncode = returncode
        self.pid = FakeProcess._next_pid
        FakeProcess._next_pid += 1

    async def wait(self):
        if self.returncode is None:
            self.returncode = self.final_returncode
        return self.returncode


def settings(tmp_path, monkeypatch, timeout=5):
    monkeypatch.setenv("VOICE_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("VOICE_CLAUDE_TIMEOUT_SECONDS", str(timeout))
    monkeypatch.setenv("ANTHROPIC_API_KEY", "local-sglang-key")
    return Settings(_env_file=None)


async def test_runner_constructs_local_cli_command_and_environment(tmp_path, monkeypatch):
    captured = []
    lines = FIXTURE.read_text().splitlines()

    async def factory(*args, **kwargs):
        captured.append((args, kwargs))
        return FakeProcess(lines)

    runner = ClaudeRunner(settings(tmp_path, monkeypatch), process_factory=factory)
    output = [event async for event in runner.run("你好", generation_id=3)]

    args, kwargs = captured[0]
    assert args[0] == "/home/agentvoice/.hermes/node/bin/claude"
    assert args[1:3] == ("-p", "你好")
    assert "--output-format" in args and "stream-json" in args
    assert "--include-partial-messages" in args
    assert kwargs["start_new_session"] is True
    assert kwargs["env"]["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:8060"
    assert kwargs["env"]["ANTHROPIC_API_KEY"] == "local-sglang-key"
    assert kwargs["env"]["MAX_THINKING_TOKENS"] == "0"
    assert args[args.index("--settings") + 1] == '{"thinking":false}'
    assert args[args.index("--effort") + 1] == "low"
    assert args[args.index("--disallowedTools") + 1] == "WebSearch,WebFetch"
    web_prompt = args[args.index("--append-system-prompt") + 1]
    assert "scripts/local-web.py search" in web_prompt
    assert "scripts/local-web.py fetch" in web_prompt
    assert output[-1].kind == "result"


async def test_resume_is_used_when_session_exists(tmp_path, monkeypatch):
    calls = []

    async def factory(*args, **kwargs):
        calls.append(args)
        return FakeProcess([json.dumps({"type": "result", "result": "好", "session_id": "s"})])

    runner = ClaudeRunner(settings(tmp_path, monkeypatch), process_factory=factory)
    _ = [item async for item in runner.run("继续", generation_id=1, session_id="old-session")]
    assert "--resume" in calls[0]
    assert calls[0][calls[0].index("--resume") + 1] == "old-session"


async def test_failed_resume_without_output_retries_fresh(tmp_path, monkeypatch):
    processes = [
        FakeProcess(returncode=1, stderr="session not found"),
        FakeProcess([json.dumps({"type": "result", "result": "新会话", "session_id": "new"})]),
    ]
    calls = []

    async def factory(*args, **kwargs):
        calls.append(args)
        return processes.pop(0)

    runner = ClaudeRunner(settings(tmp_path, monkeypatch), process_factory=factory)
    output = [item async for item in runner.run("你好", generation_id=1, session_id="missing")]
    assert len(calls) == 2
    assert "--resume" in calls[0]
    assert "--resume" not in calls[1]
    assert output[-1].text == "新会话"


async def test_cancel_terminates_active_process_group(tmp_path, monkeypatch):
    process = FakeProcess(block=True)
    killed = []

    async def factory(*args, **kwargs):
        return process

    async def terminate(target):
        killed.append(target.pid)
        target.returncode = -15

    runner = ClaudeRunner(
        settings(tmp_path, monkeypatch), process_factory=factory, process_terminator=terminate
    )
    task = asyncio.create_task(anext(runner.run("慢一点", generation_id=9)))
    await asyncio.sleep(0)
    await runner.cancel(9)
    task.cancel()
    await asyncio.gather(task, return_exceptions=True)
    assert killed == [process.pid]


async def test_timeout_reports_whether_a_tool_was_active(tmp_path, monkeypatch):
    tool_line = json.dumps(
        {
            "type": "stream_event",
            "event": {
                "type": "content_block_start",
                "content_block": {"type": "tool_use", "name": "Bash", "id": "t"},
            },
        }
    )

    for lines, expected in [([], False), ([tool_line], True)]:
        process = FakeProcess(lines, block=True)

        async def factory(*args, _process=process, **kwargs):
            return _process

        async def terminate(target):
            target.returncode = -15

        runner = ClaudeRunner(
            settings(tmp_path, monkeypatch, timeout=0.01),
            process_factory=factory,
            process_terminator=terminate,
        )
        with pytest.raises(ClaudeTimeoutError) as caught:
            _ = [item async for item in runner.run("等待", generation_id=1)]
        assert caught.value.active_tool is expected
