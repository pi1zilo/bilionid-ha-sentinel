"""
Inline keyboard builders for BilioniD HA Bot.
"""

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def main_menu_keyboard() -> InlineKeyboardMarkup:
    """Builds the primary navigation menu with 6 main sections."""
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
            InlineKeyboardButton(text="🔄 Обновить все", callback_data="nav_refresh"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def servers_keyboard() -> InlineKeyboardMarkup:
    """Builds keyboard for viewing specific server details."""
    keyboard = [
        [
            InlineKeyboardButton(text="🖥 Main", callback_data="server_detail:bili"),
            InlineKeyboardButton(text="🖥 Backup", callback_data="server_detail:susl"),
        ],
        [
            InlineKeyboardButton(text="🔄 Обновить", callback_data="nav_servers"),
            InlineKeyboardButton(text="🔙 Главное меню", callback_data="nav_main_menu"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def server_logs_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[[
        InlineKeyboardButton(text="Main", callback_data="server_logs:main"),
        InlineKeyboardButton(text="Backup", callback_data="server_logs:backup"),
    ]])


def server_detail_keyboard(server_key: str) -> InlineKeyboardMarkup:
    """Keyboard for single server view."""
    other_key = "susl" if server_key == "bili" else "bili"
    other_name = "Backup" if server_key == "bili" else "Main"
    keyboard = [
        [
            InlineKeyboardButton(text=f"Перейти к {other_name}", callback_data=f"server_detail:{other_key}"),
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
