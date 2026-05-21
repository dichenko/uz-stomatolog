import json
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from sqlalchemy.ext.asyncio import AsyncSession

from app.admin.settings_reader import get_clinic_info
from app.db.models import Appointment, Conversation, Message, User
from app.db.repositories import AppointmentRepository, MessageRepository

LLM_RECENT_MESSAGE_LIMIT = 10

ChatRole = Literal["system", "user", "assistant"]


@dataclass(frozen=True)
class ChatContextMessage:
    role: ChatRole
    content: str


@dataclass(frozen=True)
class LlmContext:
    user_profile: str
    clinic_info: str
    recent_messages: list[ChatContextMessage]
    appointment_history: str


async def build_llm_context(
    *,
    session: AsyncSession,
    user: User,
    conversation: Conversation,
    exclude_message_id: int | None = None,
    recent_message_limit: int = LLM_RECENT_MESSAGE_LIMIT,
) -> LlmContext:
    recent_messages = await MessageRepository(session).get_recent_for_conversation(
        conversation_id=conversation.id,
        limit=recent_message_limit,
        exclude_message_id=exclude_message_id,
    )
    appointments = await AppointmentRepository(session).get_all_by_user_with_history(
        user_id=user.id
    )
    clinic_info = await get_clinic_info(session)
    return LlmContext(
        user_profile=_format_user_profile(user),
        clinic_info=clinic_info.strip(),
        recent_messages=[
            _message_to_context_message(message) for message in recent_messages
        ],
        appointment_history=_format_appointments(appointments),
    )


def build_openai_context_messages(
    context: LlmContext | None,
) -> list[dict[str, str]]:
    if context is None:
        return []

    messages: list[dict[str, str]] = []
    system_sections: list[str] = []
    if context.user_profile:
        system_sections.append(
            "Known user profile from database:\n"
            f"{context.user_profile}"
        )
    if context.clinic_info:
        system_sections.append(
            "Clinic reference from admin settings:\n"
            f"{context.clinic_info}"
        )
    if context.appointment_history:
        system_sections.append(
            "Full user appointment history:\n"
            f"{context.appointment_history}"
        )
    if system_sections:
        messages.append(
            {
                "role": "system",
                "content": (
                    "Use this database context when answering. "
                    "Do not expose internal IDs unless the user explicitly "
                    "needs them.\n\n"
                    + "\n\n".join(system_sections)
                ),
            }
        )

    if context.recent_messages:
        messages.append(
            {
                "role": "system",
                "content": (
                    "The following messages are recent chat history. "
                    "Use them for continuity, but the final user message is the "
                    "current request."
                ),
            }
        )
        messages.extend(
            {"role": message.role, "content": message.content}
            for message in context.recent_messages
        )
    return messages


def _format_user_profile(user: User) -> str:
    lines: list[str] = []
    if user.patient_name:
        lines.append(f"patient_name={user.patient_name}")
    if user.primary_phone:
        lines.append(f"primary_phone={user.primary_phone}")
    if user.telegram_user_id:
        lines.append(f"telegram_user_id={user.telegram_user_id}")
    if user.telegram_username:
        lines.append(f"telegram_username={user.telegram_username}")
    return "\n".join(lines)


def _message_to_context_message(message: Message) -> ChatContextMessage:
    role: ChatRole = "assistant" if message.direction == "out" else "user"
    created_at = _format_datetime(message.created_at)
    content = (message.text or "").strip()
    prefix = f"[{created_at}; {message.message_type}]"
    return ChatContextMessage(role=role, content=f"{prefix} {content}")


def _format_appointments(appointments: list[Appointment]) -> str:
    if not appointments:
        return "No appointments found for this user."

    blocks: list[str] = []
    for appointment in appointments:
        lines = [
            (
                f"- Appointment #{appointment.id}: status={appointment.status}, "
                f"service={appointment.service_type}, doctor={appointment.doctor_type}"
            ),
            (
                f"  time={_format_datetime(appointment.start_at)}"
                f" to {_format_datetime(appointment.end_at)}, "
                f"timezone={appointment.timezone}"
            ),
            (
                f"  patient={appointment.patient_name}, "
                f"phone={appointment.primary_phone}"
            ),
        ]
        if appointment.calendar_event_id:
            lines.append(f"  calendar_event_id={appointment.calendar_event_id}")
        if appointment.cancelled_at is not None:
            lines.append(f"  cancelled_at={_format_datetime(appointment.cancelled_at)}")
        if appointment.conversation_summary:
            lines.append(f"  summary={appointment.conversation_summary}")

        history = sorted(
            appointment.history,
            key=lambda item: (item.created_at, item.id),
        )
        if history:
            lines.append("  changes:")
            for item in history:
                lines.append(
                    "  "
                    f"- {_format_datetime(item.created_at)} "
                    f"action={item.action}, actor={item.actor}, "
                    f"old={_json_compact(item.old_data)}, "
                    f"new={_json_compact(item.new_data)}"
                )
        blocks.append("\n".join(lines))

    return "\n".join(blocks)


def _format_datetime(value: datetime | None) -> str:
    if value is None:
        return "-"
    return value.isoformat()


def _json_compact(value: object) -> str:
    if value is None:
        return "{}"
    return json.dumps(value, ensure_ascii=False, default=str, separators=(",", ":"))
