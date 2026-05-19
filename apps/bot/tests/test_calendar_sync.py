from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.db.models import AppointmentHistory
from app.db.repositories import (
    AppointmentRepository,
    UserRepository,
)
from app.workers.calendar_sync_worker import _perform_sync

TZ = ZoneInfo("Asia/Tashkent")


class FakeSyncCalendarService:
    def __init__(self, events: list[dict] | None = None) -> None:
        self.events = events or []

    async def list_events(self, *, time_min, time_max):
        return self.events


def _build_calendar_event(
    *,
    event_id: str,
    start_at: datetime,
    end_at: datetime,
    timezone: str = "Asia/Tashkent",
    appointment_id: int = 0,
    telegram_user_id: int = 0,
    service_type: str = "consultation",
    doctor_type: str = "therapist",
) -> dict:
    return {
        "id": event_id,
        "status": "confirmed",
        "summary": "[Bot] consultation — Test — +998",
        "start": {
            "dateTime": start_at.isoformat(),
            "timeZone": timezone,
        },
        "end": {
            "dateTime": end_at.isoformat(),
            "timeZone": timezone,
        },
        "extendedProperties": {
            "private": {
                "appointment_id": str(appointment_id),
                "telegram_user_id": str(telegram_user_id),
                "service_type": service_type,
                "doctor_type": doctor_type,
                "created_by": "telegram_bot",
            }
        },
    }


async def test_sync_ignores_non_bot_events(session):
    user = await UserRepository(session).upsert_from_telegram(
        telegram_user_id=6001,
        preferred_language="en",
    )
    now = datetime(2026, 7, 1, 10, 0, tzinfo=TZ)
    future = now + timedelta(days=3)
    appointment = await AppointmentRepository(session).create(
        user_id=user.id,
        service_type="consultation",
        doctor_type="therapist",
        start_at=future,
        end_at=future + timedelta(minutes=30),
        patient_name="Test",
        primary_phone="+998",
            calendar_event_id="ev-non-bot",
    )

    non_bot_event = {
        "id": "ev-non-bot",
        "status": "confirmed",
        "summary": "Regular meeting",
        "start": {"dateTime": future.isoformat(), "timeZone": "Asia/Tashkent"},
        "end": {
            "dateTime": (future + timedelta(minutes=30)).isoformat(),
            "timeZone": "Asia/Tashkent",
        },
        "extendedProperties": {"private": {}},
    }
    calendar = FakeSyncCalendarService([non_bot_event])

    import app.workers.calendar_sync_worker as sync_mod

    original = sync_mod._resolve_calendar
    sync_mod._resolve_calendar = lambda cs: calendar
    try:
        await _perform_sync(
            lambda: _single_session(session),
            calendar,
        )
    finally:
        sync_mod._resolve_calendar = original

    await session.refresh(appointment)
    assert appointment.status == "scheduled"


async def test_sync_updates_time_change_in_db(session):
    user = await UserRepository(session).upsert_from_telegram(
        telegram_user_id=6002,
        preferred_language="en",
    )
    now = datetime(2026, 7, 1, 10, 0, tzinfo=TZ)
    original_start = now + timedelta(days=3)
    original_end = original_start + timedelta(minutes=30)
    appointment = await AppointmentRepository(session).create(
        user_id=user.id,
        service_type="consultation",
        doctor_type="therapist",
        start_at=original_start,
        end_at=original_end,
        patient_name="Test",
        primary_phone="+998",
        calendar_event_id="ev-time-change",
    )

    new_start = original_start + timedelta(hours=2)
    new_end = new_start + timedelta(minutes=30)
    calendar_event = _build_calendar_event(
        event_id="ev-time-change",
        start_at=new_start,
        end_at=new_end,
        appointment_id=appointment.id,
        telegram_user_id=6002,
    )
    calendar = FakeSyncCalendarService([calendar_event])

    import app.workers.calendar_sync_worker as sync_mod

    original = sync_mod._resolve_calendar
    sync_mod._resolve_calendar = lambda cs: calendar
    try:
        await _perform_sync(
            lambda: _single_session(session),
            calendar,
        )
    finally:
        sync_mod._resolve_calendar = original

    await session.refresh(appointment)
    assert appointment.status == "scheduled"
    assert appointment.start_at.replace(tzinfo=None) == new_start.replace(tzinfo=None)
    assert appointment.end_at.replace(tzinfo=None) == new_end.replace(tzinfo=None)

    history = (
        await session.execute(
            select(AppointmentHistory).where(
                AppointmentHistory.appointment_id == appointment.id,
                AppointmentHistory.actor == "calendar_sync",
            )
        )
    ).scalars().all()
    assert len(history) >= 1


async def test_sync_cancels_db_appointment_when_calendar_event_deleted(
    session,
):
    user = await UserRepository(session).upsert_from_telegram(
        telegram_user_id=6003,
        preferred_language="en",
    )
    now = datetime(2026, 7, 1, 10, 0, tzinfo=TZ)
    future = now + timedelta(days=3)
    appointment = await AppointmentRepository(session).create(
        user_id=user.id,
        service_type="consultation",
        doctor_type="therapist",
        start_at=future,
        end_at=future + timedelta(minutes=30),
        patient_name="Test",
        primary_phone="+998",
        calendar_event_id="ev-deleted",
    )

    calendar = FakeSyncCalendarService([])

    import app.workers.calendar_sync_worker as sync_mod

    original = sync_mod._resolve_calendar
    sync_mod._resolve_calendar = lambda cs: calendar
    try:
        await _perform_sync(
            lambda: _single_session(session),
            calendar,
        )
    finally:
        sync_mod._resolve_calendar = original

    await session.refresh(appointment)
    assert appointment.status == "cancelled"


class _SingleSession:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *args):
        pass


def _single_session(session):
    return _SingleSession(session)
