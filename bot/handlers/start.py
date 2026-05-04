"""Welcome, help, and main-menu callbacks."""

from __future__ import annotations

from aiogram import F, Router
from aiogram.filters import Command, CommandStart
from aiogram.types import CallbackQuery, Message

from bot.db.database import Database
from bot.handlers.list import format_subscription_lines
from bot.keyboards.inline import main_menu_keyboard

router = Router(name="start")


HELP_TEXT = (
    "Команды:\n"
    "/start — главное меню\n"
    "/subscribe КОД — следить за курсом (например: CSCI 151)\n"
    "/unsubscribe КОД — отписаться\n"
    "/mysubs — активные подписки\n"
    "/check КОД — разовая проверка без подписки\n\n"
    "Бот опрашивает каталог регистратора и присылает уведомление, "
    "когда суммарно по секциям появляются свободные места (было 0, стало >0). "
    "В ответе — все секции (L, Lb, R) и места по каждой."
)


@router.message(CommandStart())
async def cmd_start(message: Message, db: Database) -> None:
    """Greet the user and show the main inline actions."""
    if message.from_user is None:
        return
    u = message.from_user
    await db.upsert_user(u.id, u.username, u.full_name or u.first_name)
    text = (
        "Привет! Я бот для студентов NU: слежу за местами в Public Course Catalog.\n\n"
        "Выбери действие ниже или воспользуйся командами из /help."
    )
    await message.answer(text, reply_markup=main_menu_keyboard())


@router.message(Command("help"))
async def cmd_help(message: Message) -> None:
    """Show command reference."""
    await message.answer(HELP_TEXT)


@router.callback_query(F.data == "menu:help")
async def cb_help(query: CallbackQuery) -> None:
    """Answer help callback."""
    await query.answer()
    if query.message is not None:
        await query.message.answer(HELP_TEXT)


@router.callback_query(F.data == "menu:subscribe_hint")
async def cb_subscribe_hint(query: CallbackQuery) -> None:
    """Explain how to subscribe."""
    await query.answer()
    if query.message is not None:
        await query.message.answer(
            "Отправь команду в чат:\n"
            "`/subscribe CSCI 151`\n\n"
            "Формат: буквы предмета и трёхзначный номер (можно с буквой в конце).",
            parse_mode="Markdown",
        )


@router.callback_query(F.data == "menu:check_hint")
async def cb_check_hint(query: CallbackQuery) -> None:
    """Explain one-shot check."""
    await query.answer()
    if query.message is not None:
        await query.message.answer(
            "Разовая проверка:\n`/check MATH 162`",
            parse_mode="Markdown",
        )


@router.callback_query(F.data == "menu:mysubs")
async def cb_mysubs(query: CallbackQuery, db: Database) -> None:
    """Show subscriptions like /mysubs."""
    await query.answer()
    if query.from_user is None or query.message is None:
        return
    lines = await format_subscription_lines(db, query.from_user.id)
    await query.message.answer(lines)
