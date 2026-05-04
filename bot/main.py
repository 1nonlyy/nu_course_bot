"""Application entry: Telegram bot + APScheduler + Playwright lifecycle."""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import TelegramObject
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from bot.config import get_settings
from bot.db.database import get_database
from bot.handlers import register_handlers
from bot.scraper.browser import BrowserManager
from bot.scraper.catalog import CatalogScraper
from bot.scheduler.jobs import poll_catalog_job

# Placeholder / obvious non-production token prefixes (real tokens start with digits, then ":").
_FORBIDDEN_BOT_TOKEN_PREFIXES: tuple[str, ...] = (
    "change_me",
    "your_",
    "placeholder",
    "dummy",
    "invalid",
    "xxxxx",
    "test:",
    "000000000:",
)


def _ensure_production_bot_token(token: str) -> None:
    t = token.strip()
    if not t:
        raise RuntimeError(
            "BOT_TOKEN is empty. Set a valid token from @BotFather in .env "
            "(never commit .env)."
        )
    lower = t.lower()
    if any(lower.startswith(p) for p in _FORBIDDEN_BOT_TOKEN_PREFIXES):
        raise RuntimeError(
            "BOT_TOKEN looks like a placeholder or test value. Replace it with your "
            "real token from @BotFather in .env (see .env.example)."
        )


class InjectMiddleware(BaseMiddleware):
    """Provide ``db`` and ``scraper`` to handlers."""

    def __init__(self, db: Any, scraper: CatalogScraper) -> None:
        super().__init__()
        self._db = db
        self._scraper = scraper

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        data["db"] = self._db
        data["scraper"] = self._scraper
        return await handler(event, data)


def _configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stdout,
    )


async def run() -> None:
    """Start polling, scheduler, and background browser."""
    settings = get_settings()
    _ensure_production_bot_token(settings.bot_token)
    _configure_logging(settings.log_level)

    db = get_database(settings)
    await db.init_schema()

    browser = BrowserManager(settings)
    await browser.start()
    scraper = CatalogScraper(browser, settings)

    bot = Bot(
        settings.bot_token,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher()
    dp.update.middleware(InjectMiddleware(db, scraper))
    register_handlers(dp)

    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        poll_catalog_job,
        trigger=IntervalTrigger(minutes=settings.poll_interval_minutes),
        args=[bot, db, scraper],
        id="poll_catalog",
        replace_existing=True,
        misfire_grace_time=120,
        coalesce=True,
        max_instances=1,
    )
    scheduler.start()
    logging.getLogger(__name__).info(
        "Scheduler started: interval=%s min",
        settings.poll_interval_minutes,
    )

    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await browser.stop()
        await bot.session.close()


def main() -> None:
    """CLI entry point."""
    asyncio.run(run())


if __name__ == "__main__":
    main()
