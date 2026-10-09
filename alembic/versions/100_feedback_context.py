"""Record the rated line on a live-highlight thumb rating (#697).

Revision ID: 100
Revises: 099
Create Date: 2026-10-09

Adds nullable ``feedback.context`` (TEXT holding JSON): for a rating with
``target='live_highlight'``, the highlight exactly as ``/live`` served it
(``facts_hash``, ``generated_at``, ``model``, ``text``). The highlight is
regenerated as the weather changes, so the rating is only reviewable with the
line it rated. No default (MySQL rejects one on TEXT) and no backfill:
existing rows have no context.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "100"
down_revision: Union[str, None] = "099"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("feedback") as batch_op:
        batch_op.add_column(sa.Column("context", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("feedback") as batch_op:
        batch_op.drop_column("context")
