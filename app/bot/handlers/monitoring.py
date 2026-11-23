"""
Monitoring handlers for Beszel Hubs and Uptime Kuma monitors.
"""

import aiohttp
from aiogram import Router, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.formatters import format_beszel_view, format_uptime_kuma_view
from app.bot.keyboards.inline import back_to_menu_keyboard
from app.monitoring.collector import MetricsCollector
from app.monitoring.zabbix import ZabbixClient
from app.bot.formatters import format_time
from app.core.config import Settings

router = Router(name="monitoring_router")


@router.message(Command("problems"))
async def cmd_problems(message: types.Message, zabbix: ZabbixClient, settings: Settings) -> None:
    if not zabbix.url:
        await message.answer("Zabbix не настроен.", parse_mode=None)
        return
    try:
        problems = await zabbix.get_problems()
    except (aiohttp.ClientError, TimeoutError, ValueError):
        await message.answer("Данные Zabbix временно недоступны.", parse_mode=None)
        return
    lines = ["Zabbix · открытые проблемы"]
    for problem in problems:
        stamp = format_time(problem.get("clock"), settings.ALERT_TIMEZONE)
        lines.append(f"• {stamp} · {str(problem.get('name', ''))[:130]}")
    if not problems:
        lines.append("Открытых проблем нет.")
    await message.answer("\n".join(lines)[:3800], parse_mode=None)


def monitoring_menu_keyboard() -> InlineKeyboardMarkup:
    keyboard = [
        [
            InlineKeyboardButton(text="📊 Beszel Hubs", callback_data="nav_beszel"),
            InlineKeyboardButton(text="⏱ Uptime Kuma", callback_data="nav_uptime"),
        ],
        [
            InlineKeyboardButton(text="🔙 Главное меню", callback_data="nav_main_menu"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


@router.message(Command("beszel"))
async def cmd_beszel(message: types.Message, collector: MetricsCollector) -> None:
    status = await collector.get_beszel_status()
    text = format_beszel_view(status)
    await message.answer(text, reply_markup=back_to_menu_keyboard("nav_beszel"), parse_mode=None)


@router.message(Command("uptime"))
async def cmd_uptime(message: types.Message, collector: MetricsCollector) -> None:
    overview = await collector.get_uptime_kuma(include_tags=True)
    text = format_uptime_kuma_view(overview)
    await message.answer(text, reply_markup=back_to_menu_keyboard("nav_uptime"), parse_mode=None)


@router.callback_query(lambda c: c.data == "nav_monitoring")
async def cb_monitoring_menu(callback: types.CallbackQuery) -> None:
    text = (
        "📊 РАЗДЕЛ МОНИТОРИНГА\n\n"
        "Выберите сервис мониторинга для детальной информации:\n"
        "• Beszel Hubs: состояние сбора телеметрии обоих VPS\n"
        "• Uptime Kuma: состояние сервисов и внешних мониторов"
    )
    if callback.message:
        await callback.message.edit_text(text, reply_markup=monitoring_menu_keyboard(), parse_mode=None)
    await callback.answer()


@router.callback_query(lambda c: c.data == "nav_beszel")
async def cb_beszel(callback: types.CallbackQuery, collector: MetricsCollector) -> None:
    status = await collector.get_beszel_status()
    text = format_beszel_view(status)
    if callback.message:
        await callback.message.edit_text(text, reply_markup=back_to_menu_keyboard("nav_beszel"), parse_mode=None)
    await callback.answer()


@router.callback_query(lambda c: c.data == "nav_uptime")
async def cb_uptime(callback: types.CallbackQuery, collector: MetricsCollector) -> None:
    overview = await collector.get_uptime_kuma(include_tags=True)
    text = format_uptime_kuma_view(overview)
    if callback.message:
        await callback.message.edit_text(text, reply_markup=back_to_menu_keyboard("nav_uptime"), parse_mode=None)
    await callback.answer()
