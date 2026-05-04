"""Tests for ``bot.scheduler.jobs`` (catalog poll, snapshot updates, notifications)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from unittest.mock import ANY, AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

from bot.db.models import CourseSnapshot
from bot.scheduler.jobs import _send_with_retry, poll_catalog_job
from bot.scraper.catalog import CatalogScraper, CourseInfo

pytestmark = pytest.mark.usefixtures("settings_env")


def _sample_section(**overrides: object) -> CourseInfo:
    base = dict(
        course_code="CSCI 151",
        course_title="Intro CS",
        instructor_name="Prof A",
        schedule="01L · Mon 10:00",
        schedule_body="Mon 10:00",
        available_seats=3,
        total_seats=50,
        section_type="01L",
        course_id="c1",
        instance_id="i1",
    )
    base.update(overrides)
    return CourseInfo(**base)


def _scraper_with_sections(
    sections_by_code: dict[str, list[CourseInfo]],
) -> MagicMock:
    """Real aggregation logic; fetch is mocked per course code."""
    scraper = MagicMock(spec=CatalogScraper)
    real = CatalogScraper()

    async def _fetch(course_code: str, **kwargs: object) -> list[CourseInfo]:
        return sections_by_code.get(course_code, [])

    scraper.fetch_course_sections = _fetch
    scraper.aggregate_snapshot_payload = real.aggregate_snapshot_payload
    return scraper


@pytest.fixture
def mock_bot() -> MagicMock:
    bot = MagicMock()
    bot.send_message = AsyncMock()
    return bot


@pytest.mark.asyncio
async def test_poll_no_subscriptions_skips_fetch(mock_bot: MagicMock) -> None:
    db = MagicMock()
    db.all_active_subscriptions_grouped = AsyncMock(return_value={})
    db.upsert_snapshot = AsyncMock()
    scraper = MagicMock(spec=CatalogScraper)
    scraper.fetch_course_sections = AsyncMock()

    await poll_catalog_job(mock_bot, db, scraper)

    db.all_active_subscriptions_grouped.assert_awaited_once()
    scraper.fetch_course_sections.assert_not_called()
    db.upsert_snapshot.assert_not_awaited()


@pytest.mark.asyncio
async def test_poll_happy_path_upserts_snapshot(mock_bot: MagicMock) -> None:
    sec = _sample_section(available_seats=2, total_seats=40)
    db = MagicMock()
    db.all_active_subscriptions_grouped = AsyncMock(return_value={"CSCI 151": [1001]})
    db.get_snapshot = AsyncMock(return_value=None)
    db.upsert_snapshot = AsyncMock()
    db.get_user_notification_seats = AsyncMock(return_value=None)
    db.upsert_user_notification_state = AsyncMock()
    scraper = _scraper_with_sections({"CSCI 151": [sec]})

    await poll_catalog_job(mock_bot, db, scraper)

    db.upsert_snapshot.assert_awaited_once()
    args = db.upsert_snapshot.await_args[0]
    assert args[0] == "CSCI 151"
    assert args[1] == 2
    mock_bot.send_message.assert_not_awaited()
    db.upsert_user_notification_state.assert_awaited_once_with(1001, "CSCI 151", 2)


@pytest.mark.asyncio
async def test_poll_empty_sections_skips_upsert_and_logs_warning(
    mock_bot: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    db = MagicMock()
    db.all_active_subscriptions_grouped = AsyncMock(return_value={"CSCI 151": [1001]})
    db.get_snapshot = AsyncMock()
    db.upsert_snapshot = AsyncMock()
    db.get_user_notification_seats = AsyncMock()
    db.upsert_user_notification_state = AsyncMock()
    scraper = _scraper_with_sections({"CSCI 151": []})

    with caplog.at_level(logging.WARNING):
        await poll_catalog_job(mock_bot, db, scraper)

    db.upsert_snapshot.assert_not_awaited()
    db.get_snapshot.assert_not_awaited()
    db.get_user_notification_seats.assert_not_awaited()
    assert "Skipping snapshot update for CSCI 151" in caplog.text
    assert "empty sections" in caplog.text
    mock_bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_poll_empty_sections_one_course_other_still_updates(
    mock_bot: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Mixed: first course fails open (empty list); second course persists snapshot."""
    db = MagicMock()
    db.all_active_subscriptions_grouped = AsyncMock(
        return_value={"EMPTY": [1], "MATH 162": [2]}
    )
    db.get_snapshot = AsyncMock(return_value=None)
    db.upsert_snapshot = AsyncMock()
    db.get_user_notification_seats = AsyncMock(return_value=None)
    db.upsert_user_notification_state = AsyncMock()
    scraper = _scraper_with_sections(
        {
            "EMPTY": [],
            "MATH 162": [_sample_section(course_code="MATH 162", course_title="Calc")],
        }
    )

    with caplog.at_level(logging.WARNING):
        await poll_catalog_job(mock_bot, db, scraper)

    assert db.upsert_snapshot.await_count == 1
    assert db.upsert_snapshot.await_args[0][0] == "MATH 162"
    assert "Skipping snapshot update for EMPTY" in caplog.text


@pytest.mark.asyncio
async def test_poll_notifies_only_users_with_zero_baseline(
    mock_bot: MagicMock,
) -> None:
    """Per-user state: subscriber already at non-zero baseline gets no push."""
    prev = CourseSnapshot(
        id=1,
        course_code="CSCI 151",
        available_seats=0,
        instructor=None,
        schedule=None,
        last_checked=datetime.now(timezone.utc),
        raw_json=None,
    )
    sec = _sample_section(available_seats=5, total_seats=50)
    db = MagicMock()
    db.all_active_subscriptions_grouped = AsyncMock(
        return_value={"CSCI 151": [10, 20]}
    )
    db.get_snapshot = AsyncMock(return_value=prev)
    db.upsert_snapshot = AsyncMock()
    db.get_user_notification_seats = AsyncMock(
        side_effect=[0, 3]
    )  # 10: notify; 20: already "had seats"
    db.upsert_user_notification_state = AsyncMock()
    scraper = _scraper_with_sections({"CSCI 151": [sec]})

    await poll_catalog_job(mock_bot, db, scraper)

    mock_bot.send_message.assert_awaited_once_with(10, ANY)
    assert db.upsert_user_notification_state.await_count == 2


@pytest.mark.asyncio
async def test_poll_notifies_on_zero_to_positive_seats(mock_bot: MagicMock) -> None:
    prev = CourseSnapshot(
        id=1,
        course_code="CSCI 151",
        available_seats=0,
        instructor="Old",
        schedule="old",
        last_checked=datetime.now(timezone.utc),
        raw_json="{}",
    )
    sec = _sample_section(available_seats=4, total_seats=50)
    db = MagicMock()
    db.all_active_subscriptions_grouped = AsyncMock(
        return_value={"CSCI 151": [2002, 2003]}
    )
    db.get_snapshot = AsyncMock(return_value=prev)
    db.upsert_snapshot = AsyncMock()
    db.get_user_notification_seats = AsyncMock(return_value=0)
    db.upsert_user_notification_state = AsyncMock()
    scraper = _scraper_with_sections({"CSCI 151": [sec]})

    await poll_catalog_job(mock_bot, db, scraper)

    assert mock_bot.send_message.await_count == 2
    mock_bot.send_message.assert_any_await(2002, ANY)
    mock_bot.send_message.assert_any_await(2003, ANY)
    assert db.upsert_user_notification_state.await_count == 2


@pytest.mark.asyncio
async def test_poll_no_notify_when_still_zero_seats(mock_bot: MagicMock) -> None:
    prev = CourseSnapshot(
        id=1,
        course_code="CSCI 151",
        available_seats=0,
        instructor=None,
        schedule=None,
        last_checked=datetime.now(timezone.utc),
        raw_json=None,
    )
    sec = _sample_section(available_seats=0, total_seats=50)
    db = MagicMock()
    db.all_active_subscriptions_grouped = AsyncMock(return_value={"CSCI 151": [1]})
    db.get_snapshot = AsyncMock(return_value=prev)
    db.upsert_snapshot = AsyncMock()
    db.get_user_notification_seats = AsyncMock(return_value=0)
    db.upsert_user_notification_state = AsyncMock()
    scraper = _scraper_with_sections({"CSCI 151": [sec]})

    await poll_catalog_job(mock_bot, db, scraper)

    db.upsert_snapshot.assert_awaited_once()
    mock_bot.send_message.assert_not_awaited()
    db.upsert_user_notification_state.assert_awaited_once_with(1, "CSCI 151", 0)


@pytest.mark.asyncio
async def test_poll_per_course_exception_continues(
    mock_bot: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    db = MagicMock()
    db.all_active_subscriptions_grouped = AsyncMock(
        return_value={"BAD": [1], "GOOD": [2]}
    )
    db.get_snapshot = AsyncMock(return_value=None)
    db.upsert_snapshot = AsyncMock()
    db.get_user_notification_seats = AsyncMock(return_value=None)
    db.upsert_user_notification_state = AsyncMock()

    scraper = MagicMock(spec=CatalogScraper)
    real = CatalogScraper()

    async def _fetch(course_code: str, **kwargs: object) -> list[CourseInfo]:
        if course_code == "BAD":
            raise RuntimeError("simulated scrape failure")
        return [_sample_section(course_code="GOOD", course_title="OK")]

    scraper.fetch_course_sections = _fetch
    scraper.aggregate_snapshot_payload = real.aggregate_snapshot_payload

    with caplog.at_level(logging.ERROR):
        await poll_catalog_job(mock_bot, db, scraper)

    assert db.upsert_snapshot.await_count == 1
    assert db.upsert_snapshot.await_args[0][0] == "GOOD"
    assert "Poll failed for course BAD" in caplog.text


@pytest.mark.asyncio
async def test_poll_outer_exception_swallowed(
    mock_bot: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    db = MagicMock()
    db.all_active_subscriptions_grouped = AsyncMock(side_effect=RuntimeError("db down"))
    scraper = _scraper_with_sections({})

    with caplog.at_level(logging.ERROR):
        await poll_catalog_job(mock_bot, db, scraper)

    assert "Poll job crashed" in caplog.text


@pytest.mark.asyncio
async def test_send_with_retry_success_first_try(mock_bot: MagicMock) -> None:
    await _send_with_retry(mock_bot, 42, "hello")
    mock_bot.send_message.assert_awaited_once_with(42, "hello")


@pytest.mark.asyncio
async def test_send_with_retry_waits_and_retries(
    mock_bot: MagicMock,
    mocker: pytest.MockFixture,
) -> None:
    sleep = mocker.patch("bot.scheduler.jobs.asyncio.sleep", new_callable=AsyncMock)
    exc = TelegramRetryAfter()
    exc.retry_after = 7
    mock_bot.send_message = AsyncMock(side_effect=[exc, None])

    await _send_with_retry(mock_bot, 99, "x")

    assert mock_bot.send_message.await_count == 2
    sleep.assert_awaited_once_with(7)


@pytest.mark.asyncio
async def test_poll_send_failure_does_not_update_notification_state(
    mock_bot: MagicMock,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """If Telegram send fails (non-forbidden), keep notified_at_seats so the next poll retries."""
    prev = CourseSnapshot(
        id=1,
        course_code="CSCI 151",
        available_seats=0,
        instructor=None,
        schedule=None,
        last_checked=datetime.now(timezone.utc),
        raw_json=None,
    )
    sec = _sample_section(available_seats=2)
    db = MagicMock()
    db.all_active_subscriptions_grouped = AsyncMock(return_value={"CSCI 151": [5001]})
    db.get_snapshot = AsyncMock(return_value=prev)
    db.upsert_snapshot = AsyncMock()
    db.get_user_notification_seats = AsyncMock(return_value=0)
    db.upsert_user_notification_state = AsyncMock()
    scraper = _scraper_with_sections({"CSCI 151": [sec]})
    mock_bot.send_message = AsyncMock(side_effect=RuntimeError("telegram down"))

    with caplog.at_level(logging.ERROR):
        await poll_catalog_job(mock_bot, db, scraper)

    mock_bot.send_message.assert_awaited_once()
    db.upsert_user_notification_state.assert_not_awaited()


@pytest.mark.asyncio
async def test_poll_positive_seats_drop_to_zero_resets_baseline(
    mock_bot: MagicMock,
) -> None:
    """Edge: was open seats, course fills again — store 0 so a later opening can notify."""
    prev = CourseSnapshot(
        id=1,
        course_code="CSCI 151",
        available_seats=4,
        instructor=None,
        schedule=None,
        last_checked=datetime.now(timezone.utc),
        raw_json=None,
    )
    sec = _sample_section(available_seats=0, total_seats=50)
    db = MagicMock()
    db.all_active_subscriptions_grouped = AsyncMock(return_value={"CSCI 151": [6001]})
    db.get_snapshot = AsyncMock(return_value=prev)
    db.upsert_snapshot = AsyncMock()
    db.get_user_notification_seats = AsyncMock(return_value=4)
    db.upsert_user_notification_state = AsyncMock()
    scraper = _scraper_with_sections({"CSCI 151": [sec]})

    await poll_catalog_job(mock_bot, db, scraper)

    mock_bot.send_message.assert_not_awaited()
    db.upsert_user_notification_state.assert_awaited_once_with(6001, "CSCI 151", 0)


@pytest.mark.asyncio
async def test_poll_forbidden_user_skips_notify(mock_bot: MagicMock) -> None:
    prev = CourseSnapshot(
        id=1,
        course_code="CSCI 151",
        available_seats=0,
        instructor=None,
        schedule=None,
        last_checked=datetime.now(timezone.utc),
        raw_json=None,
    )
    sec = _sample_section(available_seats=2)
    db = MagicMock()
    db.all_active_subscriptions_grouped = AsyncMock(
        return_value={"CSCI 151": [3001, 3002]}
    )
    db.get_snapshot = AsyncMock(return_value=prev)
    db.upsert_snapshot = AsyncMock()
    db.get_user_notification_seats = AsyncMock(return_value=0)
    db.upsert_user_notification_state = AsyncMock()
    scraper = _scraper_with_sections({"CSCI 151": [sec]})

    mock_bot.send_message = AsyncMock(
        side_effect=[TelegramForbiddenError("blocked"), None]
    )

    await poll_catalog_job(mock_bot, db, scraper)

    assert mock_bot.send_message.await_count == 2
