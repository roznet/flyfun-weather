"""Derived visibility and fog methods on standalone scores (#979 phase 1).

Revision ID: 117
Revises: 116
Create Date: 2026-11-08

``verification_scores.visibility_methods`` (TEXT, nullable, no default:
MySQL rejects a default on TEXT, designs/migrations.md): JSON with every
visibility method's forecast and the fog rules / tier for the scored
snapshot, written by the standalone scoring cycle and by
``verify visibility-backfill`` for scores whose snapshot is still in the
database. NULL on older rows and on flight scores.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "117"
down_revision: Union[str, None] = "116"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("verification_scores") as batch_op:
        batch_op.add_column(sa.Column("visibility_methods", sa.Text(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("verification_scores") as batch_op:
        batch_op.drop_column("visibility_methods")
