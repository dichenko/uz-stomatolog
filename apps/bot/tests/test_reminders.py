from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.db.models import ReminderJob
from app.db.repositories import (
    AppointmentRepository,
    ReminderRepository,
    UserRepository,
)
from app.workers.reminder_worker import _process_due_reminders, _reminder_message

TZ = ZoneInfo("Asia/Tashkent")


class FakeBot:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send_message(self, *, chat_id: int, text: str):
        self.messages.append({"chat_id": chat_id, "text": text})
        return SimpleNamespace(message_id=999)


async def test_reminder_messages_are_localized():
    appointment = SimpleNamespace(
        start_at=datetime(2026, 5, 22, 9, 0, tzinfo=TZ),
        timezone="Asia/Tashkent",
        service_type="consultation",
        id=1,
        user_id=1001,
        status="scheduled",
    )

    en_text = _reminder_message("day_before", appointment, "en")
    assert "tomorrow" in en_text.casefold()
    assert "09:00" in en_text

    ru_text = _reminder_message("day_before", appointment, "ru")
    assert "завтра" in ru_text

    uz_text = _reminder_message("day_before", appointment, "uz")
    assert "ertaga" in uz_text

    en_2h = _reminder_message("two_hours_before", appointment, "en")
    assert "today" in en_2h.casefold()

    ru_2h = _reminder_message("two_hours_before", appointment, "ru")
    assert "сегодня" in ru_2h


async def test_reminder_repository_mark_failed(session):
    user = await UserRepository(session).upsert_from_telegram(
        telegram_user_id=5001,
        preferred_language="en",
    )
    now = datetime(2026, 6, 1, 10, 0, tzinfo=TZ)
    appointment = await AppointmentRepository(session).create(
        user_id=user.id,
        service_type="consultation",
        doctor_type="therapist",
        start_at=now + timedelta(days=5),
        end_at=now + timedelta(days=5, minutes=30),
        patient_name="Test",
        primary_phone="+998901234567",
    )
    reminder = await ReminderRepository(session).create(
        appointment_id=appointment.id,
        reminder_type="day_before",
        send_at=now + timedelta(days=4),
    )

    updated = await ReminderRepository(session).mark_failed(reminder.id)
    assert updated.status == "failed"
    assert updated.error is not None


async def test_reminder_worker_processes_due_reminders(session, monkeypatch):
    monkeypatch.setattr(
        "app.workers.reminder_worker.create_google_calendar_service",
        lambda: None,
    )
    user = await UserRepository(session).upsert_from_telegram(
        telegram_user_id=5002,
        preferred_language="en",
    )
    now = datetime.now(UTC)
    appointment = await AppointmentRepository(session).create(
        user_id=user.id,
        service_type="consultation",
        doctor_type="therapist",
        start_at=now + timedelta(hours=26),
        end_at=now + timedelta(hours=26, minutes=30),
        patient_name="Test",
        primary_phone="+998901234567",
    )

    past_time = now - timedelta(minutes=5)
    await ReminderRepository(session).create(
        appointment_id=appointment.id,
        reminder_type="two_hours_before",
        send_at=past_time,
    )
    await session.commit()

    bot = FakeBot()
    await _process_due_reminders(lambda: _single_session(session), bot, None)

    await session.refresh(appointment)
    reminders = (
        await session.execute(
            select(ReminderJob).where(ReminderJob.appointment_id == appointment.id)
        )
    ).scalars().all()

    assert any(r.status == "sent" for r in reminders)
    assert len(bot.messages) == 1
    assert "reminder" in bot.messages[0]["text"].casefold()
    assert bot.messages[0]["chat_id"] == 5002


async def test_reminder_worker_skips_cancelled_appointment(session, monkeypatch):
    monkeypatch.setattr(
        "app.workers.reminder_worker.create_google_calendar_service",
        lambda: None,
    )
    user = await UserRepository(session).upsert_from_telegram(
        telegram_user_id=5003,
        preferred_language="en",
    )
    now = datetime.now(UTC)
    appointment = await AppointmentRepository(session).create(
        user_id=user.id,
        service_type="consultation",
        doctor_type="therapist",
        start_at=now + timedelta(hours=26),
        end_at=now + timedelta(hours=26, minutes=30),
        patient_name="Test",
        primary_phone="+998901234567",
    )
    await AppointmentRepository(session).cancel(
        appointment_id=appointment.id,
        actor="user",
    )

    past_time = now - timedelta(minutes=5)
    await ReminderRepository(session).create(
        appointment_id=appointment.id,
        reminder_type="two_hours_before",
        send_at=past_time,
    )
    await session.commit()

    bot = FakeBot()
    await _process_due_reminders(lambda: _single_session(session), bot, None)

    await session.refresh(appointment)
    reminders = (
        await session.execute(
            select(ReminderJob).where(ReminderJob.appointment_id == appointment.id)
        )
    ).scalars().all()

    assert all(r.status in ("cancelled", "failed") for r in reminders)
    assert len(bot.messages) == 0


class _SingleSession:
    def __init__(self, session):
        self._session = session

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, *args):
        pass


def _single_session(session):
    return _SingleSession(session)
