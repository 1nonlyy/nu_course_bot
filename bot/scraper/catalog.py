"""NU Public Course Catalog via the registrar JSON API (httpx).

The catalog UI is a Drupal page, but section/seat data comes from POST endpoints
under ``/my-registrar/public-course-catalog/json`` (``getSearchData``, ``getSchedule``).
No browser or JS execution is required: we warm the session with a GET to the catalog
page (cookies + same origin as the browser flow), then call those endpoints directly.

If ``CATALOG_TERM_ID`` is unset, the active semester id is read from the server-rendered
``#semesterComboId`` options in that HTML — still no JS.
"""

from __future__ import annotations

import asyncio
import json
import re
import time
from collections import defaultdict
from typing import Any, Optional
from urllib.parse import urljoin

import httpx
import structlog
from pydantic import BaseModel, Field

from bot.config import Settings, get_settings

logger = structlog.get_logger(__name__)

_CATALOG_JSON_PATH = "/my-registrar/public-course-catalog/json"
_DEFAULT_HTTP_TIMEOUT = httpx.Timeout(90.0)
_CATALOG_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


class CourseInfo(BaseModel):
    """One course section row (lecture/lab) from the schedule."""

    course_code: str
    course_title: str
    instructor_name: str = ""
    schedule: str = Field(default="", description="Catalog line: ST · days/times · room.")
    schedule_body: str = Field(default="", description="Days, times, room only.")
    available_seats: int = 0
    total_seats: int = 0
    section_type: str = ""
    course_id: str = ""
    instance_id: Optional[str] = None


def _section_component_rank(st: str) -> tuple[int, str]:
    """Sort sections for display: lecture, lab, recitation, then other."""
    s = (st or "").strip()
    if s.lower().endswith("lb"):
        return (1, s)
    if len(s) >= 1 and s[-1].upper() == "R":
        return (2, s)
    if len(s) >= 1 and s[-1].upper() == "L":
        return (0, s)
    return (3, s)


def _section_kind_ru(st: str) -> str:
    s = (st or "").strip()
    sl = s.lower()
    if sl.endswith("lb"):
        return "лаб."
    if len(s) >= 1 and s[-1].upper() == "R":
        return "речит."
    if len(s) >= 1 and s[-1].upper() == "L":
        return "лекция"
    return "секция"


def _pick_display_instructor(sections: list[CourseInfo]) -> str:
    """Prefer lecture instructor; else first non-empty name."""
    lectures = [s for s in sections if _section_component_rank(s.section_type)[0] == 0]
    for group in (lectures, sections):
        for s in group:
            name = (s.instructor_name or "").strip()
            if name and name.upper() != "TBA":
                return s.instructor_name
    for s in sections:
        if (s.instructor_name or "").strip():
            return s.instructor_name
    return "—"


def normalize_course_code(raw: str) -> Optional[str]:
    """
    Normalize user input like 'csci 151' or 'CSCI151' to 'CSCI 151'.

    Returns None if the string does not look like a valid catalog code.
    """
    s = raw.strip().upper()
    s = re.sub(r"\s+", " ", s)
    m = re.match(r"^([A-Z]{2,6})\s+(\d{3}[A-Z]?)$", s)
    if m:
        return f"{m.group(1)} {m.group(2)}"
    m2 = re.match(r"^([A-Z]{2,6})(\d{3}[A-Z]?)$", s.replace(" ", ""))
    if m2:
        return f"{m2.group(1)} {m2.group(2)}"
    return None


def _catalog_http_verify_flag(settings: Settings) -> bool:
    """When catalog_ignore_tls_errors is True, disable TLS verification (registrar chain)."""
    return not settings.catalog_ignore_tls_errors


def _json_endpoint_url(settings: Settings) -> str:
    base = settings.catalog_base_url.rstrip("/") + "/"
    return urljoin(base, _CATALOG_JSON_PATH.lstrip("/"))


def _catalog_page_url(settings: Settings) -> str:
    return urljoin(settings.catalog_base_url.rstrip("/") + "/", "course-catalog")


def _parse_term_id_from_catalog_html(html: str) -> str:
    """First non-placeholder ``value`` in ``#semesterComboId`` (server-rendered HTML)."""
    m = re.search(
        r'id=["\']semesterComboId["\'][^>]*>(.*?)</select>',
        html,
        re.IGNORECASE | re.DOTALL,
    )
    if not m:
        raise RuntimeError("Could not find #semesterComboId in catalog HTML")
    block = m.group(1)
    for om in re.finditer(
        r'<option[^>]*\svalue=["\']([^"\']*)["\']',
        block,
        re.IGNORECASE,
    ):
        vid = (om.group(1) or "").strip()
        if vid and vid != "-1":
            return vid
    raise RuntimeError("Could not resolve catalog term id from semester dropdown HTML")


def _httpx_client(settings: Settings) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        verify=_catalog_http_verify_flag(settings),
        timeout=_DEFAULT_HTTP_TIMEOUT,
        headers={"User-Agent": _CATALOG_USER_AGENT},
        follow_redirects=True,
    )


async def post_catalog_json(
    client: httpx.AsyncClient,
    json_url: str,
    form: dict[str, str | int],
) -> Any:
    """POST ``application/x-www-form-urlencoded`` to the registrar JSON endpoint."""
    str_form = {k: str(v) for k, v in form.items()}
    response = await client.post(json_url, data=str_form)
    text = response.text
    if response.status_code >= 400:
        logger.error(
            "Catalog JSON HTTP %s: %s" % (response.status_code, text[:500]),
            http_status=response.status_code,
        )
        return None
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.error(
            "Catalog JSON decode error: %s" % text[:500],
        )
        return None


async def fetch_search_data(
    client: httpx.AsyncClient,
    json_url: str,
    term_id: str,
    quick: str,
) -> list[dict[str, Any]]:
    """Call ``getSearchData`` (Oracle rejects empty IN () lists — keep params minimal)."""
    payload: dict[str, str | int] = {
        "method": "getSearchData",
        "searchParams[formSimple]": "true",
        "searchParams[limit]": 50,
        "searchParams[page]": 1,
        "searchParams[start]": 0,
        "searchParams[quickSearch]": quick,
        "searchParams[sortField]": -1,
        "searchParams[sortDescending]": -1,
        "searchParams[semester]": term_id,
    }
    data = await post_catalog_json(client, json_url, payload)
    if not isinstance(data, dict):
        return []
    rows = data.get("data")
    if not isinstance(rows, list):
        return []
    return [r for r in rows if isinstance(r, dict)]


async def fetch_schedule(
    client: httpx.AsyncClient,
    json_url: str,
    term_id: str,
    course_id: str,
) -> list[dict[str, Any]]:
    """Call ``getSchedule`` for one course offering."""
    data = await post_catalog_json(
        client,
        json_url,
        {"method": "getSchedule", "courseId": course_id, "termId": term_id},
    )
    if not isinstance(data, list):
        return []
    return [r for r in data if isinstance(r, dict)]


class ScrapeRateLimiter:
    """Enforce minimum interval between scrapes per course code."""

    def __init__(self, min_interval_seconds: float) -> None:
        self._min_interval = min_interval_seconds
        self._last: dict[str, float] = defaultdict(float)
        self._lock = asyncio.Lock()

    async def wait_for_slot(self, course_code: str) -> None:
        """Sleep if this course was scraped too recently."""
        async with self._lock:
            now = time.monotonic()
            last = self._last[course_code]
            wait = self._min_interval - (now - last)
            if wait > 0:
                logger.debug(
                    "Rate limit: sleeping %.1fs for %s" % (wait, course_code),
                    wait_seconds=round(wait, 1),
                    course_code=course_code,
                )
                await asyncio.sleep(wait)
            self._last[course_code] = time.monotonic()


class CatalogScraper:
    """
    Fetches catalog data via httpx: session warmup GET, then registrar JSON POSTs.
    """

    def __init__(
        self,
        settings: Optional[Settings] = None,
        rate_limiter: Optional[ScrapeRateLimiter] = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._limiter = rate_limiter or ScrapeRateLimiter(
            float(self._settings.scrape_min_interval_seconds)
        )

    @staticmethod
    def _schedule_body(row: dict[str, Any]) -> str:
        days = " ".join(str(row.get("DAYS", "")).split())
        times = str(row.get("TIMES", "")).strip()
        room = str(row.get("ROOM", "")).strip()
        parts = [p for p in (f"{days} {times}".strip(), room) if p]
        return " · ".join(parts)

    @staticmethod
    def _schedule_label(row: dict[str, Any]) -> str:
        days = " ".join(str(row.get("DAYS", "")).split())
        times = str(row.get("TIMES", "")).strip()
        room = str(row.get("ROOM", "")).strip()
        st = str(row.get("ST", "")).strip()
        parts = [p for p in (st, f"{days} {times}".strip(), room) if p]
        return " · ".join(parts)

    @staticmethod
    def _clean_instructor(name: str) -> str:
        return re.sub(r"<br\s*/?>", ", ", name, flags=re.I).strip()

    async def fetch_course_sections(
        self,
        course_code: str,
        *,
        respect_rate_limit: bool = True,
    ) -> list[CourseInfo]:
        """
        Load all sections matching ``course_code`` for the configured term.

        ``respect_rate_limit`` is for background polling; set False for /subscribe
        and /check so users are not blocked by SCRAPE_MIN_INTERVAL_SECONDS.

        On failure (timeout, network, parse), logs and returns an empty list.
        """
        t0 = time.monotonic()
        log_course_code = course_code
        try:
            normalized = normalize_course_code(course_code)
            if not normalized:
                logger.warning(
                    "Invalid course code: %s" % course_code,
                    course_code=course_code,
                )
                return []

            log_course_code = normalized

            if respect_rate_limit:
                await self._limiter.wait_for_slot(normalized)

            catalog_url = _catalog_page_url(self._settings)
            json_url = _json_endpoint_url(self._settings)

            try:
                async with _httpx_client(self._settings) as client:
                    warm = await client.get(catalog_url)
                    if warm.status_code >= 400:
                        logger.error(
                            "Catalog page HTTP %s (session warmup)" % warm.status_code,
                            http_status=warm.status_code,
                            course_code=normalized,
                        )
                        return []

                    configured_term = (self._settings.catalog_term_id or "").strip()
                    if configured_term:
                        term_id = configured_term
                    else:
                        term_id = _parse_term_id_from_catalog_html(warm.text)

                    courses = await fetch_search_data(client, json_url, term_id, normalized)
                    target = normalized.replace(" ", "").upper()
                    matches = []
                    for row in courses:
                        abbr = str(row.get("ABBR", "")).replace(" ", "").upper()
                        if abbr == target:
                            matches.append(row)

                    if not matches:
                        logger.info(
                            "Catalog search returned no ABBR match for %s" % normalized,
                            course_code=normalized,
                        )

                    results: list[CourseInfo] = []
                    title = ""
                    if matches:
                        title = str(matches[0].get("TITLE", "") or "")

                    for row in matches:
                        cid = str(row.get("COURSEID", ""))
                        if not cid:
                            continue
                        title = str(row.get("TITLE", "") or title)
                        schedule_rows = await fetch_schedule(client, json_url, term_id, cid)
                        seen_keys: set[tuple[str, str]] = set()
                        for sec in schedule_rows:
                            st = str(sec.get("ST", "") or "")
                            inst = str(sec.get("INSTANCEID", "") or "")
                            dedupe_key = (inst, st)
                            if dedupe_key in seen_keys:
                                continue
                            seen_keys.add(dedupe_key)
                            cap_raw = sec.get("CAPACITY", 0)
                            enr_raw = sec.get("ENR", 0)
                            try:
                                cap = int(str(cap_raw).strip()) if str(cap_raw).strip() != "" else 0
                                enr = int(enr_raw)
                            except (TypeError, ValueError):
                                cap, enr = 0, 0
                            avail = max(0, cap - enr)
                            faculty = str(sec.get("FACULTY", "") or "")
                            results.append(
                                CourseInfo(
                                    course_code=normalized,
                                    course_title=title,
                                    instructor_name=self._clean_instructor(faculty),
                                    schedule=self._schedule_label(sec),
                                    schedule_body=self._schedule_body(sec),
                                    available_seats=avail,
                                    total_seats=cap,
                                    section_type=st,
                                    course_id=cid,
                                    instance_id=inst or None,
                                )
                            )
                    return results
            except httpx.TimeoutException as exc:
                logger.warning(
                    "HTTP timeout for %s: %s" % (normalized, exc),
                    course_code=normalized,
                    error=str(exc),
                )
                return []
            except Exception:
                logger.exception(
                    "Catalog scrape failed for %s" % normalized,
                    course_code=normalized,
                )
                return []
        finally:
            elapsed = time.monotonic() - t0
            logger.info(
                "Catalog scrape finished for %s (duration %.3fs)"
                % (log_course_code, elapsed),
                course_code=log_course_code,
                scrape_duration_seconds=round(elapsed, 3),
            )

    def aggregate_snapshot_payload(self, sections: list[CourseInfo]) -> dict[str, Any]:
        """Build summary fields and JSON-serializable payload for persistence."""
        total_avail = sum(s.available_seats for s in sections)
        total_cap = sum(s.total_seats for s in sections)
        ordered = sorted(
            sections,
            key=lambda s: (_section_component_rank(s.section_type), s.section_type or ""),
        )
        sched_lines: list[str] = []
        seat_lines: list[str] = []
        for s in ordered:
            kind = _section_kind_ru(s.section_type)
            body = (s.schedule_body or s.schedule or "—").strip() or "—"
            sched_lines.append(f"• {s.section_type} ({kind}): {body}")
            seat_lines.append(
                f"• {s.section_type} ({kind}): {s.available_seats} / {s.total_seats}"
            )
        schedule_block = "\n".join(sched_lines) if sched_lines else None
        seats_block = "\n".join(seat_lines) if seat_lines else ""
        payload = {
            "sections": [s.model_dump() for s in sections],
            "total_available_seats": total_avail,
            "total_capacity_seats": total_cap,
        }
        title = ordered[0].course_title if ordered else ""
        return {
            "available_seats": total_avail,
            "total_seats_display": total_cap,
            "instructor": _pick_display_instructor(sections) if sections else None,
            "schedule": schedule_block,
            "seats_by_section": seats_block,
            "course_title": title,
            "payload": payload,
        }


def format_open_seats_message(
    course_title: str,
    course_code: str,
    instructor: str,
    schedule_detail: str,
    seats_by_section: str,
    available_seats: int,
    total_seats: int,
) -> str:
    """Telegram notification body when seats open."""
    sched = schedule_detail.strip() or "—"
    seats_lines = (seats_by_section or "").strip()
    seats_part = (
        f"💺 По секциям:\n{seats_lines}\n"
        f"Всего (сумма по секциям): {available_seats} / {total_seats}\n"
        if seats_lines
        else f"💺 Доступно мест (сумма по секциям): {available_seats} / {total_seats}\n"
    )
    return (
        "🔔 Место освободилось!\n\n"
        f"📚 {course_title} ({course_code})\n"
        f"👨‍🏫 Преподаватель: {instructor}\n"
        f"🕐 Расписание:\n{sched}\n"
        f"{seats_part}\n"
        "Быстро регистрируйтесь в Course Catalog!"
    )
