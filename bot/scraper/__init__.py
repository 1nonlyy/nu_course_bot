"""Playwright-based NU course catalog integration."""

from bot.scraper.catalog import CatalogScraper, CourseInfo, normalize_course_code
from bot.scraper.browser import BrowserManager

__all__ = [
    "BrowserManager",
    "CatalogScraper",
    "CourseInfo",
    "normalize_course_code",
]
