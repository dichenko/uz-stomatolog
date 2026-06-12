import logging
from typing import Any

from aiogram import BaseMiddleware
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message, TelegramObject

logger = logging.getLogger(__name__)


class TypingActionMiddleware(BaseMiddleware):
    async def __call__(
        self,
        handler: Any,
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        if isinstance(event, Message):
            try:
                await event.bot.send_chat_action(
                    chat_id=event.chat.id,
                    action=ChatAction.TYPING,
                )
            except TelegramAPIError as exc:
                logger.info(
                    "typing_action_failed",
                    extra={"chat_id": event.chat.id, "telegram_error": str(exc)},
                )

        return await handler(event, data)
