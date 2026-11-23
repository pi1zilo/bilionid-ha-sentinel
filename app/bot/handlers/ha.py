"""HA diagnostics and administrator-only polling handover."""

import aiohttp
from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.filters.command import CommandObject

from app.bot.formatters import format_ha_status
from app.bot.keyboards.inline import back_to_menu_keyboard
from app.core.nodes import BILI, SUSLIK, find_node
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


@router.message(Command("handover"))
async def cmd_handover(message: types.Message, command: CommandObject, ha_supervisor: HASupervisor) -> None:
    admin = ha_supervisor.settings.HA_ADMIN_USER_ID
    if not admin or message.from_user is None or message.from_user.id != admin:
        await message.answer("Сменить узел может администратор бота.", parse_mode=None)
        return
    if ha_supervisor.handover is None:
        await message.answer("В ручном режиме переключение выполняется через SSH.", parse_mode=None)
        return
    node = find_node(ha_supervisor.settings, command.args)
    if node is None:
        await message.answer("Укажите /handover suslik или /handover bili.", parse_mode=None)
        return
    target = "main" if node is BILI else "backup"
    confirmation = types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(text=f"Подтвердить переход на {node.label}", callback_data=f"ha_prefer:{target}"),
    ]])
    await message.answer("Подтвердите запрос на переключение.", reply_markup=confirmation, parse_mode=None)


@router.callback_query(F.data.in_({"ha_prefer:main", "ha_prefer:backup"}))
async def cb_handover(callback: types.CallbackQuery, ha_supervisor: HASupervisor) -> None:
    admin = ha_supervisor.settings.HA_ADMIN_USER_ID
    if not admin or callback.from_user.id != admin:
        await callback.answer("Сменить узел может администратор бота.", show_alert=True)
        return
    handover = ha_supervisor.handover
    if handover is None:
        await callback.answer("Переключение выполняется через SSH.", show_alert=True)
        return
    target = callback.data.split(":", 1)[1]
    try:
        await handover.prefer(target)
    except (aiohttp.ClientError, TimeoutError, RuntimeError, ValueError):
        await callback.answer(f"{SUSLIK.label} не подтвердил запрос.", show_alert=True)
        return
    await callback.answer("Запрос подтверждён.")
    if isinstance(callback.message, types.Message):
        node = BILI if target == "main" else SUSLIK
        await callback.message.edit_text(
            f"Запрошен переход на {node.label}. Статус в /ha.", parse_mode=None,
        )
