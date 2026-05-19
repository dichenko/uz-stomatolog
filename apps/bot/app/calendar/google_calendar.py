import asyncio
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from google.oauth2 import service_account
from googleapiclient.discovery import build

from app.calendar.availability import BusyEvent
from app.config import Settings, get_settings

GOOGLE_CALENDAR_SCOPES = ("https://www.googleapis.com/auth/calendar",)


class CalendarConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class CalendarEventCreate:
    service_type: str
    doctor_type: str
    start_at: datetime
    end_at: datetime
    timezone: str
    patient_name: str
    phone: str
    telegram_user_id: int
    telegram_username: str | None
    language: str
    conversation_summary: str | None
    appointment_id: int | None
    trace_id: str


@dataclass(frozen=True)
class CalendarEventUpdate:
    start_at: datetime | None = None
    end_at: datetime | None = None
    timezone: str | None = None
    service_type: str | None = None
    doctor_type: str | None = None
    patient_name: str | None = None
    phone: str | None = None
    conversation_summary: str | None = None
    trace_id: str | None = None


class GoogleCalendarService:
    def __init__(self, *, client: Any, calendar_id: str) -> None:
        self.client = client
        self.calendar_id = calendar_id

    async def list_events(
        self,
        *,
        time_min: datetime,
        time_max: datetime,
    ) -> list[dict]:
        return await asyncio.to_thread(self._list_events_sync, time_min, time_max)

    async def get_event(self, event_id: str) -> dict:
        return await asyncio.to_thread(self._get_event_sync, event_id)

    async def create_event(self, event: CalendarEventCreate) -> dict:
        return await asyncio.to_thread(self._create_event_sync, event)

    async def update_event(self, event_id: str, update: CalendarEventUpdate) -> dict:
        return await asyncio.to_thread(self._update_event_sync, event_id, update)

    async def cancel_event(self, event_id: str) -> None:
        await asyncio.to_thread(self._cancel_event_sync, event_id)

    def _list_events_sync(self, time_min: datetime, time_max: datetime) -> list[dict]:
        response = (
            self.client.events()
            .list(
                calendarId=self.calendar_id,
                timeMin=time_min.isoformat(),
                timeMax=time_max.isoformat(),
                singleEvents=True,
                orderBy="startTime",
                showDeleted=False,
            )
            .execute()
        )
        return list(response.get("items", []))

    def _get_event_sync(self, event_id: str) -> dict:
        return (
            self.client.events()
            .get(calendarId=self.calendar_id, eventId=event_id)
            .execute()
        )

    def _create_event_sync(self, event: CalendarEventCreate) -> dict:
        return (
            self.client.events()
            .insert(calendarId=self.calendar_id, body=_create_event_body(event))
            .execute()
        )

    def _update_event_sync(self, event_id: str, update: CalendarEventUpdate) -> dict:
        body = _update_event_body(update)
        return (
            self.client.events()
            .patch(calendarId=self.calendar_id, eventId=event_id, body=body)
            .execute()
        )

    def _cancel_event_sync(self, event_id: str) -> None:
        (
            self.client.events()
            .delete(calendarId=self.calendar_id, eventId=event_id)
            .execute()
        )


def create_google_calendar_service(
    settings: Settings | None = None,
) -> GoogleCalendarService:
    resolved_settings = settings or get_settings()
    calendar_id = resolved_settings.google_calendar_id
    if not calendar_id:
        raise CalendarConfigError("GOOGLE_CALENDAR_ID is required")
    return GoogleCalendarService(
        client=build_google_calendar_client(resolved_settings),
        calendar_id=calendar_id,
    )


def build_google_calendar_client(settings: Settings | None = None) -> Any:
    resolved_settings = settings or get_settings()
    credentials_path = Path(resolved_settings.google_service_account_json_path)
    if not credentials_path.exists():
        raise CalendarConfigError(
            "Google service account file does not exist: "
            f"{resolved_settings.google_service_account_json_path}"
        )
    credentials = service_account.Credentials.from_service_account_file(
        str(credentials_path),
        scopes=list(GOOGLE_CALENDAR_SCOPES),
    )
    return build("calendar", "v3", credentials=credentials, cache_discovery=False)


def calendar_events_to_busy_events(events: list[dict]) -> list[BusyEvent]:
    busy_events: list[BusyEvent] = []
    for event in events:
        if event.get("status") == "cancelled":
            continue
        start = event.get("start", {}).get("dateTime")
        end = event.get("end", {}).get("dateTime")
        if not start or not end:
            continue
        private = event.get("extendedProperties", {}).get("private", {})
        busy_events.append(
            BusyEvent(
                start_at=datetime.fromisoformat(start),
                end_at=datetime.fromisoformat(end),
                doctor_type=private.get("doctor_type"),
                event_id=event.get("id"),
                summary=event.get("summary"),
            )
        )
    return busy_events


def format_event_title(event: CalendarEventCreate) -> str:
    return f"[Bot] {event.service_type} — {event.patient_name} — {event.phone}"


def format_event_description(event: CalendarEventCreate) -> str:
    return "\n".join(
        [
            "Created by Telegram bot",
            "",
            "Patient:",
            f"- Name: {event.patient_name}",
            f"- Phone: {event.phone}",
            f"- Telegram ID: {event.telegram_user_id}",
            f"- Telegram username: {event.telegram_username or '-'}",
            f"- Language: {event.language}",
            "",
            "Appointment:",
            f"- Service: {event.service_type}",
            f"- Doctor: {event.doctor_type}",
            f"- Duration: {_duration_minutes(event)} minutes",
            f"- Conversation summary: {event.conversation_summary or '-'}",
            "",
            "Internal:",
            f"- DB appointment ID: {event.appointment_id or '-'}",
            f"- Trace ID: {event.trace_id}",
        ]
    )


def _create_event_body(event: CalendarEventCreate) -> dict[str, Any]:
    return {
        "summary": format_event_title(event),
        "description": format_event_description(event),
        "start": {"dateTime": event.start_at.isoformat(), "timeZone": event.timezone},
        "end": {"dateTime": event.end_at.isoformat(), "timeZone": event.timezone},
        "extendedProperties": {
            "private": {
                "appointment_id": str(event.appointment_id or ""),
                "telegram_user_id": str(event.telegram_user_id),
                "service_type": event.service_type,
                "doctor_type": event.doctor_type,
                "created_by": "telegram_bot",
            }
        },
    }


def _update_event_body(update: CalendarEventUpdate) -> dict[str, Any]:
    body: dict[str, Any] = {}
    timezone = update.timezone or "Asia/Tashkent"
    if update.start_at is not None:
        body["start"] = {
            "dateTime": update.start_at.isoformat(),
            "timeZone": timezone,
        }
    if update.end_at is not None:
        body["end"] = {"dateTime": update.end_at.isoformat(), "timeZone": timezone}
    private: dict[str, str] = {}
    if update.service_type is not None:
        private["service_type"] = update.service_type
    if update.doctor_type is not None:
        private["doctor_type"] = update.doctor_type
    if private:
        body["extendedProperties"] = {"private": private}
    return body


def _duration_minutes(event: CalendarEventCreate) -> int:
    return int((event.end_at - event.start_at).total_seconds() // 60)
