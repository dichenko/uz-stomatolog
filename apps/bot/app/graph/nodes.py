import logging
import re
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.calendar import GoogleCalendarService
from app.db.models import Conversation, User
from app.db.repositories import (
    AppointmentRepository,
    ConversationRepository,
    EscalationRepository,
)
from app.graph.intents import classify_intent as classify_user_intent
from app.graph.state import BotState
from app.services.admin_notify import send_admin_notification
from app.services.booking import handle_booking_message, is_booking_in_progress
from app.services.cancellation import handle_cancellation_message
from app.services.clinic_knowledge import get_clinic_knowledge
from app.services.faq import generate_admin_faq_answer
from app.services.owner_sales import (
    handle_owner_sales_message,
    is_owner_sales_in_progress,
)
from app.services.rescheduling import (
    handle_reschedule_message,
    is_rescheduling_in_progress,
)
from app.telegram.texts import Language, text

logger = logging.getLogger(__name__)


def build_nodes(
    *,
    session: AsyncSession,
    user: User,
    conversation: Conversation,
    admin_bot: Any | None = None,
    calendar_service: GoogleCalendarService | None = None,
):
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
                "patient_name": user.patient_name,
                "primary_phone": user.primary_phone,
            },
            "conversation_summary": conversation.summary,
            "tool_calls": [
                *state["tool_calls"],
                {"tool": "get_user_profile", "status": "success"},
            ],
        }

    async def classify_intent(state: BotState) -> dict[str, Any]:
        text_intent = await classify_user_intent(
            state["input_text"],
            language=state["preferred_language"],
            current_flow=conversation.current_flow,
            current_state=conversation.current_state,
        )
        if is_owner_sales_in_progress(conversation):
            intent = "owner_sales"
        elif is_booking_in_progress(conversation):
            exit_intents = (
                "admin_faq",
                "owner_sales",
                "view_appointments",
                "cancel_appointment",
                "reschedule_appointment",
            )
            if text_intent in exit_intents:
                await ConversationRepository(session).update_state(
                    conversation_id=conversation.id,
                    current_flow=None,
                    current_state=None,
                    summary=None,
                )
                intent = text_intent
            else:
                intent = "book_appointment"
        elif is_rescheduling_in_progress(conversation):
            if text_intent in ("admin_faq", "view_appointments", "cancel_appointment"):
                await ConversationRepository(session).update_state(
                    conversation_id=conversation.id,
                    current_flow=None,
                    current_state=None,
                    summary=None,
                )
                intent = text_intent
            else:
                intent = "reschedule_appointment"
        else:
            intent = text_intent
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
            session=session,
            user=user,
            conversation=conversation,
            input_message_id=state["input_message_id"],
        )
        escalation_payload: dict[str, Any] = {}
        if not answer.answered:
            escalation_payload = await _create_escalation_and_notify(
                state=state,
                user=user,
                conversation=conversation,
                session=session,
                admin_bot=admin_bot,
                reason="unknown",
            )
        return {
            "final_response_text": answer.text,
            "faq_answered": answer.answered,
            "faq_source": answer.source,
            "tool_calls": [
                *state["tool_calls"],
                {"tool": "get_clinic_knowledge", "status": "success"},
                *escalation_payload.pop("tool_calls", []),
            ],
            **escalation_payload,
        }

    async def owner_sales(state: BotState) -> dict[str, Any]:
        language = state["preferred_language"]
        result = await handle_owner_sales_message(
            session=session,
            user=user,
            conversation=conversation,
            input_text=state["input_text"],
            language=language,
            admin_bot=admin_bot,
        )
        return {
            "final_response_text": result.text,
            "owner_sales_stage": result.stage,
            "owner_name": result.owner_name,
            "owner_clinic_name": result.clinic_name,
            "owner_locations": result.locations,
            "owner_contact": result.owner_contact,
            "owner_phone": result.phone,
            "admin_notification_sent": (
                state["admin_notification_sent"]
                or result.admin_notification_sent
            ),
            "admin_message_id": (
                result.admin_message_id or state["admin_message_id"]
            ),
            "tool_calls": [
                *state["tool_calls"],
                *result.tool_calls,
            ],
        }

    async def start_booking(state: BotState) -> dict[str, Any]:
        language = state["preferred_language"]
        result = await handle_booking_message(
            session=session,
            user=user,
            conversation=conversation,
            input_text=state["input_text"],
            language=language,
            service_type=_detect_service_type(state["input_text"]),
            doctor_type=_detect_doctor_type(state["input_text"]),
            calendar_service=calendar_service,
        )
        return {
            "service_type": result.service_type,
            "doctor_type": result.doctor_type,
            "missing_fields": result.missing_fields,
            "proposed_slots": result.proposed_slots,
            "final_response_text": result.text,
            "tool_calls": [
                *state["tool_calls"],
                {
                    "tool": "find_available_slots",
                    "status": "success" if result.proposed_slots else "skipped",
                },
            ],
        }

    async def cancel_appointment(state: BotState) -> dict[str, Any]:
        language = state["preferred_language"]
        result = await handle_cancellation_message(
            session=session,
            user=user,
            language=language,
        )
        return {
            "final_response_text": result.text,
            "active_appointments": result.active_appointments,
            "tool_calls": [
                *state["tool_calls"],
                {"tool": "find_user_appointments", "status": "success"},
            ],
        }

    async def view_appointments(state: BotState) -> dict[str, Any]:
        language = state["preferred_language"]
        appointments = await AppointmentRepository(session).get_active_future_by_user(
            user_id=user.id
        )
        if not appointments:
            response_text = text("appointments_empty", language)
            active_appointments = []
        else:
            lines = [text("appointments_header", language)]
            active_appointments = []
            for appointment in appointments:
                active_appointments.append(_appointment_to_dict(appointment))
                start = appointment.start_at.strftime("%Y-%m-%d %H:%M")
                lines.append(
                    f"- {start}: {appointment.service_type}, "
                    f"{appointment.doctor_type}"
                )
            response_text = "\n".join(lines)
        return {
            "final_response_text": response_text,
            "active_appointments": active_appointments,
            "tool_calls": [
                *state["tool_calls"],
                {"tool": "find_user_appointments", "status": "success"},
            ],
        }

    async def reschedule_appointment(state: BotState) -> dict[str, Any]:
        language = state["preferred_language"]
        result = await handle_reschedule_message(
            session=session,
            user=user,
            language=language,
        )
        return {
            "final_response_text": result.text,
            "active_appointments": result.active_appointments,
            "tool_calls": [
                *state["tool_calls"],
                {"tool": "find_user_appointments", "status": "success"},
            ],
        }

    async def emergency_or_escalation(state: BotState) -> dict[str, Any]:
        language = state["preferred_language"]
        reason = state["intent"] or "unknown"
        escalation_payload = await _create_escalation_and_notify(
            state=state,
            user=user,
            conversation=conversation,
            session=session,
            admin_bot=admin_bot,
            reason=reason,
        )
        has_phone = escalation_payload["escalation_phone"] is not None
        return {
            "final_response_text": _escalation_text(language, has_phone=has_phone),
            **escalation_payload,
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
        "owner_sales": owner_sales,
        "view_appointments": view_appointments,
        "start_booking": start_booking,
        "continue_booking": start_booking,
        "cancel_appointment": cancel_appointment,
        "reschedule_appointment": reschedule_appointment,
        "emergency_or_escalation": emergency_or_escalation,
        "fallback": fallback,
    }


async def _create_escalation_and_notify(
    *,
    state: BotState,
    user: User,
    conversation: Conversation,
    session: AsyncSession,
    admin_bot: Any | None,
    reason: str,
) -> dict[str, Any]:
    phone = _extract_phone(state["input_text"])
    escalation = await EscalationRepository(session).create(
        user_id=user.id,
        reason=reason,
        summary=_build_escalation_summary(state),
        phone=phone,
    )
    notification = await send_admin_notification(
        bot=admin_bot,
        message_text=_build_admin_notification_text(
            escalation_id=escalation.id,
            reason=reason,
            state=state,
            user=user,
            conversation=conversation,
            phone=phone,
        ),
    )
    if notification.admin_chat_id is not None:
        escalation.admin_chat_id = notification.admin_chat_id
    if notification.admin_message_id is not None:
        escalation.admin_message_id = notification.admin_message_id
    await session.flush()

    missing_fields = [] if phone else ["phone"]
    return {
        "should_escalate": True,
        "escalation_reason": reason,
        "escalation_id": escalation.id,
        "escalation_phone": phone,
        "missing_fields": missing_fields,
        "admin_notification_sent": notification.sent,
        "admin_message_id": notification.admin_message_id,
        "tool_calls": [
            {"tool": "create_escalation", "status": "success"},
            {
                "tool": "send_admin_notification",
                "status": "success" if notification.sent else "skipped",
            },
        ],
    }


def _extract_phone(text_value: str) -> str | None:
    match = re.search(r"(?:\+?\d[\d\s().-]{7,}\d)", text_value)
    if match is None:
        return None
    return re.sub(r"[^\d+]", "", match.group(0))


def _build_escalation_summary(state: BotState) -> str:
    return (
        f"Reason: {state['intent'] or 'unknown'}\n"
        f"Safety: {state['safety_status'] or 'unknown'}\n"
        f"Message: {state['input_text']}"
    )


def _build_admin_notification_text(
    *,
    escalation_id: int,
    reason: str,
    state: BotState,
    user: User,
    conversation: Conversation,
    phone: str | None,
) -> str:
    username = f"@{user.telegram_username}" if user.telegram_username else "-"
    return "\n".join(
        [
            "Escalation required",
            "",
            f"Escalation ID: {escalation_id}",
            f"Reason: {reason}",
            "",
            "Patient:",
            f"Telegram: {username} / id {user.telegram_user_id}",
            f"Phone: {phone or '-'}",
            f"Language: {state['preferred_language']}",
            "",
            "User message:",
            state["input_text"],
            "",
            "Conversation summary:",
            conversation.summary or "-",
            "",
            f"Trace ID: {state['trace_id']}",
        ]
    )


def route_intent(state: BotState) -> str:
    intent = state["intent"]
    safety_status = state["safety_status"]
    if intent == "owner_sales":
        return "owner_sales"
    if safety_status in {"emergency", "needs_escalation"}:
        return "emergency_or_escalation"
    if intent == "medical_question":
        return "admin_faq"
    if intent == "view_appointments":
        return "view_appointments"
    if intent == "book_appointment":
        return "start_booking"
    if intent == "cancel_appointment":
        return "cancel_appointment"
    if intent == "reschedule_appointment":
        return "reschedule_appointment"
    if intent == "admin_faq":
        return "admin_faq"
    if intent == "unknown":
        return "emergency_or_escalation"
    return "fallback"


def _appointment_to_dict(appointment: Any) -> dict[str, Any]:
    return {
        "id": appointment.id,
        "start_at": appointment.start_at.isoformat(),
        "end_at": appointment.end_at.isoformat(),
        "timezone": appointment.timezone,
        "service_type": appointment.service_type,
        "doctor_type": appointment.doctor_type,
        "patient_name": appointment.patient_name,
        "status": appointment.status,
        "calendar_event_id": appointment.calendar_event_id,
        "formatted": appointment.start_at.strftime("%d.%m %H:%M"),
    }


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


def _escalation_text(language: Language, *, has_phone: bool) -> str:
    if has_phone:
        return {
            "ru": "Передал ситуацию администратору. С вами свяжутся.",
            "uz": "Vaziyatni administratorga yubordim. Siz bilan bog'lanishadi.",
            "en": "I have passed this to an administrator. They will contact you.",
        }[language]
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
