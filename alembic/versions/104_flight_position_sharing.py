"""Position sharing on flights (#760).

Revision ID: 104
Revises: 103
Create Date: 2026-10-12

Adds ``flights.position_sharing`` (VARCHAR(16), NOT NULL, default
``along_route``): what a viewer of a shared flight who is not its owner sees
of the flight's live progress (``none`` / ``along_route`` / ``full``,
``tasks/live_layer.py``). A String with a ``server_default`` is portable to
MySQL (only TEXT/BLOB/JSON reject one, migrations.md).
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "104"
down_revision: Union[str, None] = "103"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("flights") as batch_op:
        batch_op.add_column(
            sa.Column(
                "position_sharing", sa.String(length=16), nullable=False,
                server_default="along_route",
            )
        )


def downgrade() -> None:
    with op.batch_alter_table("flights") as batch_op:
        batch_op.drop_column("position_sharing")
