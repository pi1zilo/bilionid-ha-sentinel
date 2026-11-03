"""
Admin and refresh handlers.
"""

from aiogram import Router, types
from aiogram.filters import Command

from app.bot.formatters import format_admin_view, format_infrastructure_status
from app.bot.keyboards.inline import back_to_menu_keyboard, servers_keyboard
from app.ha.supervisor import HASupervisor
from app.monitoring.collector import MetricsCollector

router = Router(name="admin_router")


@router.message(Command("refresh"))
async def cmd_refresh(
    message: types.Message,
    collector: MetricsCollector,
    ha_supervisor: HASupervisor,
) -> None:
    servers = await collector.get_servers(force_refresh=True)
    uptime_overview = await collector.get_uptime_kuma()
    text = "🔄 **Данные успешно обновлены!**\n\n" + format_infrastructure_status(
        servers, uptime_overview, ha_supervisor
    )
    await message.answer(text, reply_markup=servers_keyboard(), parse_mode="Markdown")


@router.callback_query(lambda c: c.data == "nav_refresh")
async def cb_refresh(
    callback: types.CallbackQuery,
    collector: MetricsCollector,
    ha_supervisor: HASupervisor,
) -> None:
    servers = await collector.get_servers(force_refresh=True)
    uptime_overview = await collector.get_uptime_kuma()
    text = "🔄 **Данные успешно обновлены!**\n\n" + format_infrastructure_status(
        servers, uptime_overview, ha_supervisor
    )
    if callback.message:
        await callback.message.edit_text(text, reply_markup=servers_keyboard(), parse_mode="Markdown")
    await callback.answer("Данные обновлены", show_alert=False)


@router.callback_query(lambda c: c.data == "nav_admin")
async def cb_admin(
    callback: types.CallbackQuery,
    ha_supervisor: HASupervisor,
) -> None:
    text = format_admin_view(ha_supervisor)
    if callback.message:
        await callback.message.edit_text(text, reply_markup=back_to_menu_keyboard("nav_admin"), parse_mode="Markdown")
    await callback.answer()
