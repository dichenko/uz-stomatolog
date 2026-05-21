"""user patient profile

Revision ID: 20260521_0003
Revises: 20260519_0002
Create Date: 2026-05-21

"""

from collections.abc import Sequence

from alembic import op
import sqlalchemy as sa


revision: str = "20260521_0003"
down_revision: str | None = "20260519_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("patient_name", sa.String(length=255), nullable=True),
    )
    op.add_column(
        "users",
        sa.Column("primary_phone", sa.String(length=64), nullable=True),
    )
    op.execute(
        sa.text("""
            UPDATE users
            SET
              patient_name = latest.patient_name,
              primary_phone = latest.primary_phone
            FROM (
              SELECT DISTINCT ON (user_id)
                user_id,
                patient_name,
                primary_phone
              FROM appointments
              WHERE patient_name IS NOT NULL
                AND primary_phone IS NOT NULL
              ORDER BY user_id, created_at DESC, id DESC
            ) AS latest
            WHERE users.id = latest.user_id
              AND users.patient_name IS NULL
              AND users.primary_phone IS NULL
        """)
    )


def downgrade() -> None:
    op.drop_column("users", "primary_phone")
    op.drop_column("users", "patient_name")
