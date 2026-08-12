"""Stamp each briefing pack with the flight parameters it was computed for.

``decide_refresh`` is a pure function of model-run freshness: it answers "has a
newer run landed since this pack?", and knows nothing about the flight itself
having changed. Right after a departure-time, duration, altitude or route edit
no new run exists, so the gate returns ``none`` and both refresh endpoints hand
the OLD pack back as ``complete`` — while the PATCH that preceded it has just
told the client ``refetch_needed``. The pilot is shown "Briefing regenerated"
over a briefing computed for the previous departure time, and ``force=true`` is
admin-gated so no client can work around it (issue #552).

This column carries a hash of the flight's route + departure time + altitude +
ceiling + duration (``storage.flights.compute_flight_params_hash``), written at
persist time. The gate compares it with the flight's current hash and forces
``mode="full"`` when they differ — stateless, self-healing, and equally
effective for an edit made on another device.

Nullable with no server_default on purpose: packs written before this shipped
genuinely have no stamp, and NULL says so. A backfilled value would be a lie
(we cannot know which parameters those packs were built from — the flight has
since been edited, which is the whole point), and any non-NULL sentinel would
read as "changed" and force one full refresh for every legacy pack the first
time its owner pressed Refresh. The gate therefore treats NULL as "don't know"
and leaves the model-freshness decision untouched.

Revision ID: 088
Revises: 087
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "088"
down_revision: Union[str, None] = "087"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("briefing_packs") as batch_op:
        batch_op.add_column(
            sa.Column("flight_params_hash", sa.String(length=32), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("briefing_packs") as batch_op:
        batch_op.drop_column("flight_params_hash")
