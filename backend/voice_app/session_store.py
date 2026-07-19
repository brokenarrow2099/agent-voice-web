from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import UTC, datetime
import math
from pathlib import Path

import aiosqlite


@dataclass(frozen=True, slots=True)
class VoiceSession:
    client_id: str
    claude_session_id: str | None
    created_at: str
    updated_at: str


class SessionStore:
    def __init__(self, database_path: Path) -> None:
        self.database_path = database_path
        self._database: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._database = await aiosqlite.connect(self.database_path)
        self._database.row_factory = aiosqlite.Row
        await self._database.execute("PRAGMA journal_mode=WAL")
        await self._database.execute("PRAGMA synchronous=NORMAL")
        await self._database.execute(
            """
            CREATE TABLE IF NOT EXISTS voice_sessions (
                client_id TEXT PRIMARY KEY,
                claude_session_id TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        cursor = await self._database.execute("PRAGMA table_info(voice_sessions)")
        columns = {str(row[1]) for row in await cursor.fetchall()}
        await cursor.close()
        if "speaker_threshold" not in columns:
            await self._database.execute(
                "ALTER TABLE voice_sessions ADD COLUMN speaker_threshold REAL"
            )
        await self._database.commit()

    async def close(self) -> None:
        if self._database is not None:
            await self._database.close()
            self._database = None

    async def get_or_create(self, client_id: str) -> VoiceSession:
        database = self._require_database()
        now = _now()
        async with self._lock:
            await database.execute(
                "INSERT OR IGNORE INTO voice_sessions "
                "(client_id, claude_session_id, created_at, updated_at) VALUES (?, NULL, ?, ?)",
                (client_id, now, now),
            )
            await database.commit()
            cursor = await database.execute(
                "SELECT client_id, claude_session_id, created_at, updated_at "
                "FROM voice_sessions WHERE client_id = ?",
                (client_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()
        if row is None:  # pragma: no cover - protected by INSERT in the same lock
            raise RuntimeError("failed to create voice session")
        return VoiceSession(**dict(row))

    async def set_claude_session(self, client_id: str, session_id: str) -> None:
        database = self._require_database()
        now = _now()
        async with self._lock:
            await database.execute(
                """
                INSERT INTO voice_sessions (client_id, claude_session_id, created_at, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(client_id) DO UPDATE SET
                    claude_session_id = excluded.claude_session_id,
                    updated_at = excluded.updated_at
                """,
                (client_id, session_id, now, now),
            )
            await database.commit()

    async def clear_claude_session(self, client_id: str) -> None:
        database = self._require_database()
        now = _now()
        async with self._lock:
            await database.execute(
                "UPDATE voice_sessions SET claude_session_id = NULL, updated_at = ? WHERE client_id = ?",
                (now, client_id),
            )
            await database.commit()

    async def get_speaker_threshold(self, client_id: str, default: float) -> float:
        if not math.isfinite(default):
            raise ValueError("speaker threshold default must be finite")
        await self.get_or_create(client_id)
        database = self._require_database()
        async with self._lock:
            cursor = await database.execute(
                "SELECT speaker_threshold FROM voice_sessions WHERE client_id = ?",
                (client_id,),
            )
            row = await cursor.fetchone()
            await cursor.close()
        if row is None or row["speaker_threshold"] is None:
            return float(default)
        threshold = float(row["speaker_threshold"])
        return threshold if math.isfinite(threshold) else float(default)

    async def set_speaker_threshold(self, client_id: str, threshold: float) -> float:
        value = float(threshold)
        if not math.isfinite(value):
            raise ValueError("speaker threshold must be finite")
        await self.get_or_create(client_id)
        database = self._require_database()
        now = _now()
        async with self._lock:
            await database.execute(
                "UPDATE voice_sessions SET speaker_threshold = ?, updated_at = ? "
                "WHERE client_id = ?",
                (value, now, client_id),
            )
            await database.commit()
        return value

    def _require_database(self) -> aiosqlite.Connection:
        if self._database is None:
            raise RuntimeError("SessionStore.open() must be called first")
        return self._database


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds")
