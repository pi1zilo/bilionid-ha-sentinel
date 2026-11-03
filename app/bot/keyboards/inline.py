"""
Inline keyboard builders for BilioniD HA Sentinel.
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
            InlineKeyboardButton(text="🖥 Main Server (Primary)", callback_data="server_detail:main"),
            InlineKeyboardButton(text="🖥 Backup Server (Standby)", callback_data="server_detail:backup"),
        ],
        [
            InlineKeyboardButton(text="🔄 Обновить", callback_data="nav_servers"),
            InlineKeyboardButton(text="🔙 Главное меню", callback_data="nav_main_menu"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def server_detail_keyboard(server_key: str) -> InlineKeyboardMarkup:
    """Keyboard for single server view."""
    is_main = server_key in ("main", "bili", "primary")
    other_key = "backup" if is_main else "main"
    other_name = "Backup Server" if is_main else "Main Server"
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


def ha_keyboard() -> InlineKeyboardMarkup:
    """Keyboard for HA diagnostics and manual failover control."""
    keyboard = [
        [
            InlineKeyboardButton(text="🔄 Обновить HA", callback_data="nav_ha"),
            InlineKeyboardButton(text="⚡ Ручное переключение", callback_data="ha_manual_prompt"),
        ],
        [
            InlineKeyboardButton(text="🔙 Главное меню", callback_data="nav_main_menu"),
        ],
    ]
    return InlineKeyboardMarkup(inline_keyboard=keyboard)


def ha_manual_confirm_keyboard() -> InlineKeyboardMarkup:
    """Confirmation keyboard for manual takeover/release."""
    keyboard = [
        [
            InlineKeyboardButton(text="⚠️ Подтвердить переключение", callback_data="ha_force_toggle"),
        ],
        [
            InlineKeyboardButton(text="❌ Отмена", callback_data="nav_ha"),
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
