"""Add llm_digest_requested flag to briefing_packs.

Records whether the LLM digest was *requested* for a pack (the profile's AI
toggle), distinct from ``has_digest`` (= the digest actually exists). A pack
built with the profile's AI summary off carries ``llm_digest_requested=0``, so
the UI can show "AI summary off for this profile" + a Generate button instead
of a perpetual "Generating summary…" spinner.

server_default="1" so all pre-existing rows read as "digest was requested",
preserving the previous has_digest semantics for legacy packs.

Revision ID: 064
Revises: 063
Create Date: 2026-06-10
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "064"
down_revision: Union[str, None] = "063"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("briefing_packs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "llm_digest_requested",
                sa.Boolean(),
                nullable=False,
                server_default="1",
            ),
        )


def downgrade() -> None:
    with op.batch_alter_table("briefing_packs") as batch_op:
        batch_op.drop_column("llm_digest_requested")
