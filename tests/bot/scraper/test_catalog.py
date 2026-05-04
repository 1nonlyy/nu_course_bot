"""Tests for ``bot.scraper.catalog`` helpers and JSON POST (mocked HTTP)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.scraper.catalog import CatalogScraper, CourseInfo, normalize_course_code

pytestmark = pytest.mark.usefixtures("settings_env")


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
    scraper = CatalogScraper(MagicMock())
    agg = scraper.aggregate_snapshot_payload([])
    assert agg["available_seats"] == 0
    assert agg["total_seats_display"] == 0
    assert agg["instructor"] is None
    assert agg["payload"]["sections"] == []


@pytest.mark.asyncio
async def test_post_json_success() -> None:
    scraper = CatalogScraper(MagicMock())
    page = MagicMock()
    response = MagicMock()
    response.status = 200
    response.text = AsyncMock(return_value='{"items": [1]}')
    page.context.request.post = AsyncMock(return_value=response)

    data = await scraper._post_json(page, {"method": "getSearchData", "n": 1})

    assert data == {"items": [1]}
    page.context.request.post.assert_awaited_once()
    _args, kwargs = page.context.request.post.await_args
    assert kwargs["form"] == {"method": "getSearchData", "n": "1"}


@pytest.mark.asyncio
async def test_post_json_http_error_returns_none() -> None:
    scraper = CatalogScraper(MagicMock())
    page = MagicMock()
    response = MagicMock()
    response.status = 500
    response.text = AsyncMock(return_value="Internal Error")
    page.context.request.post = AsyncMock(return_value=response)

    assert await scraper._post_json(page, {"method": "x"}) is None


@pytest.mark.asyncio
async def test_post_json_invalid_json_returns_none() -> None:
    scraper = CatalogScraper(MagicMock())
    page = MagicMock()
    response = MagicMock()
    response.status = 200
    response.text = AsyncMock(return_value="not json")
    page.context.request.post = AsyncMock(return_value=response)

    assert await scraper._post_json(page, {"method": "x"}) is None


def test_pick_display_instructor_prefers_lecture() -> None:
    scraper = CatalogScraper(MagicMock())
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
