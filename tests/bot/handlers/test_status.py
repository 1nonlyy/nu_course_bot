"""Tests for ``/status`` (DB-only snapshot)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.filters import CommandObject

from bot.db.database import Database
from bot.handlers.status import cmd_status, _snapshot_answer
from bot.db.models import CourseSnapshot

pytestmark = pytest.mark.usefixtures("settings_env")


def _message_with_user(uid: int = 100) -> MagicMock:
    msg = MagicMock()
    msg.from_user = MagicMock()
    msg.from_user.id = uid
    msg.answer = AsyncMock()
    return msg


def _command_args(text: str | None) -> MagicMock:
    cmd = MagicMock(spec=CommandObject)
    cmd.args = text
    return cmd


@pytest.mark.asyncio
async def test_status_no_args_prompts(tmp_path) -> None:
    db = Database(tmp_path / "s.sqlite")
    await db.init_schema()
    msg = _message_with_user()
    await cmd_status(msg, _command_args(None), db)
    msg.answer.assert_awaited()
    assert "CSCI" in (msg.answer.await_args.args[0] or "")


@pytest.mark.asyncio
async def test_status_invalid_code(tmp_path) -> None:
    db = Database(tmp_path / "s.sqlite")
    await db.init_schema()
    msg = _message_with_user()
    await cmd_status(msg, _command_args("@@@"), db)
    msg.answer.assert_awaited()
    assert "Неверный" in (msg.answer.await_args.args[0] or "")


@pytest.mark.asyncio
async def test_status_no_snapshot_yet(tmp_path) -> None:
    db = Database(tmp_path / "s.sqlite")
    await db.init_schema()
    msg = _message_with_user()
    await cmd_status(msg, _command_args("CSCI 151"), db)
    msg.answer.assert_awaited()
    assert "нет сохран" in (msg.answer.await_args.args[0] or "")


@pytest.mark.asyncio
async def test_status_shows_last_checked_and_sections(tmp_path) -> None:
    db = Database(tmp_path / "s.sqlite")
    await db.init_schema()
    payload = {
        "sections": [
            {
                "section_type": "01L",
                "available_seats": 2,
                "total_seats": 40,
            }
        ],
        "total_available_seats": 2,
        "total_capacity_seats": 40,
    }
    await db.upsert_snapshot(
        "CSCI 151",
        2,
        "Prof",
        "schedule",
        payload,
    )
    msg = _message_with_user()
    await cmd_status(msg, _command_args("CSCI151"), db)

    text = msg.answer.await_args.args[0]
    assert "CSCI 151" in text
    assert "Проверено" in text
    assert "01L" in text
    assert "2 / 40" in text
    assert "40" in text


def test_snapshot_answer_handles_bad_json() -> None:
    snap = CourseSnapshot(
        id=1,
        course_code="X",
        available_seats=1,
        instructor=None,
        schedule=None,
        last_checked=datetime.now(timezone.utc),
        raw_json="{not json",
    )
    text = _snapshot_answer("X", snap)
    assert "Свободно мест" in text or "1" in text
