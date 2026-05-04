"""initial schema

Revision ID: 0001
Revises:
Create Date: 2026-05-05

This is a *baseline* migration. It defines the schema that ``bot/db/database.py``
has historically created via ``init_schema()`` so that:

* Fresh databases come up exactly as before, just managed by Alembic.
* Existing dev/prod databases that were originally created by ``init_schema``
  can be stamped with ``alembic stamp head`` (or upgraded — the DDL is
  idempotent thanks to ``IF NOT EXISTS``) without recreating tables or losing
  data.

Future schema changes should ship as new revisions on top of this one.
"""

from __future__ import annotations

from alembic import op

# Alembic identifiers
revision: str = "0001"
down_revision: str | None = None
branch_labels: tuple[str, ...] | None = None
depends_on: tuple[str, ...] | None = None


# Single source of truth: keep this in lock-step with the SCHEMA constant in
# ``bot/db/database.py``. Future migrations should ALTER from this baseline.
_UP_SQL: tuple[str, ...] = (
    """
    CREATE TABLE IF NOT EXISTS users (
        telegram_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        created_at TEXT NOT NULL DEFAULT (datetime('now'))
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS subscriptions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
        course_code TEXT NOT NULL,
        is_active INTEGER NOT NULL DEFAULT 1,
        created_at TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(user_id, course_code)
    )
    """,
    """
    CREATE INDEX IF NOT EXISTS idx_subscriptions_active_code
    ON subscriptions (course_code, is_active)
    """,
    """
    CREATE TABLE IF NOT EXISTS course_snapshots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        course_code TEXT NOT NULL UNIQUE,
        available_seats INTEGER NOT NULL DEFAULT 0,
        instructor TEXT,
        schedule TEXT,
        last_checked TEXT NOT NULL DEFAULT (datetime('now')),
        raw_json TEXT
    )
    """,
    """
    CREATE TABLE IF NOT EXISTS user_notification_state (
        user_id INTEGER NOT NULL,
        course_code TEXT NOT NULL,
        notified_at_seats INTEGER NOT NULL,
        PRIMARY KEY (user_id, course_code)
    )
    """,
)

# Order matters on the way down: drop dependents (subscriptions, FK to users)
# before parents.
_DOWN_SQL: tuple[str, ...] = (
    "DROP TABLE IF EXISTS user_notification_state",
    "DROP TABLE IF EXISTS course_snapshots",
    "DROP INDEX IF EXISTS idx_subscriptions_active_code",
    "DROP TABLE IF EXISTS subscriptions",
    "DROP TABLE IF EXISTS users",
)


def upgrade() -> None:
    for stmt in _UP_SQL:
        op.execute(stmt)


def downgrade() -> None:
    for stmt in _DOWN_SQL:
        op.execute(stmt)
