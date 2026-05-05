"""Telegram command and callback handlers."""

from aiogram import Dispatcher

from bot.handlers import list as list_handlers
from bot.handlers import start
from bot.handlers import status
from bot.handlers import subscribe


def register_handlers(dp: Dispatcher) -> None:
    """Attach routers to the dispatcher."""
    dp.include_router(start.router)
    dp.include_router(subscribe.router)
    dp.include_router(list_handlers.router)
    dp.include_router(status.router)
