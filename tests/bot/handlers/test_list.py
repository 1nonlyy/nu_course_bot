"""Tests for ``bot.handlers.list`` (/mysubs, /check)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.filters import CommandObject

from bot.db.database import Database
from bot.handlers.list import cmd_check, cmd_mysubs, format_subscription_lines
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


def _message(uid: int = 200) -> MagicMock:
    msg = MagicMock()
    msg.from_user = MagicMock()
    msg.from_user.id = uid
    wait = MagicMock()
    wait.delete = AsyncMock()
    msg.answer = AsyncMock(return_value=wait)
    return msg


def _cmd_args(text: str | None) -> MagicMock:
    cmd = MagicMock(spec=CommandObject)
    cmd.args = text
    return cmd


@pytest.mark.asyncio
async def test_cmd_check_empty_args_shows_usage() -> None:
    msg = _message()
    scraper = MagicMock(spec=CatalogScraper)
    await cmd_check(msg, _cmd_args(None), scraper)
    scraper.fetch_course_sections.assert_not_called()
    assert "check" in (msg.answer.await_args.args[0] or "").lower()


@pytest.mark.asyncio
async def test_cmd_check_invalid_course_code() -> None:
    msg = _message()
    scraper = MagicMock(spec=CatalogScraper)
    await cmd_check(msg, _cmd_args("%%%"), scraper)
    scraper.fetch_course_sections.assert_not_called()
    assert "формат" in (msg.answer.await_args.args[0] or "").lower()


@pytest.mark.asyncio
async def test_cmd_check_empty_sections_shows_retry_message() -> None:
    """When the scraper returns no sections, user sees the retry-later message."""
    msg = _message()
    scraper = MagicMock(spec=CatalogScraper)
    scraper.fetch_course_sections = AsyncMock(return_value=[])
    scraper.aggregate_snapshot_payload = CatalogScraper().aggregate_snapshot_payload

    await cmd_check(msg, _cmd_args("CSCI 151"), scraper)

    scraper.fetch_course_sections.assert_awaited_once()
    answers = [str(c.args[0] or "") for c in msg.answer.await_args_list]
    assert any("Не удалось получить данные" in a for a in answers)


@pytest.mark.asyncio
async def test_cmd_check_happy_path_replies_with_details() -> None:
    msg = _message()
    scraper = MagicMock(spec=CatalogScraper)
    sections = [_section()]
    scraper.fetch_course_sections = AsyncMock(return_value=sections)
    scraper.aggregate_snapshot_payload = CatalogScraper().aggregate_snapshot_payload

    await cmd_check(msg, _cmd_args("csci 151"), scraper)

    final_texts = [str(c.args[0] or "") for c in msg.answer.await_args_list]
    assert any("Intro CS" in t and "CSCI 151" in t for t in final_texts)


@pytest.mark.asyncio
async def test_cmd_mysubs_from_user_none_no_crash() -> None:
    db = MagicMock()
    msg = MagicMock()
    msg.from_user = None
    await cmd_mysubs(msg, db)
    msg.answer.assert_not_called()


@pytest.mark.asyncio
async def test_format_subscription_lines_empty() -> None:
    db = MagicMock()
    db.list_active_subscriptions = AsyncMock(return_value=[])
    text = await format_subscription_lines(db, 1)
    assert "нет активных" in text.lower()


@pytest.mark.asyncio
async def test_format_subscription_lines_with_snapshot(tmp_path) -> None:
    db = Database(tmp_path / "lst.sqlite")
    await db.init_schema()
    await db.upsert_user(3, None, "u")
    await db.add_subscription(3, "MATH 162")
    await db.upsert_snapshot(
        "MATH 162",
        7,
        "Prof",
        "Mon 10:00",
        {"sections": []},
    )
    text = await format_subscription_lines(db, 3)
    assert "MATH 162" in text
    assert "7" in text
