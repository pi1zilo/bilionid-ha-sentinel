"""
Status and server metrics handlers.
"""

import asyncio

from aiogram import Router, types
from aiogram.filters import Command

from app.bot.formatters import format_infrastructure_status, format_server_card
from app.bot.keyboards.inline import server_detail_keyboard, servers_keyboard
from app.ha.supervisor import HASupervisor
from app.monitoring.collector import MetricsCollector
from app.core.config import Settings
from app.storage.db import Storage

router = Router(name="status_router")


@router.message(Command("status"))
async def cmd_status(
    message: types.Message,
    collector: MetricsCollector,
    ha_supervisor: HASupervisor,
    storage: Storage,
) -> None:
    servers, uptime_overview, delivery = await asyncio.gather(
        collector.get_servers(), collector.get_uptime_kuma(), storage.delivery_counts(),
    )
    text = format_infrastructure_status(servers, uptime_overview, ha_supervisor, delivery)
    await message.answer(text, reply_markup=servers_keyboard(), parse_mode=None)


@router.message(Command("servers"))
async def cmd_servers(
    message: types.Message,
    collector: MetricsCollector,
    ha_supervisor: HASupervisor,
    storage: Storage,
) -> None:
    await cmd_status(message, collector, ha_supervisor, storage)


@router.message(Command("server"))
async def cmd_server_detail(
    message: types.Message,
    collector: MetricsCollector,
    settings: Settings,
) -> None:
    args = message.text.split(maxsplit=1) if message.text else []
    query = args[1] if len(args) > 1 else "bili"

    server = await collector.get_server_detail(query)
    if not server:
        await message.answer(
            f"❌ Сервер по запросу {query} не найден.",
            reply_markup=servers_keyboard(),
            parse_mode=None,
        )
        return

    text = format_server_card(server, settings.ALERT_TIMEZONE)
    primary = settings.NODE_NAME if settings.is_primary else settings.PEER_NODE_NAME
    key = "bili" if server.name.casefold() == primary.casefold() else "susl"
    await message.answer(text, reply_markup=server_detail_keyboard(key), parse_mode=None)


@router.callback_query(lambda c: c.data == "nav_servers")
async def cb_servers(
    callback: types.CallbackQuery,
    collector: MetricsCollector,
    ha_supervisor: HASupervisor,
    storage: Storage,
) -> None:
    await callback.answer()
    servers, uptime_overview, delivery = await asyncio.gather(
        collector.get_servers(), collector.get_uptime_kuma(), storage.delivery_counts(),
    )
    text = format_infrastructure_status(servers, uptime_overview, ha_supervisor, delivery)
    if callback.message:
        await callback.message.edit_text(text, reply_markup=servers_keyboard(), parse_mode=None)


@router.callback_query(lambda c: c.data and c.data.startswith("server_detail:"))
async def cb_server_detail(
    callback: types.CallbackQuery,
    collector: MetricsCollector,
    settings: Settings,
) -> None:
    server_key = callback.data.split(":", 1)[1] if callback.data else "bili"
    server = await collector.get_server_detail(server_key)

    if not server:
        await callback.answer("Данные сервера временно недоступны", show_alert=True)
        return

    text = format_server_card(server, settings.ALERT_TIMEZONE)
    if callback.message:
        await callback.message.edit_text(
            text, reply_markup=server_detail_keyboard(server_key), parse_mode=None
        )
    await callback.answer()
