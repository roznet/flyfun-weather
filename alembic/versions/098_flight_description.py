"""Free-text description on flights (#587).

Revision ID: 098
Revises: 097
Create Date: 2026-10-09

Adds nullable ``flights.description`` (VARCHAR(500)) — the pilot's purpose /
description for the flight, searchable from the flights-list filter. No
default and no backfill: existing flights simply have none.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "098"
down_revision: Union[str, None] = "097"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("flights") as batch_op:
        batch_op.add_column(sa.Column("description", sa.String(500), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("flights") as batch_op:
        batch_op.drop_column("description")
