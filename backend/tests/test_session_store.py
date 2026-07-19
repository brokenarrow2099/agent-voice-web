import asyncio
import sqlite3

import pytest

from voice_app.session_store import SessionStore


@pytest.fixture
async def store(tmp_path):
    value = SessionStore(tmp_path / "sessions.sqlite3")
    await value.open()
    yield value
    await value.close()


async def test_get_or_create_persists_client(store):
    first = await store.get_or_create("phone-a")
    second = await store.get_or_create("phone-a")

    assert first.client_id == "phone-a"
    assert first.claude_session_id is None
    assert second.created_at == first.created_at


async def test_set_and_resume_claude_session(store):
    await store.get_or_create("phone-a")
    await store.set_claude_session("phone-a", "session-one")
    resumed = await store.get_or_create("phone-a")

    assert resumed.claude_session_id == "session-one"
    assert resumed.updated_at >= resumed.created_at


async def test_replacing_conversation_changes_only_target_client(store):
    await store.set_claude_session("phone-a", "old")
    await store.set_claude_session("phone-b", "other")
    await store.set_claude_session("phone-a", "new")

    assert (await store.get_or_create("phone-a")).claude_session_id == "new"
    assert (await store.get_or_create("phone-b")).claude_session_id == "other"


async def test_concurrent_upserts_do_not_lose_clients(store):
    await asyncio.gather(
        *(store.set_claude_session(f"phone-{index}", f"session-{index}") for index in range(25))
    )
    sessions = await asyncio.gather(
        *(store.get_or_create(f"phone-{index}") for index in range(25))
    )
    assert {item.claude_session_id for item in sessions} == {
        f"session-{index}" for index in range(25)
    }


async def test_delete_starts_a_fresh_conversation(store):
    await store.set_claude_session("phone-a", "old")
    await store.clear_claude_session("phone-a")
    assert (await store.get_or_create("phone-a")).claude_session_id is None


async def test_speaker_threshold_is_isolated_and_persists(tmp_path):
    path = tmp_path / "sessions.sqlite3"
    first = SessionStore(path)
    await first.open()
    assert await first.get_speaker_threshold("phone-a", 0.60) == 0.60
    assert await first.set_speaker_threshold("phone-a", 0.52) == 0.52
    assert await first.set_speaker_threshold("phone-b", 0.71) == 0.71
    await first.close()

    reopened = SessionStore(path)
    await reopened.open()
    assert await reopened.get_speaker_threshold("phone-a", 0.60) == 0.52
    assert await reopened.get_speaker_threshold("phone-b", 0.60) == 0.71
    await reopened.close()


async def test_speaker_threshold_migrates_existing_database(tmp_path):
    path = tmp_path / "sessions.sqlite3"
    connection = sqlite3.connect(path)
    connection.execute(
        "CREATE TABLE voice_sessions ("
        "client_id TEXT PRIMARY KEY, claude_session_id TEXT, "
        "created_at TEXT NOT NULL, updated_at TEXT NOT NULL)"
    )
    connection.commit()
    connection.close()

    migrated = SessionStore(path)
    await migrated.open()
    assert await migrated.get_speaker_threshold("phone", 0.60) == 0.60
    await migrated.close()

    connection = sqlite3.connect(path)
    columns = {row[1] for row in connection.execute("PRAGMA table_info(voice_sessions)")}
    connection.close()
    assert "speaker_threshold" in columns
