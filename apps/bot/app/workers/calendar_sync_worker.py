import asyncio
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.calendar import (
    CalendarConfigError,
    GoogleCalendarService,
    create_google_calendar_service,
)
from app.db.models import Appointment
from app.db.repositories import AppointmentRepository

logger = logging.getLogger(__name__)

SYNC_POLL_INTERVAL_SEC = 600


async def calendar_sync_worker_loop(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    calendar_service: GoogleCalendarService | None = None,
    stop_event: asyncio.Event,
    poll_interval_sec: float = SYNC_POLL_INTERVAL_SEC,
) -> None:
    logger.info(
        "calendar_sync_worker_started",
        extra={"poll_interval_sec": poll_interval_sec},
    )
    while not stop_event.is_set():
        try:
            await _perform_sync(session_factory, calendar_service)
        except Exception:
            logger.exception("calendar_sync_error")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=poll_interval_sec)
        except TimeoutError:
            pass
    logger.info("calendar_sync_worker_stopped")


async def _perform_sync(
    session_factory: async_sessionmaker[AsyncSession],
    calendar_service: GoogleCalendarService | None,
) -> None:
    resolved_calendar = _resolve_calendar(calendar_service)
    if resolved_calendar is None:
        return

    tz = ZoneInfo("Asia/Tashkent")
    now = datetime.now(tz)
    time_min = now - timedelta(days=7)
    time_max = now + timedelta(days=60)

    calendar_events = await resolved_calendar.list_events(
        time_min=time_min,
        time_max=time_max,
    )

    bot_events = [
        event
        for event in calendar_events
        if _is_bot_event(event)
    ]

    all_calendar_event_ids = {
        event.get("id")
        for event in calendar_events
        if event.get("id")
    }

    calendar_map: dict[str, dict] = {}
    for event in bot_events:
        event_id = event.get("id")
        if event_id:
            calendar_map[event_id] = event

    async with session_factory() as session:
        repo = AppointmentRepository(session)

        db_appointments = await _get_appointments_with_calendar_id(session)
        db_map: dict[str, Appointment] = {
            appt.calendar_event_id: appt  # type: ignore[assignment]
            for appt in db_appointments
            if appt.calendar_event_id
        }

        changes = 0
        for calendar_event_id, calendar_event in calendar_map.items():
            db_appointment = db_map.pop(calendar_event_id, None)
            if db_appointment is None:
                changes += await _restore_missing_appointment(
                    session, repo, calendar_event, tz
                )
            else:
                changes += await _sync_existing_appointment(
                    session, repo, db_appointment, calendar_event, tz
                )

        for calendar_event_id, db_appointment in db_map.items():
            if calendar_event_id in all_calendar_event_ids:
                continue
            if db_appointment.status == "scheduled":
                await repo.cancel(
                    appointment_id=db_appointment.id,
                    actor="calendar_sync",
                )
                logger.info(
                    "calendar_sync_cancelled_missing_event",
                    extra={
                        "appointment_id": db_appointment.id,
                        "calendar_event_id": calendar_event_id,
                    },
                )
                changes += 1

        if changes:
            await session.commit()
            logger.info(
                "calendar_sync_completed",
                extra={"changes": changes, "events_scanned": len(bot_events)},
            )


async def _sync_existing_appointment(
    session: AsyncSession,
    repo: AppointmentRepository,
    appointment: Appointment,
    calendar_event: dict,
    tz: ZoneInfo,
) -> int:
    calendar_start = _parse_event_datetime(calendar_event, "start")
    calendar_end = _parse_event_datetime(calendar_event, "end")
    if calendar_start is None or calendar_end is None:
        return 0

    appt_start = appointment.start_at.astimezone(tz)
    appt_end = appointment.end_at.astimezone(tz)

    if calendar_start == appt_start and calendar_end == appt_end:
        return 0

    old_start = appointment.start_at.isoformat()
    old_end = appointment.end_at.isoformat()
    appointment.start_at = calendar_start
    appointment.end_at = calendar_end
    await session.flush()
    await repo.add_history(
        appointment_id=appointment.id,
        action="calendar_sync_updated",
        actor="calendar_sync",
        old_data={"start_at": old_start, "end_at": old_end},
        new_data={
            "start_at": calendar_start.isoformat(),
            "end_at": calendar_end.isoformat(),
        },
    )
    logger.info(
        "calendar_sync_updated_time",
        extra={
            "appointment_id": appointment.id,
            "calendar_event_id": appointment.calendar_event_id,
            "old_start": old_start,
            "new_start": calendar_start.isoformat(),
        },
    )
    return 1


async def _restore_missing_appointment(
    session: AsyncSession,
    repo: AppointmentRepository,
    calendar_event: dict,
    tz: ZoneInfo,
) -> int:
    private = calendar_event.get("extendedProperties", {}).get("private", {})
    appointment_id_str = private.get("appointment_id", "")
    if not appointment_id_str:
        return 0

    try:
        appointment_id = int(appointment_id_str)
    except (ValueError, TypeError):
        return 0

    existing = await session.get(Appointment, appointment_id)
    if existing is not None and existing.calendar_event_id:
        return 0

    calendar_start = _parse_event_datetime(calendar_event, "start")
    calendar_end = _parse_event_datetime(calendar_event, "end")
    if calendar_start is None or calendar_end is None:
        return 0

    telegram_user_id_str = private.get("telegram_user_id", "")
    try:
        telegram_user_id = int(telegram_user_id_str)
    except (ValueError, TypeError):
        return 0

    user_result = await session.execute(
        select(Appointment).where(Appointment.user_id == telegram_user_id).limit(1)
    )
    if not user_result.first():
        return 0

    appointment = await repo.create(
        user_id=telegram_user_id,
        service_type=private.get("service_type", "consultation"),
        doctor_type=private.get("doctor_type", "therapist"),
        start_at=calendar_start,
        end_at=calendar_end,
        timezone=str(tz),
        patient_name=calendar_event.get("summary", "from calendar"),
        primary_phone="-",
        calendar_event_id=calendar_event.get("id"),
        calendar_etag=calendar_event.get("etag"),
        created_trace_id="calendar_sync_restore",
    )
    logger.info(
        "calendar_sync_restored_appointment",
        extra={
            "appointment_id": appointment.id,
            "calendar_event_id": calendar_event.get("id"),
            "original_db_id": appointment_id_str,
        },
    )
    return 1


async def _get_appointments_with_calendar_id(
    session: AsyncSession,
) -> list[Appointment]:
    result = await session.execute(
        select(Appointment)
        .where(Appointment.calendar_event_id.isnot(None))
        .where(Appointment.status.in_(["scheduled", "rescheduled"]))
    )
    return list(result.scalars())


def _is_bot_event(event: dict) -> bool:
    private = event.get("extendedProperties", {}).get("private", {})
    return private.get("created_by") == "telegram_bot"


def _parse_event_datetime(event: dict, key: str) -> datetime | None:
    value = event.get(key, {})
    date_str = value.get("dateTime") if isinstance(value, dict) else None
    if not date_str:
        return None
    try:
        return datetime.fromisoformat(date_str)
    except (ValueError, TypeError):
        return None


def _resolve_calendar(
    calendar_service: GoogleCalendarService | None,
) -> GoogleCalendarService | None:
    if calendar_service is not None:
        return calendar_service
    try:
        return create_google_calendar_service()
    except CalendarConfigError:
        return None
