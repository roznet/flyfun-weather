"""Record which TEMSI validity each briefing chose, per zone.

The two existing chart sources store a ``(run_cycle, default_chart_id)`` pair:
one issue cycle, plus which forecast offset within it this flight should open
on. Météo-France does not fit that shape. AEROWEB publishes no run/offset
split at all — it offers a rolling window of *absolute validities* three hours
apart — so what a briefing needs to remember is which validity it picked, and
it has to remember that separately per zone because the two TEMSI zones do not
publish in lockstep (EUROC has been observed a validity ahead of France).

Hence a JSON *list* of ``{"zone", "run_cycle"}`` picker options — "France
15Z", "France 18Z", "EUROC 18Z" — plus which one to open on, rather than a
second pair of scalar columns that would have to lie about one of the zones.

``server_default`` of ``[]`` and ``0`` so existing rows read back as
"unavailable" — which is correct: packs built before this migration never
fetched a TEMSI, and an empty list is exactly how the renderer spells that.

Note the empty list is also the normal steady state for most briefings, not
just old ones. TEMSI's horizon is ~3h, and the licence limits redistribution
to routes touching French airspace, so any briefing built the day before, or
for a route outside France, legitimately stores ``[]``.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "094"
down_revision: Union[str, None] = "093"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ``meteofrance_charts_options_json`` is added NULLable and tightened to NOT
    # NULL after a backfill, rather than declared NOT NULL with a
    # ``server_default`` of ``[]`` up front. MySQL rejects a DEFAULT on a
    # BLOB/TEXT/JSON column outright (error 1101), and because dev is SQLite --
    # which accepts it happily -- the whole test suite goes green on a migration
    # that cannot run in production. The three-step below is portable, so both
    # dialects end up with the same NOT NULL column and the same ``[]`` for
    # every pre-existing row.
    with op.batch_alter_table("briefing_packs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "meteofrance_charts_options_json",
                sa.Text(),
                nullable=True,
            ),
        )
        batch_op.add_column(
            sa.Column(
                "meteofrance_charts_default_id",
                sa.String(length=48),
                nullable=True,
            ),
        )
        batch_op.add_column(
            sa.Column(
                "meteofrance_charts_in_coverage",
                sa.Boolean(),
                nullable=False,
                server_default="0",
            ),
        )
        batch_op.add_column(
            sa.Column(
                "meteofrance_charts_within_horizon",
                sa.Boolean(),
                nullable=False,
                server_default="0",
            ),
        )

    # Existing rows predate TEMSI and so never fetched one -- an empty list is
    # exactly how the renderer spells "unavailable".
    op.execute(
        "UPDATE briefing_packs SET meteofrance_charts_options_json = '[]' "
        "WHERE meteofrance_charts_options_json IS NULL"
    )

    with op.batch_alter_table("briefing_packs") as batch_op:
        batch_op.alter_column(
            "meteofrance_charts_options_json",
            existing_type=sa.Text(),
            nullable=False,
        )


def downgrade() -> None:
    with op.batch_alter_table("briefing_packs") as batch_op:
        batch_op.drop_column("meteofrance_charts_within_horizon")
        batch_op.drop_column("meteofrance_charts_in_coverage")
        batch_op.drop_column("meteofrance_charts_default_id")
        batch_op.drop_column("meteofrance_charts_options_json")
