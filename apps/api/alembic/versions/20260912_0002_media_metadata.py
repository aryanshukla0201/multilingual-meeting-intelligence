"""Add complete media metadata columns.

Revision ID: 20260912_0002
Revises: 20260912_0001
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision: str = "20260912_0002"
down_revision: str | None = "20260912_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    existing = {column["name"] for column in inspect(op.get_bind()).get_columns("media_assets")}
    if "original_filename" not in existing:
        op.add_column("media_assets", sa.Column("original_filename", sa.String(length=500), nullable=True))
        op.execute("UPDATE media_assets SET original_filename = filename")
        op.alter_column("media_assets", "original_filename", nullable=False)
    if "width" not in existing:
        op.add_column("media_assets", sa.Column("width", sa.Integer(), nullable=True))
    if "height" not in existing:
        op.add_column("media_assets", sa.Column("height", sa.Integer(), nullable=True))


def downgrade() -> None:
    existing = {column["name"] for column in inspect(op.get_bind()).get_columns("media_assets")}
    if "height" in existing:
        op.drop_column("media_assets", "height")
    if "width" in existing:
        op.drop_column("media_assets", "width")
    if "original_filename" in existing:
        op.drop_column("media_assets", "original_filename")
