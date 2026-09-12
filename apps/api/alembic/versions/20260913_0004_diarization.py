"""Add diarization runs and speaker provider metadata.

Revision ID: 20260913_0004
Revises: 20260912_0003
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "20260913_0004"
down_revision: str | None = "20260912_0003"
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
    speaker_columns = _columns("speakers")
    for name, column in (
        ("media_asset_id", sa.Column("media_asset_id", sa.String(length=36), nullable=True)),
        ("display_name", sa.Column("display_name", sa.String(length=200), nullable=True)),
        ("provider", sa.Column("provider", sa.String(length=100), nullable=True)),
        ("provider_model", sa.Column("provider_model", sa.String(length=200), nullable=True)),
        ("configuration_key", sa.Column("configuration_key", sa.String(length=128), nullable=True)),
    ):
        if name not in speaker_columns:
            op.add_column("speakers", column)
    if "ix_speakers_configuration_key" not in _indexes("speakers"):
        op.create_index("ix_speakers_configuration_key", "speakers", ["configuration_key"])
    if not _has_foreign_key("speakers", "media_asset_id"):
        op.create_foreign_key(
            "fk_speakers_media_asset_id", "speakers", "media_assets", ["media_asset_id"], ["id"]
        )

    segment_columns = _columns("speaker_segments")
    for name, column in (
        ("media_asset_id", sa.Column("media_asset_id", sa.String(length=36), nullable=True)),
        ("provider", sa.Column("provider", sa.String(length=100), nullable=True)),
        ("provider_model", sa.Column("provider_model", sa.String(length=200), nullable=True)),
    ):
        if name not in segment_columns:
            op.add_column("speaker_segments", column)
    if "ix_speaker_segments_media_time" not in _indexes("speaker_segments"):
        op.create_index(
            "ix_speaker_segments_media_time", "speaker_segments", ["media_asset_id", "start_time"]
        )
    if not _has_foreign_key("speaker_segments", "media_asset_id"):
        op.create_foreign_key(
            "fk_speaker_segments_media_asset_id",
            "speaker_segments",
            "media_assets",
            ["media_asset_id"],
            ["id"],
        )

    if "diarization_runs" not in inspect(op.get_bind()).get_table_names():
        op.create_table(
            "diarization_runs",
            sa.Column("meeting_id", sa.String(length=36), nullable=False),
            sa.Column("media_asset_id", sa.String(length=36), nullable=False),
            sa.Column("provider", sa.String(length=100), nullable=False),
            sa.Column("provider_model", sa.String(length=200), nullable=True),
            sa.Column("provider_device", sa.String(length=64), nullable=True),
            sa.Column("configuration_key", sa.String(length=128), nullable=False),
            sa.Column("status", sa.String(length=40), nullable=False),
            sa.Column("error", sa.Text(), nullable=True),
            sa.Column("id", sa.String(length=36), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
            sa.ForeignKeyConstraint(["meeting_id"], ["meetings.id"], ondelete="CASCADE"),
            sa.ForeignKeyConstraint(["media_asset_id"], ["media_assets.id"], ondelete="CASCADE"),
            sa.PrimaryKeyConstraint("id"),
        )
        op.create_index("ix_diarization_runs_meeting_id", "diarization_runs", ["meeting_id"])
        op.create_index("ix_diarization_runs_media_asset_id", "diarization_runs", ["media_asset_id"])
        op.create_index("ix_diarization_runs_configuration_key", "diarization_runs", ["configuration_key"])
        op.create_index("ix_diarization_runs_status", "diarization_runs", ["status"])


def downgrade() -> None:
    op.drop_index("ix_diarization_runs_status", table_name="diarization_runs")
    op.drop_index("ix_diarization_runs_configuration_key", table_name="diarization_runs")
    op.drop_index("ix_diarization_runs_media_asset_id", table_name="diarization_runs")
    op.drop_index("ix_diarization_runs_meeting_id", table_name="diarization_runs")
    op.drop_table("diarization_runs")
    op.drop_constraint("fk_speaker_segments_media_asset_id", "speaker_segments", type_="foreignkey")
    op.drop_index("ix_speaker_segments_media_time", table_name="speaker_segments")
    for column in ("provider_model", "provider", "media_asset_id"):
        op.drop_column("speaker_segments", column)
    op.drop_constraint("fk_speakers_media_asset_id", "speakers", type_="foreignkey")
    op.drop_index("ix_speakers_configuration_key", table_name="speakers")
    for column in ("configuration_key", "provider_model", "provider", "display_name", "media_asset_id"):
        op.drop_column("speakers", column)
