"""
Start, help, and main menu handlers.
"""

from aiogram import Router, types
from aiogram.filters import Command

from app.bot.keyboards.inline import main_menu_keyboard
from app.core.config import Settings

router = Router(name="start_router")


HELP_TEXT = (
    "🤖 BilioniD HA Bot — Мониторинг Инфраструктуры\n\n"
    "Доступные команды:\n"
    "• /start — Главное меню с кнопками\n"
    "• /help — Справка по командам\n"
    "• /status — Общий статус всей инфраструктуры\n"
    "• /servers — Список и состояние обоих серверов\n"
    "• /server bili — Подробное состояние Main (BilioniD-factories)\n"
    "• /server susl — Подробное состояние Backup (SUSLIKVPN)\n"
    "• /containers — Список Docker-контейнеров на обоих VPS\n"
    "• /ha — Состояние обоих узлов и Telegram polling\n"
    "• /beszel — Статус обоих Beszel Hubs\n"
    "• /uptime — Мониторы Uptime Kuma\n"
    "• /alerts — Журнал сбоев, восстановления и действий операторов\n"
    "• /note <узел> <текст> — Записать действие оператора\n"
    "• /logs — Выбор сервера и последние 50 строк журнала\n"
    "• /logs backup 100 — Последние 100 строк журнала Backup\n"
    "• /problems — Открытые проблемы Zabbix\n"
    "• /refresh — Принудительное обновление данных\n\n"
    "💡 _Используйте inline-кнопки меню для быстрого переключения разделов._"
)


@router.message(Command("start"))
async def cmd_start(message: types.Message, settings: Settings) -> None:
    text = (
        f"👋 Добро пожаловать в BilioniD HA Bot!\n\n"
        f"Текущий узел: {settings.NODE_NAME} ({settings.NODE_ROLE.value})\n"
        "Выберите интересующий раздел мониторинга:"
    )
    await message.answer(text, reply_markup=main_menu_keyboard(), parse_mode=None)


@router.message(Command("help"))
async def cmd_help(message: types.Message) -> None:
    await message.answer(HELP_TEXT, reply_markup=main_menu_keyboard(), parse_mode=None)


@router.callback_query(lambda c: c.data == "nav_main_menu")
async def cb_main_menu(callback: types.CallbackQuery, settings: Settings) -> None:
    text = (
        f"👋 Главное меню BilioniD HA Bot\n\n"
        f"Текущий узел: {settings.NODE_NAME} ({settings.NODE_ROLE.value})\n"
        "Выберите раздел:"
    )
    if callback.message:
        await callback.message.edit_text(text, reply_markup=main_menu_keyboard(), parse_mode=None)
    await callback.answer()
