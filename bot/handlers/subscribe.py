"""Subscribe, unsubscribe, and course checks."""

from __future__ import annotations

from typing import Any, Dict

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from bot.db.database import Database
from bot.scraper.catalog import (
    CatalogScraper,
    format_open_seats_message,
    normalize_course_code,
)

router = Router(name="subscribe")


def _status_reply(agg: Dict[str, Any], sections_count: int) -> str:
    title = agg.get("course_title") or "—"
    code = agg.get("course_code", "")
    avail = agg.get("available_seats", 0)
    cap = agg.get("total_seats_display", 0)
    instr = agg.get("instructor") or "—"
    sched = (agg.get("schedule") or "—").strip()
    seats_sec = (agg.get("seats_by_section") or "").strip()
    seats_block = (
        f"💺 По секциям:\n{seats_sec}\n"
        f"Всего (сумма по секциям): {avail} / {cap}\n"
        if seats_sec
        else f"💺 Свободно (сумма по секциям): {avail} / {cap}\n"
    )
    return (
        f"✅ Подписка сохранена.\n\n"
        f"📚 {title} ({code})\n"
        f"👨‍🏫 {instr}\n"
        f"🕐 Расписание:\n{sched}\n"
        f"{seats_block}"
        f"📑 Секций в расписании: {sections_count}\n"
    )


async def _scrape_aggregate(
    scraper: CatalogScraper, code: str, *, respect_rate_limit: bool = True
) -> tuple[list, dict]:
    sections = await scraper.fetch_course_sections(
        code, respect_rate_limit=respect_rate_limit
    )
    agg = scraper.aggregate_snapshot_payload(sections)
    agg["course_code"] = normalize_course_code(code) or code.upper()
    return sections, agg


@router.message(Command("subscribe"))
async def cmd_subscribe(
    message: Message,
    command: CommandObject,
    db: Database,
    scraper: CatalogScraper,
) -> None:
    """Validate course code, scrape, store subscription, and optionally notify."""
    if message.from_user is None:
        return
    raw = (command.args or "").strip()
    if not raw:
        await message.answer("Укажи код курса: `/subscribe CSCI 151`", parse_mode="Markdown")
        return
    normalized = normalize_course_code(raw)
    if not normalized:
        await message.answer(
            "Неверный формат. Примеры: `CSCI 151`, `MATH 162`",
            parse_mode="Markdown",
        )
        return

    await db.upsert_user(
        message.from_user.id,
        message.from_user.username,
        message.from_user.full_name or message.from_user.first_name,
    )

    wait_msg = await message.answer("Проверяю каталог…")
    sections, agg = await _scrape_aggregate(
        scraper, normalized, respect_rate_limit=False
    )
    await wait_msg.delete()

    if not sections:
        await message.answer(
            f"Курс {normalized} не найден в выбранном семестре или каталог временно недоступен. "
            f"Проверьте семестр (CATALOG_TERM_ID) и попробуйте /check {normalized} позже."
        )
        return

    await db.add_subscription(message.from_user.id, normalized)
    await db.upsert_snapshot(
        normalized,
        int(agg["available_seats"]),
        agg.get("instructor"),
        agg.get("schedule"),
        agg["payload"],
    )
    await db.upsert_user_notification_state(
        message.from_user.id, normalized, int(agg["available_seats"])
    )

    await message.answer(_status_reply(agg, len(sections)))

    if int(agg["available_seats"]) > 0:
        note = format_open_seats_message(
            str(agg.get("course_title") or normalized),
            normalized,
            str(agg.get("instructor") or "—"),
            str(agg.get("schedule") or "—"),
            str(agg.get("seats_by_section") or ""),
            int(agg["available_seats"]),
            int(agg.get("total_seats_display") or 0),
        )
        await message.answer("Сейчас уже есть свободные места:\n\n" + note)


@router.message(Command("unsubscribe"))
async def cmd_unsubscribe(
    message: Message,
    command: CommandObject,
    db: Database,
) -> None:
    """Deactivate a subscription."""
    if message.from_user is None:
        return
    raw = (command.args or "").strip()
    if not raw:
        await message.answer("Укажи код: `/unsubscribe CSCI 151`", parse_mode="Markdown")
        return
    normalized = normalize_course_code(raw)
    if not normalized:
        await message.answer("Неверный формат курса.")
        return
    n = await db.deactivate_subscription(message.from_user.id, normalized)
    if n:
        await message.answer(f"Подписка на {normalized} отключена.")
    else:
        await message.answer("Активной подписки на этот курс не было.")
