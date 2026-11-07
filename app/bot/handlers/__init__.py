"""
Bot handlers package for BilioniD HA Sentinel.
Provides common utility functions for safe message interaction.
"""

from typing import Optional
import logging
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, InlineKeyboardMarkup

logger = logging.getLogger("bilionid_ha.bot")


async def safe_edit_message(
    callback: CallbackQuery,
    text: str,
    reply_markup: Optional[InlineKeyboardMarkup] = None,
    parse_mode: str = "Markdown",
) -> None:
    """
    Safely edits a callback query's message, suppressing the Telegram error
    when the message content and reply markup have not changed.
    """
    if not callback.message:
        return
    try:
        await callback.message.edit_text(
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
    except TelegramBadRequest as e:
        if "message is not modified" in str(e).lower():
            logger.debug("Suppressed harmless 'message is not modified' for callback %s", callback.data)
        else:
            raise
