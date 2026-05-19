from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from app.calendar import (
    BusyEvent,
    CalendarConfigError,
    CalendarEventCreate,
    CalendarEventUpdate,
    GoogleCalendarService,
    calendar_events_to_busy_events,
    create_google_calendar_service,
    find_available_slots,
    format_event_description,
    format_event_title,
    is_slot_available,
)
from app.config import Settings

TZ = ZoneInfo("Asia/Tashkent")


class FakeExecute:
    def __init__(self, payload):
        self.payload = payload

    def execute(self):
        return self.payload


class FakeEventsResource:
    def __init__(self) -> None:
        self.insert_body = None
        self.patch_body = None
        self.deleted_event_id = None

    def list(self, **_kwargs):
        return FakeExecute({"items": [{"id": "event-1"}]})

    def get(self, **kwargs):
        return FakeExecute({"id": kwargs["eventId"]})

    def insert(self, **kwargs):
        self.insert_body = kwargs["body"]
        return FakeExecute({"id": "created-event", **kwargs["body"]})

    def patch(self, **kwargs):
        self.patch_body = kwargs["body"]
        return FakeExecute({"id": kwargs["eventId"], **kwargs["body"]})

    def delete(self, **kwargs):
        self.deleted_event_id = kwargs["eventId"]
        return FakeExecute({})


class FakeCalendarClient:
    def __init__(self) -> None:
        self.events_resource = FakeEventsResource()

    def events(self):
        return self.events_resource


def test_availability_respects_doctor_and_total_capacity():
    start = datetime(2026, 5, 20, 10, 0, tzinfo=TZ)
    therapist_busy = BusyEvent(
        start_at=start,
        end_at=start + timedelta(minutes=30),
        doctor_type="therapist",
    )
    surgeon_busy = BusyEvent(
        start_at=start,
        end_at=start + timedelta(minutes=30),
        doctor_type="surgeon",
    )

    assert (
        is_slot_available(
            busy_events=[therapist_busy],
            start_at=start,
            end_at=start + timedelta(minutes=30),
            doctor_type="therapist",
        )
        is False
    )
    assert (
        is_slot_available(
            busy_events=[therapist_busy],
            start_at=start,
            end_at=start + timedelta(minutes=30),
            doctor_type="surgeon",
        )
        is True
    )
    assert (
        is_slot_available(
            busy_events=[therapist_busy, surgeon_busy],
            start_at=start,
            end_at=start + timedelta(minutes=30),
            doctor_type="therapist",
        )
        is False
    )


def test_find_available_slots_respects_working_hours_and_sunday():
    sunday = datetime(2026, 5, 24, 10, 0, tzinfo=TZ)
    slots = find_available_slots(
        busy_events=[],
        service_type="consultation",
        doctor_type="therapist",
        start_from=sunday,
        limit=1,
    )

    assert slots[0].start_at.weekday() == 0
    assert slots[0].start_at.hour == 9
    assert slots[0].end_at.hour == 9
    assert slots[0].end_at.minute == 30


async def test_google_calendar_service_crud_uses_expected_payloads():
    fake_client = FakeCalendarClient()
    service = GoogleCalendarService(client=fake_client, calendar_id="calendar-1")
    start = datetime(2026, 5, 20, 10, 0, tzinfo=TZ)
    event = _calendar_event_create(start)

    listed = await service.list_events(
        time_min=start,
        time_max=start + timedelta(days=1),
    )
    fetched = await service.get_event("event-1")
    created = await service.create_event(event)
    updated = await service.update_event(
        "event-1",
        CalendarEventUpdate(
            start_at=start + timedelta(hours=1),
            end_at=start + timedelta(hours=2),
            timezone="Asia/Tashkent",
            doctor_type="surgeon",
        ),
    )
    await service.cancel_event("event-1")

    assert listed == [{"id": "event-1"}]
    assert fetched == {"id": "event-1"}
    assert created["id"] == "created-event"
    assert fake_client.events_resource.insert_body["summary"] == format_event_title(
        event
    )
    assert created["extendedProperties"]["private"]["created_by"] == "telegram_bot"
    assert updated["extendedProperties"]["private"]["doctor_type"] == "surgeon"
    assert fake_client.events_resource.deleted_event_id == "event-1"


def test_calendar_event_formatting_and_busy_event_parsing():
    start = datetime(2026, 5, 20, 10, 0, tzinfo=TZ)
    event = _calendar_event_create(start)
    description = format_event_description(event)
    busy_events = calendar_events_to_busy_events(
        [
            {
                "id": "event-1",
                "summary": "Known",
                "start": {"dateTime": start.isoformat()},
                "end": {"dateTime": (start + timedelta(minutes=30)).isoformat()},
                "extendedProperties": {"private": {"doctor_type": "therapist"}},
            },
            {
                "id": "event-2",
                "summary": "Unknown",
                "start": {"dateTime": (start + timedelta(hours=1)).isoformat()},
                "end": {"dateTime": (start + timedelta(hours=2)).isoformat()},
            },
        ]
    )

    assert format_event_title(event) == "[Bot] consultation — Ali — +998901234567"
    assert "DB appointment ID: 42" in description
    assert "Trace ID: trace-1" in description
    assert busy_events[0].doctor_type == "therapist"
    assert busy_events[1].doctor_type is None


def test_calendar_service_requires_calendar_id():
    with pytest.raises(CalendarConfigError):
        create_google_calendar_service(
            Settings(google_calendar_id=None),
        )


def _calendar_event_create(start: datetime) -> CalendarEventCreate:
    return CalendarEventCreate(
        service_type="consultation",
        doctor_type="therapist",
        start_at=start,
        end_at=start + timedelta(minutes=30),
        timezone="Asia/Tashkent",
        patient_name="Ali",
        phone="+998901234567",
        telegram_user_id=123,
        telegram_username="ali",
        language="en",
        conversation_summary="Asked for appointment",
        appointment_id=42,
        trace_id="trace-1",
    )
