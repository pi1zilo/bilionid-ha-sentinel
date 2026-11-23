"""
Inline keyboard builders for BilioniD HA Bot.
"""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.core.nodes import BILI, SUSLIK


def main_menu_keyboard() -> InlineKeyboardMarkup:
    """Builds the main monitoring menu."""
    keyboard = [
        [
            InlineKeyboardButton(text="🖥 Серверы", callback_data="nav_servers"),
            InlineKeyboardButton(text="📊 Мониторинг", callback_data="nav_monitoring"),
        ],
        [
            InlineKeyboardButton(text="🐳 Контейнеры", callback_data="nav_containers"),
            InlineKeyboardButton(text="🔄 HA Status", callback_data="nav_ha"),
        ],
        [
            InlineKeyboardButton(text="🚨 Инциденты", callback_data="nav_alerts"),
            InlineKeyboardButton(text="⚙️ Управление", callback_data="nav_admin"),
        ],
        [
            InlineKeyboardButton(text="📄 Логи", callback_data="nav_logs"),
            InlineKeyboardButton(text="🔄 Обновить", callback_data="nav_refresh"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def servers_keyboard() -> InlineKeyboardMarkup:
    """Builds keyboard for viewing specific server details."""
    keyboard = [
        [
            InlineKeyboardButton(text=f"🖥 {BILI.label}", callback_data="server_detail:bili"),
            InlineKeyboardButton(text=f"🖥 {SUSLIK.label}", callback_data="server_detail:suslik"),
        ],
        [
            InlineKeyboardButton(text="🔄 Обновить", callback_data="nav_servers"),
            InlineKeyboardButton(text="🔙 Главное меню", callback_data="nav_main_menu"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def server_logs_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=BILI.label, callback_data="server_logs:main"),
         InlineKeyboardButton(text=SUSLIK.label, callback_data="server_logs:backup")],
        [InlineKeyboardButton(text="🔙 Главное меню", callback_data="nav_main_menu")],
    ])


def server_detail_keyboard(server_key: str) -> InlineKeyboardMarkup:
    """Keyboard for single server view."""
    other = SUSLIK if server_key in BILI.aliases else BILI
    keyboard = [
        [
            InlineKeyboardButton(text=f"Перейти к {other.label}", callback_data=f"server_detail:{other.label}"),
        ],
        [
            InlineKeyboardButton(text="🔄 Обновить", callback_data=f"server_detail:{server_key}"),
            InlineKeyboardButton(text="🖥 Все серверы", callback_data="nav_servers"),
        ],
        [
            InlineKeyboardButton(text="🔙 Главное меню", callback_data="nav_main_menu"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def back_to_menu_keyboard(refresh_callback: str = "nav_main_menu") -> InlineKeyboardMarkup:
    """Generic back & refresh keyboard."""
    keyboard = [
        [
            InlineKeyboardButton(text="🔄 Обновить", callback_data=refresh_callback),
            InlineKeyboardButton(text="🔙 Главное меню", callback_data="nav_main_menu"),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)
