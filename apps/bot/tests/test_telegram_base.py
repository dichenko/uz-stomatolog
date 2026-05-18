import pytest
from fastapi import HTTPException
from pydantic import SecretStr

from app.config import Settings
from app.telegram.keyboards import language_keyboard
from app.telegram.texts import LANGUAGE_LABELS, normalize_language, text
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
    assert text("language_saved", "en").startswith("Language saved")
    assert text("language_saved", "uz").startswith("Til saqlandi")
    assert normalize_language("unknown") == "ru"


def test_webhook_secret_validation_rejects_invalid_secret():
    settings = Settings(telegram_webhook_secret=SecretStr("expected-secret"))

    with pytest.raises(HTTPException) as exc_info:
        _validate_secret(settings, "wrong-secret")

    assert exc_info.value.status_code == 403


def test_webhook_secret_validation_allows_valid_secret():
    settings = Settings(telegram_webhook_secret=SecretStr("expected-secret"))

    _validate_secret(settings, "expected-secret")
