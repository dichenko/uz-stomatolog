from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.db.models import Appointment, ReminderJob, UserPhone
from app.db.repositories import ConversationRepository, UserRepository
from app.services.booking import confirm_booking_slot, handle_booking_message
from app.telegram.keyboards import booking_slots_keyboard, contact_request_keyboard

TZ = ZoneInfo("Asia/Tashkent")


class FakeCalendarService:
    def __init__(self) -> None:
        self.created_events: list = []

    async def list_events(self, *, time_min, time_max):
        return []

    async def create_event(self, event):
        self.created_events.append(event)
        return {"id": "calendar-event-1", "etag": "etag-1"}


class FakeAdminBot:
    def __init__(self) -> None:
        self.messages: list[dict[str, str]] = []

    async def send_message(self, *, chat_id: str, text: str):
        self.messages.append({"chat_id": chat_id, "text": text})
        return SimpleNamespace(message_id=555)


async def test_booking_flow_collects_data_proposes_slot_and_confirms(
    session,
    monkeypatch,
):
    monkeypatch.setattr(
        "app.services.admin_notify.get_settings",
        lambda: SimpleNamespace(admin_telegram_chat_id="-100123"),
    )
    user = await UserRepository(session).upsert_from_telegram(
        telegram_user_id=2001,
        telegram_username="ali",
        preferred_language="en",
    )
    conversation = await ConversationRepository(session).get_or_create(
        user_id=user.id,
        telegram_chat_id=2001,
    )
    calendar = FakeCalendarService()
    now = datetime(2026, 5, 21, 8, 0, tzinfo=TZ)

    first = await handle_booking_message(
        session=session,
        user=user,
        conversation=conversation,
        input_text="I want to book a cleaning",
        language="en",
        service_type="cleaning",
        doctor_type="therapist",
        calendar_service=calendar,
        now=now,
    )
    second = await handle_booking_message(
        session=session,
        user=user,
        conversation=conversation,
        input_text="Ali Karimov +998 90 123 45 67",
        language="en",
        service_type="cleaning",
        doctor_type="therapist",
        calendar_service=calendar,
        now=now,
    )
    confirmation = await confirm_booking_slot(
        session=session,
        user=user,
        conversation=conversation,
        slot_index=0,
        language="en",
        calendar_service=calendar,
        admin_bot=FakeAdminBot(),
    )

    appointments = (await session.execute(select(Appointment))).scalars().all()
    reminders = (await session.execute(select(ReminderJob))).scalars().all()
    phones = (await session.execute(select(UserPhone))).scalars().all()

    assert first.missing_fields == ["patient_name", "phone"]
    assert second.missing_fields == []
    assert len(second.proposed_slots) == 3
    assert confirmation.appointment.calendar_event_id == "calendar-event-1"
    assert confirmation.admin_notification_sent is True
    assert appointments[0].service_type == "cleaning"
    assert appointments[0].patient_name == "Ali Karimov"
    assert phones[0].phone == "+998901234567"
    assert {reminder.reminder_type for reminder in reminders} == {
        "day_before",
        "two_hours_before",
    }
    assert calendar.created_events[0].appointment_id == confirmation.appointment.id
    assert "confirmed" in confirmation.text.casefold()


def test_booking_keyboards_render_expected_buttons():
    slots = [
        {
            "start_at": datetime(2026, 5, 21, 9, 0, tzinfo=TZ).isoformat(),
            "end_at": datetime(2026, 5, 21, 9, 30, tzinfo=TZ).isoformat(),
            "timezone": "Asia/Tashkent",
            "service_type": "consultation",
            "doctor_type": "therapist",
        }
    ]
    slot_keyboard = booking_slots_keyboard(slots)
    contact_keyboard = contact_request_keyboard("en")

    assert slot_keyboard.inline_keyboard[0][0].callback_data == "booking_slot:0"
    assert slot_keyboard.inline_keyboard[0][0].text == "21.05 09:00"
    assert contact_keyboard.keyboard[0][0].request_contact is True
