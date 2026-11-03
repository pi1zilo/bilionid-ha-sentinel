"""
Start, help, and main menu handlers.
"""

from aiogram import Router, types
from aiogram.filters import Command

from app.bot.keyboards.inline import main_menu_keyboard
from app.core.config import Settings
from app.ha.supervisor import HASupervisor

router = Router(name="start_router")


HELP_TEXT = (
    "🤖 **BilioniD HA Sentinel — Мониторинг Инфраструктуры**\n\n"
    "Доступные команды:\n"
    "• /start — Главное меню с кнопками\n"
    "• /help — Справка по командам\n"
    "• /status — Общий статус всей инфраструктуры\n"
    "• /servers — Список и состояние серверов\n"
    "• /server main — Подробное состояние Main сервера\n"
    "• /server backup — Подробное состояние Backup сервера\n"
    "• /containers — Список Docker-контейнеров на обоих VPS\n"
    "• /ha — Состояние отказоустойчивости, роли, lease, heartbeat\n"
    "• /beszel — Статус Beszel Hubs\n"
    "• /uptime — Мониторы Uptime Kuma\n"
    "• /alerts — Журнал инцидентов и аварийных переключений\n"
    "• /refresh — Принудительное обновление данных\n\n"
    "💡 _Используйте inline-кнопки меню для быстрого переключения разделов._"
)


def _get_node_status_desc(settings: Settings, ha_supervisor: HASupervisor) -> str:
    if ha_supervisor.is_polling:
        mode = "🟢 АКТИВЕН (Лидер)"
    else:
        mode = "⚪ ОЖИДАНИЕ (Резерв)"
    return f"{settings.NODE_NAME} ({settings.NODE_ROLE.value} — {mode})"


@router.message(Command("start"))
async def cmd_start(
    message: types.Message,
    settings: Settings,
    ha_supervisor: HASupervisor,
) -> None:
    node_desc = _get_node_status_desc(settings, ha_supervisor)
    text = (
        f"👋 **Добро пожаловать в BilioniD HA Bot!**\n\n"
        f"Текущий узел: `{node_desc}`\n"
        "Выберите интересующий раздел мониторинга:"
    )
    await message.answer(text, reply_markup=main_menu_keyboard(), parse_mode="Markdown")


@router.message(Command("help"))
async def cmd_help(message: types.Message) -> None:
    await message.answer(HELP_TEXT, reply_markup=main_menu_keyboard(), parse_mode="Markdown")


@router.callback_query(lambda c: c.data == "nav_main_menu")
async def cb_main_menu(
    callback: types.CallbackQuery,
    settings: Settings,
    ha_supervisor: HASupervisor,
) -> None:
    node_desc = _get_node_status_desc(settings, ha_supervisor)
    text = (
        f"👋 **Главное меню BilioniD HA Bot**\n\n"
        f"Текущий узел: `{node_desc}`\n"
        "Выберите раздел:"
    )
    if callback.message:
        await callback.message.edit_text(text, reply_markup=main_menu_keyboard(), parse_mode="Markdown")
    await callback.answer()
