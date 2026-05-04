"""Periodic catalog polling and push notifications."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

from bot.db.database import Database
from bot.scraper.catalog import CatalogScraper, format_open_seats_message

logger = logging.getLogger(__name__)


async def _send_with_retry(bot: Bot, chat_id: int, text: str) -> None:
    """Send a message respecting Telegram flood limits."""
    try:
        await bot.send_message(chat_id, text)
    except TelegramRetryAfter as exc:
        logger.warning("FloodWait %ss for chat %s", exc.retry_after, chat_id)
        await asyncio.sleep(exc.retry_after)
        await bot.send_message(chat_id, text)


async def poll_catalog_job(bot: Bot, db: Database, scraper: CatalogScraper) -> None:
    """
    For every course with active subscribers, scrape once and notify on 0 → N seat openings.

    Updates ``course_snapshots`` and logs each check. Never raises to the scheduler.
    """
    checked_at = datetime.now(timezone.utc).replace(microsecond=0)
    try:
        grouped = await db.all_active_subscriptions_grouped()
        if not grouped:
            logger.info("[%s] No active subscriptions; skip poll", checked_at.isoformat())
            return

        for course_code, user_ids in grouped.items():
            try:
                sections = await scraper.fetch_course_sections(course_code)
                agg = scraper.aggregate_snapshot_payload(sections)
                prev = await db.get_snapshot(course_code)
                new_avail = int(agg["available_seats"])
                old_avail = int(prev.available_seats) if prev is not None else None

                await db.upsert_snapshot(
                    course_code,
                    new_avail,
                    agg.get("instructor"),
                    agg.get("schedule"),
                    agg["payload"],
                )

                logger.info(
                    "[%s] Checked %s seats=%s (prev=%s subscribers=%s)",
                    checked_at.isoformat(),
                    course_code,
                    new_avail,
                    old_avail,
                    len(user_ids),
                )

                if (
                    prev is not None
                    and old_avail == 0
                    and new_avail > 0
                    and sections
                ):
                    title = str(agg.get("course_title") or course_code)
                    body = format_open_seats_message(
                        title,
                        course_code,
                        str(agg.get("instructor") or "—"),
                        str(agg.get("schedule") or "—"),
                        str(agg.get("seats_by_section") or ""),
                        new_avail,
                        int(agg.get("total_seats_display") or 0),
                    )
                    for uid in user_ids:
                        try:
                            await _send_with_retry(bot, uid, body)
                        except TelegramForbiddenError:
                            logger.warning("User %s blocked the bot; skip notify", uid)
                        except Exception:
                            logger.exception("Failed to notify user %s", uid)
            except Exception:
                logger.exception("Poll failed for course %s", course_code)
    except Exception:
        logger.exception("Poll job crashed")
