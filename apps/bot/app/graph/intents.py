import json
import logging
from typing import Any, Literal

from langsmith import traceable
from langsmith.wrappers import wrap_openai
from openai import AsyncOpenAI

from app.config import get_settings

logger = logging.getLogger(__name__)

Intent = Literal[
    "admin_faq",
    "view_appointments",
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
VIEW_APPOINTMENTS_KEYWORDS = (
    "мои записи",
    "моя запись",
    "мои приёмы",
    "мои приемы",
    "есть записи",
    "какие записи",
    "посмотрите какие",
    "записывался",
    "записывалась",
    "yozuvlarim",
    "yozilganman",
    "qabulim",
    "my appointments",
    "my appointment",
    "do i have appointments",
    "already booked",
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


@traceable(name="classify_intent")
async def classify_intent(
    text: str,
    *,
    language: str | None = None,
    current_flow: str | None = None,
    current_state: str | None = None,
) -> Intent:
    """Classify user intent with deterministic guards, LLM routing, and fallback."""
    rules_intent = classify_intent_rules(text)
    if rules_intent is not None:
        return rules_intent

    llm_intent = await _try_llm_classify_intent(
        text=text,
        language=language,
        current_flow=current_flow,
        current_state=current_state,
    )
    if llm_intent is not None:
        return llm_intent

    return classify_intent_text(text)


def classify_intent_rules(text: str) -> Intent | None:
    """Return only high-confidence rule matches."""
    normalized = text.casefold().strip()
    if not normalized:
        return "unknown"
    if normalized.startswith("/my_appointments"):
        return "view_appointments"
    if _contains_any(normalized, EMERGENCY_KEYWORDS):
        return "emergency"
    if _contains_any(normalized, MEDICAL_KEYWORDS):
        return "medical_question"
    if _contains_any(normalized, CANCEL_KEYWORDS):
        return "cancel_appointment"
    if _contains_any(normalized, RESCHEDULE_KEYWORDS):
        return "reschedule_appointment"
    if _contains_any(normalized, DISCOUNT_KEYWORDS):
        return "discount_request"
    if _contains_any(normalized, ANGRY_KEYWORDS):
        return "angry_user"
    if _contains_any(normalized, NON_STANDARD_KEYWORDS):
        return "non_standard_service"
    if _contains_any(normalized, VIEW_APPOINTMENTS_KEYWORDS):
        return "view_appointments"
    return None


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
    if _contains_any(normalized, VIEW_APPOINTMENTS_KEYWORDS):
        return "view_appointments"
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


async def _try_llm_classify_intent(
    *,
    text: str,
    language: str | None,
    current_flow: str | None,
    current_state: str | None,
) -> Intent | None:
    settings = get_settings()
    if settings.openai_api_key is None:
        return None

    api_key = settings.openai_api_key.get_secret_value().strip()
    if not api_key:
        return None

    try:
        client = wrap_openai(
            AsyncOpenAI(
                api_key=api_key,
                base_url=settings.openai_base_url or None,
            )
        )
        response = await client.chat.completions.create(
            model=settings.openai_text_model,
            temperature=0,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": _intent_router_system_prompt(),
                },
                {
                    "role": "user",
                    "content": _intent_router_user_prompt(
                        text=text,
                        language=language,
                        current_flow=current_flow,
                        current_state=current_state,
                    ),
                },
            ],
        )
        content = response.choices[0].message.content
        if not content:
            return None
        return _parse_llm_intent(content)
    except Exception:
        logger.exception("llm_intent_classification_failed")
        return None


def _parse_llm_intent(content: str) -> Intent | None:
    try:
        payload = json.loads(content)
    except json.JSONDecodeError:
        logger.warning("llm_intent_invalid_json", extra={"content": content[:500]})
        return None

    if not isinstance(payload, dict):
        return None

    intent_value = payload.get("intent")
    confidence = _coerce_confidence(payload.get("confidence"))
    if intent_value not in _valid_intents() or confidence < 0.6:
        logger.info(
            "llm_intent_rejected",
            extra={"intent": intent_value, "confidence": confidence},
        )
        return None
    return intent_value


def _coerce_confidence(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _valid_intents() -> set[str]:
    return {
        "admin_faq",
        "view_appointments",
        "book_appointment",
        "cancel_appointment",
        "reschedule_appointment",
        "medical_question",
        "emergency",
        "discount_request",
        "non_standard_service",
        "angry_user",
        "unknown",
    }


def _intent_router_system_prompt() -> str:
    return (
        "You are an intent router for a dental clinic Telegram assistant. "
        "Return only JSON with keys: intent, confidence, reason. "
        "Allowed intents: admin_faq, view_appointments, book_appointment, "
        "cancel_appointment, reschedule_appointment, medical_question, emergency, "
        "discount_request, non_standard_service, angry_user, unknown. "
        "Use view_appointments when the user asks whether they already have "
        "appointments or asks to show existing appointments. "
        "Use book_appointment only when the user wants to create a new appointment. "
        "Use admin_faq for clinic administrative questions: prices, address, "
        "schedule, doctors, services, contacts. "
        "Use medical_question when the user asks for diagnosis, medicine, dosage, "
        "or treatment advice. Use emergency for urgent symptoms or bleeding. "
        "Use unknown only when the message is empty or impossible to classify. "
        "Confidence must be a number from 0 to 1."
    )


def _intent_router_user_prompt(
    *,
    text: str,
    language: str | None,
    current_flow: str | None,
    current_state: str | None,
) -> str:
    return "\n".join(
        [
            f"Language: {language or 'unknown'}",
            f"Current flow: {current_flow or 'none'}",
            f"Current state: {current_state or 'none'}",
            "User message:",
            text,
        ]
    )
