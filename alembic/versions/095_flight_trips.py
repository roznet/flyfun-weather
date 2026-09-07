"""Add flight_trips table and trip_id on flights.

Issue #602 — group flights into a trip whose viability is the conjunction of
its remaining legs. Follows the 004 pattern: one ``create_table`` plus one
``batch_alter_table`` add-column + named FK on ``flights``.

Two deliberate non-columns:

* **no leg position** — chain order derives from ``departure_time``, so a
  ``/move`` that reschedules a leg needs no fixup;
* **no stored chain aggregate** — it is stale the moment any member leg
  refreshes and is recomputed per read.

``ON DELETE SET NULL`` on ``flights.trip_id``: deleting a trip unlinks its
legs and never cascades into the flights, which own the (expensive) packs.

No ``server_default`` on any Text column here — MySQL error 1101 (see
designs/migrations.md); the two Text columns are plain nullable.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "095"
down_revision: Union[str, None] = "094"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "flight_trips",
        sa.Column("id", sa.String(16), primary_key=True),
        sa.Column(
            "user_id",
            sa.String(64),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("name", sa.String(200), nullable=False, server_default=""),
        sa.Column("notes", sa.Text, nullable=True),
        sa.Column("auto_refresh", sa.Boolean, nullable=False, server_default=sa.text("0")),
        sa.Column("auto_refresh_hour", sa.Integer, nullable=True),
        sa.Column(
            "notify_override", sa.String(16), nullable=False, server_default="default",
        ),
        sa.Column("share_code", sa.String(16), nullable=True, unique=True, index=True),
        sa.Column("ai_summary_text", sa.Text, nullable=True),
        sa.Column("ai_summary_key", sa.String(64), nullable=True),
        sa.Column("ai_summary_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("refresh_id", sa.String(32), nullable=True),
        sa.Column("refresh_state_json", sa.Text, nullable=True),
        sa.Column("refresh_started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=True),
    )

    with op.batch_alter_table("flights") as batch_op:
        batch_op.add_column(sa.Column("trip_id", sa.String(16), nullable=True))
        batch_op.create_foreign_key(
            "fk_flights_trip_id",
            "flight_trips",
            ["trip_id"],
            ["id"],
            ondelete="SET NULL",
        )
        batch_op.create_index("ix_flights_trip_id", ["trip_id"])


def downgrade() -> None:
    with op.batch_alter_table("flights") as batch_op:
        batch_op.drop_index("ix_flights_trip_id")
        batch_op.drop_constraint("fk_flights_trip_id", type_="foreignkey")
        batch_op.drop_column("trip_id")
    op.drop_table("flight_trips")
