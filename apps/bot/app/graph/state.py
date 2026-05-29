from dataclasses import dataclass
from typing import Any, Literal, NotRequired, TypedDict

from app.telegram.texts import Language

InputType = Literal["text", "voice"]
SafetyStatus = Literal["safe", "medical_advice", "emergency", "needs_escalation"]


class BotState(TypedDict):
    trace_id: str
    telegram_user_id: int
    telegram_chat_id: int
    input_text: str
    input_type: InputType
    input_message_id: int | None
    preferred_language: Language
    telegram_profile: dict[str, Any]

    user_profile: dict[str, Any] | None
    conversation_summary: str | None

    intent: str | None
    safety_status: str | None

    service_type: str | None
    doctor_type: str | None
    requested_date: str | None
    requested_time_of_day: str | None

    proposed_slots: list[dict[str, Any]]
    selected_slot: dict[str, Any] | None
    active_appointments: list[dict[str, Any]]

    missing_fields: list[str]

    final_response_text: str | None
    should_generate_voice: bool

    should_escalate: bool
    escalation_reason: str | None
    escalation_id: int | None
    escalation_phone: str | None

    admin_notification_sent: bool
    admin_message_id: int | None
    tool_calls: list[dict[str, Any]]

    faq_answered: NotRequired[bool]
    faq_source: NotRequired[str]
    owner_sales_stage: NotRequired[str | None]
    owner_name: NotRequired[str | None]
    owner_clinic_name: NotRequired[str | None]
    owner_locations: NotRequired[int | None]
    owner_contact: NotRequired[str | None]
    owner_phone: NotRequired[str | None]


@dataclass(frozen=True)
class GraphResult:
    final_response_text: str
    intent: str | None
    safety_status: str | None
    should_generate_voice: bool
    should_escalate: bool
    proposed_slots: list[dict[str, Any]]
    metadata: dict[str, Any]
