from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class User(Base, TimestampMixin):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(
        BigInteger,
        unique=True,
        nullable=False,
        index=True,
    )
    telegram_username: Mapped[str | None] = mapped_column(String(255))
    telegram_first_name: Mapped[str | None] = mapped_column(String(255))
    telegram_last_name: Mapped[str | None] = mapped_column(String(255))
    preferred_language: Mapped[str | None] = mapped_column(String(2), index=True)

    phones: Mapped[list["UserPhone"]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    conversations: Mapped[list["Conversation"]] = relationship(back_populates="user")
    messages: Mapped[list["Message"]] = relationship(back_populates="user")
    appointments: Mapped[list["Appointment"]] = relationship(back_populates="user")
    escalations: Mapped[list["Escalation"]] = relationship(back_populates="user")


class UserPhone(Base):
    __tablename__ = "user_phones"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    phone: Mapped[str] = mapped_column(String(64), nullable=False)
    is_primary: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="false",
    )
    source: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    user: Mapped[User] = relationship(back_populates="phones")

    __table_args__ = (
        UniqueConstraint(
            "user_id", "phone", name="uq_user_phones_user_phone"
        ),
    )


class Conversation(Base, TimestampMixin):
    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    telegram_chat_id: Mapped[int] = mapped_column(
        BigInteger, nullable=False, index=True
    )
    current_flow: Mapped[str | None] = mapped_column(String(64))
    current_state: Mapped[str | None] = mapped_column(String(128))
    summary: Mapped[str | None] = mapped_column(Text)
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="conversations")
    messages: Mapped[list["Message"]] = relationship(back_populates="conversation")

    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "telegram_chat_id",
            name="uq_conversations_user_chat",
        ),
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"),
        index=True,
    )
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger, index=True)
    direction: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    message_type: Mapped[str] = mapped_column(String(16), nullable=False, index=True)
    language: Mapped[str | None] = mapped_column(String(2), index=True)
    text: Mapped[str | None] = mapped_column(Text)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    trace_id: Mapped[str | None] = mapped_column(String(64), index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
        index=True,
    )

    user: Mapped[User] = relationship(back_populates="messages")
    conversation: Mapped[Conversation | None] = relationship(back_populates="messages")


class Appointment(Base, TimestampMixin):
    __tablename__ = "appointments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    calendar_event_id: Mapped[str | None] = mapped_column(
        String(255),
        unique=True,
        index=True,
    )
    calendar_etag: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default="scheduled",
        index=True,
    )
    service_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    doctor_type: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    start_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    end_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    timezone: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        server_default="Asia/Tashkent",
    )
    patient_name: Mapped[str] = mapped_column(String(255), nullable=False)
    primary_phone: Mapped[str] = mapped_column(String(64), nullable=False)
    conversation_summary: Mapped[str | None] = mapped_column(Text)
    created_trace_id: Mapped[str | None] = mapped_column(String(64), index=True)
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    user: Mapped[User] = relationship(back_populates="appointments")
    history: Mapped[list["AppointmentHistory"]] = relationship(
        back_populates="appointment",
        cascade="all, delete-orphan",
    )
    reminder_jobs: Mapped[list["ReminderJob"]] = relationship(
        back_populates="appointment",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        Index("ix_appointments_user_status_start", "user_id", "status", "start_at"),
    )


class AppointmentHistory(Base):
    __tablename__ = "appointment_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    appointment_id: Mapped[int] = mapped_column(
        ForeignKey("appointments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(64), nullable=False, index=True)
    old_data: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    new_data: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    appointment: Mapped[Appointment] = relationship(back_populates="history")


class ClinicKnowledge(Base, TimestampMixin):
    __tablename__ = "clinic_knowledge"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    language: Mapped[str] = mapped_column(String(2), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        server_default="true",
        index=True,
    )

    __table_args__ = (
        UniqueConstraint(
            "language",
            "version",
            name="uq_clinic_knowledge_language_version",
        ),
    )


class Escalation(Base, TimestampMixin):
    __tablename__ = "escalations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    reason: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default="new",
        index=True,
    )
    summary: Mapped[str | None] = mapped_column(Text)
    phone: Mapped[str | None] = mapped_column(String(64))
    admin_chat_id: Mapped[str | None] = mapped_column(String(128))
    admin_message_id: Mapped[int | None] = mapped_column(BigInteger)

    user: Mapped[User] = relationship(back_populates="escalations")


class ReminderJob(Base, TimestampMixin):
    __tablename__ = "reminder_jobs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    appointment_id: Mapped[int] = mapped_column(
        ForeignKey("appointments.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    reminder_type: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    send_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        server_default="pending",
        index=True,
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error: Mapped[str | None] = mapped_column(Text)

    appointment: Mapped[Appointment] = relationship(back_populates="reminder_jobs")

    __table_args__ = (
        UniqueConstraint(
            "appointment_id",
            "reminder_type",
            name="uq_reminder_jobs_appointment_type",
        ),
    )


class ExecutionRun(Base):
    __tablename__ = "execution_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    trace_id: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        index=True,
    )
    conversation_id: Mapped[int | None] = mapped_column(
        ForeignKey("conversations.id", ondelete="SET NULL"),
        index=True,
    )
    input_message_id: Mapped[int | None] = mapped_column(
        ForeignKey("messages.id", ondelete="SET NULL"),
        index=True,
    )
    intent: Mapped[str | None] = mapped_column(String(64), index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, index=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    graph_input: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    graph_output: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    tool_calls: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)


class AdminSetting(Base):
    __tablename__ = "admin_settings"

    key: Mapped[str] = mapped_column(Text, primary_key=True)
    value: Mapped[dict[str, Any]] = mapped_column(
        JSON, nullable=False, server_default="'{}'::jsonb"
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_by_tg_id: Mapped[str | None] = mapped_column(Text)


class AdminAuditLog(Base):
    __tablename__ = "admin_audit_log"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    admin_tg_id: Mapped[str] = mapped_column(Text, nullable=False)
    action: Mapped[str] = mapped_column(Text, nullable=False)
    setting_key: Mapped[str | None] = mapped_column(Text)
    old_value: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    new_value: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    ip_address: Mapped[str | None] = mapped_column(Text)
    user_agent: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
