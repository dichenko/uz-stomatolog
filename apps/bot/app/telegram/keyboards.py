from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

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
