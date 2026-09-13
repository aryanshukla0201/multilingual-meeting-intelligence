"""Add event temporal state and relationship tables.

Revision ID: 20260914_0008
Revises: 20260914_0007
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "20260914_0008"
down_revision: str | None = "20260914_0007"
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
    if "temporal_state" not in _columns("events"):
        op.add_column("events", sa.Column("temporal_state", sa.String(length=32), nullable=False, server_default="ACTIVE"))
        op.create_index("ix_events_temporal_state", "events", ["temporal_state"])
        op.create_check_constraint(
            "ck_events_temporal_state",
            "events",
            "temporal_state IN ('ACTIVE', 'SUPERSEDED', 'CANCELLED', 'COMPLETED', 'REOPENED')",
        )

    for column_name, column in (
        ("effective_at", sa.Column("effective_at", sa.DateTime(timezone=True), nullable=True)),
        ("due_at", sa.Column("due_at", sa.DateTime(timezone=True), nullable=True)),
        ("occurred_at", sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True)),
        ("temporal_metadata_json", sa.Column("temporal_metadata_json", sa.JSON(), nullable=True)),
    ):
        if column_name not in _columns("events"):
            op.add_column("events", column)

    if "event_relationships" not in inspect(op.get_bind()).get_table_names():
        op.create_table(
            "event_relationships",
            sa.Column("meeting_id", sa.String(length=36), nullable=False),
            sa.Column("source_event_id", sa.String(length=36), nullable=False),
            sa.Column("target_event_id", sa.String(length=36), nullable=False),
            sa.Column("relationship_type", sa.String(length=32), nullable=False),
            sa.Column("confidence", sa.Float(), nullable=False, server_default="0.0"),
            sa.Column("rationale", sa.Text(), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["source_event_id"], ["events.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["target_event_id"], ["events.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint("source_event_id", "target_event_id", "relationship_type", name="uq_event_relationships_unique"),
            sa.CheckConstraint("source_event_id != target_event_id", name="ck_event_relationships_no_self_ref"),
        )
        op.create_index("ix_event_relationships_meeting", "event_relationships", ["meeting_id"])
        op.create_index("ix_event_relationships_source", "event_relationships", ["source_event_id"])
        op.create_index("ix_event_relationships_target", "event_relationships", ["target_event_id"])
        op.create_index("ix_event_relationships_type", "event_relationships", ["relationship_type"])


def downgrade() -> None:
    if "event_relationships" in inspect(op.get_bind()).get_table_names():
        for index_name in (
            "ix_event_relationships_type",
            "ix_event_relationships_target",
            "ix_event_relationships_source",
            "ix_event_relationships_meeting",
        ):
            if index_name in _indexes("event_relationships"):
                op.drop_index(index_name, table_name="event_relationships")
        op.drop_constraint("uq_event_relationships_unique", "event_relationships", type_="unique")
        op.drop_constraint("ck_event_relationships_no_self_ref", "event_relationships", type_="check")
        op.drop_table("event_relationships")

    for column_name in ("temporal_metadata_json", "occurred_at", "due_at", "effective_at"):
        if column_name in _columns("events"):
            op.drop_column("events", column_name)

    if "ix_events_temporal_state" in _indexes("events"):
        op.drop_index("ix_events_temporal_state", table_name="events")
    if "ck_events_temporal_state" in {constraint.get("name") for constraint in inspect(op.get_bind()).get_check_constraints("events")}:
        op.drop_constraint("ck_events_temporal_state", "events", type_="check")
    if "temporal_state" in _columns("events"):
        op.drop_column("events", "temporal_state")
