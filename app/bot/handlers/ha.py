"""
High Availability diagnostics and manual failover control handlers.
"""

from aiogram import Router, types
from aiogram.filters import Command

from app.bot.formatters import format_ha_status
from app.bot.keyboards.inline import ha_keyboard, ha_manual_confirm_keyboard
from app.core.config import NodeRole, NodeState
from app.ha.supervisor import HASupervisor
from app.storage.db import Storage

router = Router(name="ha_router")


@router.message(Command("ha"))
async def cmd_ha(
    message: types.Message,
    ha_supervisor: HASupervisor,
    storage: Storage,
) -> None:
    lease = await storage.get_current_lease()
    text = format_ha_status(ha_supervisor, lease)
    await message.answer(text, reply_markup=ha_keyboard(), parse_mode="Markdown")


@router.callback_query(lambda c: c.data == "nav_ha")
async def cb_ha(
    callback: types.CallbackQuery,
    ha_supervisor: HASupervisor,
    storage: Storage,
) -> None:
    lease = await storage.get_current_lease()
    text = format_ha_status(ha_supervisor, lease)
    if callback.message:
        await callback.message.edit_text(text, reply_markup=ha_keyboard(), parse_mode="Markdown")
    await callback.answer()


@router.callback_query(lambda c: c.data == "ha_manual_prompt")
async def cb_ha_manual_prompt(callback: types.CallbackQuery, ha_supervisor: HASupervisor) -> None:
    current_action = "перевести в STANDBY" if ha_supervisor.is_polling else "принудительно АКТИВИРОВАТЬ"
    text = (
        "⚠️ **РУЧНОЕ ПЕРЕКЛЮЧЕНИЕ РОЛИ HA**\n\n"
        f"Текущий узел: `{ha_supervisor.settings.NODE_NAME}`\n"
        f"Текущий статус: `{'АКТИВЕН' if ha_supervisor.is_polling else 'РЕЗЕРВ'}`\n\n"
        f"Вы действительно хотите **{current_action}** данный узел вручную?\n"
        "Убедитесь, что второй узел не будет конфликтовать."
    )
    if callback.message:
        await callback.message.edit_text(text, reply_markup=ha_manual_confirm_keyboard(), parse_mode="Markdown")
    await callback.answer()


@router.callback_query(lambda c: c.data == "ha_force_toggle")
async def cb_ha_force_toggle(
    callback: types.CallbackQuery,
    ha_supervisor: HASupervisor,
    storage: Storage,
) -> None:
    if ha_supervisor.is_polling:
        # Step down to passive
        await ha_supervisor.handle_force_role("STANDBY")
        await storage.record_ha_event(
            event_type="MANUAL_DEMOTE",
            source_node=ha_supervisor.settings.NODE_NAME,
            details="Manual demotion to STANDBY via Telegram button.",
        )
        result_msg = "⚪ Узел переведен в режим ожидания (STANDBY). Polling остановлен."
    else:
        # Step up to active
        await ha_supervisor.handle_force_role("ACTIVE")
        await storage.record_ha_event(
            event_type="MANUAL_PROMOTE",
            source_node=ha_supervisor.settings.NODE_NAME,
            details="Manual promotion to ACTIVE via Telegram button.",
        )
        result_msg = "🟢 Узел принудительно АКТИВИРОВАН. Polling запущен."

    lease = await storage.get_current_lease()
    text = format_ha_status(ha_supervisor, lease) + f"\n\n⚡ *{result_msg}*"
    if callback.message:
        await callback.message.edit_text(text, reply_markup=ha_keyboard(), parse_mode="Markdown")
    await callback.answer(result_msg, show_alert=True)
