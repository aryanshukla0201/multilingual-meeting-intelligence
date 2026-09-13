"""Add canonical evidence provenance tables and bridge keys.

Revision ID: 20260914_0007
Revises: 20260913_0006
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "20260914_0007"
down_revision: str | None = "20260913_0006"
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
    if "evidence" not in inspect(op.get_bind()).get_table_names():
        op.create_table(
            "evidence",
            sa.Column("meeting_id", sa.String(length=36), nullable=False),
            sa.Column("evidence_type", sa.String(length=32), nullable=False),
            sa.Column("source_type", sa.String(length=64), nullable=True),
            sa.Column("source_id", sa.String(length=36), nullable=True),
            sa.Column("media_asset_id", sa.String(length=36), nullable=True),
            sa.Column("transcript_segment_id", sa.String(length=36), nullable=True),
            sa.Column("speaker_id", sa.String(length=36), nullable=True),
            sa.Column("start_time", sa.Float(), nullable=True),
            sa.Column("end_time", sa.Float(), nullable=True),
            sa.Column("content", sa.Text(), nullable=True),
            sa.Column("confidence", sa.Float(), nullable=True),
            sa.Column("metadata_json", sa.JSON(), nullable=True),
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["media_asset_id"], ["media_assets.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["transcript_segment_id"], ["transcript_segments.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["speaker_id"], ["speakers.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
            sa.CheckConstraint("start_time IS NULL OR end_time IS NULL OR start_time <= end_time", name="ck_evidence_time_order"),
        )
        op.create_index("ix_evidence_meeting_id", "evidence", ["meeting_id"])
        op.create_index("ix_evidence_meeting_time", "evidence", ["meeting_id", "start_time"])
        op.create_index("ix_evidence_type", "evidence", ["evidence_type"])
        op.create_index("ix_evidence_segment", "evidence", ["transcript_segment_id"])
        op.create_index("ix_evidence_media", "evidence", ["media_asset_id"])
        op.create_index("ix_evidence_source_type", "evidence", ["source_type"])
        op.create_index("ix_evidence_source_id", "evidence", ["source_id"])
        op.create_index("ix_evidence_speaker_id", "evidence", ["speaker_id"])

    if "evidence_id" not in _columns("event_evidence"):
        op.add_column("event_evidence", sa.Column("evidence_id", sa.String(length=36), nullable=True))
        op.create_foreign_key(
            "fk_event_evidence_evidence_id",
            "event_evidence",
            "evidence",
            ["evidence_id"],
            ["id"],
            ondelete="CASCADE",
        )
        op.create_index("ix_event_evidence_evidence", "event_evidence", ["evidence_id"])


def downgrade() -> None:
    if "evidence_id" in _columns("event_evidence"):
        if "ix_event_evidence_evidence" in _indexes("event_evidence"):
            op.drop_index("ix_event_evidence_evidence", table_name="event_evidence")
        if _has_foreign_key("event_evidence", "evidence_id"):
            op.drop_constraint("fk_event_evidence_evidence_id", "event_evidence", type_="foreignkey")
        op.drop_column("event_evidence", "evidence_id")

    if "evidence" in inspect(op.get_bind()).get_table_names():
        op.drop_index("ix_evidence_speaker_id", table_name="evidence")
        op.drop_index("ix_evidence_source_id", table_name="evidence")
        op.drop_index("ix_evidence_source_type", table_name="evidence")
        op.drop_index("ix_evidence_media", table_name="evidence")
        op.drop_index("ix_evidence_segment", table_name="evidence")
        op.drop_index("ix_evidence_type", table_name="evidence")
        op.drop_index("ix_evidence_meeting_time", table_name="evidence")
        op.drop_index("ix_evidence_meeting_id", table_name="evidence")
        op.drop_constraint("ck_evidence_time_order", "evidence", type_="check")
        op.drop_table("evidence")
