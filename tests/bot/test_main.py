from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.mark.parametrize(
    "token",
    [
        "",
        "   ",
        "\n\t",
    ],
)
def test_ensure_production_bot_token_rejects_empty(token: str) -> None:
    from bot.main import _ensure_production_bot_token

    with pytest.raises(RuntimeError, match=r"BOT_TOKEN is empty"):
        _ensure_production_bot_token(token)


@pytest.mark.parametrize(
    "token",
    [
        "change_me_please",
        "CHANGE_ME_please",
        "your_token_here",
        "YOUR_TOKEN_HERE",
        "placeholder-123",
        "DummyToken",
        "invalid_token",
        "xxxxx",
        "test:123",
        "000000000:abc",
        "   your_token_here   ",
    ],
)
def test_ensure_production_bot_token_rejects_known_bad_prefixes(token: str) -> None:
    from bot.main import _ensure_production_bot_token

    with pytest.raises(RuntimeError, match=r"placeholder|test|real token"):
        _ensure_production_bot_token(token)


def test_ensure_production_bot_token_allows_normal_shape() -> None:
    from bot.main import _ensure_production_bot_token

    # Real tokens look like "<digits>:<secret>"; we only ensure we don't reject valid-looking values.
    _ensure_production_bot_token("123456:ABCDEF_this_is_not_real")


@pytest.mark.asyncio
async def test_run_rejects_bad_token_before_side_effects(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Ensure the startup check fails fast (before DB/Telegram are touched).
    """
    import bot.main as main

    settings = SimpleNamespace(bot_token="change_me", log_level="INFO", poll_interval_minutes=5)
    monkeypatch.setattr(main, "get_settings", lambda: settings)

    # If these are called, the test should fail.
    def _boom(*args, **kwargs):
        raise AssertionError("Unexpected side effect")

    monkeypatch.setattr(main, "get_database", _boom)
    monkeypatch.setattr(main, "Bot", _boom)

    with pytest.raises(RuntimeError, match=r"BOT_TOKEN looks like"):
        await main.run()


@pytest.mark.asyncio
async def test_run_happy_path_is_fully_mocked(
    monkeypatch: pytest.MonkeyPatch,
    mocker: pytest.MockFixture,
) -> None:
    """
    Run through `run()` without making any external calls by mocking:
    - DB init
    - Aiogram Bot/Dispatcher
    - APScheduler
    """
    import bot.main as main

    sentry_init = mocker.patch("bot.main.sentry_sdk.init")

    settings = SimpleNamespace(
        bot_token="123456:ABCDEF_fake",
        log_level="INFO",
        poll_interval_minutes=5,
        environment="production",
        sentry_dsn="",
    )
    monkeypatch.setattr(main, "get_settings", lambda: settings)

    # Avoid real logging config churn in tests.
    monkeypatch.setattr(main, "_configure_logging", lambda *_a, **_k: None)
    # Stub out Alembic: we don't want a real migration during a unit test.
    monkeypatch.setattr(main, "_run_migrations", lambda _settings: None)

    class FakeDB: ...

    monkeypatch.setattr(main, "get_database", lambda _settings: FakeDB())

    monkeypatch.setattr(main, "CatalogScraper", lambda _settings: object())

    class FakeSession:
        async def close(self) -> None: ...

    class FakeBot:
        def __init__(self, token, default=None) -> None:
            self.token = token
            self.session = FakeSession()

    monkeypatch.setattr(main, "Bot", FakeBot)

    class FakeMiddlewareChain:
        def middleware(self, _mw) -> None: ...

    class FakeDispatcher:
        def __init__(self) -> None:
            self.update = FakeMiddlewareChain()

        async def start_polling(self, _bot) -> None:
            # Immediately return: no network.
            return None

    monkeypatch.setattr(main, "Dispatcher", FakeDispatcher)
    monkeypatch.setattr(main, "register_handlers", lambda _dp: None)

    class FakeScheduler:
        def add_job(self, *args, **kwargs) -> None: ...

        def start(self) -> None: ...

        def shutdown(self, wait: bool = False) -> None: ...

    monkeypatch.setattr(main, "AsyncIOScheduler", FakeScheduler)
    monkeypatch.setattr(main, "IntervalTrigger", lambda minutes: object())

    # Should complete without raising.
    await main.run()
    sentry_init.assert_not_called()


def test_configure_logging_production_uses_json_renderer(mocker: pytest.MockFixture) -> None:
    from structlog.processors import JSONRenderer

    import bot.main as main

    mock_configure = mocker.patch("bot.main.structlog.configure")
    mocker.patch("bot.main.logging.basicConfig")
    main._configure_logging("INFO", "production")
    processors = mock_configure.call_args.kwargs["processors"]
    assert isinstance(processors[-1], JSONRenderer)


def test_configure_logging_dev_uses_console_renderer(mocker: pytest.MockFixture) -> None:
    from structlog.dev import ConsoleRenderer

    import bot.main as main

    mock_configure = mocker.patch("bot.main.structlog.configure")
    mocker.patch("bot.main.logging.basicConfig")
    main._configure_logging("INFO", "  DEV ")
    processors = mock_configure.call_args.kwargs["processors"]
    assert isinstance(processors[-1], ConsoleRenderer)


@pytest.mark.asyncio
async def test_run_initializes_sentry_when_dsn_configured(
    monkeypatch: pytest.MonkeyPatch,
    mocker: pytest.MockFixture,
) -> None:
    import bot.main as main

    sentry_init = mocker.patch("bot.main.sentry_sdk.init")
    dsn = "https://examplePublicKey@o0.ingest.sentry.io/0"
    settings = SimpleNamespace(
        bot_token="123456:ABCDEF_fake",
        log_level="INFO",
        poll_interval_minutes=5,
        environment="production",
        sentry_dsn=dsn,
    )
    monkeypatch.setattr(main, "get_settings", lambda: settings)
    monkeypatch.setattr(main, "_configure_logging", lambda *_a, **_k: None)
    monkeypatch.setattr(main, "_run_migrations", lambda _settings: None)

    class FakeDB: ...

    monkeypatch.setattr(main, "get_database", lambda _settings: FakeDB())
    monkeypatch.setattr(main, "CatalogScraper", lambda _settings: object())

    class FakeSession:
        async def close(self) -> None: ...

    class FakeBot:
        def __init__(self, token, default=None) -> None:
            self.session = FakeSession()

    monkeypatch.setattr(main, "Bot", FakeBot)

    class FakeMiddlewareChain:
        def middleware(self, _mw) -> None: ...

    class FakeDispatcher:
        def __init__(self) -> None:
            self.update = FakeMiddlewareChain()

        async def start_polling(self, _bot) -> None:
            return None

    monkeypatch.setattr(main, "Dispatcher", FakeDispatcher)
    monkeypatch.setattr(main, "register_handlers", lambda _dp: None)

    class FakeScheduler:
        def add_job(self, *args, **kwargs) -> None: ...

        def start(self) -> None: ...

        def shutdown(self, wait: bool = False) -> None: ...

    monkeypatch.setattr(main, "AsyncIOScheduler", FakeScheduler)
    monkeypatch.setattr(main, "IntervalTrigger", lambda minutes: object())

    await main.run()
    sentry_init.assert_called_once_with(dsn=dsn, traces_sample_rate=0.1)


def test_run_migrations_creates_schema(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``_run_migrations`` should bring a brand-new SQLite file up to ``head``."""
    import sqlite3

    import bot.main as main

    db_file = tmp_path / "fresh.db"
    settings = SimpleNamespace(
        database_url=f"sqlite+aiosqlite:///{db_file}",
    )

    main._run_migrations(settings)

    # Tables from the initial migration plus Alembic's bookkeeping must exist.
    conn = sqlite3.connect(db_file)
    try:
        names = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    finally:
        conn.close()
    expected = {
        "alembic_version",
        "users",
        "subscriptions",
        "course_snapshots",
        "user_notification_state",
    }
    assert expected.issubset(names), f"missing tables: {expected - names}"


def test_run_migrations_propagates_database_url_to_environ(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``alembic/env.py`` reads ``$DATABASE_URL`` first; main.py must set it.

    pydantic-settings does NOT push values from ``.env`` into ``os.environ``,
    so without this propagation Alembic would fall back to the placeholder URL
    in ``alembic.ini`` and migrate the wrong file.
    """
    import bot.main as main

    monkeypatch.delenv("DATABASE_URL", raising=False)
    db_file = tmp_path / "from_env.db"
    settings = SimpleNamespace(database_url=f"sqlite+aiosqlite:///{db_file}")

    main._run_migrations(settings)

    import os as _os

    assert _os.environ.get("DATABASE_URL") == settings.database_url
    assert db_file.is_file(), "Alembic should have created the DB at the URL we set"


def test_run_migrations_idempotent_on_existing_legacy_db(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Running migrations against a DB previously built by ``Database.init_schema``
    must succeed without dropping data — the migration uses ``IF NOT EXISTS``."""
    import sqlite3

    import bot.main as main
    from bot.db.database import Database

    db_file = tmp_path / "legacy.db"
    db = Database(db_file)

    import asyncio as _asyncio

    _asyncio.run(db.init_schema())

    # Insert a row to prove migration is non-destructive.
    conn = sqlite3.connect(db_file)
    try:
        conn.execute(
            "INSERT INTO users (telegram_id, username, first_name) VALUES (?, ?, ?)",
            (42, "alice", "Alice"),
        )
        conn.commit()
    finally:
        conn.close()

    settings = SimpleNamespace(database_url=f"sqlite+aiosqlite:///{db_file}")
    main._run_migrations(settings)

    conn = sqlite3.connect(db_file)
    try:
        (count,) = conn.execute(
            "SELECT COUNT(*) FROM users WHERE telegram_id = 42"
        ).fetchone()
        version_rows = list(conn.execute("SELECT version_num FROM alembic_version"))
    finally:
        conn.close()

    assert count == 1, "Migration must not delete pre-existing rows"
    assert version_rows == [("0001",)], "Alembic must stamp the DB at head"

