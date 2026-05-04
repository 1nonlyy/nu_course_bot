"""NU course catalog integration (HTTP JSON API)."""

from bot.scraper.catalog import CatalogScraper, CourseInfo, normalize_course_code

__all__ = [
    "CatalogScraper",
    "CourseInfo",
    "normalize_course_code",
]
