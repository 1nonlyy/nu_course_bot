from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType

import pytest


# Ensure the repository root is importable (so `import bot...` works when running `pytest tests/`).
_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))


def _install_stub_module(name: str) -> ModuleType:
    mod = ModuleType(name)
    sys.modules[name] = mod
    return mod


def _ensure_import_stubs() -> None:
    """
    Tests must run without making external calls.
    Also, in minimal environments, optional runtime deps may not be installed.
    Provide tiny import stubs so importing `bot.main` doesn't crash.
    """
    # --- aiogram stubs ---
    if "aiogram" not in sys.modules:
        aiogram = _install_stub_module("aiogram")

        class BaseMiddleware: ...

        class Router:
            def __init__(self, *args, **kwargs) -> None: ...

            def message(self, *args, **kwargs):
                def _decorator(func):
                    return func

                return _decorator

            def callback_query(self, *args, **kwargs):
                def _decorator(func):
                    return func

                return _decorator

        class Bot:  # real Bot is mocked in tests
            def __init__(self, *args, **kwargs) -> None: ...

        class Dispatcher:
            def __init__(self, *args, **kwargs) -> None: ...

        class _F:
            def __getattr__(self, _name: str):
                return self

            def __eq__(self, _other: object) -> bool:  # noqa: D401
                # Only used to build filter expressions at import time in handlers.
                return True

        aiogram.BaseMiddleware = BaseMiddleware
        aiogram.Router = Router
        aiogram.Bot = Bot
        aiogram.Dispatcher = Dispatcher
        aiogram.F = _F()

    _install_stub_module("aiogram.client")
    if "aiogram.client.default" not in sys.modules:
        d = _install_stub_module("aiogram.client.default")

        class DefaultBotProperties:
            def __init__(self, *args, **kwargs) -> None: ...

        d.DefaultBotProperties = DefaultBotProperties

    _install_stub_module("aiogram.enums")
    if "aiogram.enums" in sys.modules:
        enums = sys.modules["aiogram.enums"]

        class ParseMode:
            HTML = "HTML"

        enums.ParseMode = ParseMode

    _install_stub_module("aiogram.types")
    if "aiogram.types" in sys.modules:
        types_mod = sys.modules["aiogram.types"]

        class TelegramObject: ...
        class Message: ...
        class CallbackQuery: ...
        class InlineKeyboardButton: ...
        class InlineKeyboardMarkup: ...

        types_mod.TelegramObject = TelegramObject
        types_mod.Message = Message
        types_mod.CallbackQuery = CallbackQuery
        types_mod.InlineKeyboardButton = InlineKeyboardButton
        types_mod.InlineKeyboardMarkup = InlineKeyboardMarkup

    if "aiogram.filters" not in sys.modules:
        flt = _install_stub_module("aiogram.filters")

        class Command:
            def __init__(self, *args, **kwargs) -> None: ...

        class CommandStart:
            def __init__(self, *args, **kwargs) -> None: ...

        class CommandObject: ...

        flt.Command = Command
        flt.CommandStart = CommandStart
        flt.CommandObject = CommandObject

    if "aiogram.exceptions" not in sys.modules:
        exc = _install_stub_module("aiogram.exceptions")

        class TelegramForbiddenError(Exception): ...

        class TelegramRetryAfter(Exception):
            def __init__(self, *args, **kwargs) -> None:
                super().__init__(*args)
                self.retry_after = getattr(self, "retry_after", 0)

        exc.TelegramForbiddenError = TelegramForbiddenError
        exc.TelegramRetryAfter = TelegramRetryAfter

    # --- apscheduler stubs ---
    _install_stub_module("apscheduler")
    _install_stub_module("apscheduler.schedulers")
    if "apscheduler.schedulers.asyncio" not in sys.modules:
        sched = _install_stub_module("apscheduler.schedulers.asyncio")

        class AsyncIOScheduler:
            def __init__(self, *args, **kwargs) -> None: ...

        sched.AsyncIOScheduler = AsyncIOScheduler

    _install_stub_module("apscheduler.triggers")
    if "apscheduler.triggers.interval" not in sys.modules:
        trig = _install_stub_module("apscheduler.triggers.interval")

        class IntervalTrigger:
            def __init__(self, *args, **kwargs) -> None: ...

        trig.IntervalTrigger = IntervalTrigger

    # --- aiosqlite stubs (only if the real package is not installed) ---
    try:
        import aiosqlite  # noqa: F401
    except ImportError:
        if "aiosqlite" not in sys.modules:
            aiosqlite = _install_stub_module("aiosqlite")

            class Connection: ...

            async def connect(*args, **kwargs):  # pragma: no cover
                return Connection()

            aiosqlite.Connection = Connection
            aiosqlite.connect = connect


_ensure_import_stubs()


@pytest.fixture(autouse=True)
def _clean_settings_cache() -> None:
    """
    Tests import `bot.config.get_settings()` which is `@lru_cache`'d.
    Clear it between tests to avoid env var cross-talk.
    """
    from bot.config import get_settings

    get_settings.cache_clear()


@pytest.fixture
def settings_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Provide a complete, valid environment for `bot.config.Settings`."""
    monkeypatch.setenv("BOT_TOKEN", "123456:ABCDEF_fake_but_valid_shape")
    monkeypatch.setenv("POLL_INTERVAL_MINUTES", "5")
    monkeypatch.setenv("DATABASE_URL", "sqlite+aiosqlite:///./data/test.db")
    monkeypatch.setenv("LOG_LEVEL", "INFO")
    monkeypatch.setenv("CATALOG_BASE_URL", "https://registrar.nu.edu.kz")
    monkeypatch.setenv("CATALOG_TERM_ID", "824")
    monkeypatch.setenv("SCRAPE_MIN_INTERVAL_SECONDS", "180")
    monkeypatch.setenv("CATALOG_IGNORE_TLS_ERRORS", "true")

    # Ensure `.env` isn't implicitly used in CI/dev machines running tests.
    # Pydantic will prefer explicit env vars, but this avoids surprises.
    monkeypatch.delenv("DOTENV_PATH", raising=False)

