"""Inline keyboards for common bot actions."""

from __future__ import annotations

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu_keyboard() -> InlineKeyboardMarkup:
    """Primary actions shown on /start."""
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="📌 Подписаться",
                    callback_data="menu:subscribe_hint",
                ),
                InlineKeyboardButton(
                    text="📋 Мои подписки",
                    callback_data="menu:mysubs",
                ),
            ],
            [
                InlineKeyboardButton(
                    text="🔎 Проверить курс",
                    callback_data="menu:check_hint",
                ),
                InlineKeyboardButton(
                    text="❓ Помощь",
                    callback_data="menu:help",
                ),
            ],
        ]
    )
