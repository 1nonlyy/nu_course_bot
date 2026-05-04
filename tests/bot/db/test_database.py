"""Tests for ``bot.db.database`` (SQLite + per-user notification state)."""

from __future__ import annotations

import pytest

from bot.db.database import Database

pytestmark = pytest.mark.usefixtures("settings_env")


@pytest.fixture
def db(tmp_path) -> Database:
    return Database(tmp_path / "notify_test.sqlite")


@pytest.mark.asyncio
async def test_user_notification_state_insert_and_get(db: Database) -> None:
    await db.init_schema()
    assert await db.get_user_notification_seats(42, "csci 151") is None
    await db.upsert_user_notification_state(42, "CSCI 151", 0)
    assert await db.get_user_notification_seats(42, "csci 151") == 0
    await db.upsert_user_notification_state(42, "CSCI 151", 7)
    assert await db.get_user_notification_seats(42, "CSCI 151") == 7


@pytest.mark.asyncio
async def test_deactivate_subscription_deletes_notification_state(db: Database) -> None:
    """Happy path: unsubscribing clears per-user notification baseline."""
    await db.init_schema()
    await db.upsert_user(1, "u", "User")
    await db.add_subscription(1, "MATH 162")
    await db.upsert_user_notification_state(1, "MATH 162", 3)
    assert await db.get_user_notification_seats(1, "MATH 162") == 3

    n = await db.deactivate_subscription(1, "math 162")
    assert n == 1
    assert await db.get_user_notification_seats(1, "MATH 162") is None


@pytest.mark.asyncio
async def test_deactivate_subscription_no_row_leaves_notification_state(
    db: Database,
) -> None:
    """No active subscription: deactivate returns 0 and does not remove orphan notify rows."""
    await db.init_schema()
    await db.upsert_user(9, None, "Ghost")
    await db.upsert_user_notification_state(9, "CSCI 151", 5)

    assert await db.deactivate_subscription(9, "CSCI 151") == 0
    assert await db.get_user_notification_seats(9, "CSCI 151") == 5


@pytest.mark.asyncio
async def test_reactivate_subscription_can_refresh_notification_state(db: Database) -> None:
    """Re-subscribe after unsubscribe starts clean; new upsert sets current seats."""
    await db.init_schema()
    await db.upsert_user(2, None, "A")
    await db.add_subscription(2, "CSCI 151")
    await db.upsert_user_notification_state(2, "CSCI 151", 0)
    await db.deactivate_subscription(2, "CSCI 151")
    assert await db.get_user_notification_seats(2, "CSCI 151") is None

    await db.add_subscription(2, "CSCI 151")
    await db.upsert_user_notification_state(2, "CSCI 151", 4)
    assert await db.get_user_notification_seats(2, "CSCI 151") == 4
