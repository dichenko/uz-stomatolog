from dataclasses import dataclass
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession

from app.calendar import (
    CalendarConfigError,
    GoogleCalendarService,
    create_google_calendar_service,
)
from app.config import Settings
from app.db.models import Appointment, User
from app.db.repositories import (
    AppointmentRepository,
    ReminderRepository,
)
from app.services.admin_notify import send_admin_notification
from app.telegram.texts import Language


@dataclass(frozen=True)
class CancellationResult:
    text: str
    active_appointments: list[dict[str, Any]]


@dataclass(frozen=True)
class CancellationConfirmationResult:
    text: str
    appointment: Appointment
    calendar_cancelled: bool
    admin_notification_sent: bool


async def handle_cancellation_message(
    *,
    session: AsyncSession,
    user: User,
    language: Language,
) -> CancellationResult:
    appointments = await AppointmentRepository(session).get_active_future_by_user(
        user_id=user.id,
    )
    if not appointments:
        return CancellationResult(
            text=_no_appointments_text(language),
            active_appointments=[],
        )

    appointment_dicts = [
        _appointment_to_dict(appointment)
        for appointment in appointments
    ]

    if len(appointments) == 1:
        return CancellationResult(
            text=_cancel_single_prompt(language, appointments[0]),
            active_appointments=appointment_dicts,
        )

    return CancellationResult(
        text=_cancel_multiple_prompt(language),
        active_appointments=appointment_dicts,
    )


async def confirm_cancellation(
    *,
    session: AsyncSession,
    user: User,
    appointment_id: int,
    language: Language,
    calendar_service: GoogleCalendarService | None = None,
    admin_bot: Any | None = None,
    settings: Settings | None = None,
) -> CancellationConfirmationResult:
    repo = AppointmentRepository(session)
    appointment = await session.get(Appointment, appointment_id)
    if appointment is None or appointment.user_id != user.id:
        raise CancellationError("Appointment not found or does not belong to user")
    if appointment.status != "scheduled":
        raise CancellationError("Appointment is not in scheduled status")

    calendar_cancelled = False
    resolved_calendar = _resolve_calendar(calendar_service)
    if resolved_calendar is not None and appointment.calendar_event_id:
        await resolved_calendar.cancel_event(appointment.calendar_event_id)
        calendar_cancelled = True

    await repo.cancel(appointment_id=appointment_id, actor="user")
    await ReminderRepository(session).cancel_for_appointment(appointment_id)

    notification = await send_admin_notification(
        bot=admin_bot,
        message_text=_cancellation_admin_notification(user, appointment),
        settings=settings,
    )

    return CancellationConfirmationResult(
        text=_cancellation_confirmation_text(language, appointment),
        appointment=appointment,
        calendar_cancelled=calendar_cancelled,
        admin_notification_sent=notification.sent,
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


def _no_appointments_text(language: Language) -> str:
    return {
        "ru": "У вас нет активных записей для отмены.",
        "uz": "Sizda bekor qilish uchun faol yozuvlar yo'q.",
        "en": "You do not have active appointments to cancel.",
    }[language]


def _cancel_single_prompt(language: Language, appointment: Appointment) -> str:
    tz = ZoneInfo(appointment.timezone)
    start_at = appointment.start_at.astimezone(tz)
    formatted = start_at.strftime("%d.%m.%Y %H:%M")
    return {
        "ru": (
            f"Запись: {appointment.service_type}, "
            f"{formatted}. Нажмите, чтобы отменить:"
        ),
        "uz": (
            f"Yozuv: {appointment.service_type}, "
            f"{formatted}. Bekor qilish uchun bosing:"
        ),
        "en": (
            f"Appointment: {appointment.service_type}, "
            f"{formatted}. Tap to cancel:"
        ),
    }[language]


def _cancel_multiple_prompt(language: Language) -> str:
    return {
        "ru": "Выберите запись для отмены:",
        "uz": "Bekor qilish uchun yozuvni tanlang:",
        "en": "Select an appointment to cancel:",
    }[language]


def _cancellation_confirmation_text(
    language: Language,
    appointment: Appointment,
) -> str:
    tz = ZoneInfo(appointment.timezone)
    start_at = appointment.start_at.astimezone(tz)
    formatted = start_at.strftime("%d.%m.%Y %H:%M")
    return {
        "ru": (
            f"Запись на {formatted} ({appointment.service_type}) отменена."
        ),
        "uz": (
            f"{formatted} sanasidagi yozuv ({appointment.service_type}) "
            f"bekor qilindi."
        ),
        "en": (
            f"Appointment on {formatted} ({appointment.service_type}) "
            f"has been cancelled."
        ),
    }[language]


def _cancellation_admin_notification(user: User, appointment: Appointment) -> str:
    username = f"@{user.telegram_username}" if user.telegram_username else "-"
    return "\n".join(
        [
            "Appointment cancelled",
            "",
            "Patient:",
            f"Telegram: {username} / id {user.telegram_user_id}",
            "",
            "Appointment:",
            f"Service: {appointment.service_type}",
            f"Doctor: {appointment.doctor_type}",
            f"Time: {appointment.start_at.isoformat()}",
            f"Patient name: {appointment.patient_name}",
            f"Phone: {appointment.primary_phone}",
            "",
            "Cancelled by: user",
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


class CancellationError(RuntimeError):
    pass
