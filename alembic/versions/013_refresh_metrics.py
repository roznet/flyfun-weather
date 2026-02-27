"""Add refresh timing metrics to briefing_usage.

Revision ID: 013
Revises: 012
Create Date: 2026-02-27
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "013"
down_revision: Union[str, None] = "012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("briefing_usage", sa.Column("elapsed_seconds", sa.Float, nullable=True))
    op.add_column("briefing_usage", sa.Column("queue_wait_seconds", sa.Float, nullable=True))
    op.add_column("briefing_usage", sa.Column("triggered_by", sa.String(16), nullable=True))


def downgrade() -> None:
    op.drop_column("briefing_usage", "triggered_by")
    op.drop_column("briefing_usage", "queue_wait_seconds")
    op.drop_column("briefing_usage", "elapsed_seconds")
