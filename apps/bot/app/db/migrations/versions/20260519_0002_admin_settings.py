"""admin_settings and admin_audit_log

Revision ID: 20260519_0002
Revises: 20260518_0001
Create Date: 2026-05-19

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "20260519_0002"
down_revision: str | None = "20260518_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "admin_settings",
        sa.Column("key", sa.Text(), nullable=False),
        sa.Column(
            "value",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("updated_by_tg_id", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_admin_settings")),
    )

    op.create_table(
        "admin_audit_log",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("admin_tg_id", sa.Text(), nullable=False),
        sa.Column("action", sa.Text(), nullable=False),
        sa.Column("setting_key", sa.Text(), nullable=True),
        sa.Column(
            "old_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column(
            "new_value", postgresql.JSONB(astext_type=sa.Text()), nullable=True
        ),
        sa.Column("ip_address", sa.Text(), nullable=True),
        sa.Column("user_agent", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admin_audit_log")),
    )

    # Insert seed settings
    op.execute(
        sa.text("""
            INSERT INTO admin_settings (key, value) VALUES
              ('llm.system_prompt', '{"text": ""}'::jsonb),
              ('bot.welcome_messages', '{"ru": "", "uz": "", "en": ""}'::jsonb),
              ('tts.prompts', '{"ru": "", "uz": "", "en": ""}'::jsonb),
              ('clinic.info', '{"text": ""}'::jsonb)
            ON CONFLICT (key) DO NOTHING
        """)
    )


def downgrade() -> None:
    op.drop_table("admin_audit_log")
    op.drop_table("admin_settings")
