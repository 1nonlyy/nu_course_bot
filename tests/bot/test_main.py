from __future__ import annotations

import json
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


# ---------------------------------------------------------------------------
# Health endpoint helpers (aiohttp server is mocked)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_check_db_health_happy_path_returns_ok() -> None:
    import bot.main as main

    class _Cursor:
        async def fetchone(self):
            return (1,)

    class _Conn:
        async def execute(self, _sql: str):
            return _Cursor()

    class _ConnectCM:
        async def __aenter__(self):
            return _Conn()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeDB:
        def connect(self):
            return _ConnectCM()

    assert await main._check_db_health(FakeDB()) == "ok"


@pytest.mark.asyncio
async def test_check_db_health_error_returns_error() -> None:
    import bot.main as main

    class _ConnectCM:
        async def __aenter__(self):
            raise RuntimeError("db down")

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeDB:
        def connect(self):
            return _ConnectCM()

    assert await main._check_db_health(FakeDB()) == "error"


def test_scheduler_status_running_and_stopped() -> None:
    import bot.main as main

    class Running:
        running = True

    class Stopped:
        running = False

    class MissingAttr: ...

    assert main._scheduler_status(Running()) == "running"
    assert main._scheduler_status(Stopped()) == "stopped"
    assert main._scheduler_status(MissingAttr()) == "stopped"


@pytest.mark.asyncio
async def test_run_health_server_registers_route_and_serves_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio

    import bot.main as main

    class _Cursor:
        async def fetchone(self):
            return (1,)

    class _Conn:
        async def execute(self, _sql: str):
            return _Cursor()

    class _ConnectCM:
        async def __aenter__(self):
            return _Conn()

        async def __aexit__(self, exc_type, exc, tb):
            return False

    class FakeDB:
        def connect(self):
            return _ConnectCM()

    class FakeScheduler:
        running = True

    captured: dict[str, object] = {}

    class FakeRouter:
        def add_get(self, path: str, handler):
            captured["path"] = path
            captured["handler"] = handler

    class FakeApplication:
        def __init__(self):
            self.router = FakeRouter()

    class FakeRunner:
        def __init__(self, app, access_log=None):
            self.app = app
            self.access_log = access_log
            self.cleaned = False

        async def setup(self) -> None:
            return None

        async def cleanup(self) -> None:
            self.cleaned = True

    class FakeSite:
        def __init__(self, runner, host: str, port: int):
            self.runner = runner
            self.host = host
            self.port = port
            self.started = False

        async def start(self) -> None:
            self.started = True

    monkeypatch.setattr(main.web, "Application", FakeApplication)

    runner_box: dict[str, FakeRunner] = {}

    def _runner_factory(app, access_log=None):
        r = FakeRunner(app, access_log=access_log)
        runner_box["runner"] = r
        return r

    monkeypatch.setattr(main.web, "AppRunner", _runner_factory)

    site_box: dict[str, FakeSite] = {}

    def _site_factory(runner, host: str, port: int):
        s = FakeSite(runner, host=host, port=port)
        site_box["site"] = s
        return s

    monkeypatch.setattr(main.web, "TCPSite", _site_factory)

    # Stable uptime: started at 100.0, now at 105.2 → uptime_seconds = 5.
    monkeypatch.setattr(main.time, "monotonic", lambda: 105.2)
    stop_event = asyncio.Event()

    task = asyncio.create_task(
        main._run_health_server(
            db=FakeDB(),
            scheduler=FakeScheduler(),
            started_at_monotonic=100.0,
            stop_event=stop_event,
        )
    )

    # Let the server "start".
    await asyncio.sleep(0)

    assert captured["path"] == "/healthz"
    assert site_box["site"].host == "0.0.0.0"
    assert site_box["site"].port == 8080
    assert site_box["site"].started is True

    handler = captured["handler"]
    resp = await handler(object())
    payload = json.loads(resp.body.decode())
    assert payload == {
        "status": "ok",
        "db": "ok",
        "scheduler": "running",
        "uptime_seconds": 5,
    }

    stop_event.set()
    await task
    assert runner_box["runner"].cleaned is True


@pytest.mark.asyncio
async def test_run_health_server_uptime_never_negative_and_db_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import asyncio
    from unittest.mock import AsyncMock

    import bot.main as main

    class FakeDB:
        def connect(self):
            raise RuntimeError("connect should not be called directly")

    class FakeScheduler:
        running = False

    captured: dict[str, object] = {}

    class FakeRouter:
        def add_get(self, path: str, handler):
            captured["handler"] = handler

    class FakeApplication:
        def __init__(self):
            self.router = FakeRouter()

    class FakeRunner:
        def __init__(self, app, access_log=None):
            self.cleaned = False

        async def setup(self) -> None:
            return None

        async def cleanup(self) -> None:
            self.cleaned = True

    class FakeSite:
        def __init__(self, runner, host: str, port: int):
            self.started = False

        async def start(self) -> None:
            self.started = True

    monkeypatch.setattr(main.web, "Application", FakeApplication)
    monkeypatch.setattr(main.web, "AppRunner", lambda app, access_log=None: FakeRunner(app))
    monkeypatch.setattr(main.web, "TCPSite", lambda runner, host, port: FakeSite(runner, host, port))

    # started_at_monotonic is in the future → uptime must clamp to 0.
    monkeypatch.setattr(main.time, "monotonic", lambda: 50.0)
    monkeypatch.setattr(main, "_check_db_health", AsyncMock(return_value="error"))

    stop_event = asyncio.Event()
    task = asyncio.create_task(
        main._run_health_server(
            db=FakeDB(),
            scheduler=FakeScheduler(),
            started_at_monotonic=100.0,
            stop_event=stop_event,
        )
    )
    await asyncio.sleep(0)

    resp = await captured["handler"](object())
    payload = json.loads(resp.body.decode())
    assert payload["db"] == "error"
    assert payload["scheduler"] == "stopped"
    assert payload["uptime_seconds"] == 0

    stop_event.set()
    await task

