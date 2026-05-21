from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.db.models import Appointment, ReminderJob
from app.db.repositories import (
    AppointmentRepository,
    ConversationRepository,
    ReminderRepository,
    UserRepository,
)
from app.graph import run_bot_graph
from app.services.cancellation import (
    CancellationError,
    confirm_cancellation,
    handle_cancellation_message,
)
from app.telegram.keyboards import cancel_appointments_keyboard

TZ = ZoneInfo("Asia/Tashkent")


class FakeCalendarService:
    def __init__(self) -> None:
        self.cancelled_event_ids: list[str] = []

    async def cancel_event(self, event_id: str) -> None:
        self.cancelled_event_ids.append(event_id)


class FakeAdminBot:
    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []

    async def send_message(self, *, chat_id: str, text: str):
        self.messages.append({"chat_id": chat_id, "text": text})
        return SimpleNamespace(message_id=777)


async def test_cancellation_message_returns_active_appointments(session):
    user = await UserRepository(session).upsert_from_telegram(
        telegram_user_id=3001,
        preferred_language="en",
    )
    now = datetime(2026, 5, 21, 10, 0, tzinfo=TZ)
    future_start = now + timedelta(days=2)
    future_end = future_start + timedelta(minutes=60)
    await AppointmentRepository(session).create(
        user_id=user.id,
        service_type="past_cleaning",
        doctor_type="therapist",
        start_at=datetime(2020, 1, 1, 9, 0, tzinfo=TZ),
        end_at=datetime(2020, 1, 1, 10, 0, tzinfo=TZ),
        patient_name="Past Patient",
        primary_phone="+998901234567",
    )
    await AppointmentRepository(session).create(
        user_id=user.id,
        service_type="cleaning",
        doctor_type="therapist",
        start_at=future_start,
        end_at=future_end,
        patient_name="Test Patient",
        primary_phone="+998901234567",
    )

    result = await handle_cancellation_message(
        session=session,
        user=user,
        language="en",
    )

    assert len(result.active_appointments) == 1
    assert result.active_appointments[0]["service_type"] == "cleaning"
    assert "past_cleaning" not in result.text
    assert "Appointment:" in result.text


async def test_cancellation_message_no_appointments(session):
    user = await UserRepository(session).upsert_from_telegram(
        telegram_user_id=3002,
        preferred_language="ru",
    )

    result = await handle_cancellation_message(
        session=session,
        user=user,
        language="ru",
    )

    assert result.active_appointments == []
    assert "активных записей" in result.text


async def test_confirm_cancellation_updates_db_cancels_calendar_and_reminders(
    session,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.services.admin_notify.get_settings",
        lambda: SimpleNamespace(admin_telegram_chat_id="-100999"),
    )
    user = await UserRepository(session).upsert_from_telegram(
        telegram_user_id=3003,
        telegram_username="ali",
        preferred_language="en",
    )
    now = datetime(2026, 5, 21, 10, 0, tzinfo=TZ)
    future_start = now + timedelta(days=2)
    future_end = future_start + timedelta(minutes=60)
    appointment = await AppointmentRepository(session).create(
        user_id=user.id,
        service_type="treatment",
        doctor_type="therapist",
        start_at=future_start,
        end_at=future_end,
        patient_name="Ali Karimov",
        primary_phone="+998901234567",
        calendar_event_id="calendar-event-abc",
    )
    await ReminderRepository(session).create(
        appointment_id=appointment.id,
        reminder_type="day_before",
        send_at=future_start - timedelta(hours=24),
    )
    await ReminderRepository(session).create(
        appointment_id=appointment.id,
        reminder_type="two_hours_before",
        send_at=future_start - timedelta(hours=2),
    )

    calendar = FakeCalendarService()
    admin_bot = FakeAdminBot()

    result = await confirm_cancellation(
        session=session,
        user=user,
        appointment_id=appointment.id,
        language="en",
        calendar_service=calendar,
        admin_bot=admin_bot,
    )

    db_appointment = (await session.execute(
        select(Appointment).where(Appointment.id == appointment.id)
    )).scalar_one()

    reminders = (await session.execute(
        select(ReminderJob).where(ReminderJob.appointment_id == appointment.id)
    )).scalars().all()

    assert result.appointment.id == appointment.id
    assert result.calendar_cancelled is True
    assert result.admin_notification_sent is True
    assert "cancelled" in result.text.casefold()
    assert db_appointment.status == "cancelled"
    assert db_appointment.cancelled_at is not None
    assert all(reminder.status == "cancelled" for reminder in reminders)
    assert "calendar-event-abc" in calendar.cancelled_event_ids
    assert "Appointment cancelled" in admin_bot.messages[0]["text"]


async def test_confirm_cancellation_raises_for_not_scheduled(session):
    user = await UserRepository(session).upsert_from_telegram(
        telegram_user_id=3004,
        preferred_language="en",
    )
    appointment = await AppointmentRepository(session).create(
        user_id=user.id,
        service_type="consultation",
        doctor_type="therapist",
        start_at=datetime(2026, 5, 21, 11, 0, tzinfo=TZ),
        end_at=datetime(2026, 5, 21, 11, 30, tzinfo=TZ),
        patient_name="Test",
        primary_phone="+998901234567",
    )
    await AppointmentRepository(session).cancel(
        appointment_id=appointment.id,
        actor="user",
    )

    try:
        await confirm_cancellation(
            session=session,
            user=user,
            appointment_id=appointment.id,
            language="en",
        )
        raise AssertionError("Expected CancellationError")
    except CancellationError:
        pass


async def test_confirm_cancellation_raises_for_wrong_user(session):
    user = await UserRepository(session).upsert_from_telegram(
        telegram_user_id=3005,
        preferred_language="en",
    )
    other_user = await UserRepository(session).upsert_from_telegram(
        telegram_user_id=3006,
        preferred_language="en",
    )
    appointment = await AppointmentRepository(session).create(
        user_id=other_user.id,
        service_type="consultation",
        doctor_type="therapist",
        start_at=datetime(2026, 5, 21, 12, 0, tzinfo=TZ),
        end_at=datetime(2026, 5, 21, 12, 30, tzinfo=TZ),
        patient_name="Test",
        primary_phone="+998901234567",
    )

    try:
        await confirm_cancellation(
            session=session,
            user=user,
            appointment_id=appointment.id,
            language="en",
        )
        raise AssertionError("Expected CancellationError")
    except CancellationError:
        pass


async def test_graph_cancellation_node_returns_appointments(session):
    user = await UserRepository(session).upsert_from_telegram(
        telegram_user_id=3007,
        preferred_language="en",
    )
    conversation = await ConversationRepository(session).get_or_create(
        user_id=user.id,
        telegram_chat_id=3007,
    )
    now = datetime(2026, 5, 21, 10, 0, tzinfo=TZ)
    future_start = now + timedelta(days=1)
    future_end = future_start + timedelta(minutes=60)
    await AppointmentRepository(session).create(
        user_id=user.id,
        service_type="cleaning",
        doctor_type="therapist",
        start_at=future_start,
        end_at=future_end,
        patient_name="Test",
        primary_phone="+998901234567",
    )

    result = await run_bot_graph(
        session=session,
        user=user,
        conversation=conversation,
        trace_id="cancel-graph-1",
        telegram_chat_id=3007,
        input_text="cancel my appointment",
        input_type="text",
        preferred_language="en",
        telegram_profile={},
    )

    active_appointments = result.metadata.get("active_appointments") or []
    assert result.intent == "cancel_appointment"
    assert len(active_appointments) == 1
    assert active_appointments[0]["service_type"] == "cleaning"
    assert "Appointment:" in result.final_response_text


async def test_graph_cancellation_node_no_appointments(session):
    user = await UserRepository(session).upsert_from_telegram(
        telegram_user_id=3008,
        preferred_language="ru",
    )
    conversation = await ConversationRepository(session).get_or_create(
        user_id=user.id,
        telegram_chat_id=3008,
    )

    result = await run_bot_graph(
        session=session,
        user=user,
        conversation=conversation,
        trace_id="cancel-graph-2",
        telegram_chat_id=3008,
        input_text="отмени запись",
        input_type="text",
        preferred_language="ru",
        telegram_profile={},
    )

    active_appointments = result.metadata.get("active_appointments") or []
    assert result.intent == "cancel_appointment"
    assert active_appointments == []
    assert "активных записей" in result.final_response_text


def test_cancel_keyboards_render_expected_buttons():
    appointments = [
        {
            "id": 1,
            "start_at": datetime(2026, 5, 22, 9, 0, tzinfo=TZ).isoformat(),
            "end_at": datetime(2026, 5, 22, 9, 30, tzinfo=TZ).isoformat(),
            "timezone": "Asia/Tashkent",
            "service_type": "consultation",
            "doctor_type": "therapist",
            "patient_name": "Ali",
            "status": "scheduled",
            "calendar_event_id": "ev-1",
            "formatted": "22.05 09:00",
        }
    ]
    keyboard = cancel_appointments_keyboard(appointments)

    assert keyboard.inline_keyboard[0][0].callback_data == "cancel_appointment:1"
    assert "22.05 09:00" in keyboard.inline_keyboard[0][0].text
    assert "consultation" in keyboard.inline_keyboard[0][0].text
