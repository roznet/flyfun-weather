"""Add digest_trace_id to briefing_packs.

Stores the LangSmith root run id of the digest LLM call (issue #244) so a
later 👍/👎 thumb rating can attach feedback to the run that produced the
digest. Nullable: NULL for legacy packs created before #244 and for packs
whose digest hasn't run / didn't succeed.

Revision ID: 066
Revises: 065
Create Date: 2026-06-13
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "066"
down_revision: Union[str, None] = "065"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("briefing_packs") as batch_op:
        batch_op.add_column(
            sa.Column("digest_trace_id", sa.String(length=36), nullable=True),
        )


def downgrade() -> None:
    with op.batch_alter_table("briefing_packs") as batch_op:
        batch_op.drop_column("digest_trace_id")
