"""Tests for ``bot.handlers.subscribe`` (mocked Telegram + scraper)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.filters import CommandObject

from bot.db.database import Database
from bot.handlers.subscribe import cmd_subscribe, cmd_unsubscribe
from bot.scraper.catalog import CatalogScraper, CourseInfo

pytestmark = pytest.mark.usefixtures("settings_env")


def _section(**kwargs: object) -> CourseInfo:
    base = dict(
        course_code="CSCI 151",
        course_title="Intro CS",
        instructor_name="Prof A",
        schedule="01L · Mon",
        schedule_body="Mon",
        available_seats=2,
        total_seats=40,
        section_type="01L",
        course_id="c1",
        instance_id="i1",
    )
    base.update(kwargs)
    return CourseInfo(**base)


def _message_with_user(uid: int = 100) -> MagicMock:
    msg = MagicMock()
    msg.from_user = MagicMock()
    msg.from_user.id = uid
    msg.from_user.username = "tester"
    msg.from_user.full_name = "Test User"
    msg.from_user.first_name = "Test"
    wait = MagicMock()
    wait.delete = AsyncMock()
    msg.answer = AsyncMock(return_value=wait)
    return msg


def _command_args(text: str | None) -> MagicMock:
    cmd = MagicMock(spec=CommandObject)
    cmd.args = text
    return cmd


@pytest.mark.asyncio
async def test_subscribe_empty_args_prompts(tmp_path) -> None:
    db = Database(tmp_path / "x.sqlite")
    scraper = MagicMock()
    msg = _message_with_user()
    await cmd_subscribe(msg, _command_args(None), db, scraper)
    scraper.fetch_course_sections.assert_not_called()
    msg.answer.assert_awaited()
    assert "CSCI 151" in (msg.answer.await_args.args[0] or "")


@pytest.mark.asyncio
async def test_subscribe_invalid_code_format(tmp_path) -> None:
    db = Database(tmp_path / "x.sqlite")
    scraper = MagicMock()
    msg = _message_with_user()
    await cmd_subscribe(msg, _command_args("NOTACOURSE"), db, scraper)
    scraper.fetch_course_sections.assert_not_called()
    msg.answer.assert_awaited()


@pytest.mark.asyncio
async def test_subscribe_course_not_found_no_subscription(tmp_path) -> None:
    """Empty sections: catalog miss / error path — no DB subscription or notify state."""
    db = Database(tmp_path / "x.sqlite")
    await db.init_schema()
    scraper = MagicMock()
    scraper.fetch_course_sections = AsyncMock(return_value=[])
    real = CatalogScraper()
    scraper.aggregate_snapshot_payload = real.aggregate_snapshot_payload
    msg = _message_with_user()

    await cmd_subscribe(msg, _command_args("CSCI 151"), db, scraper)

    scraper.fetch_course_sections.assert_awaited()
    subs = await db.list_active_subscriptions(100)
    assert subs == []
    assert await db.get_user_notification_seats(100, "CSCI 151") is None


@pytest.mark.asyncio
async def test_subscribe_happy_path_sets_notification_baseline(tmp_path) -> None:
    db = Database(tmp_path / "x.sqlite")
    await db.init_schema()
    sections = [_section(available_seats=3)]
    scraper = MagicMock()
    scraper.fetch_course_sections = AsyncMock(return_value=sections)
    real = CatalogScraper()
    scraper.aggregate_snapshot_payload = real.aggregate_snapshot_payload
    msg = _message_with_user(uid=55)

    await cmd_subscribe(msg, _command_args("CSCI 151"), db, scraper)

    subs = await db.list_active_subscriptions(55)
    assert len(subs) == 1
    assert await db.get_user_notification_seats(55, "CSCI 151") == 3
    snap = await db.get_snapshot("CSCI 151")
    assert snap is not None
    assert snap.available_seats == 3
    assert msg.answer.await_count >= 2


@pytest.mark.asyncio
async def test_subscribe_zero_seats_still_records_baseline(tmp_path) -> None:
    """Edge: subscribe while full — notified_at_seats = 0 for future 0→N opens."""
    db = Database(tmp_path / "x.sqlite")
    await db.init_schema()
    sections = [_section(available_seats=0)]
    scraper = MagicMock()
    scraper.fetch_course_sections = AsyncMock(return_value=sections)
    real = CatalogScraper()
    scraper.aggregate_snapshot_payload = real.aggregate_snapshot_payload
    msg = _message_with_user(uid=66)

    await cmd_subscribe(msg, _command_args("csci 151"), db, scraper)

    assert await db.get_user_notification_seats(66, "CSCI 151") == 0


@pytest.mark.asyncio
async def test_unsubscribe_empty_args(tmp_path) -> None:
    db = Database(tmp_path / "x.sqlite")
    msg = _message_with_user()
    await cmd_unsubscribe(msg, _command_args(None), db)
    msg.answer.assert_awaited()


@pytest.mark.asyncio
async def test_unsubscribe_invalid_code(tmp_path) -> None:
    db = Database(tmp_path / "x.sqlite")
    msg = _message_with_user()
    await cmd_unsubscribe(msg, _command_args("BADCODE"), db)
    msg.answer.assert_awaited()


@pytest.mark.asyncio
async def test_unsubscribe_happy_path_clears_notification_state(tmp_path) -> None:
    db = Database(tmp_path / "x.sqlite")
    await db.init_schema()
    await db.upsert_user(7, None, "U")
    await db.add_subscription(7, "MATH 162")
    await db.upsert_user_notification_state(7, "MATH 162", 1)
    msg = _message_with_user(uid=7)

    await cmd_unsubscribe(msg, _command_args("MATH 162"), db)

    assert await db.get_user_notification_seats(7, "MATH 162") is None
    assert await db.list_active_subscriptions(7) == []


@pytest.mark.asyncio
async def test_unsubscribe_no_active_subscription(tmp_path) -> None:
    db = Database(tmp_path / "x.sqlite")
    await db.init_schema()
    msg = _message_with_user(uid=8)
    await cmd_unsubscribe(msg, _command_args("CSCI 151"), db)
    assert "не было" in (msg.answer.await_args.args[0] or "").lower()


@pytest.mark.asyncio
async def test_subscribe_from_user_none_no_crash() -> None:
    db = MagicMock()
    scraper = MagicMock()
    msg = MagicMock()
    msg.from_user = None
    await cmd_subscribe(msg, _command_args("CSCI 151"), db, scraper)
    db.upsert_user.assert_not_called()
