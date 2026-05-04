"""Periodic catalog polling and push notifications."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

import structlog
from aiogram import Bot
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter

from bot.db.database import Database
from bot.scraper.catalog import CatalogScraper, format_open_seats_message

logger = structlog.get_logger(__name__)


async def _send_with_retry(bot: Bot, chat_id: int, text: str) -> None:
    """Send a message respecting Telegram flood limits."""
    try:
        await bot.send_message(chat_id, text)
    except TelegramRetryAfter as exc:
        logger.warning(
            "FloodWait %ss for chat %s" % (exc.retry_after, chat_id),
            retry_after_seconds=exc.retry_after,
            chat_id=chat_id,
        )
        await asyncio.sleep(exc.retry_after)
        await bot.send_message(chat_id, text)


async def poll_catalog_job(bot: Bot, db: Database, scraper: CatalogScraper) -> None:
    """
    For every course with active subscribers, scrape once and notify per user when their
    baseline was zero seats and the course now has open seats.

    Updates ``course_snapshots`` (for /mysubs) and ``user_notification_state`` for
    notification decisions. Never raises to the scheduler.
    """
    checked_at = datetime.now(timezone.utc).replace(microsecond=0)
    try:
        grouped = await db.all_active_subscriptions_grouped()
        if not grouped:
            logger.info(
                "[%s] No active subscriptions; skip poll" % checked_at.isoformat(),
                checked_at=checked_at.isoformat(),
            )
            return

        for course_code, user_ids in grouped.items():
            user_count = len(user_ids)
            seats_found: int | None = None
            try:
                try:
                    sections = await scraper.fetch_course_sections(course_code)
                except Exception:
                    logger.exception(
                        "Poll failed for course scrape",
                        course_code=course_code,
                        user_count=user_count,
                        seats_found=None,
                    )
                    continue
                if not sections:
                    logger.warning(
                        (
                            "Skipping snapshot update for %s — scraper returned empty "
                            "sections (timeout, HTTP/API error, or no matching course in search)"
                        )
                        % course_code,
                        course_code=course_code,
                        user_count=user_count,
                        seats_found=None,
                    )
                    continue

                agg = scraper.aggregate_snapshot_payload(sections)
                prev = await db.get_snapshot(course_code)
                new_avail = int(agg["available_seats"])
                seats_found = new_avail
                old_avail = int(prev.available_seats) if prev is not None else None

                await db.upsert_snapshot(
                    course_code,
                    new_avail,
                    agg.get("instructor"),
                    agg.get("schedule"),
                    agg["payload"],
                )

                logger.info(
                    "[%s] Checked %s seats=%s (prev=%s subscribers=%s)"
                    % (
                        checked_at.isoformat(),
                        course_code,
                        new_avail,
                        old_avail,
                        user_count,
                    ),
                    course_code=course_code,
                    user_count=user_count,
                    seats_found=new_avail,
                    checked_at=checked_at.isoformat(),
                    prev_seats=old_avail,
                )

                title = str(agg.get("course_title") or course_code)
                for uid in user_ids:
                    last_notified = await db.get_user_notification_seats(uid, course_code)
                    if last_notified is None:
                        await db.upsert_user_notification_state(
                            uid, course_code, new_avail
                        )
                        continue
                    if new_avail == 0:
                        await db.upsert_user_notification_state(uid, course_code, 0)
                        continue
                    if last_notified == 0 and new_avail > 0:
                        body = format_open_seats_message(
                            title,
                            course_code,
                            str(agg.get("instructor") or "—"),
                            str(agg.get("schedule") or "—"),
                            str(agg.get("seats_by_section") or ""),
                            new_avail,
                            int(agg.get("total_seats_display") or 0),
                        )
                        try:
                            await _send_with_retry(bot, uid, body)
                        except TelegramForbiddenError:
                            logger.warning(
                                "User %s blocked the bot; skip notify" % uid,
                                user_id=uid,
                                course_code=course_code,
                            )
                            await db.upsert_user_notification_state(
                                uid, course_code, new_avail
                            )
                        except Exception:
                            logger.exception(
                                "Failed to notify user %s" % uid,
                                user_id=uid,
                                course_code=course_code,
                            )
                        else:
                            await db.upsert_user_notification_state(
                                uid, course_code, new_avail
                            )
                        continue
                    await db.upsert_user_notification_state(
                        uid, course_code, new_avail
                    )
            except Exception:
                logger.exception(
                    "Poll failed for course %s" % course_code,
                    course_code=course_code,
                    user_count=user_count,
                    seats_found=seats_found,
                )
    except Exception:
        logger.exception("Poll job crashed")
