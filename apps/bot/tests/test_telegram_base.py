from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.enums import ChatAction
from aiogram.exceptions import TelegramAPIError
from aiogram.methods import SendChatAction
from aiogram.types import Chat, Message, User
from fastapi import HTTPException
from pydantic import SecretStr

from app.config import Settings
from app.telegram.handlers_start import _admin_access_denied_text, _admin_link_text
from app.telegram.keyboards import language_keyboard
from app.telegram.texts import LANGUAGE_LABELS, normalize_language, text
from app.telegram.typing_action import TypingActionMiddleware
from app.telegram.webhook import _validate_secret


def test_language_keyboard_contains_supported_languages():
    keyboard = language_keyboard()
    buttons = [row[0] for row in keyboard.inline_keyboard]

    assert [button.text for button in buttons] == list(LANGUAGE_LABELS.values())
    assert [button.callback_data for button in buttons] == [
        "language:ru",
        "language:uz",
        "language:en",
    ]


def test_texts_are_localized_and_language_falls_back_to_ru():
    assert text("language_saved", "en").startswith("Hello")
    assert "Madina" in text("language_saved", "en")
    assert text("language_saved", "uz").startswith("Assalomu alaykum")
    assert normalize_language("unknown") == "ru"


def test_admin_command_texts():
    assert _admin_link_text("https://bot.example.com/", "ru") == (
        "Админка: https://bot.example.com/admin/login"
    )
    assert _admin_link_text("https://bot.example.com", "en") == (
        "Admin panel: https://bot.example.com/admin/login"
    )
    assert "only to administrators" in _admin_access_denied_text("en")


def test_webhook_secret_validation_rejects_invalid_secret():
    settings = Settings(telegram_webhook_secret=SecretStr("expected-secret"))

    with pytest.raises(HTTPException) as exc_info:
        _validate_secret(settings, "wrong-secret")

    assert exc_info.value.status_code == 403


def test_webhook_secret_validation_allows_valid_secret():
    settings = Settings(telegram_webhook_secret=SecretStr("expected-secret"))

    _validate_secret(settings, "expected-secret")


def _message_with_bot(bot):
    message = Message(
        message_id=1,
        date=datetime.now(),
        chat=Chat(id=123, type="private"),
        from_user=User(id=456, is_bot=False, first_name="Ali"),
        text="hello",
    )
    return message.as_(bot)


async def test_typing_action_middleware_sends_typing_action():
    bot = SimpleNamespace(send_chat_action=AsyncMock())
    event = _message_with_bot(bot)
    handler = AsyncMock(return_value="ok")

    result = await TypingActionMiddleware()(handler, event, {})

    assert result == "ok"
    bot.send_chat_action.assert_awaited_once_with(
        chat_id=123,
        action=ChatAction.TYPING,
    )
    handler.assert_awaited_once_with(event, {})


async def test_typing_action_middleware_does_not_block_handler_on_api_error():
    method = SendChatAction(chat_id=123, action=ChatAction.TYPING)
    bot = SimpleNamespace(
        send_chat_action=AsyncMock(
            side_effect=TelegramAPIError(method=method, message="failed")
        )
    )
    event = _message_with_bot(bot)
    handler = AsyncMock(return_value="ok")

    result = await TypingActionMiddleware()(handler, event, {})

    assert result == "ok"
    bot.send_chat_action.assert_awaited_once_with(
        chat_id=123,
        action=ChatAction.TYPING,
    )
    handler.assert_awaited_once_with(event, {})
