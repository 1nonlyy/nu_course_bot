"""Tests for ``bot.scraper.catalog`` (HTTP client mocked; no real registrar calls)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from bot.scraper.catalog import (
    CatalogScraper,
    CourseInfo,
    ScrapeRateLimiter,
    _parse_term_id_from_catalog_html,
    fetch_schedule,
    fetch_search_data,
    format_open_seats_message,
    normalize_course_code,
    post_catalog_json,
)

pytestmark = pytest.mark.usefixtures("settings_env")


def _async_client_cm(mock_client: MagicMock) -> MagicMock:
    """``async with _httpx_client(...) as client`` → yields ``mock_client``."""
    cm = MagicMock()
    cm.__aenter__ = AsyncMock(return_value=mock_client)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("csci 151", "CSCI 151"),
        ("CSCI151", "CSCI 151"),
        ("  math 162  ", "MATH 162"),
    ],
)
def test_normalize_course_code_valid(raw: str, expected: str) -> None:
    assert normalize_course_code(raw) == expected


@pytest.mark.parametrize(
    "raw",
    [
        "",
        "   ",
        "NOTACOURSE",
        "X 99",
        "TOOLONGSUBJECT 151",
    ],
)
def test_normalize_course_code_invalid(raw: str) -> None:
    assert normalize_course_code(raw) is None


def test_aggregate_snapshot_payload_empty_sections() -> None:
    scraper = CatalogScraper()
    agg = scraper.aggregate_snapshot_payload([])
    assert agg["available_seats"] == 0
    assert agg["total_seats_display"] == 0
    assert agg["instructor"] is None
    assert agg["payload"]["sections"] == []


@pytest.mark.asyncio
async def test_post_catalog_json_success() -> None:
    client = MagicMock()
    response = MagicMock()
    response.status_code = 200
    response.text = '{"items": [1]}'
    client.post = AsyncMock(return_value=response)

    data = await post_catalog_json(client, "https://x/json", {"method": "getSearchData", "n": 1})

    assert data == {"items": [1]}
    client.post.assert_awaited_once()
    _args, kwargs = client.post.await_args
    assert kwargs["data"] == {"method": "getSearchData", "n": "1"}


@pytest.mark.asyncio
async def test_post_catalog_json_http_error_returns_none() -> None:
    client = MagicMock()
    response = MagicMock()
    response.status_code = 500
    response.text = "Internal Error"
    client.post = AsyncMock(return_value=response)

    assert await post_catalog_json(client, "https://x/json", {"method": "x"}) is None


@pytest.mark.asyncio
async def test_post_catalog_json_invalid_json_returns_none() -> None:
    client = MagicMock()
    response = MagicMock()
    response.status_code = 200
    response.text = "not json"
    client.post = AsyncMock(return_value=response)

    assert await post_catalog_json(client, "https://x/json", {"method": "x"}) is None


@pytest.mark.asyncio
async def test_fetch_search_data_happy_path(mocker: pytest.MockFixture) -> None:
    mocker.patch(
        "bot.scraper.catalog.post_catalog_json",
        AsyncMock(
            return_value={
                "data": [
                    {"ABBR": "CSCI 151", "COURSEID": "1"},
                    "skip",
                    {"ABBR": "MATH 162", "COURSEID": "2"},
                ]
            }
        ),
    )
    client = MagicMock()
    rows = await fetch_search_data(client, "https://j", "824", "CSCI 151")
    assert len(rows) == 2
    assert rows[0]["COURSEID"] == "1"


@pytest.mark.asyncio
async def test_fetch_search_data_non_dict_response_empty(mocker: pytest.MockFixture) -> None:
    mocker.patch("bot.scraper.catalog.post_catalog_json", AsyncMock(return_value=None))
    assert await fetch_search_data(MagicMock(), "u", "1", "x") == []


@pytest.mark.asyncio
async def test_fetch_search_data_data_not_list_empty(mocker: pytest.MockFixture) -> None:
    mocker.patch("bot.scraper.catalog.post_catalog_json", AsyncMock(return_value={"data": {}}))
    assert await fetch_search_data(MagicMock(), "u", "1", "x") == []


@pytest.mark.asyncio
async def test_fetch_schedule_happy_path(mocker: pytest.MockFixture) -> None:
    mocker.patch(
        "bot.scraper.catalog.post_catalog_json",
        AsyncMock(return_value=[{"ST": "01L"}, "bad", {"ST": "02L"}]),
    )
    rows = await fetch_schedule(MagicMock(), "u", "824", "99")
    assert [r["ST"] for r in rows] == ["01L", "02L"]


@pytest.mark.asyncio
async def test_fetch_schedule_non_list_empty(mocker: pytest.MockFixture) -> None:
    mocker.patch("bot.scraper.catalog.post_catalog_json", AsyncMock(return_value={"not": "list"}))
    assert await fetch_schedule(MagicMock(), "u", "824", "99") == []


def test_pick_display_instructor_prefers_lecture() -> None:
    scraper = CatalogScraper()
    sections = [
        CourseInfo(
            course_code="X 100",
            course_title="T",
            instructor_name="",
            section_type="01LB",
            schedule="",
            schedule_body="",
            available_seats=0,
            total_seats=10,
            course_id="1",
        ),
        CourseInfo(
            course_code="X 100",
            course_title="T",
            instructor_name="Lec Prof",
            section_type="01L",
            schedule="",
            schedule_body="",
            available_seats=0,
            total_seats=10,
            course_id="1",
        ),
    ]
    agg = scraper.aggregate_snapshot_payload(sections)
    assert agg["instructor"] == "Lec Prof"


def test_pick_display_instructor_skips_tba_uses_next() -> None:
    """Prefer lecture but skip TBA; fall through to next non-empty name."""
    scraper = CatalogScraper()
    sections = [
        CourseInfo(
            course_code="X 100",
            course_title="T",
            instructor_name="TBA",
            section_type="01L",
            schedule="",
            schedule_body="",
            available_seats=0,
            total_seats=10,
            course_id="1",
        ),
        CourseInfo(
            course_code="X 100",
            course_title="T",
            instructor_name="Real Prof",
            section_type="01LB",
            schedule="",
            schedule_body="",
            available_seats=0,
            total_seats=10,
            course_id="1",
        ),
    ]
    assert scraper.aggregate_snapshot_payload(sections)["instructor"] == "Real Prof"


def test_parse_term_id_skips_placeholder_option() -> None:
    html = (
        '<select id="semesterComboId">'
        '<option value="-1">Select</option>'
        '<option value="824">Summer 2026</option>'
        "</select>"
    )
    assert _parse_term_id_from_catalog_html(html) == "824"


def test_parse_term_id_missing_select_raises() -> None:
    with pytest.raises(RuntimeError, match=r"#semesterComboId"):
        _parse_term_id_from_catalog_html("<html></html>")


def test_parse_term_id_only_placeholder_options_raises() -> None:
    html = (
        '<select id="semesterComboId">'
        '<option value="-1">Select</option>'
        "</select>"
    )
    with pytest.raises(RuntimeError, match=r"term id"):
        _parse_term_id_from_catalog_html(html)


def test_clean_instructor_replaces_br() -> None:
    assert CatalogScraper._clean_instructor("A<br/>B") == "A, B"


@pytest.mark.asyncio
async def test_scrape_rate_limiter_sleeps_when_interval_not_elapsed(
    monkeypatch: pytest.MonkeyPatch,
    mocker: pytest.MockFixture,
) -> None:
    """Second scrape within min_interval triggers ``asyncio.sleep`` for the remainder."""
    times = [100.0, 100.0, 102.0, 105.0]
    i = [0]

    def mono() -> float:
        if i[0] < len(times):
            v = times[i[0]]
            i[0] += 1
            return v
        return 99999.0

    monkeypatch.setattr("bot.scraper.catalog.time.monotonic", mono)
    sleep = mocker.patch("bot.scraper.catalog.asyncio.sleep", new_callable=AsyncMock)
    limiter = ScrapeRateLimiter(5.0)
    await limiter.wait_for_slot("CSCI 151")
    await limiter.wait_for_slot("CSCI 151")
    sleep.assert_awaited_once_with(pytest.approx(3.0))


@pytest.mark.asyncio
async def test_fetch_course_sections_invalid_code_returns_empty(
    capsys: pytest.CaptureFixture[str],
) -> None:
    scraper = CatalogScraper()
    out = await scraper.fetch_course_sections("not-a-course", respect_rate_limit=False)
    assert out == []
    assert "Invalid course code" in capsys.readouterr().out


@pytest.mark.asyncio
async def test_fetch_course_sections_warmup_http_error_returns_empty(
    mocker: pytest.MockFixture,
) -> None:
    scraper = CatalogScraper()
    client = MagicMock()
    warm = MagicMock()
    warm.status_code = 503
    client.get = AsyncMock(return_value=warm)
    mocker.patch("bot.scraper.catalog._httpx_client", return_value=_async_client_cm(client))

    assert await scraper.fetch_course_sections("CSCI 151", respect_rate_limit=False) == []


@pytest.mark.asyncio
async def test_fetch_course_sections_happy_path_builds_course_info(
    mocker: pytest.MockFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scraper = CatalogScraper()
    client = MagicMock()

    warm = MagicMock()
    warm.status_code = 200
    warm.text = "<html/>"
    client.get = AsyncMock(return_value=warm)

    search_resp = MagicMock()
    search_resp.status_code = 200
    search_resp.text = (
        '{"data": [{"ABBR": "CSCI 151", "COURSEID": "42", "TITLE": "Intro CS"}]}'
    )

    sched_resp = MagicMock()
    sched_resp.status_code = 200
    sched_resp.text = (
        '[{"ST": "01L", "INSTANCEID": "i1", "CAPACITY": 50, "ENR": 10, '
        '"FACULTY": "Dr<br/>X", "DAYS": "MW", "TIMES": "10:00", "ROOM": "101"}]'
    )

    client.post = AsyncMock(side_effect=[search_resp, sched_resp])
    mocker.patch("bot.scraper.catalog._httpx_client", return_value=_async_client_cm(client))

    sections = await scraper.fetch_course_sections("csci 151", respect_rate_limit=False)
    assert len(sections) == 1
    s = sections[0]
    assert s.course_code == "CSCI 151"
    assert s.course_title == "Intro CS"
    assert s.available_seats == 40
    assert s.total_seats == 50
    assert s.instructor_name == "Dr, X"
    assert "01L" in s.schedule
    assert s.instance_id == "i1"
    finish_log = capsys.readouterr().out
    assert "Catalog scrape finished" in finish_log
    assert "scrape_duration_seconds" in finish_log
    assert "CSCI 151" in finish_log


@pytest.mark.asyncio
async def test_fetch_course_sections_dedupes_same_instance_and_section(
    mocker: pytest.MockFixture,
) -> None:
    scraper = CatalogScraper()
    client = MagicMock()
    warm = MagicMock(status_code=200, text="<html/>")
    client.get = AsyncMock(return_value=warm)
    search_resp = MagicMock(
        status_code=200,
        text='{"data": [{"ABBR": "CSCI 151", "COURSEID": "42", "TITLE": "T"}]}',
    )
    sched_resp = MagicMock(
        status_code=200,
        text=(
            "["
            '{"ST": "01L", "INSTANCEID": "i1", "CAPACITY": 10, "ENR": 0},'
            '{"ST": "01L", "INSTANCEID": "i1", "CAPACITY": 10, "ENR": 0}'
            "]"
        ),
    )
    client.post = AsyncMock(side_effect=[search_resp, sched_resp])
    mocker.patch("bot.scraper.catalog._httpx_client", return_value=_async_client_cm(client))

    sections = await scraper.fetch_course_sections("CSCI 151", respect_rate_limit=False)
    assert len(sections) == 1


@pytest.mark.asyncio
async def test_fetch_course_sections_skips_row_without_course_id(
    mocker: pytest.MockFixture,
) -> None:
    scraper = CatalogScraper()
    client = MagicMock()
    warm = MagicMock(status_code=200, text="<html/>")
    client.get = AsyncMock(return_value=warm)
    search_resp = MagicMock(
        status_code=200,
        text=(
            '{"data": ['
            '{"ABBR": "CSCI 151", "COURSEID": "", "TITLE": "Bad"},'
            '{"ABBR": "CSCI 151", "COURSEID": "7", "TITLE": "Good"}'
            "]}"
        ),
    )
    sched_resp = MagicMock(
        status_code=200,
        text='[{"ST": "01L", "INSTANCEID": "x", "CAPACITY": 5, "ENR": 1}]',
    )
    client.post = AsyncMock(side_effect=[search_resp, sched_resp])
    mocker.patch("bot.scraper.catalog._httpx_client", return_value=_async_client_cm(client))

    sections = await scraper.fetch_course_sections("CSCI 151", respect_rate_limit=False)
    assert len(sections) == 1
    assert sections[0].course_id == "7"


@pytest.mark.asyncio
async def test_fetch_course_sections_bad_capacity_enrollment_defaults_zero(
    mocker: pytest.MockFixture,
) -> None:
    scraper = CatalogScraper()
    client = MagicMock()
    warm = MagicMock(status_code=200, text="<html/>")
    client.get = AsyncMock(return_value=warm)
    search_resp = MagicMock(
        status_code=200,
        text='{"data": [{"ABBR": "CSCI 151", "COURSEID": "1", "TITLE": "T"}]}',
    )
    sched_resp = MagicMock(
        status_code=200,
        text=(
            '[{"ST": "01L", "INSTANCEID": "a", "CAPACITY": "nope", "ENR": "x", '
            '"FACULTY": ""}]'
        ),
    )
    client.post = AsyncMock(side_effect=[search_resp, sched_resp])
    mocker.patch("bot.scraper.catalog._httpx_client", return_value=_async_client_cm(client))

    sections = await scraper.fetch_course_sections("CSCI 151", respect_rate_limit=False)
    assert sections[0].total_seats == 0
    assert sections[0].available_seats == 0


@pytest.mark.asyncio
async def test_fetch_course_sections_no_abbr_match_returns_empty(
    mocker: pytest.MockFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scraper = CatalogScraper()
    client = MagicMock()
    warm = MagicMock(status_code=200, text="<html/>")
    client.get = AsyncMock(return_value=warm)
    search_resp = MagicMock(
        status_code=200,
        text='{"data": [{"ABBR": "MATH 162", "COURSEID": "1", "TITLE": "Calc"}]}',
    )
    client.post = AsyncMock(side_effect=[search_resp])
    mocker.patch("bot.scraper.catalog._httpx_client", return_value=_async_client_cm(client))

    sections = await scraper.fetch_course_sections("CSCI 151", respect_rate_limit=False)
    assert sections == []
    out = capsys.readouterr().out
    assert "no ABBR match" in out
    assert "scrape_duration_seconds" in out


@pytest.mark.asyncio
async def test_fetch_course_sections_resolves_term_from_html_when_env_empty(
    monkeypatch: pytest.MonkeyPatch,
    mocker: pytest.MockFixture,
) -> None:
    monkeypatch.setenv("CATALOG_TERM_ID", "")
    from bot.config import get_settings

    get_settings.cache_clear()

    scraper = CatalogScraper()
    client = MagicMock()
    html = (
        '<select id="semesterComboId">'
        '<option value="-1">Pick</option>'
        '<option value="999">Term</option>'
        "</select>"
    )
    warm = MagicMock(status_code=200, text=f"<html>{html}</html>")
    client.get = AsyncMock(return_value=warm)

    search_resp = MagicMock(
        status_code=200,
        text='{"data": [{"ABBR": "CSCI 151", "COURSEID": "1", "TITLE": "T"}]}',
    )
    sched_resp = MagicMock(
        status_code=200,
        text='[{"ST": "01L", "INSTANCEID": "z", "CAPACITY": 3, "ENR": 3}]',
    )
    client.post = AsyncMock(side_effect=[search_resp, sched_resp])
    mocker.patch("bot.scraper.catalog._httpx_client", return_value=_async_client_cm(client))

    await scraper.fetch_course_sections("CSCI 151", respect_rate_limit=False)

    assert client.post.await_count == 2
    _url, kwargs0 = client.post.call_args_list[0]
    assert kwargs0["data"]["searchParams[semester]"] == "999"
    assert kwargs0["data"]["method"] == "getSearchData"
    _url2, kwargs1 = client.post.call_args_list[1]
    assert kwargs1["data"]["termId"] == "999"


@pytest.mark.asyncio
async def test_fetch_course_sections_timeout_returns_empty(
    mocker: pytest.MockFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scraper = CatalogScraper()
    client = MagicMock()
    client.get = AsyncMock(side_effect=httpx.TimeoutException("timeout"))
    mocker.patch("bot.scraper.catalog._httpx_client", return_value=_async_client_cm(client))

    out = await scraper.fetch_course_sections("CSCI 151", respect_rate_limit=False)
    assert out == []
    assert "timeout" in capsys.readouterr().out.lower()


@pytest.mark.asyncio
async def test_fetch_course_sections_unexpected_error_returns_empty(
    mocker: pytest.MockFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    scraper = CatalogScraper()
    client = MagicMock()
    client.get = AsyncMock(side_effect=RuntimeError("boom"))
    mocker.patch("bot.scraper.catalog._httpx_client", return_value=_async_client_cm(client))

    out = await scraper.fetch_course_sections("CSCI 151", respect_rate_limit=False)
    assert out == []
    captured = capsys.readouterr().out
    assert "Catalog scrape failed" in captured or "boom" in captured


def test_format_open_seats_message_with_section_lines() -> None:
    msg = format_open_seats_message(
        course_title="Intro",
        course_code="CSCI 151",
        instructor="Prof",
        schedule_detail="Mon",
        seats_by_section="• 01L: 2 / 50",
        available_seats=2,
        total_seats=50,
    )
    assert "По секциям" in msg
    assert "01L" in msg


def test_format_open_seats_message_without_section_lines_uses_total_only() -> None:
    msg = format_open_seats_message(
        course_title="Intro",
        course_code="CSCI 151",
        instructor="Prof",
        schedule_detail="Mon",
        seats_by_section="",
        available_seats=3,
        total_seats=30,
    )
    assert "Доступно мест (сумма по секциям): 3 / 30" in msg
    assert "По секциям" not in msg
