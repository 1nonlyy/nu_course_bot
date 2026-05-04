"""Application entry: Telegram bot + APScheduler."""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Awaitable, Callable
from typing import Any

import sentry_sdk
import structlog
from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import TelegramObject
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from bot.config import get_settings
from bot.db.database import get_database
from bot.handlers import register_handlers
from bot.scraper.catalog import CatalogScraper
from bot.scheduler.jobs import poll_catalog_job

logger = structlog.get_logger(__name__)

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


def _configure_logging(level: str, environment: str) -> None:
    """
    structlog: JSON in production, console in ENV=dev; stdlib logging for third-party modules.

    Private to this module — not a stable API for importers.
    """
    log_level = getattr(logging, level.upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
        stream=sys.stdout,
    )
    is_dev = environment.strip().lower() == "dev"
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        structlog.dev.set_exc_info,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
    ]
    processors: list[structlog.types.Processor]
    if is_dev:
        processors = [*shared_processors, structlog.dev.ConsoleRenderer()]
    else:
        processors = [*shared_processors, structlog.processors.JSONRenderer()]
    structlog.configure(
        processors=processors,
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        logger_factory=structlog.PrintLoggerFactory(file=sys.stdout),
        cache_logger_on_first_use=True,
    )


async def run() -> None:
    """Start polling and scheduler."""
    settings = get_settings()
    _ensure_production_bot_token(settings.bot_token)
    _configure_logging(settings.log_level, settings.environment)
    if (settings.sentry_dsn or "").strip():
        sentry_sdk.init(dsn=settings.sentry_dsn, traces_sample_rate=0.1)

    db = get_database(settings)
    await db.init_schema()

    scraper = CatalogScraper(settings)

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
    logger.info(
        "Scheduler started: interval=%s min" % settings.poll_interval_minutes,
        poll_interval_minutes=settings.poll_interval_minutes,
    )

    try:
        await dp.start_polling(bot)
    finally:
        scheduler.shutdown(wait=False)
        await bot.session.close()


def main() -> None:
    """CLI entry point."""
    asyncio.run(run())


if __name__ == "__main__":
    main()
