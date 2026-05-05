"""Tests for ``bot.scheduler.jobs`` (catalog poll, snapshot updates, notifications)."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import ANY, AsyncMock, MagicMock

import pytest
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

from bot.db.models import CourseSnapshot
from bot.scheduler.jobs import _send_with_retry, check_one_course, poll_catalog_job
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
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = MagicMock()
    db.all_active_subscriptions_grouped = AsyncMock(return_value={"CSCI 151": [1001]})
    db.get_snapshot = AsyncMock()
    db.upsert_snapshot = AsyncMock()
    db.get_user_notification_seats = AsyncMock()
    db.upsert_user_notification_state = AsyncMock()
    scraper = _scraper_with_sections({"CSCI 151": []})

    await poll_catalog_job(mock_bot, db, scraper)

    db.upsert_snapshot.assert_not_awaited()
    db.get_snapshot.assert_not_awaited()
    db.get_user_notification_seats.assert_not_awaited()
    out = capsys.readouterr().out
    assert "Skipping snapshot update for CSCI 151" in out
    assert "empty sections" in out.lower()
    mock_bot.send_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_poll_empty_sections_one_course_other_still_updates(
    mock_bot: MagicMock,
    capsys: pytest.CaptureFixture[str],
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

    await poll_catalog_job(mock_bot, db, scraper)

    assert db.upsert_snapshot.await_count == 1
    assert db.upsert_snapshot.await_args[0][0] == "MATH 162"
    assert "Skipping snapshot update for EMPTY" in capsys.readouterr().out


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
    capsys: pytest.CaptureFixture[str],
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

    await poll_catalog_job(mock_bot, db, scraper)

    assert db.upsert_snapshot.await_count == 1
    assert db.upsert_snapshot.await_args[0][0] == "GOOD"
    out = capsys.readouterr().out
    assert "poll_catalog course check failed" in out
    assert "BAD" in out


@pytest.mark.asyncio
async def test_poll_outer_exception_swallowed(
    mock_bot: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = MagicMock()
    db.all_active_subscriptions_grouped = AsyncMock(side_effect=RuntimeError("db down"))
    scraper = _scraper_with_sections({})

    await poll_catalog_job(mock_bot, db, scraper)

    assert "Poll job crashed" in capsys.readouterr().out


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
    capsys: pytest.CaptureFixture[str],
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

    await poll_catalog_job(mock_bot, db, scraper)

    mock_bot.send_message.assert_awaited_once()
    db.upsert_user_notification_state.assert_not_awaited()
    assert "Failed to notify user" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_poll_positive_seats_drop_to_zero_resets_baseline(
    mock_bot: MagicMock,
) -> None:
    """Edge: was open seats, course fills again — N→0 notify and store 0 for next 0→N."""
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

    mock_bot.send_message.assert_awaited_once_with(
        6001,
        "⚠️ Места на CSCI 151 снова закончились (было 4)",
    )
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


@pytest.mark.asyncio
async def test_poll_checked_log_includes_structured_seat_fields(
    mock_bot: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Observability: success path emits course_code, user_count, seats_found (JSON log)."""
    sec = _sample_section(available_seats=2, total_seats=40)
    db = MagicMock()
    db.all_active_subscriptions_grouped = AsyncMock(return_value={"CSCI 151": [1001, 1002]})
    db.get_snapshot = AsyncMock(return_value=None)
    db.upsert_snapshot = AsyncMock()
    db.get_user_notification_seats = AsyncMock(return_value=None)
    db.upsert_user_notification_state = AsyncMock()
    scraper = _scraper_with_sections({"CSCI 151": [sec]})

    await poll_catalog_job(mock_bot, db, scraper)

    out = capsys.readouterr().out
    assert "seats_found" in out
    assert "user_count" in out
    assert "CSCI 151" in out


@pytest.mark.asyncio
async def test_poll_scrape_raises_includes_course_code_and_user_count(
    mock_bot: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    db = MagicMock()
    db.all_active_subscriptions_grouped = AsyncMock(return_value={"BAD": [7, 8, 9]})
    db.upsert_snapshot = AsyncMock()
    scraper = MagicMock(spec=CatalogScraper)
    scraper.fetch_course_sections = AsyncMock(side_effect=OSError("network"))
    scraper.aggregate_snapshot_payload = CatalogScraper().aggregate_snapshot_payload

    await poll_catalog_job(mock_bot, db, scraper)

    out = capsys.readouterr().out
    assert "poll_catalog course check failed" in out
    assert "BAD" in out
    assert "user_count" in out


# ---------------------------------------------------------------------------
# check_one_course — direct unit tests
# ---------------------------------------------------------------------------

def _make_sem() -> asyncio.Semaphore:
    return asyncio.Semaphore(5)


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.mark.asyncio
async def test_check_one_course_happy_path_upserts_snapshot(
    mock_bot: MagicMock,
) -> None:
    """Happy path: scrapes sections, upserts snapshot with correct seats."""
    sec = _sample_section(available_seats=3, total_seats=50)
    db = MagicMock()
    db.get_snapshot = AsyncMock(return_value=None)
    db.upsert_snapshot = AsyncMock()
    db.get_user_notification_seats = AsyncMock(return_value=None)
    db.upsert_user_notification_state = AsyncMock()
    scraper = _scraper_with_sections({"CSCI 151": [sec]})

    await check_one_course(
        "CSCI 151", [1001], mock_bot, db, scraper, sem=_make_sem(), checked_at=_now()
    )

    db.upsert_snapshot.assert_awaited_once()
    assert db.upsert_snapshot.await_args[0][0] == "CSCI 151"
    assert db.upsert_snapshot.await_args[0][1] == 3


@pytest.mark.asyncio
async def test_check_one_course_logs_structured_fields(
    mock_bot: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Success path emits course_code, seats_found, user_count, checked_at in log."""
    sec = _sample_section(available_seats=7)
    db = MagicMock()
    db.get_snapshot = AsyncMock(return_value=None)
    db.upsert_snapshot = AsyncMock()
    db.get_user_notification_seats = AsyncMock(return_value=None)
    db.upsert_user_notification_state = AsyncMock()
    scraper = _scraper_with_sections({"CSCI 151": [sec]})

    await check_one_course(
        "CSCI 151", [1], mock_bot, db, scraper, sem=_make_sem(), checked_at=_now()
    )

    out = capsys.readouterr().out
    assert "seats_found" in out
    assert "user_count" in out
    assert "CSCI 151" in out
    assert "checked_at" in out


@pytest.mark.asyncio
async def test_check_one_course_empty_sections_returns_early(
    mock_bot: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Empty sections: no snapshot upsert, logs warning with 'empty sections'."""
    db = MagicMock()
    db.upsert_snapshot = AsyncMock()
    db.get_snapshot = AsyncMock()
    db.get_user_notification_seats = AsyncMock()
    scraper = _scraper_with_sections({"CSCI 151": []})

    await check_one_course(
        "CSCI 151", [1], mock_bot, db, scraper, sem=_make_sem(), checked_at=_now()
    )

    db.upsert_snapshot.assert_not_awaited()
    db.get_snapshot.assert_not_awaited()
    out = capsys.readouterr().out
    assert "Skipping snapshot update for CSCI 151" in out
    assert "empty sections" in out.lower()


@pytest.mark.asyncio
async def test_check_one_course_scrape_exception_propagates(
    mock_bot: MagicMock,
) -> None:
    """Scrape error is NOT swallowed — asyncio.gather captures it via return_exceptions."""
    scraper = MagicMock(spec=CatalogScraper)
    scraper.fetch_course_sections = AsyncMock(side_effect=RuntimeError("boom"))
    db = MagicMock()

    with pytest.raises(RuntimeError, match="boom"):
        await check_one_course(
            "CSCI 151", [1], mock_bot, db, scraper, sem=_make_sem(), checked_at=_now()
        )


@pytest.mark.asyncio
async def test_check_one_course_first_time_subscriber_stores_seats_no_push(
    mock_bot: MagicMock,
) -> None:
    """First subscription (last_notified=None): state is seeded with current seats, no push."""
    sec = _sample_section(available_seats=5)
    db = MagicMock()
    db.get_snapshot = AsyncMock(return_value=None)
    db.upsert_snapshot = AsyncMock()
    db.get_user_notification_seats = AsyncMock(return_value=None)
    db.upsert_user_notification_state = AsyncMock()
    scraper = _scraper_with_sections({"CSCI 151": [sec]})

    await check_one_course(
        "CSCI 151", [42], mock_bot, db, scraper, sem=_make_sem(), checked_at=_now()
    )

    mock_bot.send_message.assert_not_awaited()
    db.upsert_user_notification_state.assert_awaited_once_with(42, "CSCI 151", 5)


@pytest.mark.asyncio
async def test_check_one_course_zero_to_positive_triggers_push(
    mock_bot: MagicMock,
) -> None:
    """Zero baseline + seats just opened → exactly one Telegram push, state updated."""
    sec = _sample_section(available_seats=2)
    db = MagicMock()
    db.get_snapshot = AsyncMock(return_value=None)
    db.upsert_snapshot = AsyncMock()
    db.get_user_notification_seats = AsyncMock(return_value=0)
    db.upsert_user_notification_state = AsyncMock()
    scraper = _scraper_with_sections({"CSCI 151": [sec]})

    await check_one_course(
        "CSCI 151", [99], mock_bot, db, scraper, sem=_make_sem(), checked_at=_now()
    )

    mock_bot.send_message.assert_awaited_once_with(99, ANY)
    db.upsert_user_notification_state.assert_awaited_once_with(99, "CSCI 151", 2)


@pytest.mark.asyncio
async def test_check_one_course_nonzero_baseline_no_push(
    mock_bot: MagicMock,
) -> None:
    """User already had seats (last_notified > 0): no push, state still updated."""
    sec = _sample_section(available_seats=4)
    db = MagicMock()
    db.get_snapshot = AsyncMock(return_value=None)
    db.upsert_snapshot = AsyncMock()
    db.get_user_notification_seats = AsyncMock(return_value=3)
    db.upsert_user_notification_state = AsyncMock()
    scraper = _scraper_with_sections({"CSCI 151": [sec]})

    await check_one_course(
        "CSCI 151", [77], mock_bot, db, scraper, sem=_make_sem(), checked_at=_now()
    )

    mock_bot.send_message.assert_not_awaited()
    db.upsert_user_notification_state.assert_awaited_once_with(77, "CSCI 151", 4)


@pytest.mark.asyncio
async def test_check_one_course_positive_to_zero_resets_state(
    mock_bot: MagicMock,
) -> None:
    """Course fills up again (seats → 0): N→0 push and reset user state to 0."""
    prev = CourseSnapshot(
        id=1,
        course_code="CSCI 151",
        available_seats=5,
        instructor=None,
        schedule=None,
        last_checked=datetime.now(timezone.utc),
        raw_json=None,
    )
    sec = _sample_section(available_seats=0)
    db = MagicMock()
    db.get_snapshot = AsyncMock(return_value=prev)
    db.upsert_snapshot = AsyncMock()
    db.get_user_notification_seats = AsyncMock(return_value=5)
    db.upsert_user_notification_state = AsyncMock()
    scraper = _scraper_with_sections({"CSCI 151": [sec]})

    await check_one_course(
        "CSCI 151", [55], mock_bot, db, scraper, sem=_make_sem(), checked_at=_now()
    )

    mock_bot.send_message.assert_awaited_once_with(
        55,
        "⚠️ Места на CSCI 151 снова закончились (было 5)",
    )
    db.upsert_user_notification_state.assert_awaited_once_with(55, "CSCI 151", 0)


@pytest.mark.asyncio
async def test_check_one_course_n_to_zero_notifies_all_subscribers(
    mock_bot: MagicMock,
) -> None:
    """N→0: every subscriber gets the closed-again message."""
    prev = CourseSnapshot(
        id=1,
        course_code="CSCI 151",
        available_seats=3,
        instructor=None,
        schedule=None,
        last_checked=datetime.now(timezone.utc),
        raw_json=None,
    )
    sec = _sample_section(available_seats=0)
    db = MagicMock()
    db.get_snapshot = AsyncMock(return_value=prev)
    db.upsert_snapshot = AsyncMock()
    db.get_user_notification_seats = AsyncMock(side_effect=[3, 1])
    db.upsert_user_notification_state = AsyncMock()
    scraper = _scraper_with_sections({"CSCI 151": [sec]})

    await check_one_course(
        "CSCI 151",
        [10, 20],
        mock_bot,
        db,
        scraper,
        sem=_make_sem(),
        checked_at=_now(),
    )

    assert mock_bot.send_message.await_count == 2
    msg = "⚠️ Места на CSCI 151 снова закончились (было 3)"
    mock_bot.send_message.assert_any_await(10, msg)
    mock_bot.send_message.assert_any_await(20, msg)


@pytest.mark.asyncio
async def test_check_one_course_forbidden_user_updates_state(
    mock_bot: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """TelegramForbiddenError: logs warning, updates state, does not propagate."""
    sec = _sample_section(available_seats=1)
    db = MagicMock()
    db.get_snapshot = AsyncMock(return_value=None)
    db.upsert_snapshot = AsyncMock()
    db.get_user_notification_seats = AsyncMock(return_value=0)
    db.upsert_user_notification_state = AsyncMock()
    scraper = _scraper_with_sections({"CSCI 151": [sec]})
    mock_bot.send_message = AsyncMock(side_effect=TelegramForbiddenError("blocked"))

    await check_one_course(
        "CSCI 151", [11], mock_bot, db, scraper, sem=_make_sem(), checked_at=_now()
    )

    db.upsert_user_notification_state.assert_awaited_once_with(11, "CSCI 151", 1)
    assert "blocked the bot" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_check_one_course_send_error_does_not_update_state(
    mock_bot: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Non-forbidden Telegram error: logs exception, does NOT update notification state."""
    sec = _sample_section(available_seats=1)
    db = MagicMock()
    db.get_snapshot = AsyncMock(return_value=None)
    db.upsert_snapshot = AsyncMock()
    db.get_user_notification_seats = AsyncMock(return_value=0)
    db.upsert_user_notification_state = AsyncMock()
    scraper = _scraper_with_sections({"CSCI 151": [sec]})
    mock_bot.send_message = AsyncMock(side_effect=RuntimeError("net error"))

    await check_one_course(
        "CSCI 151", [22], mock_bot, db, scraper, sem=_make_sem(), checked_at=_now()
    )

    db.upsert_user_notification_state.assert_not_awaited()
    assert "Failed to notify user" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_check_one_course_multiple_users_independent(
    mock_bot: MagicMock,
) -> None:
    """Multiple users for one course: each gets the right notification decision."""
    sec = _sample_section(available_seats=3)
    db = MagicMock()
    db.get_snapshot = AsyncMock(return_value=None)
    db.upsert_snapshot = AsyncMock()
    # user 10: zero baseline → notify; user 20: non-zero baseline → no push
    db.get_user_notification_seats = AsyncMock(side_effect=[0, 2])
    db.upsert_user_notification_state = AsyncMock()
    scraper = _scraper_with_sections({"CSCI 151": [sec]})

    await check_one_course(
        "CSCI 151", [10, 20], mock_bot, db, scraper, sem=_make_sem(), checked_at=_now()
    )

    mock_bot.send_message.assert_awaited_once_with(10, ANY)
    assert db.upsert_user_notification_state.await_count == 2


@pytest.mark.asyncio
async def test_check_one_course_prev_snapshot_used_for_log(
    mock_bot: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """prev_seats appears in log output when a prior snapshot exists."""
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
    db.get_snapshot = AsyncMock(return_value=prev)
    db.upsert_snapshot = AsyncMock()
    db.get_user_notification_seats = AsyncMock(return_value=0)
    db.upsert_user_notification_state = AsyncMock()
    scraper = _scraper_with_sections({"CSCI 151": [sec]})

    await check_one_course(
        "CSCI 151", [1], mock_bot, db, scraper, sem=_make_sem(), checked_at=_now()
    )

    out = capsys.readouterr().out
    assert "prev_seats" in out


# ---------------------------------------------------------------------------
# asyncio.gather error-path and semaphore concurrency tests for poll_catalog_job
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_poll_gather_logs_course_code_and_error_message(
    mock_bot: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Gather captures the exception; log includes course_code and the error string."""
    db = MagicMock()
    db.all_active_subscriptions_grouped = AsyncMock(return_value={"FAIL": [1, 2]})
    scraper = MagicMock(spec=CatalogScraper)
    scraper.fetch_course_sections = AsyncMock(side_effect=ValueError("bad data"))
    scraper.aggregate_snapshot_payload = CatalogScraper().aggregate_snapshot_payload

    await poll_catalog_job(mock_bot, db, scraper)

    out = capsys.readouterr().out
    assert "poll_catalog course check failed" in out
    assert "FAIL" in out
    assert "bad data" in out


@pytest.mark.asyncio
async def test_poll_gather_all_failures_logged_job_does_not_raise(
    mock_bot: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Every course failing is still swallowed; every course_code appears in logs."""
    codes = {"AA": [1], "BB": [2], "CC": [3]}
    db = MagicMock()
    db.all_active_subscriptions_grouped = AsyncMock(return_value=codes)
    scraper = MagicMock(spec=CatalogScraper)
    scraper.fetch_course_sections = AsyncMock(side_effect=RuntimeError("x"))
    scraper.aggregate_snapshot_payload = CatalogScraper().aggregate_snapshot_payload

    await poll_catalog_job(mock_bot, db, scraper)  # must not raise

    out = capsys.readouterr().out
    for code in codes:
        assert code in out


@pytest.mark.asyncio
async def test_poll_gather_partial_failure_good_courses_complete(
    mock_bot: MagicMock,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Failing courses are logged; unrelated good courses still upsert their snapshot."""
    good = "MATH 101"
    fail_codes = {"FAIL1": [1], "FAIL2": [2]}
    sec = _sample_section(course_code=good, course_title="Math")

    real = CatalogScraper()
    scraper = MagicMock(spec=CatalogScraper)

    async def _fetch(course_code: str, **kwargs: object) -> list[CourseInfo]:
        if course_code in fail_codes:
            raise RuntimeError("deliberate failure")
        return [sec]

    scraper.fetch_course_sections = _fetch
    scraper.aggregate_snapshot_payload = real.aggregate_snapshot_payload

    db = MagicMock()
    db.all_active_subscriptions_grouped = AsyncMock(
        return_value={**fail_codes, good: [3]}
    )
    db.get_snapshot = AsyncMock(return_value=None)
    db.upsert_snapshot = AsyncMock()
    db.get_user_notification_seats = AsyncMock(return_value=None)
    db.upsert_user_notification_state = AsyncMock()

    await poll_catalog_job(mock_bot, db, scraper)

    assert db.upsert_snapshot.await_count == 1
    assert db.upsert_snapshot.await_args[0][0] == good
    out = capsys.readouterr().out
    assert "FAIL1" in out
    assert "FAIL2" in out


@pytest.mark.asyncio
async def test_poll_semaphore_limits_concurrent_scrapes(
    mock_bot: MagicMock,
) -> None:
    """With Semaphore(5), at most 5 fetch_course_sections calls run simultaneously."""
    n_courses = 12
    codes = [f"COURSE {i:02d}" for i in range(n_courses)]
    grouped = {code: [i] for i, code in enumerate(codes)}

    concurrent_now: int = 0
    peak_concurrent: int = 0
    lock = asyncio.Lock()

    async def slow_fetch(course_code: str, **kwargs: object) -> list[CourseInfo]:
        nonlocal concurrent_now, peak_concurrent
        async with lock:
            concurrent_now += 1
            peak_concurrent = max(peak_concurrent, concurrent_now)
        await asyncio.sleep(0.02)
        async with lock:
            concurrent_now -= 1
        return [_sample_section(course_code=course_code, course_title="T")]

    real = CatalogScraper()
    scraper = MagicMock(spec=CatalogScraper)
    scraper.fetch_course_sections = slow_fetch
    scraper.aggregate_snapshot_payload = real.aggregate_snapshot_payload

    db = MagicMock()
    db.all_active_subscriptions_grouped = AsyncMock(return_value=grouped)
    db.get_snapshot = AsyncMock(return_value=None)
    db.upsert_snapshot = AsyncMock()
    db.get_user_notification_seats = AsyncMock(return_value=None)
    db.upsert_user_notification_state = AsyncMock()

    await poll_catalog_job(mock_bot, db, scraper)

    assert peak_concurrent <= 5, f"peak_concurrent={peak_concurrent} exceeded semaphore of 5"
    assert db.upsert_snapshot.await_count == n_courses


@pytest.mark.asyncio
async def test_poll_all_courses_run_concurrently_not_sequentially(
    mock_bot: MagicMock,
) -> None:
    """With N courses and Semaphore(5), total elapsed time is much less than N * delay."""
    import time

    n_courses = 10
    delay = 0.05
    grouped = {f"C{i}": [i] for i in range(n_courses)}

    real = CatalogScraper()
    scraper = MagicMock(spec=CatalogScraper)

    async def slow_fetch(course_code: str, **kwargs: object) -> list[CourseInfo]:
        await asyncio.sleep(delay)
        return [_sample_section(course_code=course_code, course_title="T")]

    scraper.fetch_course_sections = slow_fetch
    scraper.aggregate_snapshot_payload = real.aggregate_snapshot_payload

    db = MagicMock()
    db.all_active_subscriptions_grouped = AsyncMock(return_value=grouped)
    db.get_snapshot = AsyncMock(return_value=None)
    db.upsert_snapshot = AsyncMock()
    db.get_user_notification_seats = AsyncMock(return_value=None)
    db.upsert_user_notification_state = AsyncMock()

    start = time.monotonic()
    await poll_catalog_job(mock_bot, db, scraper)
    elapsed = time.monotonic() - start

    # Sequential would take n_courses * delay = 0.5 s; concurrent batches of 5 → ~0.1 s.
    # We allow generous headroom (3× a single batch) to avoid flakiness on slow CI.
    assert elapsed < delay * n_courses * 0.6, (
        f"elapsed={elapsed:.3f}s suggests courses ran sequentially"
    )
    assert db.upsert_snapshot.await_count == n_courses
