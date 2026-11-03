"""
Docker containers monitoring handlers.
"""

from aiogram import Router, types
from aiogram.filters import Command

from app.bot.formatters import format_containers_view
from app.bot.keyboards.inline import back_to_menu_keyboard
from app.monitoring.collector import MetricsCollector

router = Router(name="containers_router")


@router.message(Command("containers"))
async def cmd_containers(message: types.Message, collector: MetricsCollector) -> None:
    containers = await collector.get_all_containers()
    text = format_containers_view(containers, collector.containers_errors)
    await message.answer(text, reply_markup=back_to_menu_keyboard("nav_containers"), parse_mode="Markdown")


@router.callback_query(lambda c: c.data == "nav_containers")
async def cb_containers(callback: types.CallbackQuery, collector: MetricsCollector) -> None:
    containers = await collector.get_all_containers()
    text = format_containers_view(containers, collector.containers_errors)
    if callback.message:
        await callback.message.edit_text(
            text, reply_markup=back_to_menu_keyboard("nav_containers"), parse_mode="Markdown"
        )
    await callback.answer()
