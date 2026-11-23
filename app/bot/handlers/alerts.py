"""
Alerts and incident log handlers.
"""

import asyncio

from aiogram import Router, types
from aiogram.filters import Command
from aiogram.filters.command import CommandObject

from app.bot.formatters import display_node, format_incidents_view
from app.bot.keyboards.inline import back_to_menu_keyboard
from app.monitoring.collector import MetricsCollector
from app.storage.db import Storage
from app.core.config import Settings
from app.core.nodes import node_for_role

router = Router(name="alerts_router")


async def alerts_view(storage: Storage, settings: Settings, collector: MetricsCollector) -> str:
    events, incidents = await asyncio.gather(
        storage.get_recent_ha_events(limit=3), storage.get_recent_incidents(limit=10),
    )
    peer_incidents = await collector.get_peer_incidents() if settings.HA_PEER_ENABLED else []
    text = format_incidents_view(events, incidents + (peer_incidents or []), settings)
    if peer_incidents is None:
        text += f"\n\nЖурнал {node_for_role(not settings.is_primary).label} недоступен."
    return text


@router.message(Command("alerts"))
async def cmd_alerts(message: types.Message, storage: Storage, settings: Settings, collector: MetricsCollector) -> None:
    text = await alerts_view(storage, settings, collector)
    await message.answer(text, reply_markup=back_to_menu_keyboard("nav_alerts"), parse_mode=None)


@router.message(Command("note"))
async def cmd_note(message: types.Message, command: CommandObject, storage: Storage, settings: Settings) -> None:
    parts = (command.args or "").split(maxsplit=1)
    if len(parts) != 2 or len(parts[0]) > 120 or len(parts[1]) > 300:
        await message.answer("Пример: /note suslik Перезагрузил сервер вручную\nДо 300 символов в описании.")
        return
    host, detail = parts
    host = display_node(settings, host)
    user = message.from_user
    author = f"@{user.username}" if user and user.username else user.full_name if user else "Участник чата"
    note_id = await storage.record_event(
        "Оператор", host, f"{author}: {detail}", message.date,
    )
    await message.answer(f"📝 {host} · {node_for_role(settings.is_primary).label} #{note_id}\n{author}: {detail}", parse_mode=None)


@router.callback_query(lambda c: c.data == "nav_alerts")
async def cb_alerts(
    callback: types.CallbackQuery, storage: Storage, settings: Settings, collector: MetricsCollector,
) -> None:
    if callback.message:
        text = await alerts_view(storage, settings, collector)
        await callback.message.edit_text(text, reply_markup=back_to_menu_keyboard("nav_alerts"), parse_mode=None)
    await callback.answer()
