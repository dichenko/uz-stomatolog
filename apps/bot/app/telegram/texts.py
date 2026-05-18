from typing import Literal

Language = Literal["ru", "uz", "en"]

SUPPORTED_LANGUAGES: tuple[Language, ...] = ("ru", "uz", "en")
DEFAULT_LANGUAGE: Language = "ru"


LANGUAGE_LABELS: dict[Language, str] = {
    "ru": "Русский",
    "uz": "O'zbekcha",
    "en": "English",
}


TEXTS: dict[str, dict[Language, str]] = {
    "choose_language": {
        "ru": "Выберите язык общения:",
        "uz": "Muloqot tilini tanlang:",
        "en": "Choose your language:",
    },
    "language_saved": {
        "ru": "Язык сохранён. Чем могу помочь?",
        "uz": "Til saqlandi. Qanday yordam bera olaman?",
        "en": "Language saved. How can I help?",
    },
    "welcome": {
        "ru": "Здравствуйте. Я административный ассистент стоматологической клиники.",
        "uz": "Assalomu alaykum. Men stomatologiya klinikasining ma'muriy yordamchisiman.",
        "en": "Hello. I am the dental clinic administrative assistant.",
    },
    "help": {
        "ru": (
            "Я помогу с административными вопросами: услуги, цены, график, адрес, "
            "запись, перенос и отмена приёма. Медицинские советы не даю."
        ),
        "uz": (
            "Men ma'muriy savollar bo'yicha yordam beraman: xizmatlar, narxlar, "
            "ish vaqti, manzil, qabulga yozilish, ko'chirish va bekor qilish. "
            "Tibbiy maslahat bermayman."
        ),
        "en": (
            "I can help with administrative questions: services, prices, schedule, "
            "address, booking, rescheduling, and cancellation. I do not provide "
            "medical advice."
        ),
    },
    "appointments_empty": {
        "ru": "У вас пока нет активных записей.",
        "uz": "Sizda hozircha faol yozuvlar yo'q.",
        "en": "You do not have active appointments yet.",
    },
    "appointments_header": {
        "ru": "Ваши активные записи:",
        "uz": "Faol yozuvlaringiz:",
        "en": "Your active appointments:",
    },
    "language_required": {
        "ru": "Сначала выберите язык.",
        "uz": "Avval tilni tanlang.",
        "en": "Please choose your language first.",
    },
    "fallback": {
        "ru": "Пока я понимаю только базовые команды. Используйте /help.",
        "uz": "Hozircha faqat asosiy buyruqlarni tushunaman. /help dan foydalaning.",
        "en": "For now I understand basic commands only. Use /help.",
    },
    "webhook_not_configured": {
        "ru": "Telegram bot is not configured.",
        "uz": "Telegram bot is not configured.",
        "en": "Telegram bot is not configured.",
    },
}


def normalize_language(language: str | None) -> Language:
    if language in SUPPORTED_LANGUAGES:
        return language  # type: ignore[return-value]
    return DEFAULT_LANGUAGE


def text(key: str, language: str | None = None) -> str:
    return TEXTS[key][normalize_language(language)]
