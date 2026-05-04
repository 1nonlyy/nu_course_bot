"""Alembic environment.

Reads ``DATABASE_URL`` from :class:`bot.config.Settings` so migrations always
target the same SQLite file as the running bot, then converts the async URL
form (``sqlite+aiosqlite://``) into the sync form (``sqlite://``) that
Alembic / synchronous SQLAlchemy expect.

The application currently uses raw aiosqlite + handwritten SQL (see
``bot/db/database.py``), so there is no SQLAlchemy ``MetaData`` to autogenerate
from. ``target_metadata = None`` keeps Alembic in manual-migration mode.
"""

from __future__ import annotations

import os
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import engine_from_config, pool

from alembic import context

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None


def _resolve_database_url() -> str:
    """Pick the URL: explicit ``-x dburl=...`` > env var > Settings default.

    ``-x dburl=sqlite:///./other.db`` lets ops point one-off ``alembic upgrade``
    invocations at a different file (e.g. when running migrations against a
    backup). Otherwise we read ``DATABASE_URL`` from the same pydantic Settings
    the bot uses, which keeps prod and migrations in lockstep.
    """
    x_args = context.get_x_argument(as_dictionary=True)
    if "dburl" in x_args:
        url = x_args["dburl"]
    elif os.environ.get("DATABASE_URL"):
        url = os.environ["DATABASE_URL"]
    else:
        # Imported lazily so that running ``alembic`` in a stripped-down
        # environment (e.g. ``-x dburl=...``) does not require the full bot
        # dependency tree.
        from bot.config import get_settings

        url = get_settings().database_url

    return _to_sync_url(url)


def _to_sync_url(url: str) -> str:
    """Convert ``sqlite+aiosqlite:///...`` to ``sqlite:///...``.

    Alembic uses the synchronous SQLAlchemy stack; the project default URL
    declares the aiosqlite async driver. Stripping the ``+aiosqlite`` suffix
    keeps the same database file but lets Alembic's sync engine connect.
    """
    if url.startswith("sqlite+aiosqlite:"):
        url = "sqlite:" + url[len("sqlite+aiosqlite:") :]
    return url


def _ensure_sqlite_parent_dir(url: str) -> None:
    """Make sure the SQLite parent directory exists (mkdir -p).

    SQLAlchemy will happily create the database file but not its parent
    directory, so the first ``alembic upgrade`` on a fresh checkout would
    otherwise fail with ``unable to open database file``.
    """
    if not url.startswith("sqlite:"):
        return
    raw = url.split(":", 1)[1].lstrip("/")
    if not raw or raw == ":memory:":
        return
    Path(raw).resolve().parent.mkdir(parents=True, exist_ok=True)


def run_migrations_offline() -> None:
    """Generate SQL without connecting (``alembic upgrade --sql``)."""
    url = _resolve_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        # SQLite needs batch mode for ALTER TABLE since it lacks full DDL.
        render_as_batch=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations against a live engine (the normal path)."""
    url = _resolve_database_url()
    _ensure_sqlite_parent_dir(url)

    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = url

    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
