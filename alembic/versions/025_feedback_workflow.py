"""Add workflow columns to feedback table for triage and admin reply.

Revision ID: 025
Revises: 024
Create Date: 2026-03-23
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "025"
down_revision: Union[str, None] = "024"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("feedback") as batch_op:
        batch_op.add_column(
            sa.Column("status", sa.String(16), nullable=False, server_default="pending")
        )
        batch_op.add_column(
            sa.Column("classification", sa.String(32), nullable=True)
        )
        batch_op.add_column(sa.Column("ai_analysis", sa.Text, nullable=True))
        batch_op.add_column(sa.Column("admin_reply", sa.Text, nullable=True))
        batch_op.add_column(sa.Column("admin_notes", sa.Text, nullable=True))
        batch_op.add_column(sa.Column("confidence", sa.Float, nullable=True))
        batch_op.add_column(
            sa.Column("replied_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.add_column(
            sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True)
        )
        batch_op.create_index("idx_feedback_status", ["status"])


def downgrade() -> None:
    with op.batch_alter_table("feedback") as batch_op:
        batch_op.drop_index("idx_feedback_status")
        batch_op.drop_column("processed_at")
        batch_op.drop_column("replied_at")
        batch_op.drop_column("confidence")
        batch_op.drop_column("admin_notes")
        batch_op.drop_column("admin_reply")
        batch_op.drop_column("ai_analysis")
        batch_op.drop_column("classification")
        batch_op.drop_column("status")
