import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.calendar import (
    CalendarConfigError,
    CalendarEventCreate,
    GoogleCalendarService,
    calendar_events_to_busy_events,
    create_google_calendar_service,
    find_available_slots,
    is_slot_available,
)
from app.config import Settings, get_settings
from app.db.models import Appointment, Conversation, User
from app.db.repositories import (
    AppointmentRepository,
    ConversationRepository,
    ReminderRepository,
    UserRepository,
)
from app.services.admin_notify import send_admin_notification
from app.telegram.texts import Language

BOOKING_FLOW = "booking"
STATE_COLLECTING = "collecting_patient"
STATE_SELECT_SLOT = "select_slot"


@dataclass(frozen=True)
class BookingMessageResult:
    text: str
    missing_fields: list[str]
    proposed_slots: list[dict[str, Any]]
    service_type: str
    doctor_type: str


@dataclass(frozen=True)
class BookingConfirmationResult:
    text: str
    appointment: Appointment
    calendar_event_id: str | None
    admin_notification_sent: bool


async def handle_booking_message(
    *,
    session: AsyncSession,
    user: User,
    conversation: Conversation,
    input_text: str,
    language: Language,
    service_type: str,
    doctor_type: str,
    calendar_service: GoogleCalendarService | None = None,
    now: datetime | None = None,
) -> BookingMessageResult:
    draft = _load_booking_draft(conversation)
    draft["service_type"] = draft.get("service_type") or service_type
    draft["doctor_type"] = draft.get("doctor_type") or doctor_type
    _merge_patient_data(draft, input_text)

    missing_fields = _missing_booking_fields(draft)
    if missing_fields:
        await _save_booking_draft(
            session=session,
            conversation=conversation,
            draft=draft,
            state=STATE_COLLECTING,
        )
        return BookingMessageResult(
            text=_missing_fields_text(language, missing_fields),
            missing_fields=missing_fields,
            proposed_slots=[],
            service_type=draft["service_type"],
            doctor_type=draft["doctor_type"],
        )

    slots = await _propose_slots(
        calendar_service=calendar_service,
        service_type=draft["service_type"],
        doctor_type=draft["doctor_type"],
        now=now,
    )
    draft["proposed_slots"] = slots
    await _save_booking_draft(
        session=session,
        conversation=conversation,
        draft=draft,
        state=STATE_SELECT_SLOT,
    )
    return BookingMessageResult(
        text=_slot_proposal_text(language),
        missing_fields=[],
        proposed_slots=slots,
        service_type=draft["service_type"],
        doctor_type=draft["doctor_type"],
    )


async def confirm_booking_slot(
    *,
    session: AsyncSession,
    user: User,
    conversation: Conversation,
    slot_index: int,
    language: Language,
    calendar_service: GoogleCalendarService | None = None,
    admin_bot: Any | None = None,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> BookingConfirmationResult:
    draft = _load_booking_draft(conversation)
    slots = draft.get("proposed_slots") or []
    if slot_index < 0 or slot_index >= len(slots):
        raise BookingFlowError("Selected slot is not available in current draft")

    slot = slots[slot_index]
    start_at = datetime.fromisoformat(slot["start_at"])
    end_at = datetime.fromisoformat(slot["end_at"])
    service_type = str(draft["service_type"])
    doctor_type = str(draft["doctor_type"])
    timezone = str(slot.get("timezone") or get_settings().app_timezone)

    resolved_calendar_service = _resolve_calendar_service(calendar_service)
    if resolved_calendar_service is not None:
        busy_events = calendar_events_to_busy_events(
            await resolved_calendar_service.list_events(
                time_min=start_at - timedelta(minutes=1),
                time_max=end_at + timedelta(minutes=1),
            )
        )
        if not is_slot_available(
            busy_events=busy_events,
            start_at=start_at,
            end_at=end_at,
            doctor_type=doctor_type,
            timezone=timezone,
        ):
            raise BookingSlotConflictError("Selected slot is no longer available")

    appointment = await AppointmentRepository(session).create(
        user_id=user.id,
        service_type=service_type,
        doctor_type=doctor_type,
        start_at=start_at,
        end_at=end_at,
        timezone=timezone,
        patient_name=str(draft["patient_name"]),
        primary_phone=str(draft["phone"]),
        conversation_summary=_booking_summary(draft),
        created_trace_id=str(draft.get("trace_id") or ""),
    )
    await UserRepository(session).add_phone(
        user_id=user.id,
        phone=str(draft["phone"]),
        is_primary=True,
        source="booking",
    )

    calendar_event_id: str | None = None
    if resolved_calendar_service is not None:
        event = await resolved_calendar_service.create_event(
            CalendarEventCreate(
                service_type=service_type,
                doctor_type=doctor_type,
                start_at=start_at,
                end_at=end_at,
                timezone=timezone,
                patient_name=str(draft["patient_name"]),
                phone=str(draft["phone"]),
                telegram_user_id=user.telegram_user_id,
                telegram_username=user.telegram_username,
                language=language,
                conversation_summary=_booking_summary(draft),
                appointment_id=appointment.id,
                trace_id=str(draft.get("trace_id") or ""),
            )
        )
        calendar_event_id = event.get("id")
        appointment.calendar_event_id = calendar_event_id
        appointment.calendar_etag = event.get("etag")

    await _schedule_booking_reminders(session, appointment, now=now)
    notification = await send_admin_notification(
        bot=admin_bot,
        message_text=_booking_admin_notification(
            user=user,
            appointment=appointment,
            calendar_event_id=calendar_event_id,
        ),
        settings=settings,
    )
    await ConversationRepository(session).update_state(
        conversation_id=conversation.id,
        current_flow=None,
        current_state=None,
        summary=_booking_summary(draft),
    )
    return BookingConfirmationResult(
        text=_booking_confirmation_text(language, appointment),
        appointment=appointment,
        calendar_event_id=calendar_event_id,
        admin_notification_sent=notification.sent,
    )


def is_booking_in_progress(conversation: Conversation) -> bool:
    return conversation.current_flow == BOOKING_FLOW


def _load_booking_draft(conversation: Conversation) -> dict[str, Any]:
    if conversation.current_flow != BOOKING_FLOW or not conversation.summary:
        return {}
    try:
        payload = json.loads(conversation.summary)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


async def _save_booking_draft(
    *,
    session: AsyncSession,
    conversation: Conversation,
    draft: dict[str, Any],
    state: str,
) -> None:
    await ConversationRepository(session).update_state(
        conversation_id=conversation.id,
        current_flow=BOOKING_FLOW,
        current_state=state,
        summary=json.dumps(draft, ensure_ascii=False),
    )


def _merge_patient_data(draft: dict[str, Any], input_text: str) -> None:
    phone = _extract_phone(input_text)
    if phone:
        draft["phone"] = phone
    patient_name = _extract_patient_name(input_text, phone)
    if patient_name:
        draft["patient_name"] = patient_name


def _extract_phone(input_text: str) -> str | None:
    match = re.search(r"(?:\+?\d[\d\s().-]{7,}\d)", input_text)
    if match is None:
        return None
    return re.sub(r"[^\d+]", "", match.group(0))


def _extract_patient_name(input_text: str, phone: str | None) -> str | None:
    if phone is None and _looks_like_initial_booking_request(input_text):
        return None
    candidate = input_text
    if phone:
        candidate = re.sub(r"(?:\+?\d[\d\s().-]{7,}\d)", " ", candidate)
    candidate = re.sub(
        r"\b(book|appointment|cleaning|consultation|treatment|phone|for)\b",
        " ",
        candidate,
        flags=re.IGNORECASE,
    )
    candidate = re.sub(r"\s+", " ", candidate).strip(" ,.;:-")
    if not candidate:
        return None
    words = candidate.split()
    if len(words) > 4:
        return None
    return candidate


def _looks_like_initial_booking_request(input_text: str) -> bool:
    normalized = input_text.casefold()
    return any(
        keyword in normalized
        for keyword in (
            "book",
            "appointment",
            "cleaning",
            "consultation",
            "treatment",
            "запис",
            "прием",
            "приём",
            "qabul",
            "yozil",
        )
    )


def _missing_booking_fields(draft: dict[str, Any]) -> list[str]:
    missing: list[str] = []
    if not draft.get("patient_name"):
        missing.append("patient_name")
    if not draft.get("phone"):
        missing.append("phone")
    return missing


async def _propose_slots(
    *,
    calendar_service: GoogleCalendarService | None,
    service_type: str,
    doctor_type: str,
    now: datetime | None,
) -> list[dict[str, Any]]:
    current_time = now or datetime.now(ZoneInfo("Asia/Tashkent"))
    resolved_calendar_service = _resolve_calendar_service(calendar_service)
    busy_events = []
    if resolved_calendar_service is not None:
        events = await resolved_calendar_service.list_events(
            time_min=current_time,
            time_max=current_time + timedelta(days=14),
        )
        busy_events = calendar_events_to_busy_events(events)
    return [
        slot.as_dict()
        for slot in find_available_slots(
            busy_events=busy_events,
            service_type=service_type,
            doctor_type=doctor_type,
            start_from=current_time,
            limit=3,
        )
    ]


def _resolve_calendar_service(
    calendar_service: GoogleCalendarService | None,
) -> GoogleCalendarService | None:
    if calendar_service is not None:
        return calendar_service
    try:
        return create_google_calendar_service()
    except CalendarConfigError:
        return None


async def _schedule_booking_reminders(
    session: AsyncSession,
    appointment: Appointment,
    now: datetime | None = None,
) -> None:
    reminders = ReminderRepository(session)
    day_before = appointment.start_at - timedelta(hours=24)
    two_hours_before = appointment.start_at - timedelta(hours=2)
    resolved_now = now if now is not None else datetime.now(UTC)
    if day_before > resolved_now:
        await reminders.create(
            appointment_id=appointment.id,
            reminder_type="day_before",
            send_at=day_before,
        )
    if two_hours_before > resolved_now:
        await reminders.create(
            appointment_id=appointment.id,
            reminder_type="two_hours_before",
            send_at=two_hours_before,
        )


def _missing_fields_text(language: Language, missing_fields: list[str]) -> str:
    if missing_fields == ["patient_name"]:
        return {
            "ru": "Напишите, пожалуйста, имя пациента.",
            "uz": "Iltimos, bemor ismini yozing.",
            "en": "Please send the patient's name.",
        }[language]
    if missing_fields == ["phone"]:
        return {
            "ru": "Напишите номер телефона или отправьте контакт.",
            "uz": "Telefon raqamingizni yozing yoki kontakt yuboring.",
            "en": "Please send a phone number or share a contact.",
        }[language]
    return {
        "ru": "Напишите, пожалуйста, имя пациента и номер телефона.",
        "uz": "Iltimos, bemor ismi va telefon raqamini yozing.",
        "en": "Please send the patient's name and phone number.",
    }[language]


def _slot_proposal_text(language: Language) -> str:
    return {
        "ru": "Выберите удобное время:",
        "uz": "Qulay vaqtni tanlang:",
        "en": "Please choose a convenient time:",
    }[language]


def _booking_confirmation_text(language: Language, appointment: Appointment) -> str:
    start = appointment.start_at.astimezone(ZoneInfo(appointment.timezone))
    formatted = start.strftime("%Y-%m-%d %H:%M")
    return {
        "ru": f"Запись подтверждена: {formatted}. Ждём вас в клинике.",
        "uz": f"Qabul tasdiqlandi: {formatted}. Sizni klinikada kutamiz.",
        "en": f"Appointment confirmed: {formatted}. We will see you at the clinic.",
    }[language]


def _booking_summary(draft: dict[str, Any]) -> str:
    return (
        f"Booking: {draft.get('service_type')} with {draft.get('doctor_type')}; "
        f"patient={draft.get('patient_name')}; phone={draft.get('phone')}"
    )


def _booking_admin_notification(
    *,
    user: User,
    appointment: Appointment,
    calendar_event_id: str | None,
) -> str:
    username = f"@{user.telegram_username}" if user.telegram_username else "-"
    return "\n".join(
        [
            "New appointment created",
            "",
            "Patient:",
            f"Name: {appointment.patient_name}",
            f"Phone: {appointment.primary_phone}",
            f"Telegram: {username} / id {user.telegram_user_id}",
            "",
            "Appointment:",
            f"Service: {appointment.service_type}",
            f"Doctor: {appointment.doctor_type}",
            f"Time: {appointment.start_at.isoformat()}",
            f"Duration: {_appointment_duration_minutes(appointment)} minutes",
            "",
            "Conversation summary:",
            appointment.conversation_summary or "-",
            "",
            f"Calendar event: {calendar_event_id or '-'}",
        ]
    )


def _appointment_duration_minutes(appointment: Appointment) -> int:
    return int((appointment.end_at - appointment.start_at).total_seconds() // 60)


class BookingFlowError(RuntimeError):
    pass


class BookingSlotConflictError(BookingFlowError):
    pass
