from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from app.telegram.texts import LANGUAGE_LABELS


def language_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=label,
                    callback_data=f"language:{language}",
                )
            ]
            for language, label in LANGUAGE_LABELS.items()
        ]
    )


def contact_request_keyboard(language: str | None = None) -> ReplyKeyboardMarkup:
    labels = {
        "ru": "Отправить контакт",
        "uz": "Kontakt yuborish",
        "en": "Share contact",
    }
    return ReplyKeyboardMarkup(
        keyboard=[
            [
                KeyboardButton(
                    text=labels.get(language or "ru", labels["ru"]),
                    request_contact=True,
                )
            ]
        ],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def booking_slots_keyboard(slots: list[dict]) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=_slot_label(slot),
                    callback_data=f"booking_slot:{index}",
                )
            ]
            for index, slot in enumerate(slots)
        ]
    )


def _slot_label(slot: dict) -> str:
    timezone = slot.get("timezone") or "Asia/Tashkent"
    start_at = datetime.fromisoformat(slot["start_at"]).astimezone(ZoneInfo(timezone))
    return start_at.strftime("%d.%m %H:%M")
