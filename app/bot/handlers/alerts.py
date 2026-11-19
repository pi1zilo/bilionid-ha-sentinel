"""
Alerts and incident log handlers.
"""

from aiogram import Router, types
from aiogram.filters import Command

from app.bot.formatters import format_incidents_view
from app.bot.keyboards.inline import back_to_menu_keyboard
from app.storage.db import Storage

router = Router(name="alerts_router")


@router.message(Command("alerts"))
async def cmd_alerts(message: types.Message, storage: Storage) -> None:
    events = await storage.get_recent_ha_events(limit=10)
    incidents = await storage.get_recent_incidents(limit=10)
    text = format_incidents_view(events, incidents)
    await message.answer(text, reply_markup=back_to_menu_keyboard("nav_alerts"), parse_mode="Markdown")


@router.callback_query(lambda c: c.data == "nav_alerts")
async def cb_alerts(callback: types.CallbackQuery, storage: Storage) -> None:
    events = await storage.get_recent_ha_events(limit=10)
    incidents = await storage.get_recent_incidents(limit=10)
    text = format_incidents_view(events, incidents)
    if callback.message:
        await callback.message.edit_text(text, reply_markup=back_to_menu_keyboard("nav_alerts"), parse_mode="Markdown")
    await callback.answer()
