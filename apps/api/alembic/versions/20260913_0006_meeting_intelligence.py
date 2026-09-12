"""Add meeting intelligence generation and derived output tables.

Revision ID: 20260913_0006
Revises: 20260913_0005
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "20260913_0006"
down_revision: str | None = "20260913_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _tables() -> set[str]:
    return set(inspect(op.get_bind()).get_table_names())


def upgrade() -> None:
    tables = _tables()
    if "meeting_intelligence_runs" not in tables:
        op.create_table(
            "meeting_intelligence_runs",
            sa.Column("meeting_id", sa.String(length=36), nullable=False),
            sa.Column("provider", sa.String(length=100), nullable=False),
            sa.Column("provider_model", sa.String(length=200), nullable=True),
            sa.Column("configuration_key", sa.String(length=128), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_meeting_intelligence_runs_meeting_id", "meeting_intelligence_runs", ["meeting_id"])
        op.create_index("ix_meeting_intelligence_runs_configuration_key", "meeting_intelligence_runs", ["configuration_key"])
        op.create_index("ix_meeting_intelligence_runs_status", "meeting_intelligence_runs", ["status"])

    if "meeting_intelligence" not in _tables():
        op.create_table(
            "meeting_intelligence",
            sa.Column("meeting_id", sa.String(length=36), nullable=False),
            sa.Column("generation_run_id", sa.String(length=36), nullable=False),
            sa.Column("provider", sa.String(length=100), nullable=False),
            sa.Column("provider_model", sa.String(length=200), nullable=True),
            sa.Column("configuration_key", sa.String(length=128), nullable=False),
            sa.Column("status", sa.String(length=32), nullable=False),
            sa.Column("summary", sa.Text(), nullable=False),
            sa.Column("sections", sa.JSON(), nullable=False),
            sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["generation_run_id"], ["meeting_intelligence_runs.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("generation_run_id"),
        )
        op.create_index("ix_meeting_intelligence_meeting_id", "meeting_intelligence", ["meeting_id"])
        op.create_index("ix_meeting_intelligence_generation_run_id", "meeting_intelligence", ["generation_run_id"])
        op.create_index("ix_meeting_intelligence_configuration_key", "meeting_intelligence", ["configuration_key"])
        op.create_index("ix_meeting_intelligence_status", "meeting_intelligence", ["status"])


def downgrade() -> None:
    op.drop_table("meeting_intelligence")
    op.drop_table("meeting_intelligence_runs")
