"""Drop three unused secondary indexes on verification_scores.

``verification_scores`` carries 8.8M rows for ~968 MB of data and ~2052 MB of
index against a shared MySQL server with a 1 GiB InnoDB buffer pool. Three of
its eight indexes have no consumer at all:

- ``ix_verif_scores_lead`` (lead_hours) — ``lead_hours`` is written by the
  scorer and read back as an output column, but is never a filter, join or
  GROUP BY predicate anywhere in the codebase.
- ``ix_verif_scores_icao`` (icao) — the only ``icao =`` predicate is the
  scoring dedup in ``tasks/scoring``, which supplies all five columns of
  ``uq_verif_scores_key`` and is answered as a ``type=const`` lookup.
- ``ix_verif_scores_model`` (model, days_out) — same dedup; every other use of
  ``model``/``days_out`` is a GROUP BY over a time-ranged scan that drives off
  ``ix_verif_scores_source_time``.

Verified before dropping rather than argued from the schema: each of the seven
known consumer queries was EXPLAINed on production, none named these three in
``possible_keys`` let alone chose one. They were then set INVISIBLE in
production for a 21-hour soak — plans re-checked identical, every scan-related
status counter below its lifetime rate (``Handler_read_rnd_next`` 0.25x,
``Select_scan`` 0.91x), no application errors, and none of the 46 slow-query
entries in that window touched them.

Worth ~620 MB of index and, more usefully, three fewer index trees to maintain
on every insert — the table takes ~2.3M inserts/month.

The four that remain all earn their place: ``uq_verif_scores_key`` (dedup and
the uniqueness constraint), ``ix_verif_scores_source_time`` (the workhorse —
daily rollup, gust rollup, notable-misses, missed-warnings, and the FORCE INDEX
hint in ``verification_stats``), ``ix_verif_scores_obs`` (FK cascade), and
``ix_verif_scores_source_model_days``, whose only consumer is the unhinted
activity COUNT(DISTINCT) and which becomes droppable once #522's Phase 1 read
switch retires that query.

Downgrade recreates all three, so this is reversible if a consumer surfaces.

Revision ID: 086
Revises: 085
"""

from typing import Sequence, Union

from alembic import op

revision: str = "086"
down_revision: Union[str, None] = "085"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # drop_index needs no batch_alter_table: it is supported natively on both
    # SQLite and MySQL, unlike column/constraint ALTERs.
    op.drop_index("ix_verif_scores_model", table_name="verification_scores")
    op.drop_index("ix_verif_scores_icao", table_name="verification_scores")
    op.drop_index("ix_verif_scores_lead", table_name="verification_scores")


def downgrade() -> None:
    op.create_index(
        "ix_verif_scores_lead", "verification_scores", ["lead_hours"]
    )
    op.create_index("ix_verif_scores_icao", "verification_scores", ["icao"])
    op.create_index(
        "ix_verif_scores_model", "verification_scores", ["model", "days_out"]
    )
