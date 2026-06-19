"""Add advisory_summary_json to briefing_packs.

Denormalizes a compact RED/AMBER advisory breakdown (AdvisorySummary JSON)
onto each pack at briefing-build time so the flights-list card can render
per-flight summary chips without reading route_advisories.json per flight
(issue #276). Nullable: NULL for packs created before this migration and
before their next refresh. No backfill in the migration itself.

Revision ID: 072
Revises: 071
Create Date: 2026-06-19
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "072"
down_revision: Union[str, None] = "071"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("briefing_packs") as batch_op:
        batch_op.add_column(
            sa.Column("advisory_summary_json", sa.Text(), nullable=True),
        )


def downgrade() -> None:
    with op.batch_alter_table("briefing_packs") as batch_op:
        batch_op.drop_column("advisory_summary_json")
