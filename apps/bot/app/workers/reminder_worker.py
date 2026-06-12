import asyncio
import logging
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.calendar import (
    CalendarConfigError,
    GoogleCalendarService,
    create_google_calendar_service,
)
from app.db.models import Appointment, User
from app.db.repositories import ReminderRepository
from app.telegram.texts import Language

logger = logging.getLogger(__name__)

REMINDER_POLL_INTERVAL_SEC = 30


async def reminder_worker_loop(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    bot: Any | None = None,
    calendar_service: GoogleCalendarService | None = None,
    stop_event: asyncio.Event,
    poll_interval_sec: float = REMINDER_POLL_INTERVAL_SEC,
) -> None:
    logger.info(
        "reminder_worker_started",
        extra={"poll_interval_sec": poll_interval_sec},
    )
    while not stop_event.is_set():
        try:
            await _process_due_reminders(session_factory, bot, calendar_service)
        except Exception:
            logger.exception("reminder_worker_error")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll_interval_sec)
        except TimeoutError:
            pass
    logger.info("reminder_worker_stopped")


async def _process_due_reminders(
    session_factory: async_sessionmaker[AsyncSession],
    bot: Any | None,
    calendar_service: GoogleCalendarService | None,
) -> None:
    now = datetime.now(UTC)
    async with session_factory() as session:
        reminders = await ReminderRepository(session).get_due_pending(now)
        if not reminders:
            return

        resolved_calendar = _resolve_calendar(calendar_service)
        for reminder in reminders:
            try:
                await _send_reminder(session, reminder, bot, resolved_calendar, now)
            except Exception:
                logger.exception(
                    "reminder_send_failed",
                    extra={"reminder_id": reminder.id},
                )
                await _mark_failed(session, reminder.id)


async def _send_reminder(
    session: AsyncSession,
    reminder: Any,
    bot: Any | None,
    calendar_service: GoogleCalendarService | None,
    now: datetime,
) -> None:
    appointment = await session.get(Appointment, reminder.appointment_id)
    if appointment is None:
        logger.warning(
            "reminder_appointment_missing",
            extra={
                "reminder_id": reminder.id,
                "appointment_id": reminder.appointment_id,
            },
        )
        await _mark_failed(session, reminder.id)
        return

    if appointment.status != "scheduled":
        logger.info(
            "reminder_appointment_not_scheduled",
            extra={"reminder_id": reminder.id, "appointment_id": appointment.id,
                   "status": appointment.status},
        )
        await ReminderRepository(session).cancel_for_appointment(appointment.id)
        return

    if calendar_service is not None and appointment.calendar_event_id:
        try:
            event = await calendar_service.get_event(appointment.calendar_event_id)
        except Exception:
            logger.warning(
                "reminder_calendar_event_missing",
                extra={"reminder_id": reminder.id,
                       "calendar_event_id": appointment.calendar_event_id},
            )
            await ReminderRepository(session).cancel_for_appointment(appointment.id)
            return

        if event.get("status") == "cancelled":
            await ReminderRepository(session).cancel_for_appointment(appointment.id)
            return

    if bot is None:
        logger.warning(
            "reminder_bot_not_available",
            extra={"reminder_id": reminder.id},
        )
        return

    user = await session.get(User, appointment.user_id)
    language = user.preferred_language if user else None
    chat_id = user.telegram_user_id if user else appointment.user_id

    reminder_text = _reminder_message(reminder.reminder_type, appointment, language)

    await bot.send_message(
        chat_id=chat_id,
        text=reminder_text,
    )
    await ReminderRepository(session).mark_sent(reminder.id)
    logger.info(
        "reminder_sent",
        extra={
            "reminder_id": reminder.id,
            "appointment_id": appointment.id,
            "reminder_type": reminder.reminder_type,
        },
    )


async def _mark_failed(session: AsyncSession, reminder_id: int) -> None:
    try:
        await ReminderRepository(session).mark_failed(reminder_id)
    except Exception:
        logger.exception(
            "reminder_mark_failed_error",
            extra={"reminder_id": reminder_id},
        )


def _reminder_message(
    reminder_type: str,
    appointment: Any,
    language: str | None,
) -> str:
    lang: Language = language if language in ("ru", "uz", "en") else "ru"  # type: ignore[assignment]
    tz = ZoneInfo(appointment.timezone)
    start_at = appointment.start_at.astimezone(tz)

    if reminder_type == "day_before":
        return {
            "ru": (
                f"Напоминание: завтра в {start_at.strftime('%H:%M')} у вас запись "
                f"({appointment.service_type}) в стоматологической клинике."
            ),
            "uz": (
                f"Eslatma: ertaga soat {start_at.strftime('%H:%M')} da "
                f"stomatologiya klinikasida qabulingiz bor "
                f"({appointment.service_type})."
            ),
            "en": (
                f"Reminder: tomorrow at {start_at.strftime('%H:%M')} you have an "
                f"appointment ({appointment.service_type}) at the dental clinic."
            ),
        }[lang]
    return {
        "ru": (
            f"Напоминание: сегодня в {start_at.strftime('%H:%M')} у вас запись "
            f"({appointment.service_type}) в стоматологической клинике."
        ),
        "uz": (
            f"Eslatma: bugun soat {start_at.strftime('%H:%M')} da stomatologiya "
            f"klinikasida qabulingiz bor ({appointment.service_type})."
        ),
        "en": (
            f"Reminder: today at {start_at.strftime('%H:%M')} you have an "
            f"appointment ({appointment.service_type}) at the dental clinic."
        ),
    }[lang]


def _resolve_calendar(
    calendar_service: GoogleCalendarService | None,
) -> GoogleCalendarService | None:
    if calendar_service is not None:
        return calendar_service
    try:
        return create_google_calendar_service()
    except CalendarConfigError:
        return None
