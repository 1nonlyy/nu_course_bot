"""Application settings loaded from environment variables."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from urllib.parse import urlparse

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


def _sqlite_path_from_url(database_url: str) -> Path:
    """
    Resolve filesystem path from a SQLAlchemy-style SQLite URL.

    Same rules as SQLAlchemy: ``sqlite:///relative/db.db`` (three slashes) is
    relative to the current working directory; ``sqlite:////absolute/db.db``
    (four slashes) is an absolute path. ``Path`` is built by stripping the
    leading ``/`` that :func:`urllib.parse.urlparse` always adds to ``path``.
    """
    parsed = urlparse(database_url.replace("sqlite+aiosqlite:", "sqlite:", 1))
    if parsed.scheme != "sqlite":
        raise ValueError("DATABASE_URL must use sqlite+aiosqlite:///...")
    raw = parsed.path or ""
    if not raw or raw == "/":
        raise ValueError("DATABASE_URL must include a database file path")
    if len(raw) > 3 and raw[0] == "/" and raw[2] == ":" and raw[1] != "/":
        # Windows ``/C:/Users/...`` from URL
        return Path(raw[1:])
    if raw.startswith("//"):
        return Path(raw[1:])  # absolute: ``//tmp/a.db`` -> ``/tmp/a.db``
    return Path(raw[1:])  # relative: ``/data/x.db`` -> ``data/x.db``


class Settings(BaseSettings):
    """Bot and scraper configuration."""

    model_config = SettingsConfigDict(
        # Never commit `.env` (secrets). Track `.env.example` only; copy it locally.
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    bot_token: str = Field(..., alias="BOT_TOKEN")
    sentry_dsn: str = Field("", alias="SENTRY_DSN")
    poll_interval_minutes: int = Field(5, ge=1, alias="POLL_INTERVAL_MINUTES")
    database_url: str = Field("sqlite+aiosqlite:///./data/nu_bot.db", alias="DATABASE_URL")
    environment: str = Field("production", alias="ENV")
    log_level: str = Field("INFO", alias="LOG_LEVEL")
    catalog_base_url: str = Field(
        "https://registrar.nu.edu.kz",
        alias="CATALOG_BASE_URL",
    )
    catalog_term_id: str = Field(
        "824",
        description=(
            "Registrar semester id from the catalog dropdown (824 = Summer 2026). "
            "Override in .env, e.g. CATALOG_TERM_ID=823 for Spring 2026."
        ),
        alias="CATALOG_TERM_ID",
    )
    scrape_min_interval_seconds: int = Field(
        180,
        ge=1,
        alias="SCRAPE_MIN_INTERVAL_SECONDS",
    )
    catalog_ignore_tls_errors: bool = Field(
        True,
        description=(
            "If True, httpx skips TLS certificate verification for the catalog "
            "(needed when registrar.nu.edu.kz chain is not in the default trust store). "
            "Set CATALOG_IGNORE_TLS_ERRORS=false if verification works on your machine."
        ),
        alias="CATALOG_IGNORE_TLS_ERRORS",
    )

    @property
    def sqlite_path(self) -> Path:
        """Filesystem path to the SQLite database file."""
        return _sqlite_path_from_url(self.database_url)

    @field_validator("log_level")
    @classmethod
    def log_level_upper(cls, v: str) -> str:
        """Normalize log level to upper case."""
        return v.upper()


@lru_cache
def get_settings() -> Settings:
    """Return cached settings instance."""
    # Pydantic settings are populated from env/.env at runtime; mypy can't model this.
    return Settings()  # type: ignore[call-arg]
