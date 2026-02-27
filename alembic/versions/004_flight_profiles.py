"""Add flight_profiles table and profile_id to flights.

Revision ID: 004
Revises: 003
Create Date: 2026-02-19
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "004"
down_revision: Union[str, None] = "003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "flight_profiles",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.String(64), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("name", sa.String(100), nullable=False, server_default="Default"),
        sa.Column("is_default", sa.Boolean, nullable=False, server_default=sa.text("0")),
        sa.Column("settings_json", sa.Text, nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
    )
    with op.batch_alter_table("flights") as batch_op:
        batch_op.add_column(
            sa.Column("profile_id", sa.Integer, nullable=True),
        )
        batch_op.create_foreign_key(
            "fk_flights_profile_id",
            "flight_profiles",
            ["profile_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("flights") as batch_op:
        batch_op.drop_constraint("fk_flights_profile_id", type_="foreignkey")
        batch_op.drop_column("profile_id")
    op.drop_table("flight_profiles")
