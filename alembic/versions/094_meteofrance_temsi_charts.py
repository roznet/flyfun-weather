"""Record which TEMSI validity each briefing chose, per zone.

The two existing chart sources store a ``(run_cycle, default_chart_id)`` pair:
one issue cycle, plus which forecast offset within it this flight should open
on. Météo-France does not fit that shape. AEROWEB publishes no run/offset
split at all — it offers a rolling window of *absolute validities* three hours
apart — so what a briefing needs to remember is which validity it picked, and
it has to remember that separately per zone because the two TEMSI zones do not
publish in lockstep (EUROC has been observed a validity ahead of France).

Hence one JSON column mapping zone slug -> validity, e.g.
``{"france": "2026-08-31T15Z", "euroc": "2026-08-31T18Z"}``, rather than a
second pair of scalar columns that would have to lie about one of the zones.

``server_default`` of ``{}`` and ``0`` so existing rows read back as
"unavailable" — which is correct: packs built before this migration never
fetched a TEMSI, and an empty map is exactly how the renderer spells that.

Note the empty map is also the normal steady state for most briefings, not
just old ones. TEMSI's horizon is ~3h, and the licence limits redistribution
to routes touching French airspace, so any briefing built the day before, or
for a route outside France, legitimately stores ``{}``.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "094"
down_revision: Union[str, None] = "093"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("briefing_packs") as batch_op:
        batch_op.add_column(
            sa.Column(
                "meteofrance_charts_zone_cycles_json",
                sa.Text(),
                nullable=False,
                server_default="{}",
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


def downgrade() -> None:
    with op.batch_alter_table("briefing_packs") as batch_op:
        batch_op.drop_column("meteofrance_charts_within_horizon")
        batch_op.drop_column("meteofrance_charts_in_coverage")
        batch_op.drop_column("meteofrance_charts_zone_cycles_json")
