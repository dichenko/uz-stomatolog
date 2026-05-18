from typing import Literal

Intent = Literal[
    "admin_faq",
    "book_appointment",
    "cancel_appointment",
    "reschedule_appointment",
    "medical_question",
    "emergency",
    "discount_request",
    "non_standard_service",
    "angry_user",
    "unknown",
]


BOOKING_KEYWORDS = (
    "запис",
    "прием",
    "приём",
    "бронь",
    "yozil",
    "qabul",
    "book",
    "appointment",
)
CANCEL_KEYWORDS = ("отмен", "бекор", "cancel")
RESCHEDULE_KEYWORDS = ("перен", "друг", "ko'chir", "reschedule", "move")
EMERGENCY_KEYWORDS = (
    "срочно",
    "экстренно",
    "кров",
    "опух",
    "urgent",
    "emergency",
    "shoshilinch",
)
DISCOUNT_KEYWORDS = ("скид", "дешев", "chegirma", "discount")
ANGRY_KEYWORDS = ("жалоб", "плохо", "ужас", "angry", "complaint", "shikoyat")
NON_STANDARD_KEYWORDS = ("рассроч", "страхов", "insurance", "installment")
MEDICAL_KEYWORDS = (
    "что выпить",
    "какое лекарство",
    "антибиотик",
    "обезбол",
    "диагноз",
    "лечить дома",
    "og'riq",
    "dori",
    "antibiotik",
    "painkiller",
    "medicine",
    "antibiotic",
    "diagnose",
)


def classify_intent_text(text: str) -> Intent:
    normalized = text.casefold()
    if _contains_any(normalized, EMERGENCY_KEYWORDS):
        return "emergency"
    if _contains_any(normalized, MEDICAL_KEYWORDS):
        return "medical_question"
    if _contains_any(normalized, CANCEL_KEYWORDS):
        return "cancel_appointment"
    if _contains_any(normalized, RESCHEDULE_KEYWORDS):
        return "reschedule_appointment"
    if _contains_any(normalized, BOOKING_KEYWORDS):
        return "book_appointment"
    if _contains_any(normalized, DISCOUNT_KEYWORDS):
        return "discount_request"
    if _contains_any(normalized, ANGRY_KEYWORDS):
        return "angry_user"
    if _contains_any(normalized, NON_STANDARD_KEYWORDS):
        return "non_standard_service"
    if normalized.strip():
        return "admin_faq"
    return "unknown"


def _contains_any(text: str, keywords: tuple[str, ...]) -> bool:
    return any(keyword in text for keyword in keywords)
