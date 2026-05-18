from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Select, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import (
    Appointment,
    AppointmentHistory,
    ClinicKnowledge,
    Conversation,
    Escalation,
    ExecutionRun,
    Message,
    ReminderJob,
    User,
    UserPhone,
)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_telegram_id(self, telegram_user_id: int) -> User | None:
        result = await self.session.execute(
            select(User).where(User.telegram_user_id == telegram_user_id)
        )
        return result.scalar_one_or_none()

    async def upsert_from_telegram(
        self,
        *,
        telegram_user_id: int,
        telegram_username: str | None = None,
        telegram_first_name: str | None = None,
        telegram_last_name: str | None = None,
        preferred_language: str | None = None,
    ) -> User:
        user = await self.get_by_telegram_id(telegram_user_id)
        if user is None:
            user = User(telegram_user_id=telegram_user_id)
            self.session.add(user)

        user.telegram_username = telegram_username
        user.telegram_first_name = telegram_first_name
        user.telegram_last_name = telegram_last_name
        if preferred_language is not None:
            user.preferred_language = preferred_language
        await self.session.flush()
        return user

    async def set_language(self, telegram_user_id: int, language: str) -> User:
        user = await self.get_by_telegram_id(telegram_user_id)
        if user is None:
            user = User(telegram_user_id=telegram_user_id)
            self.session.add(user)
        user.preferred_language = language
        await self.session.flush()
        return user

    async def add_phone(
        self,
        *,
        user_id: int,
        phone: str,
        is_primary: bool = False,
        source: str | None = None,
    ) -> UserPhone:
        if is_primary:
            existing = await self.session.execute(
                select(UserPhone).where(UserPhone.user_id == user_id)
            )
            for current in existing.scalars():
                current.is_primary = False

        result = await self.session.execute(
            select(UserPhone).where(
                UserPhone.user_id == user_id,
                UserPhone.phone == phone,
            )
        )
        user_phone = result.scalar_one_or_none()
        if user_phone is None:
            user_phone = UserPhone(user_id=user_id, phone=phone, source=source)
            self.session.add(user_phone)
        user_phone.is_primary = is_primary
        if source is not None:
            user_phone.source = source
        await self.session.flush()
        return user_phone


class ConversationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_or_create(
        self,
        *,
        user_id: int,
        telegram_chat_id: int,
    ) -> Conversation:
        result = await self.session.execute(
            select(Conversation).where(
                Conversation.user_id == user_id,
                Conversation.telegram_chat_id == telegram_chat_id,
            )
        )
        conversation = result.scalar_one_or_none()
        if conversation is None:
            conversation = Conversation(
                user_id=user_id,
                telegram_chat_id=telegram_chat_id,
            )
            self.session.add(conversation)
        conversation.last_message_at = datetime.now(UTC)
        await self.session.flush()
        return conversation

    async def update_state(
        self,
        *,
        conversation_id: int,
        current_flow: str | None = None,
        current_state: str | None = None,
        summary: str | None = None,
    ) -> Conversation:
        conversation = await self.session.get(Conversation, conversation_id)
        if conversation is None:
            raise ValueError(f"Conversation {conversation_id} not found")
        conversation.current_flow = current_flow
        conversation.current_state = current_state
        if summary is not None:
            conversation.summary = summary
        conversation.last_message_at = datetime.now(UTC)
        await self.session.flush()
        return conversation


class MessageRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def save_message(
        self,
        *,
        user_id: int,
        direction: str,
        message_type: str,
        conversation_id: int | None = None,
        telegram_message_id: int | None = None,
        language: str | None = None,
        text: str | None = None,
        raw_payload: dict[str, Any] | None = None,
        trace_id: str | None = None,
    ) -> Message:
        message = Message(
            user_id=user_id,
            conversation_id=conversation_id,
            telegram_message_id=telegram_message_id,
            direction=direction,
            message_type=message_type,
            language=language,
            text=text,
            raw_payload=raw_payload,
            trace_id=trace_id,
        )
        self.session.add(message)
        await self.session.flush()
        return message


class AppointmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        user_id: int,
        service_type: str,
        doctor_type: str,
        start_at: datetime,
        end_at: datetime,
        patient_name: str,
        primary_phone: str,
        timezone: str = "Asia/Tashkent",
        calendar_event_id: str | None = None,
        calendar_etag: str | None = None,
        conversation_summary: str | None = None,
        created_trace_id: str | None = None,
    ) -> Appointment:
        appointment = Appointment(
            user_id=user_id,
            calendar_event_id=calendar_event_id,
            calendar_etag=calendar_etag,
            service_type=service_type,
            doctor_type=doctor_type,
            start_at=start_at,
            end_at=end_at,
            timezone=timezone,
            patient_name=patient_name,
            primary_phone=primary_phone,
            conversation_summary=conversation_summary,
            created_trace_id=created_trace_id,
        )
        self.session.add(appointment)
        await self.session.flush()
        await self.add_history(
            appointment_id=appointment.id,
            action="created",
            actor="bot",
            new_data={"status": appointment.status, "start_at": start_at.isoformat()},
        )
        return appointment

    async def get_active_future_by_user(
        self,
        *,
        user_id: int,
        now: datetime | None = None,
    ) -> list[Appointment]:
        stmt: Select[tuple[Appointment]] = select(Appointment).where(
            Appointment.user_id == user_id,
            Appointment.status == "scheduled",
        )
        if now is not None:
            stmt = stmt.where(Appointment.start_at >= now)
        result = await self.session.execute(stmt.order_by(Appointment.start_at))
        return list(result.scalars())

    async def update_status(
        self,
        *,
        appointment_id: int,
        status: str,
        actor: str,
        cancelled_at: datetime | None = None,
    ) -> Appointment:
        appointment = await self.session.get(Appointment, appointment_id)
        if appointment is None:
            raise ValueError(f"Appointment {appointment_id} not found")
        old_status = appointment.status
        appointment.status = status
        if cancelled_at is not None:
            appointment.cancelled_at = cancelled_at
        await self.session.flush()
        await self.add_history(
            appointment_id=appointment.id,
            action=status,
            actor=actor,
            old_data={"status": old_status},
            new_data={"status": status},
        )
        return appointment

    async def cancel(self, *, appointment_id: int, actor: str) -> Appointment:
        return await self.update_status(
            appointment_id=appointment_id,
            status="cancelled",
            actor=actor,
            cancelled_at=datetime.now(UTC),
        )

    async def add_history(
        self,
        *,
        appointment_id: int,
        action: str,
        actor: str,
        old_data: dict[str, Any] | None = None,
        new_data: dict[str, Any] | None = None,
    ) -> AppointmentHistory:
        history = AppointmentHistory(
            appointment_id=appointment_id,
            action=action,
            actor=actor,
            old_data=old_data,
            new_data=new_data,
        )
        self.session.add(history)
        await self.session.flush()
        return history


class ClinicKnowledgeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_active_by_language(self, language: str) -> ClinicKnowledge | None:
        result = await self.session.execute(
            select(ClinicKnowledge)
            .where(
                ClinicKnowledge.language == language,
                ClinicKnowledge.is_active.is_(True),
            )
            .order_by(ClinicKnowledge.version.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        *,
        language: str,
        content: str,
        version: int = 1,
        is_active: bool = True,
    ) -> ClinicKnowledge:
        knowledge = ClinicKnowledge(
            language=language,
            content=content,
            version=version,
            is_active=is_active,
        )
        self.session.add(knowledge)
        await self.session.flush()
        return knowledge

    async def count(self) -> int:
        result = await self.session.execute(select(func.count(ClinicKnowledge.id)))
        return int(result.scalar_one())


class EscalationRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        user_id: int,
        reason: str,
        summary: str | None = None,
        phone: str | None = None,
        admin_chat_id: str | None = None,
        admin_message_id: int | None = None,
    ) -> Escalation:
        escalation = Escalation(
            user_id=user_id,
            reason=reason,
            summary=summary,
            phone=phone,
            admin_chat_id=admin_chat_id,
            admin_message_id=admin_message_id,
        )
        self.session.add(escalation)
        await self.session.flush()
        return escalation


class ReminderRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        appointment_id: int,
        reminder_type: str,
        send_at: datetime,
    ) -> ReminderJob:
        reminder = ReminderJob(
            appointment_id=appointment_id,
            reminder_type=reminder_type,
            send_at=send_at,
        )
        self.session.add(reminder)
        await self.session.flush()
        return reminder

    async def get_due_pending(self, now: datetime) -> list[ReminderJob]:
        result = await self.session.execute(
            select(ReminderJob)
            .where(ReminderJob.status == "pending", ReminderJob.send_at <= now)
            .order_by(ReminderJob.send_at)
        )
        return list(result.scalars())

    async def mark_sent(self, reminder_id: int) -> ReminderJob:
        reminder = await self.session.get(ReminderJob, reminder_id)
        if reminder is None:
            raise ValueError(f"Reminder job {reminder_id} not found")
        reminder.status = "sent"
        reminder.sent_at = datetime.now(UTC)
        await self.session.flush()
        return reminder

    async def cancel_for_appointment(self, appointment_id: int) -> int:
        result = await self.session.execute(
            select(ReminderJob).where(
                ReminderJob.appointment_id == appointment_id,
                ReminderJob.status == "pending",
            )
        )
        count = 0
        for reminder in result.scalars():
            reminder.status = "cancelled"
            count += 1
        await self.session.flush()
        return count


class ExecutionRunRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def start(
        self,
        *,
        trace_id: str,
        status: str = "running",
        user_id: int | None = None,
        conversation_id: int | None = None,
        input_message_id: int | None = None,
        graph_input: dict[str, Any] | None = None,
    ) -> ExecutionRun:
        run = ExecutionRun(
            trace_id=trace_id,
            status=status,
            started_at=datetime.now(UTC),
            user_id=user_id,
            conversation_id=conversation_id,
            input_message_id=input_message_id,
            graph_input=graph_input,
        )
        self.session.add(run)
        await self.session.flush()
        return run

    async def finish(
        self,
        *,
        trace_id: str,
        status: str,
        intent: str | None = None,
        graph_output: dict[str, Any] | None = None,
        tool_calls: list[dict[str, Any]] | None = None,
        error: str | None = None,
    ) -> ExecutionRun:
        result = await self.session.execute(
            select(ExecutionRun).where(ExecutionRun.trace_id == trace_id)
        )
        run = result.scalar_one_or_none()
        if run is None:
            raise ValueError(f"Execution run {trace_id} not found")

        finished_at = datetime.now(UTC)
        run.status = status
        run.intent = intent
        run.finished_at = finished_at
        if run.started_at is not None:
            run.duration_ms = int(
                (finished_at - _as_utc(run.started_at)).total_seconds() * 1000
            )
        run.graph_output = graph_output
        run.tool_calls = tool_calls
        run.error = error
        await self.session.flush()
        return run
