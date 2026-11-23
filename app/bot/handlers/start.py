"""
Start, help, and main menu handlers.
"""

from aiogram import Router, types
from aiogram.filters import Command

from app.bot.commands import HELP_TEXT
from app.bot.keyboards.inline import main_menu_keyboard
from app.core.config import Settings
from app.core.nodes import node_for_role

router = Router(name="start_router")


@router.message(Command("start"))
async def cmd_start(message: types.Message, settings: Settings) -> None:
    text = f"Мониторинг · активный узел {node_for_role(settings.is_primary).label}"
    await message.answer(text, reply_markup=main_menu_keyboard(), parse_mode=None)


@router.message(Command("help"))
async def cmd_help(message: types.Message) -> None:
    await message.answer(HELP_TEXT, reply_markup=main_menu_keyboard(), parse_mode=None)


@router.callback_query(lambda c: c.data == "nav_main_menu")
async def cb_main_menu(callback: types.CallbackQuery, settings: Settings) -> None:
    text = f"Мониторинг · активный узел {node_for_role(settings.is_primary).label}"
    if callback.message:
        await callback.message.edit_text(text, reply_markup=main_menu_keyboard(), parse_mode=None)
    await callback.answer()
