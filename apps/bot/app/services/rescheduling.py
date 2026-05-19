import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.calendar import (
    CalendarConfigError,
    CalendarEventUpdate,
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
)
from app.services.admin_notify import send_admin_notification
from app.telegram.texts import Language

RESCHEDULING_FLOW = "rescheduling"
STATE_SELECT_APPOINTMENT = "select_appointment"
STATE_SELECT_SLOT = "select_slot"


@dataclass(frozen=True)
class RescheduleMessageResult:
    text: str
    active_appointments: list[dict[str, Any]]


@dataclass(frozen=True)
class RescheduleSlotResult:
    text: str
    proposed_slots: list[dict[str, Any]]
    appointment_id: int


@dataclass(frozen=True)
class RescheduleConfirmationResult:
    text: str
    appointment: Appointment
    calendar_event_id: str | None
    calendar_event_updated: bool
    admin_notification_sent: bool


async def handle_reschedule_message(
    *,
    session: AsyncSession,
    user: User,
    language: Language,
) -> RescheduleMessageResult:
    appointments = await AppointmentRepository(session).get_active_future_by_user(
        user_id=user.id,
    )
    if not appointments:
        return RescheduleMessageResult(
            text=_no_appointments_text(language),
            active_appointments=[],
        )

    appointment_dicts = [
        _appointment_to_dict(appointment)
        for appointment in appointments
    ]

    if len(appointments) == 1:
        return RescheduleMessageResult(
            text=_reschedule_single_prompt(language, appointments[0]),
            active_appointments=appointment_dicts,
        )

    return RescheduleMessageResult(
        text=_reschedule_multiple_prompt(language),
        active_appointments=appointment_dicts,
    )


async def propose_reschedule_slots(
    *,
    session: AsyncSession,
    conversation: Conversation,
    appointment_id: int,
    language: Language,
    calendar_service: GoogleCalendarService | None = None,
    now: datetime | None = None,
) -> RescheduleSlotResult:
    appointment = await session.get(Appointment, appointment_id)
    if appointment is None or appointment.status != "scheduled":
        raise ReschedulingError("Appointment not found or not in scheduled status")

    current_time = now or datetime.now(ZoneInfo("Asia/Tashkent"))
    resolved_calendar = _resolve_calendar(calendar_service)
    busy_events = []
    if resolved_calendar is not None:
        events = await resolved_calendar.list_events(
            time_min=current_time,
            time_max=current_time + timedelta(days=14),
        )
        busy_events = calendar_events_to_busy_events(events)

    slots = [
        slot.as_dict()
        for slot in find_available_slots(
            busy_events=busy_events,
            service_type=appointment.service_type,
            doctor_type=appointment.doctor_type,
            start_from=current_time,
            limit=3,
        )
    ]

    draft = {
        "appointment_id": appointment_id,
        "service_type": appointment.service_type,
        "doctor_type": appointment.doctor_type,
        "proposed_slots": slots,
    }
    await _save_draft(
        session=session,
        conversation=conversation,
        draft=draft,
        state=STATE_SELECT_SLOT,
    )

    if not slots:
        return RescheduleSlotResult(
            text=_no_slots_text(language),
            proposed_slots=[],
            appointment_id=appointment_id,
        )

    return RescheduleSlotResult(
        text=_select_new_time_text(language),
        proposed_slots=slots,
        appointment_id=appointment_id,
    )


async def confirm_reschedule_slot(
    *,
    session: AsyncSession,
    user: User,
    conversation: Conversation,
    slot_index: int,
    language: Language,
    calendar_service: GoogleCalendarService | None = None,
    admin_bot: Any | None = None,
    settings: Settings | None = None,
) -> RescheduleConfirmationResult:
    draft = _load_draft(conversation)
    appointment_id = draft.get("appointment_id")
    if appointment_id is None:
        raise ReschedulingError("No rescheduling appointment selected")

    original = await session.get(Appointment, appointment_id)
    if original is None or original.status != "scheduled":
        raise ReschedulingError("Original appointment is no longer active")

    slots = draft.get("proposed_slots") or []
    if slot_index < 0 or slot_index >= len(slots):
        raise ReschedulingError("Selected slot is not available")

    slot = slots[slot_index]
    new_start = datetime.fromisoformat(slot["start_at"])
    new_end = datetime.fromisoformat(slot["end_at"])
    timezone = str(slot.get("timezone") or get_settings().app_timezone)

    resolved_calendar = _resolve_calendar(calendar_service)
    if resolved_calendar is not None:
        busy_events = calendar_events_to_busy_events(
            await resolved_calendar.list_events(
                time_min=new_start - timedelta(minutes=1),
                time_max=new_end + timedelta(minutes=1),
            )
        )
        if not is_slot_available(
            busy_events=busy_events,
            start_at=new_start,
            end_at=new_end,
            doctor_type=original.doctor_type,
            timezone=timezone,
        ):
            raise ReschedulingSlotConflictError("Selected slot is no longer available")

    calendar_event_updated = False
    if resolved_calendar is not None and original.calendar_event_id:
        await resolved_calendar.update_event(
            original.calendar_event_id,
            CalendarEventUpdate(
                start_at=new_start,
                end_at=new_end,
                timezone=timezone,
            ),
        )
        calendar_event_updated = True

    repo = AppointmentRepository(session)
    old_start = original.start_at
    old_end = original.end_at
    original.start_at = new_start
    original.end_at = new_end
    original.timezone = timezone
    await session.flush()
    await repo.add_history(
        appointment_id=original.id,
        action="rescheduled",
        actor="user",
        old_data={
            "start_at": old_start.isoformat(),
            "end_at": old_end.isoformat(),
        },
        new_data={
            "start_at": new_start.isoformat(),
            "end_at": new_end.isoformat(),
        },
    )

    await ReminderRepository(session).cancel_for_appointment(appointment_id)
    await _schedule_reschedule_reminders(session, original)

    notification = await send_admin_notification(
        bot=admin_bot,
        message_text=_reschedule_admin_notification(
            user, original, old_start, old_end, new_start, new_end,
        ),
        settings=settings,
    )

    await ConversationRepository(session).update_state(
        conversation_id=conversation.id,
        current_flow=None,
        current_state=None,
        summary=None,
    )

    return RescheduleConfirmationResult(
        text=_reschedule_confirmation_text(language, original),
        appointment=original,
        calendar_event_id=original.calendar_event_id,
        calendar_event_updated=calendar_event_updated,
        admin_notification_sent=notification.sent,
    )


def is_rescheduling_in_progress(conversation: Conversation) -> bool:
    return conversation.current_flow == RESCHEDULING_FLOW


def _load_draft(conversation: Conversation) -> dict[str, Any]:
    if conversation.current_flow != RESCHEDULING_FLOW or not conversation.summary:
        return {}
    try:
        payload = json.loads(conversation.summary)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


async def _save_draft(
    *,
    session: AsyncSession,
    conversation: Conversation,
    draft: dict[str, Any],
    state: str,
) -> None:
    await ConversationRepository(session).update_state(
        conversation_id=conversation.id,
        current_flow=RESCHEDULING_FLOW,
        current_state=state,
        summary=json.dumps(draft, ensure_ascii=False),
    )


def _appointment_to_dict(appointment: Appointment) -> dict[str, Any]:
    tz = ZoneInfo(appointment.timezone)
    start_at = appointment.start_at.astimezone(tz)
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
        "formatted": start_at.strftime("%d.%m %H:%M"),
    }


async def _schedule_reschedule_reminders(
    session: AsyncSession,
    appointment: Appointment,
) -> None:
    reminders = ReminderRepository(session)
    day_before = appointment.start_at - timedelta(hours=24)
    two_hours_before = appointment.start_at - timedelta(hours=2)
    now = datetime.now(ZoneInfo("Asia/Tashkent"))
    if day_before > now:
        await reminders.create(
            appointment_id=appointment.id,
            reminder_type="day_before",
            send_at=day_before,
        )
    if two_hours_before > now:
        await reminders.create(
            appointment_id=appointment.id,
            reminder_type="two_hours_before",
            send_at=two_hours_before,
        )


def _no_appointments_text(language: Language) -> str:
    return {
        "ru": "У вас нет активных записей для переноса.",
        "uz": "Sizda ko'chirish uchun faol yozuvlar yo'q.",
        "en": "You do not have active appointments to reschedule.",
    }[language]


def _reschedule_single_prompt(language: Language, appointment: Appointment) -> str:
    tz = ZoneInfo(appointment.timezone)
    start_at = appointment.start_at.astimezone(tz)
    formatted = start_at.strftime("%d.%m.%Y %H:%M")
    return {
        "ru": (
            f"Запись: {appointment.service_type}, "
            f"{formatted}. Нажмите, чтобы перенести:"
        ),
        "uz": (
            f"Yozuv: {appointment.service_type}, "
            f"{formatted}. Ko'chirish uchun bosing:"
        ),
        "en": (
            f"Appointment: {appointment.service_type}, "
            f"{formatted}. Tap to reschedule:"
        ),
    }[language]


def _reschedule_multiple_prompt(language: Language) -> str:
    return {
        "ru": "Выберите запись для переноса:",
        "uz": "Ko'chirish uchun yozuvni tanlang:",
        "en": "Select an appointment to reschedule:",
    }[language]


def _select_new_time_text(language: Language) -> str:
    return {
        "ru": "Выберите новое время:",
        "uz": "Yangi vaqtni tanlang:",
        "en": "Choose a new time:",
    }[language]


def _no_slots_text(language: Language) -> str:
    return {
        "ru": "К сожалению, свободных слотов сейчас нет. Попробуйте позже.",
        "uz": "Afsuski, hozir bo'sh vaqt yo'q. Keyinroq urinib ko'ring.",
        "en": "Unfortunately, no available slots right now. Please try later.",
    }[language]


def _reschedule_confirmation_text(
    language: Language,
    appointment: Appointment,
) -> str:
    tz = ZoneInfo(appointment.timezone)
    new_start = appointment.start_at.astimezone(tz)
    formatted = new_start.strftime("%d.%m.%Y %H:%M")
    return {
        "ru": (
            f"Запись перенесена на {formatted} "
            f"({appointment.service_type}). Ждём вас в клинике."
        ),
        "uz": (
            f"Yozuv {formatted} ga ko'chirildi "
            f"({appointment.service_type}). Sizni klinikada kutamiz."
        ),
        "en": (
            f"Appointment rescheduled to {formatted} "
            f"({appointment.service_type}). We will see you at the clinic."
        ),
    }[language]


def _reschedule_admin_notification(
    user: User,
    appointment: Appointment,
    old_start: datetime,
    old_end: datetime,
    new_start: datetime,
    new_end: datetime,
) -> str:
    username = f"@{user.telegram_username}" if user.telegram_username else "-"
    tz = ZoneInfo(appointment.timezone)
    old_time = old_start.astimezone(tz).isoformat()
    new_time = new_start.astimezone(tz).isoformat()
    return "\n".join(
        [
            "Appointment rescheduled",
            "",
            "Patient:",
            f"Telegram: {username} / id {user.telegram_user_id}",
            "",
            "Appointment:",
            f"Service: {appointment.service_type}",
            f"Doctor: {appointment.doctor_type}",
            f"Old time: {old_time}",
            f"New time: {new_time}",
            f"Patient name: {appointment.patient_name}",
            f"Phone: {appointment.primary_phone}",
        ]
    )


def _resolve_calendar(
    calendar_service: GoogleCalendarService | None,
) -> GoogleCalendarService | None:
    if calendar_service is not None:
        return calendar_service
    try:
        return create_google_calendar_service()
    except CalendarConfigError:
        return None


class ReschedulingError(RuntimeError):
    pass


class ReschedulingSlotConflictError(ReschedulingError):
    pass
