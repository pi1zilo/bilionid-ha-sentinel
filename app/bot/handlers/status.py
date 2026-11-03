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

router = Router(name="status_router")


@router.message(Command("status"))
async def cmd_status(
    message: types.Message,
    collector: MetricsCollector,
    ha_supervisor: HASupervisor,
) -> None:
    servers, uptime_overview = await asyncio.gather(
        collector.get_servers(),
        collector.get_uptime_kuma(),
    )
    text = format_infrastructure_status(servers, uptime_overview, ha_supervisor)
    await message.answer(text, reply_markup=servers_keyboard(), parse_mode="Markdown")


@router.message(Command("servers"))
async def cmd_servers(
    message: types.Message,
    collector: MetricsCollector,
    ha_supervisor: HASupervisor,
) -> None:
    servers, uptime_overview = await asyncio.gather(
        collector.get_servers(),
        collector.get_uptime_kuma(),
    )
    text = format_infrastructure_status(servers, uptime_overview, ha_supervisor)
    await message.answer(text, reply_markup=servers_keyboard(), parse_mode="Markdown")


@router.message(Command("server"))
async def cmd_server_detail(
    message: types.Message,
    collector: MetricsCollector,
) -> None:
    args = message.text.split(maxsplit=1) if message.text else []
    query = args[1] if len(args) > 1 else "main"

    server = await collector.get_server_detail(query)
    if not server:
        await message.answer(
            f"❌ Сервер по запросу `{query}` не найден.",
            reply_markup=servers_keyboard(),
            parse_mode="Markdown",
        )
        return

    text = format_server_card(server)
    key = "main" if ("main" in server.name.lower() or "bili" in server.name.lower()) else "backup"
    await message.answer(text, reply_markup=server_detail_keyboard(key), parse_mode="Markdown")


@router.callback_query(lambda c: c.data == "nav_servers")
async def cb_servers(
    callback: types.CallbackQuery,
    collector: MetricsCollector,
    ha_supervisor: HASupervisor,
) -> None:
    servers, uptime_overview = await asyncio.gather(
        collector.get_servers(),
        collector.get_uptime_kuma(),
    )
    text = format_infrastructure_status(servers, uptime_overview, ha_supervisor)
    if callback.message:
        await callback.message.edit_text(text, reply_markup=servers_keyboard(), parse_mode="Markdown")
    await callback.answer()


@router.callback_query(lambda c: c.data and c.data.startswith("server_detail:"))
async def cb_server_detail(
    callback: types.CallbackQuery,
    collector: MetricsCollector,
) -> None:
    server_key = callback.data.split(":", 1)[1] if callback.data else "bili"
    server = await collector.get_server_detail(server_key)

    if not server:
        await callback.answer("Данные сервера временно недоступны", show_alert=True)
        return

    text = format_server_card(server)
    if callback.message:
        await callback.message.edit_text(
            text, reply_markup=server_detail_keyboard(server_key), parse_mode="Markdown"
        )
    await callback.answer()
