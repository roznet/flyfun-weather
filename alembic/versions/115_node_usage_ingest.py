"""Node usage upload (#922): ``api_usage_log.origin``, ``node_usage_ingest``,
``instance_settings``.

Dev laptops and the compute node push their own ``api_usage_log`` and
``cost_ledger`` rows to prod. ``origin`` is nullable with no default: NULL
means "recorded by this server", so existing rows need no backfill and a
node never mistakes its own rows for uploaded ones. ``node_usage_ingest``
keys each uploaded row by ``(node_db, source_table, local_id)`` so a re-send
is a no-op; ``instance_settings`` holds the node DB's own UUID.

Revision ID: 115
Revises: 114
Create Date: 2026-10-25
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "115"
down_revision: Union[str, None] = "114"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("api_usage_log") as batch_op:
        batch_op.add_column(sa.Column("origin", sa.String(16), nullable=True))

    op.create_table(
        "node_usage_ingest",
        sa.Column("id", sa.Integer, primary_key=True, autoincrement=True),
        sa.Column("node_db", sa.String(36), nullable=False),
        sa.Column("origin", sa.String(16), nullable=False),
        sa.Column("source_table", sa.String(16), nullable=False),
        sa.Column("local_id", sa.Integer, nullable=False),
        sa.Column("target_id", sa.Integer, nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "node_db", "source_table", "local_id", name="uq_node_usage_ingest_row",
        ),
    )

    op.create_table(
        "instance_settings",
        sa.Column("key", sa.String(64), primary_key=True),
        sa.Column("value", sa.String(256), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("instance_settings")
    op.drop_table("node_usage_ingest")
    with op.batch_alter_table("api_usage_log") as batch_op:
        batch_op.drop_column("origin")
