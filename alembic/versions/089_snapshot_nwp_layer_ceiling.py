"""Record the model-native cloud-layer ceiling alongside the DD one.

Verification scores the model's ceiling against METARs, but only ever saw two
of the three estimates the engine can produce: the DD (dewpoint-depression)
ceiling in ``sounding_ceiling_ft`` and the model's own ceiling *product* in
``nwp_ceiling_ft``. The third — the ceiling derived from the model's own cloud
field (ECMWF/ICON 3D fraction, GFS GRIB bands, HRRR condensate) — is what the
briefing engine switched to for grading, and it was not persisted anywhere. So
"did that switch improve ceiling accuracy?" could not be answered from stored
data, only bracketed: a 5-day replay over 411K forecast/observation pairs could
compare ``min(DD, diag)`` against ``diag`` alone, but not the method actually
shipped, which sits between them.

Two columns, because one cannot express the difference that matters:

- ``nwp_layer_source`` NULL  -> the model has no native layer source at all
  (GEM/UKMO/MétéoFrance, or any hour outside the GRIB enrichment window). The
  engine falls back to DD there, so the new ceiling equals the old one.
- source set, ceiling NULL   -> native layers exist but carry no BKN/OVC deck.
- source set, ceiling set    -> new ceiling = min(this, ``nwp_ceiling_ft``).

With the pair stored, the reconciled ceiling under either method is
reconstructable offline and the three estimates can be scored head-to-head
against observed METARs once a couple of weeks have accumulated.

Nullable with no server_default: rows written before this genuinely have no
value, and NULL says so. Backfilling is impossible — the layer geometry is
derived from pressure-level cloud fields that the snapshot table never stored.

Revision ID: 089
Revises: 088
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "089"
down_revision: Union[str, None] = "088"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("airport_forecast_snapshots") as batch_op:
        batch_op.add_column(
            sa.Column("nwp_layer_ceiling_ft", sa.Float(), nullable=True)
        )
        batch_op.add_column(
            sa.Column("nwp_layer_source", sa.String(length=16), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("airport_forecast_snapshots") as batch_op:
        batch_op.drop_column("nwp_layer_source")
        batch_op.drop_column("nwp_layer_ceiling_ft")
