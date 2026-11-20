"""Read-only HA diagnostics."""

from aiogram import Router, types
from aiogram.filters import Command

from app.bot.formatters import format_ha_status
from app.bot.keyboards.inline import back_to_menu_keyboard
from app.ha.supervisor import HASupervisor

router = Router(name="ha_router")


@router.message(Command("ha"))
async def cmd_ha(message: types.Message, ha_supervisor: HASupervisor) -> None:
    await message.answer(
        format_ha_status(ha_supervisor), reply_markup=back_to_menu_keyboard("nav_ha"), parse_mode=None
    )


@router.callback_query(lambda c: c.data == "nav_ha")
async def cb_ha(callback: types.CallbackQuery, ha_supervisor: HASupervisor) -> None:
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            format_ha_status(ha_supervisor), reply_markup=back_to_menu_keyboard("nav_ha"), parse_mode=None
        )
