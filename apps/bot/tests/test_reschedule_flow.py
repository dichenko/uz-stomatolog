from datetime import datetime, timedelta
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from app.db.repositories import (
    AppointmentRepository,
    ConversationRepository,
    UserRepository,
)
from app.graph import run_bot_graph
from app.services.rescheduling import (
    ReschedulingError,
    confirm_reschedule_slot,
    handle_reschedule_message,
    propose_reschedule_slots,
)
from app.telegram.keyboards import (
    reschedule_appointments_keyboard,
    reschedule_slots_keyboard,
)

TZ = ZoneInfo("Asia/Tashkent")


def _future_window(*, days: int, duration_minutes: int = 60):
    start = datetime.now(TZ).replace(microsecond=0) + timedelta(days=days)
    end = start + timedelta(minutes=duration_minutes)
    return start, end


class FakeCalendarService:
    def __init__(self) -> None:
        self.updated_events: list[dict] = []

    async def list_events(self, *, time_min, time_max):
        return []

    async def update_event(self, event_id, update):
        self.updated_events.append({"event_id": event_id, "update": update})
        return {"id": event_id}


class FakeAdminBot:
    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []

    async def send_message(self, *, chat_id: str, text: str):
        self.messages.append({"chat_id": chat_id, "text": text})
        return SimpleNamespace(message_id=888)


async def test_reschedule_message_returns_active_appointments(session):
    user = await UserRepository(session).upsert_from_telegram(
        telegram_user_id=4001,
        preferred_language="en",
    )
    future_start, future_end = _future_window(days=2)
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

    result = await handle_reschedule_message(
        session=session,
        user=user,
        language="en",
    )

    assert len(result.active_appointments) == 1
    assert result.active_appointments[0]["service_type"] == "cleaning"
    assert "past_cleaning" not in result.text
    assert "Appointment:" in result.text


async def test_reschedule_message_no_appointments(session):
    user = await UserRepository(session).upsert_from_telegram(
        telegram_user_id=4002,
        preferred_language="ru",
    )

    result = await handle_reschedule_message(
        session=session,
        user=user,
        language="ru",
    )

    assert result.active_appointments == []
    assert "активных записей" in result.text


async def test_propose_reschedule_slots_returns_slots(session):
    user = await UserRepository(session).upsert_from_telegram(
        telegram_user_id=4003,
        preferred_language="en",
    )
    conversation = await ConversationRepository(session).get_or_create(
        user_id=user.id,
        telegram_chat_id=4003,
    )
    now = datetime(2026, 5, 21, 10, 0, tzinfo=TZ)
    future_start = now + timedelta(days=2)
    future_end = future_start + timedelta(minutes=60)
    appointment = await AppointmentRepository(session).create(
        user_id=user.id,
        service_type="consultation",
        doctor_type="therapist",
        start_at=future_start,
        end_at=future_end,
        patient_name="Test",
        primary_phone="+998901234567",
    )

    result = await propose_reschedule_slots(
        session=session,
        conversation=conversation,
        appointment_id=appointment.id,
        language="en",
        calendar_service=FakeCalendarService(),
        now=now,
    )

    assert len(result.proposed_slots) == 3
    assert result.appointment_id == appointment.id
    assert "new time" in result.text.casefold()


async def test_propose_reschedule_raises_for_cancelled(session):
    user = await UserRepository(session).upsert_from_telegram(
        telegram_user_id=4004,
        preferred_language="en",
    )
    conversation = await ConversationRepository(session).get_or_create(
        user_id=user.id,
        telegram_chat_id=4004,
    )
    appointment = await AppointmentRepository(session).create(
        user_id=user.id,
        service_type="consultation",
        doctor_type="therapist",
        start_at=datetime(2026, 5, 21, 12, 0, tzinfo=TZ),
        end_at=datetime(2026, 5, 21, 12, 30, tzinfo=TZ),
        patient_name="Test",
        primary_phone="+998901234567",
    )
    await AppointmentRepository(session).cancel(
        appointment_id=appointment.id,
        actor="user",
    )

    try:
        await propose_reschedule_slots(
            session=session,
            conversation=conversation,
            appointment_id=appointment.id,
            language="en",
        )
        raise AssertionError("Expected ReschedulingError")
    except ReschedulingError:
        pass


async def test_confirm_reschedule_updates_calendar_db_and_reminders(
    session,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.services.admin_notify.get_settings",
        lambda: SimpleNamespace(admin_telegram_chat_id="-100888"),
    )
    user = await UserRepository(session).upsert_from_telegram(
        telegram_user_id=4005,
        telegram_username="bek",
        preferred_language="en",
    )
    conversation = await ConversationRepository(session).get_or_create(
        user_id=user.id,
        telegram_chat_id=4005,
    )
    now = datetime(2026, 5, 21, 10, 0, tzinfo=TZ)
    original_start = now + timedelta(days=2)
    original_end = original_start + timedelta(minutes=60)
    await AppointmentRepository(session).create(
        user_id=user.id,
        service_type="treatment",
        doctor_type="therapist",
        start_at=original_start,
        end_at=original_end,
        patient_name="Bek Karimov",
        primary_phone="+998901234567",
        calendar_event_id="calendar-ev-reschedule",
    )

    new_start = now + timedelta(days=3, hours=1)
    new_end = new_start + timedelta(minutes=90)
    slot = {
        "start_at": new_start.isoformat(),
        "end_at": new_end.isoformat(),
        "timezone": "Asia/Tashkent",
        "service_type": "treatment",
        "doctor_type": "therapist",
    }
    conversation.current_flow = "rescheduling"
    conversation.summary = (
        '{"appointment_id": 1, "proposed_slots": ['
        + f'{{"start_at": "{slot["start_at"]}", "end_at": "{slot["end_at"]}", '
        + f'"timezone": "{slot["timezone"]}", "service_type": "treatment", '
        + '"doctor_type": "therapist"}'
        + "]}"
    )
    await session.flush()

    calendar = FakeCalendarService()
    admin_bot = FakeAdminBot()

    result = await confirm_reschedule_slot(
        session=session,
        user=user,
        conversation=conversation,
        slot_index=0,
        language="en",
        calendar_service=calendar,
        admin_bot=admin_bot,
    )

    assert result.calendar_event_updated is True
    assert result.admin_notification_sent is True
    assert "rescheduled" in result.text.casefold()
    assert len(calendar.updated_events) == 1
    assert calendar.updated_events[0]["event_id"] == "calendar-ev-reschedule"
    assert "Appointment rescheduled" in admin_bot.messages[0]["text"]
    assert result.appointment.service_type == "treatment"
    assert result.appointment.status == "scheduled"


async def test_graph_reschedule_node_returns_appointments(session):
    user = await UserRepository(session).upsert_from_telegram(
        telegram_user_id=4006,
        preferred_language="en",
    )
    conversation = await ConversationRepository(session).get_or_create(
        user_id=user.id,
        telegram_chat_id=4006,
    )
    future_start, future_end = _future_window(days=1)
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
        trace_id="reschedule-graph-1",
        telegram_chat_id=4006,
        input_text="reschedule my appointment",
        input_type="text",
        preferred_language="en",
        telegram_profile={},
    )

    active_appointments = result.metadata.get("active_appointments") or []
    assert result.intent == "reschedule_appointment"
    assert len(active_appointments) == 1
    assert "Appointment:" in result.final_response_text


def test_reschedule_keyboards_render_expected_buttons():
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
    keyboard = reschedule_appointments_keyboard(appointments)
    assert keyboard.inline_keyboard[0][0].callback_data == "reschedule_select:1"
    assert "22.05 09:00" in keyboard.inline_keyboard[0][0].text

    slots = [
        {
            "start_at": datetime(2026, 5, 23, 10, 0, tzinfo=TZ).isoformat(),
            "end_at": datetime(2026, 5, 23, 10, 30, tzinfo=TZ).isoformat(),
            "timezone": "Asia/Tashkent",
            "service_type": "consultation",
            "doctor_type": "therapist",
        }
    ]
    slot_keyboard = reschedule_slots_keyboard(slots)
    assert slot_keyboard.inline_keyboard[0][0].callback_data == "reschedule_slot:0"
