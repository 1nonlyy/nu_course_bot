"""Show last catalog snapshot for a course (database only, no scraping)."""

from __future__ import annotations

import json
from typing import Any

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from bot.db.database import Database
from bot.db.models import CourseSnapshot
from bot.scraper.catalog import normalize_course_code

router = Router(name="status")


def _format_section_lines(raw_payload: dict[str, Any]) -> tuple[str, int]:
    """Return per-section text and section count from stored snapshot JSON."""
    sections = raw_payload.get("sections")
    if not isinstance(sections, list) or not sections:
        return "", 0
    lines: list[str] = []
    for row in sections:
        if not isinstance(row, dict):
            continue
        st = str(row.get("section_type") or "—").strip() or "—"
        try:
            avail = int(row.get("available_seats") or 0)
        except (TypeError, ValueError):
            avail = 0
        try:
            cap = int(row.get("total_seats") or 0)
        except (TypeError, ValueError):
            cap = 0
        lines.append(f"• {st}: {avail} / {cap}")
    return ("\n".join(lines), len(lines))


def _snapshot_answer(course_code: str, snap: CourseSnapshot) -> str:
    updated = snap.last_checked.strftime("%Y-%m-%d %H:%M UTC")
    total_avail = int(snap.available_seats)
    raw = snap.raw_json
    cap_total: int | None = None
    section_block = ""
    n_sections = 0

    if raw:
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            payload = {}
        else:
            if isinstance(payload, dict):
                tc = payload.get("total_capacity_seats")
                if tc is not None:
                    try:
                        cap_total = int(tc)
                    except (TypeError, ValueError):
                        cap_total = None
                sec_text, n_sections = _format_section_lines(payload)
                if sec_text:
                    section_block = f"💺 По секциям:\n{sec_text}\n"

    cap_part = (
        f"Всего мест (ёмкость): {cap_total}\n" if cap_total is not None else ""
    )
    seats_summary = (
        f"Свободно мест (сумма): {total_avail}\n"
        f"{cap_part}"
        f"{section_block}"
        f"📑 Секций в снимке: {n_sections}\n"
        if section_block or cap_total is not None
        else f"Свободно мест (сумма): {total_avail}\n"
    )
    return (
        f"📊 Последний снимок: {course_code}\n"
        f"🕐 Проверено: {updated}\n"
        f"{seats_summary}"
    )


@router.message(Command("status"))
async def cmd_status(
    message: Message,
    command: CommandObject,
    db: Database,
) -> None:
    """Reply with last stored enrollment snapshot for a course code."""
    if message.from_user is None:
        return
    raw = (command.args or "").strip()
    if not raw:
        await message.answer("Пример: `/status CSCI 151`", parse_mode="Markdown")
        return
    normalized = normalize_course_code(raw)
    if not normalized:
        await message.answer("Неверный формат курса.")
        return

    snap = await db.get_snapshot(normalized)
    if snap is None:
        await message.answer(
            f"По курсу {normalized} ещё нет сохранённых данных. "
            "Подпишись через /subscribe или дождись следующей проверки каталога."
        )
        return

    await message.answer(_snapshot_answer(normalized, snap))
