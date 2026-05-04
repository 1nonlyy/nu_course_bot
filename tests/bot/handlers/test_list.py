"""Tests for ``bot.handlers.list`` (/mysubs, /check)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.filters import CommandObject

from bot.db.database import Database
import bot.handlers.list as list_handlers
from bot.handlers.list import cmd_check, cmd_mysubs, format_subscription_lines
from bot.scraper.catalog import CatalogScraper, CourseInfo

pytestmark = pytest.mark.usefixtures("settings_env")


@pytest.fixture(autouse=True)
def _clear_check_rate_limit_state() -> None:
    list_handlers._check_last_at.clear()
    yield
    list_handlers._check_last_at.clear()


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
async def test_cmd_check_invalid_input_does_not_set_rate_limit_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Bad args must not consume the user's /check quota (no entry in _check_last_at)."""
    monkeypatch.setattr(list_handlers.time, "time", lambda: 500_000.0)
    msg = _message(uid=42)
    scraper = MagicMock(spec=CatalogScraper)

    await cmd_check(msg, _cmd_args("%%%"), scraper)

    assert 42 not in list_handlers._check_last_at


@pytest.mark.asyncio
async def test_cmd_check_empty_args_does_not_set_rate_limit_timestamp(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(list_handlers.time, "time", lambda: 500_000.0)
    msg = _message(uid=99)
    scraper = MagicMock(spec=CatalogScraper)

    await cmd_check(msg, _cmd_args(None), scraper)

    assert 99 not in list_handlers._check_last_at


@pytest.mark.asyncio
async def test_cmd_check_from_user_none_skips_rate_limit_and_scrapes() -> None:
    """Channels/anonymous updates: no user id — rate limiter skipped, catalog still queried."""
    msg = MagicMock()
    msg.from_user = None
    wait = MagicMock()
    wait.delete = AsyncMock()
    msg.answer = AsyncMock(return_value=wait)
    scraper = MagicMock(spec=CatalogScraper)
    sections = [_section()]
    scraper.fetch_course_sections = AsyncMock(return_value=sections)
    scraper.aggregate_snapshot_payload = CatalogScraper().aggregate_snapshot_payload

    await cmd_check(msg, _cmd_args("CSCI 151"), scraper)

    scraper.fetch_course_sections.assert_awaited_once()
    assert list_handlers._check_last_at == {}


@pytest.mark.asyncio
async def test_cmd_check_rate_limit_exact_interval_allows_next_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When elapsed == check_rate_limit_seconds, the next /check is allowed (strict < in handler)."""
    from bot.config import get_settings

    t0 = 10_000.0
    lim = get_settings().check_rate_limit_seconds
    times = iter([t0, t0 + lim])

    def _fake_time() -> float:
        return next(times)

    monkeypatch.setattr(list_handlers.time, "time", _fake_time)
    msg = _message()
    scraper = MagicMock(spec=CatalogScraper)
    sections = [_section()]
    scraper.fetch_course_sections = AsyncMock(return_value=sections)
    scraper.aggregate_snapshot_payload = CatalogScraper().aggregate_snapshot_payload

    await cmd_check(msg, _cmd_args("CSCI 151"), scraper)
    await cmd_check(msg, _cmd_args("MATH 162"), scraper)

    assert scraper.fetch_course_sections.await_count == 2


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
async def test_cmd_check_rate_limited_second_call_within_window(monkeypatch: pytest.MonkeyPatch) -> None:
    times = iter([1_000_000.0, 1_000_005.0])

    def _fake_time() -> float:
        return next(times)

    monkeypatch.setattr(list_handlers.time, "time", _fake_time)
    msg = _message()
    scraper = MagicMock(spec=CatalogScraper)
    sections = [_section()]
    scraper.fetch_course_sections = AsyncMock(return_value=sections)
    scraper.aggregate_snapshot_payload = CatalogScraper().aggregate_snapshot_payload

    await cmd_check(msg, _cmd_args("CSCI 151"), scraper)
    await cmd_check(msg, _cmd_args("MATH 162"), scraper)

    assert scraper.fetch_course_sections.await_count == 1
    assert "подождите" in (msg.answer.await_args.args[0] or "").lower()


@pytest.mark.asyncio
async def test_cmd_check_allowed_after_rate_limit_window(monkeypatch: pytest.MonkeyPatch) -> None:
    times = iter([1_000_000.0, 1_000_031.0])

    def _fake_time() -> float:
        return next(times)

    monkeypatch.setattr(list_handlers.time, "time", _fake_time)
    msg = _message()
    scraper = MagicMock(spec=CatalogScraper)
    sections = [_section()]
    scraper.fetch_course_sections = AsyncMock(return_value=sections)
    scraper.aggregate_snapshot_payload = CatalogScraper().aggregate_snapshot_payload

    await cmd_check(msg, _cmd_args("CSCI 151"), scraper)
    await cmd_check(msg, _cmd_args("MATH 162"), scraper)

    assert scraper.fetch_course_sections.await_count == 2


@pytest.mark.asyncio
async def test_cmd_check_happy_path_replies_with_details() -> None:
    msg = _message()
    scraper = MagicMock(spec=CatalogScraper)
    sections = [_section()]
    scraper.fetch_course_sections = AsyncMock(return_value=sections)
    scraper.aggregate_snapshot_payload = CatalogScraper().aggregate_snapshot_payload

    await cmd_check(msg, _cmd_args("csci 151"), scraper)

    scraper.fetch_course_sections.assert_awaited_once()
    call_kw = scraper.fetch_course_sections.await_args.kwargs
    assert call_kw.get("respect_rate_limit") is False
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
