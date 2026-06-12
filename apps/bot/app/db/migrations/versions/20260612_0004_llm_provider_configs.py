"""llm provider configs

Revision ID: 20260612_0004
Revises: 20260521_0003
Create Date: 2026-06-12

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260612_0004"
down_revision: str | None = "20260521_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


MODEL_ROWS = [
    (
        "openai",
        "gpt-5.5",
        "GPT-5.5",
        "Highest-quality default for complex reasoning, coding, and agentic work",
        None,
        10,
    ),
    (
        "openai",
        "gpt-5.4",
        "GPT-5.4",
        "Strong model, cheaper than GPT-5.5",
        None,
        20,
    ),
    (
        "openai",
        "gpt-5.4-mini",
        "GPT-5.4 mini",
        "Strong lower-latency model for production workloads",
        None,
        30,
    ),
    (
        "openai",
        "gpt-5.4-nano",
        "GPT-5.4 nano",
        "Cheap, fast, high-volume tasks and sub-agents",
        None,
        40,
    ),
    (
        "openai",
        "gpt-5-nano",
        "GPT-5 nano",
        "Very cheap fallback option for simple tasks",
        None,
        50,
    ),
    (
        "anthropic",
        "claude-fable-5",
        "Claude Fable 5",
        "Strongest widely released Anthropic model",
        None,
        10,
    ),
    (
        "anthropic",
        "claude-opus-4-8",
        "Claude Opus 4.8",
        "Complex reasoning, long-horizon agentic coding, high-autonomy work",
        None,
        20,
    ),
    (
        "anthropic",
        "claude-sonnet-4-6",
        "Claude Sonnet 4.6",
        "Strong balance of intelligence, speed, and cost",
        None,
        30,
    ),
    (
        "anthropic",
        "claude-haiku-4-5",
        "Claude Haiku 4.5",
        "Fast and cheaper model with strong reasoning",
        None,
        40,
    ),
    (
        "anthropic",
        "claude-mythos-5",
        "Claude Mythos 5",
        "Limited-availability Anthropic model",
        "Limited access",
        50,
    ),
    (
        "mistral",
        "mistral-medium-3-5",
        "Mistral Medium 3.5",
        "Frontier-class multimodal model optimized for agentic and coding use cases",
        None,
        10,
    ),
    (
        "mistral",
        "mistral-small-2603",
        "Mistral Small 4",
        "Hybrid instruct/reasoning/coding model with good cost/performance",
        None,
        20,
    ),
    (
        "mistral",
        "mistral-large-2512",
        "Mistral Large 3",
        "Strong general-purpose multimodal model",
        None,
        30,
    ),
    (
        "mistral",
        "mistral-medium-2508",
        "Mistral Medium 3.1",
        "Strong general multimodal model",
        None,
        40,
    ),
    (
        "mistral",
        "magistral-medium-2509",
        "Magistral Medium 1.2",
        "Mistral reasoning model",
        None,
        50,
    ),
]

PROVIDER_ROWS = [
    ("anthropic", "Anthropic", True, 1, "claude-sonnet-4-6"),
    ("openai", "OpenAI", False, 2, "gpt-5.4-mini"),
    ("mistral", "Mistral", False, 3, "mistral-medium-3-5"),
]


def upgrade() -> None:
    op.create_table(
        "llm_model_catalog",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider_code", sa.String(length=32), nullable=False),
        sa.Column("model_id", sa.String(length=128), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("availability_note", sa.String(length=255), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_model_catalog")),
        sa.UniqueConstraint(
            "provider_code",
            "model_id",
            name=op.f("uq_llm_model_catalog_provider_model"),
        ),
    )
    op.create_index(
        op.f("ix_llm_model_catalog_provider_code"),
        "llm_model_catalog",
        ["provider_code"],
    )
    op.create_index(
        op.f("ix_llm_model_catalog_is_active"),
        "llm_model_catalog",
        ["is_active"],
    )
    op.create_index(
        "ix_llm_model_catalog_provider_active_sort",
        "llm_model_catalog",
        ["provider_code", "is_active", "sort_order"],
    )

    op.create_table(
        "llm_provider_configs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("provider_code", sa.String(length=32), nullable=False),
        sa.Column("display_name", sa.String(length=128), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("priority", sa.SmallInteger(), nullable=True),
        sa.Column("selected_model_id", sa.String(length=128), nullable=True),
        sa.Column("api_key_encrypted", sa.Text(), nullable=True),
        sa.Column("api_key_masked", sa.String(length=64), nullable=True),
        sa.Column("api_key_fingerprint", sa.String(length=64), nullable=True),
        sa.Column(
            "last_status",
            sa.String(length=32),
            server_default="unknown",
            nullable=True,
        ),
        sa.Column("last_tested_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_failure_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error_code", sa.String(length=128), nullable=True),
        sa.Column("last_error_message", sa.Text(), nullable=True),
        sa.Column("updated_by_admin_id", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "provider_code in ('openai', 'anthropic', 'mistral')",
            name=op.f("ck_llm_provider_configs_provider_code_valid"),
        ),
        sa.CheckConstraint(
            "priority is null or priority in (1, 2, 3)",
            name=op.f("ck_llm_provider_configs_priority_valid"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_provider_configs")),
        sa.UniqueConstraint("provider_code", name=op.f("uq_llm_provider_configs_provider_code")),
    )
    op.create_index(
        op.f("ix_llm_provider_configs_enabled"),
        "llm_provider_configs",
        ["enabled"],
    )
    op.create_index(
        "ix_llm_provider_configs_enabled_priority",
        "llm_provider_configs",
        ["enabled", "priority"],
    )
    op.create_index(
        "uq_llm_provider_configs_enabled_priority",
        "llm_provider_configs",
        ["priority"],
        unique=True,
        postgresql_where=sa.text("enabled = true AND priority IS NOT NULL"),
    )

    op.create_table(
        "llm_provider_call_logs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("request_id", sa.String(length=64), nullable=True),
        sa.Column("telegram_user_id", sa.BigInteger(), nullable=True),
        sa.Column("provider_code", sa.String(length=32), nullable=False),
        sa.Column("model_id", sa.String(length=128), nullable=True),
        sa.Column("priority", sa.SmallInteger(), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_type", sa.String(length=128), nullable=True),
        sa.Column("error_message_sanitized", sa.Text(), nullable=True),
        sa.Column("latency_ms", sa.Integer(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column("total_tokens", sa.Integer(), nullable=True),
        sa.Column("fallback_attempt_number", sa.Integer(), nullable=True),
        sa.Column("was_fallback", sa.Boolean(), server_default="false", nullable=False),
        sa.Column(
            "tool_executed_before_failure",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_provider_call_logs")),
    )
    op.create_index(
        op.f("ix_llm_provider_call_logs_created_at"),
        "llm_provider_call_logs",
        ["created_at"],
    )
    op.create_index(
        op.f("ix_llm_provider_call_logs_provider_code"),
        "llm_provider_call_logs",
        ["provider_code"],
    )
    op.create_index(
        op.f("ix_llm_provider_call_logs_request_id"),
        "llm_provider_call_logs",
        ["request_id"],
    )
    op.create_index(
        op.f("ix_llm_provider_call_logs_status"),
        "llm_provider_call_logs",
        ["status"],
    )
    op.create_index(
        op.f("ix_llm_provider_call_logs_telegram_user_id"),
        "llm_provider_call_logs",
        ["telegram_user_id"],
    )

    _seed_catalog()
    _seed_provider_configs()


def downgrade() -> None:
    op.drop_table("llm_provider_call_logs")
    op.drop_index(
        "uq_llm_provider_configs_enabled_priority",
        table_name="llm_provider_configs",
    )
    op.drop_table("llm_provider_configs")
    op.drop_table("llm_model_catalog")


def _seed_catalog() -> None:
    bind = op.get_bind()
    for row in MODEL_ROWS:
        bind.execute(
            sa.text("""
                INSERT INTO llm_model_catalog (
                    provider_code,
                    model_id,
                    display_name,
                    description,
                    availability_note,
                    sort_order
                )
                VALUES (
                    :provider_code,
                    :model_id,
                    :display_name,
                    :description,
                    :availability_note,
                    :sort_order
                )
                ON CONFLICT (provider_code, model_id) DO NOTHING
            """),
            {
                "provider_code": row[0],
                "model_id": row[1],
                "display_name": row[2],
                "description": row[3],
                "availability_note": row[4],
                "sort_order": row[5],
            },
        )


def _seed_provider_configs() -> None:
    bind = op.get_bind()
    for row in PROVIDER_ROWS:
        bind.execute(
            sa.text("""
                INSERT INTO llm_provider_configs (
                    provider_code,
                    display_name,
                    enabled,
                    priority,
                    selected_model_id
                )
                VALUES (
                    :provider_code,
                    :display_name,
                    :enabled,
                    :priority,
                    :selected_model_id
                )
                ON CONFLICT (provider_code) DO NOTHING
            """),
            {
                "provider_code": row[0],
                "display_name": row[1],
                "enabled": row[2],
                "priority": row[3],
                "selected_model_id": row[4],
            },
        )
