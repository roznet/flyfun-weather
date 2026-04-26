"""Add raw_route and parser_version to flights for re-derive support.

Revision ID: 047
Revises: 046
Create Date: 2026-04-26

Stores the pilot's original Field-15 input alongside the resolved
waypoint list. Both columns are nullable: NULL means no raw input
was captured (iOS/MCP-created flights, or flights that pre-date
this migration). When raw_route is present, parser_version records
which euro_aip release derived the waypoint list, so a future
re-derive job can identify flights that would benefit from a
newer parser.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "047"
down_revision: Union[str, None] = "046"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("flights") as batch_op:
        batch_op.add_column(sa.Column("raw_route", sa.String(4000), nullable=True))
        batch_op.add_column(sa.Column("parser_version", sa.String(32), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("flights") as batch_op:
        batch_op.drop_column("parser_version")
        batch_op.drop_column("raw_route")
