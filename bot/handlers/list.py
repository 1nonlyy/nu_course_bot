"""Listing subscriptions and manual checks."""

from __future__ import annotations

import time

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.types import Message

from bot.config import get_settings
from bot.db.database import Database
from bot.scraper.catalog import CatalogScraper, normalize_course_code

router = Router(name="list")

# Per-user last /check time; in-process only (not shared across multiple bot replicas).
_check_last_at: dict[int, float] = {}


async def format_subscription_lines(db: Database, user_id: int) -> str:
    """Build human-readable multi-line summary of active subscriptions."""
    subs = await db.list_active_subscriptions(user_id)
    if not subs:
        return "У тебя пока нет активных подписок. Используй /subscribe КОД."

    lines: list[str] = ["📋 Активные подписки:\n"]
    for s in subs:
        snap = await db.get_snapshot(s.course_code)
        if snap:
            updated = snap.last_checked.strftime("%Y-%m-%d %H:%M")
            lines.append(
                f"• {s.course_code} — свободно: {snap.available_seats} "
                f"(обновлено {updated})"
            )
        else:
            lines.append(f"• {s.course_code} — данных ещё нет (скоро появятся)")
    return "\n".join(lines)


@router.message(Command("mysubs"))
async def cmd_mysubs(message: Message, db: Database) -> None:
    """List active subscriptions with last known seat totals."""
    if message.from_user is None:
        return
    text = await format_subscription_lines(db, message.from_user.id)
    await message.answer(text)


@router.message(Command("check"))
async def cmd_check(
    message: Message,
    command: CommandObject,
    scraper: CatalogScraper,
) -> None:
    """Run a single catalog check without persisting a subscription."""
    raw = (command.args or "").strip()
    if not raw:
        await message.answer("Пример: `/check CSCI 151`", parse_mode="Markdown")
        return
    normalized = normalize_course_code(raw)
    if not normalized:
        await message.answer("Неверный формат курса.")
        return

    if message.from_user is not None:
        uid = message.from_user.id
        now = time.time()
        last = _check_last_at.get(uid)
        limit_s = get_settings().check_rate_limit_seconds
        if last is not None and now - last < limit_s:
            await message.answer("Подождите немного перед следующей проверкой")
            return
        _check_last_at[uid] = now

    wait = await message.answer("Запрашиваю каталог…")
    sections = await scraper.fetch_course_sections(
        normalized, respect_rate_limit=False
    )
    agg = scraper.aggregate_snapshot_payload(sections)
    await wait.delete()

    if not sections:
        await message.answer("Не удалось получить данные. Попробуйте позже.")
        return

    title = agg.get("course_title") or normalized
    sched = (agg.get("schedule") or "—").strip()
    seats_sec = (agg.get("seats_by_section") or "").strip()
    seats_part = (
        f"💺 По секциям:\n{seats_sec}\n"
        f"Всего (сумма по секциям): {agg['available_seats']} / "
        f"{agg.get('total_seats_display', 0)}\n"
        if seats_sec
        else (
            f"💺 Свободно (сумма по секциям): {agg['available_seats']} / "
            f"{agg.get('total_seats_display', 0)}\n"
        )
    )
    await message.answer(
        f"📚 {title} ({normalized})\n"
        f"👨‍🏫 {agg.get('instructor') or '—'}\n"
        f"🕐 Расписание:\n{sched}\n"
        f"{seats_part}"
        f"📑 Секций: {len(sections)}"
    )
