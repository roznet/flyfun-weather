"""Record the convective ingredients, not just the verdict.

``airport_forecast_snapshots`` stored ``sounding_convective_risk`` — the graded
answer — and a single ``cape_jkg``. That is enough to ask "was the grade right"
and nothing else. A candidate scheme (#442 replaces the DD floor with an
NWP-native grade) cannot be scored over history from a verdict, because the
inputs it would consume were never written down.

Ceiling had to take the opposite approach: migration 089 stores three parallel
*verdicts* because the layer ceiling derives from a full 3D cloud-fraction
profile that a snapshot row cannot hold, so it can never be recomputed. The
convective grade is a function of a handful of scalars, so storing those lets
any future scheme — including ones not yet invented — be recomputed
retroactively. That is the whole point of the shape.

Why one shared column per quantity rather than per model: the three models
expose different signals (GFS convective cover, ICON base+top, ECMWF top only)
but they are the *same physical quantities*. Only the provenance differs, and
``nwp_conv_method`` records it, exactly as ``nwp_layer_source`` does for the
ceiling. That collapses ~20 per-model columns into these, and keeps every
consumer reading one name.

``nwp_cape_type`` earns its place for a subtler reason: Open-Meteo's ``cape``
is surface-based for GFS, most-unstable for ECMWF and mixed-layer for ICON
(``NWP_CAPE_TYPE``). Without the parcel recorded alongside, the existing
``cape_jkg`` column is three different quantities stacked in one place, and any
cross-model comparison silently compares parcel definitions instead of skill.

Nullable with no server default. NULL means the value was genuinely absent —
either the row predates this migration, or GRIB enrichment did not cover that
hour. **The second case matters to readers: a NULL here means "no GRIB at this
forecast hour", not "no convection".** GRIB enrichment is windowed (ECMWF ±3 h
around the flight window, GFS/ICON forward; the standalone precache spans
D-0..D-3), so sparsity is expected and is itself information.

Cost, measured against a real archived day (2026-08-16: 110,801 rows,
5.72 MB compressed Parquet, 51.6 bytes/row): about +14 bytes/row, ~+27%, so
~1.5 MB/day. The convective columns are the cheap half — convection is the
exception, so they are mostly NULL and NULL costs almost nothing in Parquet.

**Archive note.** ``airport_forecast_snapshots`` is archived to Parquet (#522
Phase 2) and its column set is derived from this ORM model, so these ride into
the artifact and the archive automatically — no format change. Files written
before this migration lack the columns; DuckDB reads across with
``union_by_name=true``. Additive nullable columns are safe that way, renames
and drops are not.

**Compute-node note.** Nodes run ``ENVIRONMENT=development``, so their local
SQLite is built by ``create_all``, which never ALTERs an existing table. Hand
-apply this ALTER, or delete the node's disposable DB, *before* pulling a node
past this revision — migration 081 hard-failed every cycle exactly that way.

Revision ID: 092
Revises: 091
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "092"
down_revision: Union[str, None] = "091"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

_FLOAT_COLUMNS = (
    "sounding_lfc_ft",
    "sounding_el_ft",
    "nwp_conv_cover_pct",
    "nwp_conv_base_ft",
    "nwp_conv_top_ft",
    "nwp_conv_precip_mm_h",
    "nwp_ml_cape_jkg",
    "nwp_ml_cin_jkg",
    "nwp_cin_jkg",
    "nwp_lifted_index",
    "nwp_k_index",
    "nwp_total_totals",
)


def upgrade() -> None:
    with op.batch_alter_table("airport_forecast_snapshots") as batch_op:
        for name in _FLOAT_COLUMNS:
            batch_op.add_column(sa.Column(name, sa.Float(), nullable=True))
        batch_op.add_column(
            sa.Column("nwp_conv_method", sa.String(length=20), nullable=True)
        )
        batch_op.add_column(
            sa.Column("nwp_cape_type", sa.String(length=8), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("airport_forecast_snapshots") as batch_op:
        batch_op.drop_column("nwp_cape_type")
        batch_op.drop_column("nwp_conv_method")
        for name in reversed(_FLOAT_COLUMNS):
            batch_op.drop_column(name)
