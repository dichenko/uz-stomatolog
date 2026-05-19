from dataclasses import dataclass
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

CLINIC_OPEN_TIME = time(hour=9)
CLINIC_CLOSE_TIME = time(hour=21)
SLOT_STEP_MINUTES = 30
MAX_SIMULTANEOUS_APPOINTMENTS = 2
SERVICE_DURATIONS_MINUTES = {
    "consultation": 30,
    "cleaning": 60,
    "treatment": 90,
}


@dataclass(frozen=True)
class BusyEvent:
    start_at: datetime
    end_at: datetime
    doctor_type: str | None = None
    event_id: str | None = None
    summary: str | None = None


@dataclass(frozen=True)
class AvailabilitySlot:
    start_at: datetime
    end_at: datetime
    timezone: str
    service_type: str
    doctor_type: str

    def as_dict(self) -> dict[str, str]:
        return {
            "start_at": self.start_at.isoformat(),
            "end_at": self.end_at.isoformat(),
            "timezone": self.timezone,
            "service_type": self.service_type,
            "doctor_type": self.doctor_type,
        }


def find_available_slots(
    *,
    busy_events: list[BusyEvent],
    service_type: str,
    doctor_type: str,
    start_from: datetime,
    timezone: str = "Asia/Tashkent",
    days_ahead: int = 14,
    limit: int = 3,
) -> list[AvailabilitySlot]:
    duration_minutes = SERVICE_DURATIONS_MINUTES.get(service_type, 30)
    tz = ZoneInfo(timezone)
    current = _round_up_to_slot(start_from.astimezone(tz))
    end_search = current + timedelta(days=days_ahead)
    slots: list[AvailabilitySlot] = []

    while current < end_search and len(slots) < limit:
        if _is_working_candidate(current, duration_minutes):
            end_at = current + timedelta(minutes=duration_minutes)
            if is_slot_available(
                busy_events=busy_events,
                start_at=current,
                end_at=end_at,
                doctor_type=doctor_type,
                timezone=timezone,
            ):
                slots.append(
                    AvailabilitySlot(
                        start_at=current,
                        end_at=end_at,
                        timezone=timezone,
                        service_type=service_type,
                        doctor_type=doctor_type,
                    )
                )
        current += timedelta(minutes=SLOT_STEP_MINUTES)

    return slots


def is_slot_available(
    *,
    busy_events: list[BusyEvent],
    start_at: datetime,
    end_at: datetime,
    doctor_type: str,
    timezone: str = "Asia/Tashkent",
) -> bool:
    tz = ZoneInfo(timezone)
    slot_start = start_at.astimezone(tz)
    slot_end = end_at.astimezone(tz)
    overlapping_events = [
        event
        for event in busy_events
        if _overlaps(
            slot_start,
            slot_end,
            event.start_at.astimezone(tz),
            event.end_at.astimezone(tz),
        )
    ]
    if len(overlapping_events) >= MAX_SIMULTANEOUS_APPOINTMENTS:
        return False

    return not any(event.doctor_type == doctor_type for event in overlapping_events)


def _is_working_candidate(start_at: datetime, duration_minutes: int) -> bool:
    if start_at.weekday() == 6:
        return False
    end_at = start_at + timedelta(minutes=duration_minutes)
    return (
        start_at.time() >= CLINIC_OPEN_TIME
        and end_at.time() <= CLINIC_CLOSE_TIME
        and start_at.date() == end_at.date()
    )


def _round_up_to_slot(value: datetime) -> datetime:
    minute = (value.minute // SLOT_STEP_MINUTES) * SLOT_STEP_MINUTES
    rounded = value.replace(minute=minute, second=0, microsecond=0)
    if rounded < value:
        rounded += timedelta(minutes=SLOT_STEP_MINUTES)
    return rounded


def _overlaps(
    start_a: datetime,
    end_a: datetime,
    start_b: datetime,
    end_b: datetime,
) -> bool:
    return start_a < end_b and start_b < end_a
