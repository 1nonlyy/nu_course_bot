"""Row representations for SQLite tables."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Optional


@dataclass
class User:
    """Telegram user stored locally."""

    telegram_id: int
    username: Optional[str]
    first_name: Optional[str]
    created_at: datetime


@dataclass
class Subscription:
    """A user's watch on a course code."""

    id: int
    user_id: int
    course_code: str
    is_active: bool
    created_at: datetime


@dataclass
class CourseSnapshot:
    """Last known enrollment snapshot for a course code."""

    id: int
    course_code: str
    available_seats: int
    instructor: Optional[str]
    schedule: Optional[str]
    last_checked: datetime
    raw_json: Optional[str]
