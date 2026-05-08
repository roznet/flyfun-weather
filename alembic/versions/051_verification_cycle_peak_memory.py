"""Add peak_rss_mb and peak_cgroup_mb to verification_cycles (issue #137).

Per-cycle memory observability so we can spot regressions before the next
OOM. ``peak_rss_mb`` is the parent uvicorn process's max RSS during the
cycle; ``peak_cgroup_mb`` is the max ``memory.current`` reading from the
container's memory cgroup (covers parent + GRIB decode worker pool).

Both nullable: legacy rows stay NULL, new rows populate forward. The
anomaly WARN (``run_standalone_cycle``) skips comparison until at least 3
populated rows exist for the same source.

Revision ID: 051
Revises: 050
Create Date: 2026-05-08
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "051"
down_revision: Union[str, None] = "050"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("verification_cycles") as batch_op:
        batch_op.add_column(sa.Column("peak_rss_mb", sa.Integer, nullable=True))
        batch_op.add_column(sa.Column("peak_cgroup_mb", sa.Integer, nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("verification_cycles") as batch_op:
        batch_op.drop_column("peak_cgroup_mb")
        batch_op.drop_column("peak_rss_mb")
