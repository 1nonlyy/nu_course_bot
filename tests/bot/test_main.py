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
    Ensure the startup check fails fast (before DB/Playwright/Telegram are touched).
    """
    import bot.main as main

    settings = SimpleNamespace(bot_token="change_me", log_level="INFO", poll_interval_minutes=5)
    monkeypatch.setattr(main, "get_settings", lambda: settings)

    # If these are called, the test should fail.
    def _boom(*args, **kwargs):
        raise AssertionError("Unexpected side effect")

    monkeypatch.setattr(main, "get_database", _boom)
    monkeypatch.setattr(main, "BrowserManager", _boom)
    monkeypatch.setattr(main, "Bot", _boom)

    with pytest.raises(RuntimeError, match=r"BOT_TOKEN looks like"):
        await main.run()


@pytest.mark.asyncio
async def test_run_happy_path_is_fully_mocked(monkeypatch: pytest.MonkeyPatch) -> None:
    """
    Run through `run()` without making any external calls by mocking:
    - DB init
    - Playwright browser lifecycle
    - Aiogram Bot/Dispatcher
    - APScheduler
    """
    import bot.main as main

    settings = SimpleNamespace(
        bot_token="123456:ABCDEF_fake",
        log_level="INFO",
        poll_interval_minutes=5,
    )
    monkeypatch.setattr(main, "get_settings", lambda: settings)

    # Avoid real logging config churn in tests.
    monkeypatch.setattr(main, "_configure_logging", lambda level: None)

    class FakeDB:
        async def init_schema(self) -> None: ...

    monkeypatch.setattr(main, "get_database", lambda _settings: FakeDB())

    class FakeBrowser:
        async def start(self) -> None: ...

        async def stop(self) -> None: ...

    class FakeBrowserManager:
        def __init__(self, _settings) -> None:
            self._browser = FakeBrowser()

        async def start(self) -> None:
            await self._browser.start()

        async def stop(self) -> None:
            await self._browser.stop()

    monkeypatch.setattr(main, "BrowserManager", FakeBrowserManager)

    monkeypatch.setattr(main, "CatalogScraper", lambda browser, _settings: object())

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

