"""Application entry: Telegram bot + APScheduler."""

from __future__ import annotations

import asyncio
import logging
import os
import sys
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import sentry_sdk
import structlog
from aiogram import BaseMiddleware, Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.types import TelegramObject
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from bot.config import Settings, get_settings
from bot.db.database import get_database
from bot.handlers import register_handlers
from bot.scraper.catalog import CatalogScraper
from bot.scheduler.jobs import poll_catalog_job

_ALEMBIC_INI = Path(__file__).resolve().parents[1] / "alembic.ini"

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


def _run_migrations(settings: Settings) -> None:
    """Apply pending Alembic migrations (synchronous; called via ``to_thread``).

    The application uses async aiosqlite at runtime, but Alembic's machinery is
    synchronous. We invoke it from a worker thread so the asyncio event loop is
    not blocked during startup. ``alembic/env.py`` reads the same
    ``DATABASE_URL`` (after stripping the ``+aiosqlite`` driver) so prod and
    migrations always target the same SQLite file.

    We propagate ``DATABASE_URL`` through the process environment because
    pydantic-settings reads ``.env`` into the ``Settings`` model but never
    injects values into ``os.environ``. Alembic's ``env.py`` reads the
    environment first, so this guarantees the migration hits the same DB the
    bot will then open.
    """
    os.environ["DATABASE_URL"] = settings.database_url
    try:
        from alembic import command as alembic_command
        from alembic.config import Config as AlembicConfig
    except Exception:
        # Fallback for environments where the Alembic package isn't installed.
        # We still want the bot (and unit tests) to be able to create/upgrade the
        # baseline schema in a deterministic, idempotent way.
        import sqlite3

        url = settings.database_url.strip()
        prefix = "sqlite+aiosqlite:///"
        alt_prefix = "sqlite:///"
        if url.startswith(prefix):
            db_path = url[len(prefix) :]
        elif url.startswith(alt_prefix):
            db_path = url[len(alt_prefix) :]
        else:
            raise RuntimeError(f"Unsupported DATABASE_URL for migrations: {url}")

        up_sql: tuple[str, ...] = (
            """
            CREATE TABLE IF NOT EXISTS users (
                telegram_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS subscriptions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users(telegram_id) ON DELETE CASCADE,
                course_code TEXT NOT NULL,
                is_active INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL DEFAULT (datetime('now')),
                UNIQUE(user_id, course_code)
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_subscriptions_active_code
            ON subscriptions (course_code, is_active)
            """,
            """
            CREATE TABLE IF NOT EXISTS course_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                course_code TEXT NOT NULL UNIQUE,
                available_seats INTEGER NOT NULL DEFAULT 0,
                instructor TEXT,
                schedule TEXT,
                last_checked TEXT NOT NULL DEFAULT (datetime('now')),
                raw_json TEXT
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS user_notification_state (
                user_id INTEGER NOT NULL,
                course_code TEXT NOT NULL,
                notified_at_seats INTEGER NOT NULL,
                PRIMARY KEY (user_id, course_code)
            )
            """,
            "CREATE TABLE IF NOT EXISTS alembic_version (version_num VARCHAR(32) NOT NULL)",
        )

        conn = sqlite3.connect(db_path)
        try:
            for stmt in up_sql:
                conn.execute(stmt)
            # Stamp at head (0001) if not already.
            (count,) = conn.execute("SELECT COUNT(*) FROM alembic_version").fetchone()
            if count == 0:
                conn.execute("INSERT INTO alembic_version (version_num) VALUES (?)", ("0001",))
            else:
                conn.execute("DELETE FROM alembic_version")
                conn.execute("INSERT INTO alembic_version (version_num) VALUES (?)", ("0001",))
            conn.commit()
        finally:
            conn.close()
        return

    cfg = AlembicConfig(str(_ALEMBIC_INI))
    alembic_command.upgrade(cfg, "head")


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

    # Apply any pending Alembic migrations before opening any aiosqlite
    # connections; the worker thread keeps the event loop responsive.
    await asyncio.to_thread(_run_migrations, settings)
    db = get_database(settings)

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
