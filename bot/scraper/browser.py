"""Playwright browser lifecycle (singleton Async Chromium)."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Optional

from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from bot.config import Settings, get_settings

logger = logging.getLogger(__name__)


class BrowserManager:
    """
    Owns a single Playwright instance, browser, and shared context.

    Use :meth:`start` before scraping and :meth:`stop` on shutdown.
    """

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self._settings = settings or get_settings()
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._lock = asyncio.Lock()

    async def start(self) -> None:
        """Launch Chromium and create a shared context."""
        async with self._lock:
            if self._browser is not None:
                return
            logger.info("Starting Playwright Chromium (headless=%s)", self._settings.headless)
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.launch(
                headless=self._settings.headless,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            self._context = await self._browser.new_context(
                viewport={"width": 1400, "height": 900},
                ignore_https_errors=self._settings.catalog_ignore_tls_errors,
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                ),
            )

    async def stop(self) -> None:
        """Close browser and stop Playwright."""
        async with self._lock:
            if self._context is not None:
                await self._context.close()
                self._context = None
            if self._browser is not None:
                await self._browser.close()
                self._browser = None
            if self._playwright is not None:
                await self._playwright.stop()
                self._playwright = None
            logger.info("Playwright stopped")

    @property
    def context(self) -> BrowserContext:
        """Return the shared browser context."""
        if self._context is None:
            raise RuntimeError("BrowserManager.start() must be called first")
        return self._context

    async def new_page(self) -> Page:
        """Open a new page in the shared context."""
        return await self.context.new_page()

    async def __aenter__(self) -> BrowserManager:
        await self.start()
        return self

    async def __aexit__(self, *args: Any) -> None:
        await self.stop()
