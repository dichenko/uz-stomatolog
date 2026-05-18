import logging
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Conversation, User
from app.graph.intents import classify_intent_text
from app.graph.state import BotState
from app.services.clinic_knowledge import get_clinic_knowledge
from app.services.faq import generate_admin_faq_answer
from app.telegram.texts import Language, text

logger = logging.getLogger(__name__)


def build_nodes(*, session: AsyncSession, user: User, conversation: Conversation):
    async def load_user_context(state: BotState) -> dict[str, Any]:
        logger.info(
            "graph_node_started",
            extra={"trace_id": state["trace_id"], "node": "load_user_context"},
        )
        return {
            "user_profile": {
                "id": user.id,
                "telegram_user_id": user.telegram_user_id,
                "telegram_username": user.telegram_username,
                "telegram_first_name": user.telegram_first_name,
                "telegram_last_name": user.telegram_last_name,
                "preferred_language": user.preferred_language,
            },
            "conversation_summary": conversation.summary,
            "tool_calls": [
                *state["tool_calls"],
                {"tool": "get_user_profile", "status": "success"},
            ],
        }

    async def classify_intent(state: BotState) -> dict[str, Any]:
        intent = classify_intent_text(state["input_text"])
        logger.info(
            "graph_node_completed",
            extra={
                "trace_id": state["trace_id"],
                "node": "classify_intent",
                "intent": intent,
            },
        )
        return {"intent": intent}

    async def safety_guard(state: BotState) -> dict[str, Any]:
        intent = state["intent"]
        if intent == "medical_question":
            safety_status = "medical_advice"
        elif intent == "emergency":
            safety_status = "emergency"
        elif intent in {"discount_request", "non_standard_service", "angry_user"}:
            safety_status = "needs_escalation"
        else:
            safety_status = "safe"
        logger.info(
            "graph_node_completed",
            extra={
                "trace_id": state["trace_id"],
                "node": "safety_guard",
                "safety_status": safety_status,
            },
        )
        return {"safety_status": safety_status}

    async def admin_faq(state: BotState) -> dict[str, Any]:
        language = state["preferred_language"]
        knowledge = await get_clinic_knowledge(session, language)
        answer = await generate_admin_faq_answer(
            question=state["input_text"],
            language=language,
            knowledge=knowledge,
        )
        return {
            "final_response_text": answer.text,
            "faq_answered": answer.answered,
            "faq_source": answer.source,
            "tool_calls": [
                *state["tool_calls"],
                {"tool": "get_clinic_knowledge", "status": "success"},
            ],
        }

    async def start_booking(state: BotState) -> dict[str, Any]:
        language = state["preferred_language"]
        return {
            "service_type": _detect_service_type(state["input_text"]),
            "doctor_type": _detect_doctor_type(state["input_text"]),
            "missing_fields": ["patient_name", "phone"],
            "final_response_text": _booking_start_text(language),
        }

    async def cancel_appointment(state: BotState) -> dict[str, Any]:
        language = state["preferred_language"]
        return {
            "final_response_text": _not_ready_text(language, "cancel"),
        }

    async def reschedule_appointment(state: BotState) -> dict[str, Any]:
        language = state["preferred_language"]
        return {
            "final_response_text": _not_ready_text(language, "reschedule"),
        }

    async def emergency_or_escalation(state: BotState) -> dict[str, Any]:
        language = state["preferred_language"]
        reason = state["intent"] or "unknown"
        return {
            "should_escalate": True,
            "escalation_reason": reason,
            "missing_fields": ["phone"],
            "final_response_text": _escalation_text(language),
        }

    async def fallback(state: BotState) -> dict[str, Any]:
        language = state["preferred_language"]
        return {
            "final_response_text": text("fallback", language),
        }

    return {
        "load_user_context": load_user_context,
        "classify_intent": classify_intent,
        "safety_guard": safety_guard,
        "admin_faq": admin_faq,
        "start_booking": start_booking,
        "continue_booking": start_booking,
        "cancel_appointment": cancel_appointment,
        "reschedule_appointment": reschedule_appointment,
        "emergency_or_escalation": emergency_or_escalation,
        "fallback": fallback,
    }


def route_intent(state: BotState) -> str:
    intent = state["intent"]
    safety_status = state["safety_status"]
    if safety_status in {"emergency", "needs_escalation"}:
        return "emergency_or_escalation"
    if intent == "medical_question":
        return "admin_faq"
    if intent == "book_appointment":
        return "start_booking"
    if intent == "cancel_appointment":
        return "cancel_appointment"
    if intent == "reschedule_appointment":
        return "reschedule_appointment"
    if intent == "admin_faq":
        return "admin_faq"
    return "fallback"


def _detect_service_type(input_text: str) -> str:
    normalized = input_text.casefold()
    if any(keyword in normalized for keyword in ("чист", "cleaning", "tozal")):
        return "cleaning"
    surgical_keywords = ("удал", "хирург", "surgeon", "jarroh")
    if any(keyword in normalized for keyword in surgical_keywords):
        return "consultation"
    if any(keyword in normalized for keyword in ("леч", "treatment", "davol")):
        return "treatment"
    return "consultation"


def _detect_doctor_type(input_text: str) -> str:
    normalized = input_text.casefold()
    surgical_keywords = ("удал", "хирург", "surgeon", "jarroh")
    if any(keyword in normalized for keyword in surgical_keywords):
        return "surgeon"
    return "therapist"


def _booking_start_text(language: Language) -> str:
    return {
        "ru": (
            "Могу начать запись на консультацию. Напишите, пожалуйста, "
            "имя пациента и номер телефона."
        ),
        "uz": (
            "Qabulga yozishni boshlayman. Iltimos, bemor ismi va telefon "
            "raqamini yozing."
        ),
        "en": "I can start booking. Please send the patient's name and phone number.",
    }[language]


def _not_ready_text(language: Language, flow: str) -> str:
    messages = {
        "cancel": {
            "ru": (
                "Отмена записи будет подключена на следующем этапе. "
                "Пока напишите администратору."
            ),
            "uz": (
                "Yozuvni bekor qilish keyingi bosqichda ulanadi. "
                "Hozircha administratorga yozing."
            ),
            "en": (
                "Cancellation will be connected in the next stage. "
                "For now, please contact an administrator."
            ),
        },
        "reschedule": {
            "ru": (
                "Перенос записи будет подключён на следующем этапе. "
                "Пока напишите администратору."
            ),
            "uz": (
                "Yozuvni ko'chirish keyingi bosqichda ulanadi. "
                "Hozircha administratorga yozing."
            ),
            "en": (
                "Rescheduling will be connected in the next stage. "
                "For now, please contact an administrator."
            ),
        },
    }
    return messages[flow][language]


def _escalation_text(language: Language) -> str:
    return {
        "ru": (
            "Передам ситуацию администратору. "
            "Напишите, пожалуйста, ваш номер телефона."
        ),
        "uz": (
            "Vaziyatni administratorga yuboraman. "
            "Iltimos, telefon raqamingizni yozing."
        ),
        "en": "I will pass this to an administrator. Please send your phone number.",
    }[language]
