"""
Admin and refresh handlers.
"""

from aiogram import F, Router, types
from aiogram.filters import Command
from aiogram.filters.command import CommandObject

from app.bot.formatters import format_admin_view, format_infrastructure_status
from app.bot.keyboards.inline import back_to_menu_keyboard, server_logs_keyboard, servers_keyboard
from app.core.config import Settings
from app.ha.supervisor import HASupervisor
from app.monitoring.collector import MetricsCollector
from app.monitoring.server_logs import DEFAULT_LOG_LINES, MAX_LOG_LINES, JournalUnavailable, parse_log_lines

router = Router(name="admin_router")


@router.message(Command("logs"))
async def cmd_logs(
    message: types.Message, command: CommandObject, settings: Settings, collector: MetricsCollector,
) -> None:
    args = (command.args or "").split()
    if not args:
        await message.answer(
            f"Выберите сервер, чтобы посмотреть последние {DEFAULT_LOG_LINES} строк журнала.",
            reply_markup=server_logs_keyboard(), parse_mode=None,
        )
        return

    targets = {
        settings.PEER_NODE_NAME.casefold(): True,
        settings.NODE_NAME.casefold(): False,
        "main": not settings.is_primary,
        "backup": settings.is_primary,
        "local": False,
    }
    try:
        if len(args) > 2:
            raise ValueError
        peer = targets[args[0].casefold()]
        lines = parse_log_lines(args[1]) if len(args) == 2 else DEFAULT_LOG_LINES
    except (KeyError, ValueError):
        await message.answer(f"Пример — /logs backup 100. Укажите от 1 до {MAX_LOG_LINES} строк.", parse_mode=None)
        return
    await _send_server_logs(message, settings, collector, peer=peer, lines=lines)


@router.callback_query(F.data.in_({"server_logs:main", "server_logs:backup"}))
async def cb_server_logs(callback: types.CallbackQuery, settings: Settings, collector: MetricsCollector) -> None:
    await callback.answer()
    if isinstance(callback.message, types.Message):
        selected_main = callback.data == "server_logs:main"
        await _send_server_logs(
            callback.message, settings, collector, peer=selected_main != settings.is_primary, lines=DEFAULT_LOG_LINES,
        )


async def _send_server_logs(
    message: types.Message, settings: Settings, collector: MetricsCollector, *, peer: bool, lines: int,
) -> None:
    node = settings.PEER_NODE_NAME if peer else settings.NODE_NAME
    try:
        journal = await collector.get_server_logs(peer, lines)
    except JournalUnavailable:
        await message.answer(f"Системный журнал {node} недоступен. Проверьте настройки и связь с узлом.", parse_mode=None)
        return

    title = f"Системный журнал {node} · UTC · сначала новые записи"
    if not journal:
        await message.answer(f"{title}\n\nЗаписей нет.", parse_mode=None)
    elif len(title) + len(journal) <= 3500:
        await message.answer(
            f"{title}\n\n{journal}", parse_mode=None, protect_content=True,
            link_preview_options=types.LinkPreviewOptions(is_disabled=True),
        )
    else:
        document = types.BufferedInputFile(journal.encode("utf-8"), filename="server-journal.txt")
        await message.answer_document(document, caption=title, parse_mode=None, protect_content=True)


@router.message(Command("refresh"))
async def cmd_refresh(
    message: types.Message,
    collector: MetricsCollector,
    ha_supervisor: HASupervisor,
) -> None:
    servers = await collector.get_servers(force_refresh=True)
    uptime_overview = await collector.get_uptime_kuma()
    text = "🔄 Данные успешно обновлены!\n\n" + format_infrastructure_status(
        servers, uptime_overview, ha_supervisor
    )
    await message.answer(text, reply_markup=servers_keyboard(), parse_mode=None)


@router.callback_query(lambda c: c.data == "nav_refresh")
async def cb_refresh(
    callback: types.CallbackQuery,
    collector: MetricsCollector,
    ha_supervisor: HASupervisor,
) -> None:
    servers = await collector.get_servers(force_refresh=True)
    uptime_overview = await collector.get_uptime_kuma()
    text = "🔄 Данные успешно обновлены!\n\n" + format_infrastructure_status(
        servers, uptime_overview, ha_supervisor
    )
    if callback.message:
        await callback.message.edit_text(text, reply_markup=servers_keyboard(), parse_mode=None)
    await callback.answer("Данные обновлены", show_alert=False)


@router.callback_query(lambda c: c.data == "nav_admin")
async def cb_admin(
    callback: types.CallbackQuery,
    ha_supervisor: HASupervisor,
) -> None:
    text = format_admin_view(ha_supervisor)
    if callback.message:
        await callback.message.edit_text(text, reply_markup=back_to_menu_keyboard("nav_admin"), parse_mode=None)
    await callback.answer()
