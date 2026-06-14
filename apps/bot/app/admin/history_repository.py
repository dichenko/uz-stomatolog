from dataclasses import dataclass, field
from datetime import date, datetime, time
from typing import Any, Literal

from sqlalchemy import Select, String, cast, exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.elements import ColumnElement

from app.db.models import Appointment, ExecutionRun, Message, User

PAGE_SIZE_OPTIONS = (10, 20, 50, 100)
DEFAULT_PAGE_SIZE = 10

HistorySortField = Literal[
    "date",
    "time",
    "direction",
    "tg_id",
    "user_text",
    "llm_answer_text",
    "language",
    "message_type",
    "agent_action",
]


@dataclass(frozen=True)
class HistoryFilters:
    date_from: date | None = None
    date_to: date | None = None
    time_from: time | None = None
    time_to: time | None = None
    direction: str = ""
    tg_id: str = ""
    user_text: str = ""
    llm_answer_text: str = ""
    language: str = ""
    message_type: str = ""
    agent_action: str = ""
    global_search: str = ""


@dataclass(frozen=True)
class HistoryQuery:
    start: int = 0
    length: int = DEFAULT_PAGE_SIZE
    sort_field: HistorySortField = "date"
    sort_dir: Literal["asc", "desc"] = "desc"
    filters: HistoryFilters = field(default_factory=HistoryFilters)


@dataclass(frozen=True)
class HistoryRow:
    date: str
    time: str
    direction: str
    tg_id: int
    user_text: str
    llm_answer_text: str
    language: str
    message_type: str
    agent_action: str
    created_at: datetime

    def as_dict(self) -> dict[str, Any]:
        return {
            "date": self.date,
            "time": self.time,
            "direction": self.direction,
            "tg_id": self.tg_id,
            "user_text": self.user_text,
            "llm_answer_text": self.llm_answer_text,
            "language": self.language,
            "message_type": self.message_type,
            "agent_action": self.agent_action,
        }


@dataclass(frozen=True)
class HistoryPage:
    rows: list[HistoryRow]
    total: int
    filtered: int


@dataclass(frozen=True)
class _RawHistoryRow:
    message: Message
    user: User
    payload: dict[str, Any]
    run_intent: str | None
    run_output: dict[str, Any]
    has_created_appointment: bool


class MessageHistoryRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def fetch_page(self, query: HistoryQuery) -> HistoryPage:
        normalized = _normalize_query(query)
        total = await self._count_total()

        if _requires_python_action_pass(normalized):
            raw_rows = await self._fetch_raw(normalized, paginate=False)
            rows = [_to_history_row(row) for row in raw_rows]
            rows = _apply_python_filters(rows, normalized.filters)
            rows = _sort_rows(rows, normalized.sort_field, normalized.sort_dir)
            filtered = len(rows)
            page_rows = rows[normalized.start : normalized.start + normalized.length]
            return HistoryPage(rows=page_rows, total=total, filtered=filtered)

        filtered = await self._count_filtered(normalized)
        raw_rows = await self._fetch_raw(normalized, paginate=True)
        rows = [_to_history_row(row) for row in raw_rows]
        return HistoryPage(rows=rows, total=total, filtered=filtered)

    async def _count_total(self) -> int:
        stmt = select(func.count(Message.id)).where(*_base_message_conditions())
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def _count_filtered(self, query: HistoryQuery) -> int:
        stmt = (
            select(func.count(Message.id))
            .select_from(Message)
            .join(User, User.id == Message.user_id)
            .where(*_base_message_conditions())
        )
        stmt = _apply_sql_filters(stmt, query.filters)
        result = await self.session.execute(stmt)
        return int(result.scalar_one())

    async def _fetch_raw(
        self,
        query: HistoryQuery,
        *,
        paginate: bool,
    ) -> list[_RawHistoryRow]:
        run = aliased(ExecutionRun)
        stmt = (
            select(
                Message,
                User,
                Message.raw_payload.label("payload"),
                run.intent.label("run_intent"),
                run.graph_output.label("run_output"),
                exists()
                .where(Appointment.created_trace_id == Message.trace_id)
                .label("has_created_appointment"),
            )
            .select_from(Message)
            .join(User, User.id == Message.user_id)
            .outerjoin(run, run.input_message_id == Message.id)
            .where(*_base_message_conditions())
        )
        stmt = _apply_sql_filters(stmt, query.filters)
        stmt = _apply_sql_sort(stmt, query.sort_field, query.sort_dir)
        if paginate:
            stmt = stmt.offset(query.start).limit(query.length)

        result = await self.session.execute(stmt)
        rows = []
        for (
            input_message,
            user,
            payload,
            run_intent,
            run_output,
            has_created_appointment,
        ) in result.all():
            rows.append(
                _RawHistoryRow(
                    message=input_message,
                    user=user,
                    payload=payload if isinstance(payload, dict) else {},
                    run_intent=run_intent,
                    run_output=run_output if isinstance(run_output, dict) else {},
                    has_created_appointment=bool(has_created_appointment),
                )
            )
        return rows


def _normalize_query(query: HistoryQuery) -> HistoryQuery:
    length = query.length if query.length in PAGE_SIZE_OPTIONS else DEFAULT_PAGE_SIZE
    start = max(query.start, 0)
    sort_dir: Literal["asc", "desc"] = "asc" if query.sort_dir == "asc" else "desc"
    return HistoryQuery(
        start=start,
        length=length,
        sort_field=query.sort_field,
        sort_dir=sort_dir,
        filters=query.filters,
    )


def _requires_python_action_pass(query: HistoryQuery) -> bool:
    return (
        bool(query.filters.agent_action.strip())
        or query.filters.time_from is not None
        or query.filters.time_to is not None
        or query.sort_field == "time"
        or query.sort_field == "agent_action"
    )


def _base_message_conditions() -> tuple[ColumnElement[bool], ...]:
    return (
        Message.text.is_not(None),
        Message.text != "",
        Message.message_type.in_(("text", "voice", "callback", "system")),
    )


def _apply_sql_filters(
    stmt: Select,
    filters: HistoryFilters,
) -> Select:
    conditions: list[ColumnElement[bool]] = []
    if filters.date_from is not None:
        conditions.append(
            Message.created_at >= datetime.combine(filters.date_from, time.min)
        )
    if filters.date_to is not None:
        conditions.append(
            Message.created_at <= datetime.combine(filters.date_to, time.max)
        )
    if filters.tg_id:
        conditions.append(
            cast(User.telegram_user_id, String).like(f"%{filters.tg_id}%")
        )
    if filters.direction:
        conditions.append(Message.direction == filters.direction)
    if filters.user_text:
        conditions.append(Message.direction == "in")
        conditions.append(Message.text.ilike(f"%{filters.user_text}%"))
    if filters.llm_answer_text:
        conditions.append(Message.direction == "out")
        conditions.append(Message.text.ilike(f"%{filters.llm_answer_text}%"))
    if filters.language:
        language_expr = func.coalesce(Message.language, User.preferred_language, "")
        conditions.append(language_expr == filters.language)
    if filters.message_type:
        conditions.append(Message.message_type == filters.message_type)
    if filters.global_search:
        needle = f"%{filters.global_search}%"
        conditions.append(
            or_(
                Message.text.ilike(needle),
                cast(User.telegram_user_id, String).like(needle),
                func.coalesce(
                    Message.language, User.preferred_language, ""
                ).ilike(needle),
                Message.message_type.ilike(needle),
                Message.direction.ilike(needle),
            )
        )
    if conditions:
        stmt = stmt.where(*conditions)
    return stmt


def _apply_sql_sort(
    stmt: Select,
    sort_field: HistorySortField,
    sort_dir: Literal["asc", "desc"],
) -> Select:
    sort_expr = _sort_expression(sort_field)
    ordered = sort_expr.asc() if sort_dir == "asc" else sort_expr.desc()
    return stmt.order_by(ordered, Message.id.desc())


def _sort_expression(sort_field: HistorySortField):
    if sort_field == "time":
        return Message.created_at
    if sort_field == "direction":
        return Message.direction
    if sort_field == "tg_id":
        return User.telegram_user_id
    if sort_field == "user_text":
        return Message.text
    if sort_field == "llm_answer_text":
        return Message.text
    if sort_field == "language":
        return func.coalesce(Message.language, User.preferred_language, "")
    if sort_field == "message_type":
        return Message.message_type
    return Message.created_at


def _to_history_row(row: _RawHistoryRow) -> HistoryRow:
    message = row.message
    created_at = message.created_at
    language = message.language or row.user.preferred_language or ""
    message_text = message.text or ""
    return HistoryRow(
        date=created_at.date().isoformat(),
        time=created_at.time().replace(microsecond=0).isoformat(),
        direction=message.direction,
        tg_id=row.user.telegram_user_id,
        user_text=message_text if message.direction == "in" else "",
        llm_answer_text=message_text if message.direction == "out" else "",
        language=language,
        message_type=message.message_type,
        agent_action=", ".join(_detect_actions(row)),
        created_at=created_at,
    )


def _detect_actions(row: _RawHistoryRow) -> list[str]:
    payload = row.payload
    output = row.run_output
    actions: list[str] = []

    if row.has_created_appointment or payload.get("booking_confirmed"):
        if payload.get("calendar_event_id"):
            actions.append("Создана запись в календаре")
        else:
            actions.append("Создана запись")
    if payload.get("cancellation_confirmed"):
        if payload.get("calendar_cancelled"):
            actions.append("Отменена запись в календаре")
        else:
            actions.append("Отменена запись")
    if payload.get("reschedule_confirmed"):
        if payload.get("calendar_event_updated"):
            actions.append("Перенесена запись в календаре")
        else:
            actions.append("Перенесена запись")
    if payload.get("escalation_id") or output.get("escalation_id"):
        actions.append("Эскалация админам")
    if payload.get("admin_notification_sent") or output.get("admin_notification_sent"):
        actions.append("Оповещение группы админов")

    return _dedupe(actions)


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        if value not in seen:
            result.append(value)
            seen.add(value)
    return result


def _apply_python_filters(
    rows: list[HistoryRow],
    filters: HistoryFilters,
) -> list[HistoryRow]:
    filtered = rows
    if filters.time_from is not None:
        filtered = [
            row for row in filtered if time.fromisoformat(row.time) >= filters.time_from
        ]
    if filters.time_to is not None:
        filtered = [
            row for row in filtered if time.fromisoformat(row.time) <= filters.time_to
        ]
    if filters.agent_action.strip():
        needle = filters.agent_action.casefold()
        filtered = [
            row for row in filtered if needle in row.agent_action.casefold()
        ]
    return filtered


def _sort_rows(
    rows: list[HistoryRow],
    sort_field: HistorySortField,
    sort_dir: Literal["asc", "desc"],
) -> list[HistoryRow]:
    reverse = sort_dir == "desc"
    return sorted(rows, key=lambda row: getattr(row, sort_field), reverse=reverse)


def parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def parse_time(value: str | None) -> time | None:
    if not value:
        return None
    try:
        return time.fromisoformat(value)
    except ValueError:
        return None
