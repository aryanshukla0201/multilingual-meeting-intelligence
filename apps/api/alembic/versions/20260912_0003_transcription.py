"""Add ASR transcript metadata and segment ordering.

Revision ID: 20260912_0003
Revises: 20260912_0002
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "20260912_0003"
down_revision: str | None = "20260912_0002"
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
    transcript_columns = _columns("transcripts")
    if "media_asset_id" not in transcript_columns:
        op.add_column("transcripts", sa.Column("media_asset_id", sa.String(length=36), nullable=True))
    if "provider_device" not in transcript_columns:
        op.add_column("transcripts", sa.Column("provider_device", sa.String(length=64), nullable=True))
    if "processing_duration" not in transcript_columns:
        op.add_column("transcripts", sa.Column("processing_duration", sa.Float(), nullable=True))
    if "configuration_key" not in transcript_columns:
        op.add_column("transcripts", sa.Column("configuration_key", sa.String(length=128), nullable=True))
    if "status" not in transcript_columns:
        op.add_column(
            "transcripts",
            sa.Column("status", sa.String(length=32), nullable=False, server_default="TRANSCRIBED"),
        )
    if "error" not in transcript_columns:
        op.add_column("transcripts", sa.Column("error", sa.Text(), nullable=True))

    segment_columns = _columns("transcript_segments")
    if "media_asset_id" not in segment_columns:
        op.add_column("transcript_segments", sa.Column("media_asset_id", sa.String(length=36), nullable=True))
    if "language" not in segment_columns:
        op.add_column("transcript_segments", sa.Column("language", sa.String(length=32), nullable=True))
    if "sequence" not in segment_columns:
        op.add_column("transcript_segments", sa.Column("sequence", sa.Integer(), nullable=True))
        op.execute("UPDATE transcript_segments SET sequence = segment_index")
        op.alter_column("transcript_segments", "sequence", nullable=False)

    if "ix_transcripts_configuration_key" not in _indexes("transcripts"):
        op.create_index("ix_transcripts_configuration_key", "transcripts", ["configuration_key"])
    if "ix_transcript_segments_media_time" not in _indexes("transcript_segments"):
        op.create_index(
            "ix_transcript_segments_media_time",
            "transcript_segments",
            ["media_asset_id", "start_time"],
        )
    if "ix_transcript_segments_meeting_sequence" not in _indexes("transcript_segments"):
        op.create_index(
            "ix_transcript_segments_meeting_sequence",
            "transcript_segments",
            ["meeting_id", "segment_index"],
        )

    if not _has_foreign_key("transcripts", "media_asset_id"):
        op.create_foreign_key(
            "fk_transcripts_media_asset_id",
            "transcripts",
            "media_assets",
            ["media_asset_id"],
            ["id"],
        )
    if not _has_foreign_key("transcript_segments", "media_asset_id"):
        op.create_foreign_key(
            "fk_transcript_segments_media_asset_id",
            "transcript_segments",
            "media_assets",
            ["media_asset_id"],
            ["id"],
        )


def downgrade() -> None:
    op.drop_constraint("fk_transcript_segments_media_asset_id", "transcript_segments", type_="foreignkey")
    op.drop_constraint("fk_transcripts_media_asset_id", "transcripts", type_="foreignkey")
    for index_name, table in (
        ("ix_transcript_segments_meeting_sequence", "transcript_segments"),
        ("ix_transcript_segments_media_time", "transcript_segments"),
        ("ix_transcripts_configuration_key", "transcripts"),
    ):
        op.drop_index(index_name, table_name=table)
    for column_name in ("sequence", "language", "media_asset_id"):
        op.drop_column("transcript_segments", column_name)
    for column_name in (
        "error",
        "status",
        "configuration_key",
        "processing_duration",
        "provider_device",
        "media_asset_id",
    ):
        op.drop_column("transcripts", column_name)
