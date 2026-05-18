"""Initial database schema.

Revision ID: 20260518_0001
Revises:
Create Date: 2026-05-18
"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision: str = "20260518_0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=False),
        sa.Column("telegram_username", sa.String(length=255), nullable=True),
        sa.Column("telegram_first_name", sa.String(length=255), nullable=True),
        sa.Column("telegram_last_name", sa.String(length=255), nullable=True),
        sa.Column("preferred_language", sa.String(length=2), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_users")),
        sa.UniqueConstraint("telegram_user_id", name=op.f("uq_users_telegram_user_id")),
    )
    op.create_index(op.f("ix_users_preferred_language"), "users", ["preferred_language"])
    op.create_index(op.f("ix_users_telegram_user_id"), "users", ["telegram_user_id"])

    op.create_table(
        "clinic_knowledge",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("language", sa.String(length=2), nullable=False),
        sa.Column("content", sa.Text(), nullable=False),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.true(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_clinic_knowledge")),
        sa.UniqueConstraint("language", "version", name="uq_clinic_knowledge_language_version"),
    )
    op.create_index(op.f("ix_clinic_knowledge_is_active"), "clinic_knowledge", ["is_active"])
    op.create_index(op.f("ix_clinic_knowledge_language"), "clinic_knowledge", ["language"])

    op.create_table(
        "conversations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("telegram_chat_id", sa.BigInteger(), nullable=False),
        sa.Column("current_flow", sa.String(length=64), nullable=True),
        sa.Column("current_state", sa.String(length=128), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_conversations_user_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_conversations")),
        sa.UniqueConstraint("user_id", "telegram_chat_id", name="uq_conversations_user_chat"),
    )
    op.create_index(op.f("ix_conversations_telegram_chat_id"), "conversations", ["telegram_chat_id"])
    op.create_index(op.f("ix_conversations_user_id"), "conversations", ["user_id"])

    op.create_table(
        "user_phones",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("phone", sa.String(length=64), nullable=False),
        sa.Column("is_primary", sa.Boolean(), server_default=sa.false(), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_user_phones_user_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_user_phones")),
        sa.UniqueConstraint("user_id", "phone", name="uq_user_phones_user_phone"),
    )
    op.create_index(op.f("ix_user_phones_user_id"), "user_phones", ["user_id"])

    op.create_table(
        "messages",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("conversation_id", sa.Integer(), nullable=True),
        sa.Column("telegram_message_id", sa.BigInteger(), nullable=True),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("message_type", sa.String(length=16), nullable=False),
        sa.Column("language", sa.String(length=2), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("raw_payload", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("trace_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], name=op.f("fk_messages_conversation_id_conversations"), ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_messages_user_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_messages")),
    )
    op.create_index(op.f("ix_messages_conversation_id"), "messages", ["conversation_id"])
    op.create_index(op.f("ix_messages_created_at"), "messages", ["created_at"])
    op.create_index(op.f("ix_messages_direction"), "messages", ["direction"])
    op.create_index(op.f("ix_messages_language"), "messages", ["language"])
    op.create_index(op.f("ix_messages_message_type"), "messages", ["message_type"])
    op.create_index(op.f("ix_messages_telegram_message_id"), "messages", ["telegram_message_id"])
    op.create_index(op.f("ix_messages_trace_id"), "messages", ["trace_id"])
    op.create_index(op.f("ix_messages_user_id"), "messages", ["user_id"])

    op.create_table(
        "appointments",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("calendar_event_id", sa.String(length=255), nullable=True),
        sa.Column("calendar_etag", sa.String(length=255), nullable=True),
        sa.Column("status", sa.String(length=32), server_default="scheduled", nullable=False),
        sa.Column("service_type", sa.String(length=64), nullable=False),
        sa.Column("doctor_type", sa.String(length=64), nullable=False),
        sa.Column("start_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone", sa.String(length=64), server_default="Asia/Tashkent", nullable=False),
        sa.Column("patient_name", sa.String(length=255), nullable=False),
        sa.Column("primary_phone", sa.String(length=64), nullable=False),
        sa.Column("conversation_summary", sa.Text(), nullable=True),
        sa.Column("created_trace_id", sa.String(length=64), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_appointments_user_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_appointments")),
        sa.UniqueConstraint("calendar_event_id", name=op.f("uq_appointments_calendar_event_id")),
    )
    op.create_index(op.f("ix_appointments_calendar_event_id"), "appointments", ["calendar_event_id"])
    op.create_index(op.f("ix_appointments_created_trace_id"), "appointments", ["created_trace_id"])
    op.create_index(op.f("ix_appointments_doctor_type"), "appointments", ["doctor_type"])
    op.create_index(op.f("ix_appointments_service_type"), "appointments", ["service_type"])
    op.create_index(op.f("ix_appointments_start_at"), "appointments", ["start_at"])
    op.create_index(op.f("ix_appointments_status"), "appointments", ["status"])
    op.create_index("ix_appointments_user_status_start", "appointments", ["user_id", "status", "start_at"])
    op.create_index(op.f("ix_appointments_user_id"), "appointments", ["user_id"])

    op.create_table(
        "escalations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=128), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="new", nullable=False),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("phone", sa.String(length=64), nullable=True),
        sa.Column("admin_chat_id", sa.String(length=128), nullable=True),
        sa.Column("admin_message_id", sa.BigInteger(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_escalations_user_id_users"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_escalations")),
    )
    op.create_index(op.f("ix_escalations_reason"), "escalations", ["reason"])
    op.create_index(op.f("ix_escalations_status"), "escalations", ["status"])
    op.create_index(op.f("ix_escalations_user_id"), "escalations", ["user_id"])

    op.create_table(
        "appointment_history",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("appointment_id", sa.Integer(), nullable=False),
        sa.Column("action", sa.String(length=64), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("old_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("new_data", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["appointment_id"], ["appointments.id"], name=op.f("fk_appointment_history_appointment_id_appointments"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_appointment_history")),
    )
    op.create_index(op.f("ix_appointment_history_action"), "appointment_history", ["action"])
    op.create_index(op.f("ix_appointment_history_actor"), "appointment_history", ["actor"])
    op.create_index(op.f("ix_appointment_history_appointment_id"), "appointment_history", ["appointment_id"])

    op.create_table(
        "reminder_jobs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("appointment_id", sa.Integer(), nullable=False),
        sa.Column("reminder_type", sa.String(length=32), nullable=False),
        sa.Column("send_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(length=32), server_default="pending", nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(["appointment_id"], ["appointments.id"], name=op.f("fk_reminder_jobs_appointment_id_appointments"), ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_reminder_jobs")),
        sa.UniqueConstraint("appointment_id", "reminder_type", name="uq_reminder_jobs_appointment_type"),
    )
    op.create_index(op.f("ix_reminder_jobs_appointment_id"), "reminder_jobs", ["appointment_id"])
    op.create_index(op.f("ix_reminder_jobs_reminder_type"), "reminder_jobs", ["reminder_type"])
    op.create_index(op.f("ix_reminder_jobs_send_at"), "reminder_jobs", ["send_at"])
    op.create_index(op.f("ix_reminder_jobs_status"), "reminder_jobs", ["status"])

    op.create_table(
        "execution_runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("trace_id", sa.String(length=64), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("conversation_id", sa.Integer(), nullable=True),
        sa.Column("input_message_id", sa.Integer(), nullable=True),
        sa.Column("intent", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("graph_input", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("graph_output", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("tool_calls", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], name=op.f("fk_execution_runs_conversation_id_conversations"), ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["input_message_id"], ["messages.id"], name=op.f("fk_execution_runs_input_message_id_messages"), ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_execution_runs_user_id_users"), ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_execution_runs")),
        sa.UniqueConstraint("trace_id", name=op.f("uq_execution_runs_trace_id")),
    )
    op.create_index(op.f("ix_execution_runs_conversation_id"), "execution_runs", ["conversation_id"])
    op.create_index(op.f("ix_execution_runs_input_message_id"), "execution_runs", ["input_message_id"])
    op.create_index(op.f("ix_execution_runs_intent"), "execution_runs", ["intent"])
    op.create_index(op.f("ix_execution_runs_status"), "execution_runs", ["status"])
    op.create_index(op.f("ix_execution_runs_user_id"), "execution_runs", ["user_id"])


def downgrade() -> None:
    op.drop_table("execution_runs")
    op.drop_table("reminder_jobs")
    op.drop_table("appointment_history")
    op.drop_table("escalations")
    op.drop_table("appointments")
    op.drop_table("messages")
    op.drop_table("user_phones")
    op.drop_table("conversations")
    op.drop_table("clinic_knowledge")
    op.drop_table("users")
