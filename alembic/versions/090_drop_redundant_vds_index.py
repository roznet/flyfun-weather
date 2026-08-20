"""Drop the redundant ``ix_vds_date_model`` on verification_daily_stats.

``ix_vds_date_model (date, source, model, days_out)`` is an exact leftmost
prefix of ``uq_vds_key (date, source, model, days_out, icao)`` — same columns
in the same order, with the unique index simply carrying ``icao`` as a fifth.
Every access path the prefix can serve, the superset serves too, so the two
have never been anything but one index stored twice.

The table has grown into the #522 tiering work: 1.2M rows, ~244 MB of data and
~283 MB of index, rewritten every cycle by the rollup's idempotent
DELETE+INSERT. ``ix_vds_date_model`` is 71 MB of that against a shared MySQL
server with a 1 GiB InnoDB buffer pool, and it took ~1.95M index writes over
the last 105 days of server uptime.

Verified on production rather than argued from the schema. The two code sites
that name this index in a comment — the global rollup SELECT in
``verification_daily_rollup`` and the ``COUNT(DISTINCT icao)`` in
``verification_stats.get_activity_summary`` — do not actually use it: the
former already chooses ``uq_vds_key``, the latter
``ix_vds_source_model_days_date``. Nor does the rollup's
``DELETE ... WHERE date = ?``, which also picks ``uq_vds_key``. Those comments
are corrected in the same change.

It was then set INVISIBLE in production on 2026-08-20 and all three plans
re-EXPLAINed: identical keys, identical row estimates, no degradation.

Note the size detail that makes this safe rather than merely tidy: the prefix
index (71 MB) is no smaller than the superset it defers to (66 MB), so the
reads it currently attracts relocate at no cost. Dropping a *narrower* index
onto a wider one would be the trade worth arguing about; this is not that.

``uq_vds_key`` cannot be dropped in its place — it is the uniqueness
constraint behind the rollup's per-(date, source, model, days_out, icao) grain.

Downgrade recreates the index, so this is reversible if a consumer surfaces.

Revision ID: 090
Revises: 089
"""

from typing import Sequence, Union

from alembic import op

revision: str = "090"
down_revision: Union[str, None] = "089"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # drop_index needs no batch_alter_table: it is supported natively on both
    # SQLite and MySQL, unlike column/constraint ALTERs. (Same as 086.)
    op.drop_index("ix_vds_date_model", table_name="verification_daily_stats")


def downgrade() -> None:
    op.create_index(
        "ix_vds_date_model",
        "verification_daily_stats",
        ["date", "source", "model", "days_out"],
    )
