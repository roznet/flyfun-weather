"""``api_usage_log.weighted_calls``: provider-billed calls (Open-Meteo weight).

Open-Meteo bills locations x variables x days per request, not requests; the
``api_calls`` count missed a 13.6x plan overage (#910). Nullable Float, no
server default: rows before this migration stay NULL ("not measured").

Revision ID: 114
Revises: 113
Create Date: 2026-10-24
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "114"
down_revision: Union[str, None] = "113"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("api_usage_log") as batch_op:
        batch_op.add_column(sa.Column("weighted_calls", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("api_usage_log") as batch_op:
        batch_op.drop_column("weighted_calls")
