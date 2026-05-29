from aiogram.exceptions import TelegramBadRequest
from aiogram.methods import SendMessage

from app.config import Settings
from app.services.admin_notify import send_admin_notification


class FailingBot:
    async def send_message(self, *, chat_id: str, text: str):
        raise TelegramBadRequest(
            method=SendMessage(chat_id=chat_id, text=text),
            message="chat not found",
        )


async def test_send_admin_notification_does_not_raise_on_telegram_error():
    result = await send_admin_notification(
        bot=FailingBot(),
        message_text="Escalation required",
        settings=Settings(admin_telegram_chat_id="-100bad"),
    )

    assert result.sent is False
    assert result.admin_chat_id == "-100bad"
    assert result.admin_message_id is None
