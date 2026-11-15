"""Why a briefing pack has no GRAMET (#994).

Revision ID: 118
Revises: 117
Create Date: 2026-11-15

``briefing_packs.gramet_skip_reason`` (VARCHAR(16), nullable): set when a
pack was built without a GRAMET, to one of ``not_linked`` (the owner had no
Autorouter link), ``disabled`` (profile toggle off), ``historical``,
``daily_limit``, ``failed`` (Autorouter or server error), ``expired``
(retention deleted it from an old pack). NULL on packs that
have a GRAMET and on packs built before this column. The API turns it into
``gramet_status`` so the apps can say why the GRAMET is missing, and the
refresh gate uses it to fetch the GRAMET once the owner links Autorouter.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "118"
down_revision: Union[str, None] = "117"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("briefing_packs") as batch_op:
        batch_op.add_column(sa.Column("gramet_skip_reason", sa.String(length=16), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("briefing_packs") as batch_op:
        batch_op.drop_column("gramet_skip_reason")
