"""Add evidence-backed event extraction tables.

Revision ID: 20260913_0005
Revises: 20260913_0004
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "20260913_0005"
down_revision: str | None = "20260913_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _columns(table: str) -> set[str]:
    return {column["name"] for column in inspect(op.get_bind()).get_columns(table)}


def _indexes(table: str) -> set[str]:
    return {index["name"] for index in inspect(op.get_bind()).get_indexes(table)}


def _has_foreign_key(table: str, column: str) -> bool:
    return any(
        column in foreign_key.get("constrained_columns", [])
        for foreign_key in inspect(op.get_bind()).get_foreign_keys(table)
    )


def upgrade() -> None:
    event_columns = _columns("events")
    for name, column in (
        ("title", sa.Column("title", sa.String(length=300), nullable=True)),
        ("value", sa.Column("value", sa.Text(), nullable=True)),
        ("extraction_run_id", sa.Column("extraction_run_id", sa.String(length=36), nullable=True)),
    ):
        if name not in event_columns:
            op.add_column("events", column)
    if "ix_events_extraction_run" not in _indexes("events"):
        op.create_index("ix_events_extraction_run", "events", ["extraction_run_id"])
    if "ck_events_event_type" not in {constraint.get("name") for constraint in inspect(op.get_bind()).get_check_constraints("events")}:
        op.create_check_constraint(
            "ck_events_event_type",
            "events",
            "event_type IN ('FACT', 'CLAIM', 'OPINION', 'DECISION', 'COMMITMENT', 'ACTION', 'DEADLINE', 'RISK', 'BLOCKER', 'QUESTION', 'DISAGREEMENT', 'PROPOSAL', 'OBJECTION', 'ASSUMPTION', 'CONSTRAINT')",
        )

    if "event_extraction_runs" not in inspect(op.get_bind()).get_table_names():
        op.create_table(
            "event_extraction_runs",
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
        op.create_index("ix_event_extraction_runs_meeting_id", "event_extraction_runs", ["meeting_id"])
        op.create_index("ix_event_extraction_runs_configuration_key", "event_extraction_runs", ["configuration_key"])
        op.create_index("ix_event_extraction_runs_status", "event_extraction_runs", ["status"])

    if not _has_foreign_key("events", "extraction_run_id"):
        op.create_foreign_key(
            "fk_events_extraction_run_id",
            "events",
            "event_extraction_runs",
            ["extraction_run_id"],
            ["id"],
        )

    if "event_evidence" not in inspect(op.get_bind()).get_table_names():
        op.create_table(
            "event_evidence",
            sa.Column("event_id", sa.String(length=36), nullable=False),
            sa.Column("transcript_segment_id", sa.String(length=36), nullable=False),
            sa.Column("evidence_start", sa.Float(), nullable=False),
            sa.Column("evidence_end", sa.Float(), nullable=False),
            sa.Column("relevance", sa.Float(), nullable=True),
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["event_id"], ["events.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["transcript_segment_id"], ["transcript_segments.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_event_evidence_event", "event_evidence", ["event_id"])
        op.create_index("ix_event_evidence_transcript_segment", "event_evidence", ["transcript_segment_id"])


def downgrade() -> None:
    op.drop_table("event_evidence")
    op.drop_table("event_extraction_runs")
    op.drop_constraint("ck_events_event_type", "events", type_="check")
    op.drop_constraint("fk_events_extraction_run_id", "events", type_="foreignkey")
    op.drop_index("ix_events_extraction_run", table_name="events")
    for column in ("extraction_run_id", "value", "title"):
        op.drop_column("events", column)
