from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from sqlalchemy import select

from app.db.models import Appointment, ReminderJob, User, UserPhone
from app.db.repositories import (
    ConversationRepository,
    MessageRepository,
    UserRepository,
)
from app.services.booking import (
    BookingSlotConflictError,
    confirm_booking_slot,
    handle_booking_message,
)
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
    now = datetime(2026, 5, 24, 8, 0, tzinfo=TZ)

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
    await MessageRepository(session).save_message(
        user_id=user.id,
        conversation_id=conversation.id,
        telegram_message_id=10,
        direction="in",
        message_type="text",
        language="en",
        text="How much is cleaning and can I book it?",
        trace_id="booking-test-1",
    )
    await MessageRepository(session).save_message(
        user_id=user.id,
        conversation_id=conversation.id,
        telegram_message_id=11,
        direction="in",
        message_type="text",
        language="en",
        text="Ali Karimov +998 90 123 45 67",
        trace_id="booking-test-2",
    )
    admin_bot = FakeAdminBot()
    confirmation = await confirm_booking_slot(
        session=session,
        user=user,
        conversation=conversation,
        slot_index=0,
        language="en",
        calendar_service=calendar,
        admin_bot=admin_bot,
        now=now,
    )

    appointments = (await session.execute(select(Appointment))).scalars().all()
    reminders = (await session.execute(select(ReminderJob))).scalars().all()
    phones = (await session.execute(select(UserPhone))).scalars().all()
    db_user = (
        await session.execute(select(User).where(User.id == user.id))
    ).scalar_one()

    assert first.missing_fields == ["patient_name", "phone"]
    assert second.missing_fields == []
    assert len(second.proposed_slots) == 3
    assert confirmation.appointment.calendar_event_id == "calendar-event-1"
    assert confirmation.admin_notification_sent is True
    assert appointments[0].service_type == "cleaning"
    assert appointments[0].patient_name == "Ali Karimov"
    assert db_user.patient_name == "Ali Karimov"
    assert db_user.primary_phone == "+998901234567"
    assert phones[0].phone == "+998901234567"
    assert phones[0].is_primary is True
    assert {reminder.reminder_type for reminder in reminders} == {
        "day_before",
        "two_hours_before",
    }
    assert calendar.created_events[0].appointment_id == confirmation.appointment.id
    assert "confirmed" in confirmation.text.casefold()
    assert "Service: cleaning" in confirmation.text
    assert "Specialist: therapist" in confirmation.text
    assert "Duration: 60 minutes" in confirmation.text
    assert "We look forward to seeing you at our clinic." in confirmation.text
    assert "Conversation summary:" in admin_bot.messages[0]["text"]
    assert "How much is cleaning and can I book it?" in admin_bot.messages[0]["text"]
    assert "Final booking: service=cleaning" in admin_bot.messages[0]["text"]
    assert "[phone]" in admin_bot.messages[0]["text"]


async def test_booking_flow_reuses_saved_patient_contact(session):
    user = await UserRepository(session).upsert_from_telegram(
        telegram_user_id=2004,
        telegram_username="ali",
        preferred_language="en",
    )
    await UserRepository(session).remember_patient_contact(
        user_id=user.id,
        patient_name="Ali Karimov",
        phone="+998901234567",
        source="test",
    )
    conversation = await ConversationRepository(session).get_or_create(
        user_id=user.id,
        telegram_chat_id=2004,
    )

    result = await handle_booking_message(
        session=session,
        user=user,
        conversation=conversation,
        input_text="I want to book a cleaning",
        language="en",
        service_type="cleaning",
        doctor_type="therapist",
        calendar_service=FakeCalendarService(),
        now=datetime(2026, 5, 24, 8, 0, tzinfo=TZ),
    )

    assert result.missing_fields == []
    assert len(result.proposed_slots) == 3


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


async def test_booking_rejects_conflicting_slot(session, monkeypatch):
    monkeypatch.setattr(
        "app.services.admin_notify.get_settings",
        lambda: SimpleNamespace(admin_telegram_chat_id="-100123"),
    )
    user = await UserRepository(session).upsert_from_telegram(
        telegram_user_id=2002,
        preferred_language="en",
    )
    conversation = await ConversationRepository(session).get_or_create(
        user_id=user.id,
        telegram_chat_id=2002,
    )
    now = datetime(2026, 5, 21, 8, 0, tzinfo=TZ)

    result = await handle_booking_message(
        session=session,
        user=user,
        conversation=conversation,
        input_text="Ali +998 90 123 45 67",
        language="en",
        service_type="cleaning",
        doctor_type="therapist",
        calendar_service=FakeCalendarService(),
        now=now,
    )

    assert len(result.proposed_slots) == 3

    busy_start = datetime.fromisoformat(result.proposed_slots[0]["start_at"])
    busy_end = datetime.fromisoformat(result.proposed_slots[0]["end_at"])

    class ConflictingCalendarService:
        async def list_events(self, *, time_min, time_max):
            return [
                {
                    "id": "conflict-event",
                    "status": "confirmed",
                    "summary": "[Bot] treatment — Other Patient — +998",
                    "start": {
                        "dateTime": busy_start.isoformat(),
                        "timeZone": "Asia/Tashkent",
                    },
                    "end": {
                        "dateTime": busy_end.isoformat(),
                        "timeZone": "Asia/Tashkent",
                    },
                    "extendedProperties": {
                        "private": {
                            "doctor_type": "therapist",
                            "created_by": "telegram_bot",
                        }
                    },
                }
            ]

    try:
        await confirm_booking_slot(
            session=session,
            user=user,
            conversation=conversation,
            slot_index=0,
            language="en",
            calendar_service=ConflictingCalendarService(),
            admin_bot=FakeAdminBot(),
            now=now,
        )
        raise AssertionError("Expected BookingSlotConflictError")
    except BookingSlotConflictError:
        pass
