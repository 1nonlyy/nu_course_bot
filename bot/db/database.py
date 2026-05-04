"""SQLite connection helper, schema creation, and data access."""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

import aiosqlite

from bot.config import Settings, get_settings
from bot.db.models import CourseSnapshot, Subscription

logger = logging.getLogger(__name__)

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    telegram_id INTEGER PRIMARY KEY,
    username TEXT,
    first_name TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS subscriptions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
    course_code TEXT NOT NULL,
    is_active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(user_id, course_code)
);

CREATE INDEX IF NOT EXISTS idx_subscriptions_active_code
ON subscriptions (course_code, is_active);

CREATE TABLE IF NOT EXISTS course_snapshots (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_code TEXT NOT NULL UNIQUE,
    available_seats INTEGER NOT NULL DEFAULT 0,
    instructor TEXT,
    schedule TEXT,
    last_checked TEXT NOT NULL DEFAULT (datetime('now')),
    raw_json TEXT
);
"""


def _utc_now_iso() -> str:
    """Return current UTC time in ISO format."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Database:
    """Thin async repository over aiosqlite."""

    def __init__(self, db_path: Path) -> None:
        self._db_path = db_path

    @asynccontextmanager
    async def connect(self) -> AsyncIterator[aiosqlite.Connection]:
        """Open a connection with foreign keys and WAL; closes when the block exits."""
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self._db_path) as conn:
            await conn.execute("PRAGMA foreign_keys = ON")
            await conn.execute("PRAGMA journal_mode = WAL")
            yield conn

    async def init_schema(self) -> None:
        """Create tables and indexes if they do not exist."""
        async with self.connect() as conn:
            await conn.executescript(SCHEMA)
            await conn.commit()
        logger.info("Database schema ensured at %s", self._db_path)

    @asynccontextmanager
    async def session(self) -> AsyncIterator[aiosqlite.Connection]:
        """Yield a connection with transaction commit/rollback."""
        async with self.connect() as conn:
            try:
                yield conn
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise

    async def upsert_user(
        self,
        telegram_id: int,
        username: Optional[str],
        first_name: Optional[str],
    ) -> None:
        """Insert or update a Telegram user."""
        async with self.session() as conn:
            await conn.execute(
                """
                INSERT INTO users (telegram_id, username, first_name, created_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(telegram_id) DO UPDATE SET
                    username = excluded.username,
                    first_name = excluded.first_name
                """,
                (telegram_id, username, first_name, _utc_now_iso()),
            )

    async def add_subscription(self, user_id: int, course_code: str) -> int:
        """Create or reactivate a subscription; return subscription id."""
        async with self.session() as conn:
            await conn.execute(
                """
                INSERT INTO subscriptions (user_id, course_code, is_active, created_at)
                VALUES (?, ?, 1, ?)
                ON CONFLICT(user_id, course_code) DO UPDATE SET is_active = 1
                """,
                (user_id, course_code.upper(), _utc_now_iso()),
            )
            cur = await conn.execute(
                "SELECT id FROM subscriptions WHERE user_id = ? AND course_code = ?",
                (user_id, course_code.upper()),
            )
            row = await cur.fetchone()
            if row is None:
                raise RuntimeError("Failed to read subscription id")
            return int(row[0])

    async def deactivate_subscription(self, user_id: int, course_code: str) -> int:
        """Deactivate subscription; return number of rows updated."""
        async with self.session() as conn:
            cur = await conn.execute(
                """
                UPDATE subscriptions SET is_active = 0
                WHERE user_id = ? AND course_code = ? AND is_active = 1
                """,
                (user_id, course_code.upper()),
            )
            return cur.rowcount if cur.rowcount is not None else 0

    async def list_active_subscriptions(self, user_id: int) -> list[Subscription]:
        """Return active subscriptions for a user."""
        async with self.connect() as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                """
                SELECT id, user_id, course_code, is_active, created_at
                FROM subscriptions
                WHERE user_id = ? AND is_active = 1
                ORDER BY course_code
                """,
                (user_id,),
            )
            rows = await cur.fetchall()
        return [_subscription_from_row(dict(r)) for r in rows]

    async def all_active_subscriptions_grouped(self) -> dict[str, list[int]]:
        """Map normalized course_code -> list of subscriber telegram ids."""
        async with self.connect() as conn:
            cur = await conn.execute(
                """
                SELECT course_code, user_id
                FROM subscriptions
                WHERE is_active = 1
                ORDER BY course_code, user_id
                """
            )
            rows = await cur.fetchall()
        result: dict[str, list[int]] = {}
        for code, uid in rows:
            result.setdefault(code, []).append(int(uid))
        return result

    async def get_snapshot(self, course_code: str) -> Optional[CourseSnapshot]:
        """Return last snapshot for a course code, if any."""
        async with self.connect() as conn:
            conn.row_factory = aiosqlite.Row
            cur = await conn.execute(
                """
                SELECT id, course_code, available_seats, instructor, schedule,
                       last_checked, raw_json
                FROM course_snapshots WHERE course_code = ?
                """,
                (course_code.upper(),),
            )
            row = await cur.fetchone()
        if row is None:
            return None
        return _snapshot_from_row(dict(row))

    async def upsert_snapshot(
        self,
        course_code: str,
        available_seats: int,
        instructor: Optional[str],
        schedule: Optional[str],
        raw_payload: Any,
    ) -> None:
        """Insert or update enrollment snapshot."""
        raw_json = json.dumps(raw_payload, ensure_ascii=False)
        async with self.session() as conn:
            await conn.execute(
                """
                INSERT INTO course_snapshots
                    (course_code, available_seats, instructor, schedule, last_checked, raw_json)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(course_code) DO UPDATE SET
                    available_seats = excluded.available_seats,
                    instructor = excluded.instructor,
                    schedule = excluded.schedule,
                    last_checked = excluded.last_checked,
                    raw_json = excluded.raw_json
                """,
                (
                    course_code.upper(),
                    available_seats,
                    instructor,
                    schedule,
                    _utc_now_iso(),
                    raw_json,
                ),
            )


def _subscription_from_row(d: dict[str, Any]) -> Subscription:
    created = datetime.fromisoformat(str(d["created_at"]))
    return Subscription(
        id=int(d["id"]),
        user_id=int(d["user_id"]),
        course_code=str(d["course_code"]),
        is_active=bool(d["is_active"]),
        created_at=created,
    )


def _snapshot_from_row(d: dict[str, Any]) -> CourseSnapshot:
    last_checked = datetime.fromisoformat(str(d["last_checked"]))
    return CourseSnapshot(
        id=int(d["id"]),
        course_code=str(d["course_code"]),
        available_seats=int(d["available_seats"]),
        instructor=d["instructor"] if d["instructor"] is not None else None,
        schedule=d["schedule"] if d["schedule"] is not None else None,
        last_checked=last_checked,
        raw_json=d["raw_json"] if d["raw_json"] is not None else None,
    )


_db_singleton: Optional[Database] = None


def get_database(settings: Optional[Settings] = None) -> Database:
    """Return process-wide Database instance."""
    global _db_singleton
    if _db_singleton is None:
        cfg = settings or get_settings()
        _db_singleton = Database(cfg.sqlite_path)
    return _db_singleton
