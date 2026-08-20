"""Distinguish routine METARs from SPECIs in the verification observations.

A SPECI is the *special* report, issued off-cycle precisely because something
significant happened: thunderstorm onset, ceiling collapsing below minima,
visibility crossing a threshold, CB appearing. That makes it the single most
valuable observation class for verifying the convective and ceiling methods —
and until now it was being discarded.

Two gates dropped it. ``fetch_observations_batch`` kept only
``raw.latest_metar`` out of the three hours of history ``euro_aip`` already
returns, and the 30-minute bucket filter in ``store_observations`` then kept
whichever report arrived *first*, which is almost always the routine one. The
raw data was arriving; we threw it away.

Why a column rather than inferring from ``metar_raw``:

The prefix is present in the stored text (``euro_aip``'s parser keeps the
original ``raw_text``), so classification *is* recoverable — but every consumer
would have to re-derive it with a ``LIKE`` on a ``Text`` column, and the
archive would carry the ambiguity forward into Parquet where the fix is much
more expensive.

Nullable, and NULL means "written before this column existed". Every read path
treats NULL as routine, which is what makes this change behaviour-preserving:
existing rows are whatever ``latest_metar`` returned, i.e. overwhelmingly
routine, and any stray SPECI among them is already baked into today's scores.
Reclassifying them is a deliberate, separate act — ``verify backfill-report-type``,
which is chunked and runs off-peak, not an implicit side effect of a migration
scanning 2.3M rows on a shared MySQL with a 1 GiB buffer pool.

**Archive note.** ``verification_observations`` is archived to Parquet (#522
Phase 2), so this is also a schema change to the archive: monthly files written
before this migration will not carry the column. DuckDB reads across the
boundary with ``union_by_name=true``. Additive nullable columns are safe this
way; renames and drops are not.

Revision ID: 091
Revises: 090
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "091"
down_revision: Union[str, None] = "090"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("verification_observations") as batch_op:
        batch_op.add_column(
            sa.Column("report_type", sa.String(length=6), nullable=True)
        )


def downgrade() -> None:
    with op.batch_alter_table("verification_observations") as batch_op:
        batch_op.drop_column("report_type")
