"""Flight facts: the durable per-flight record behind the past-flights finder (#1031).

Revision ID: 123
Revises: 122
Create Date: 2026-11-26

``flight_facts`` is a 1:1 sidecar to ``flights`` (cascade delete) that
retention never touches: route ends and line, forecast hazard tags from the
last briefing before departure, reported tags and the debrief outcome. Rows
are written by ``storage/flight_facts.update_flight_facts``; existing flights
are filled by ``scripts/ops/backfill_flight_facts.py``.
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# DateTime(timezone=True) renders the same DDL as the models' TZDateTime()
# (designs/time-alignment-audit.md): none of these times is a key.

revision: str = "123"
down_revision: Union[str, None] = "122"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "flight_facts",
        sa.Column("flight_id", sa.String(length=256),
                  sa.ForeignKey("flights.id", ondelete="CASCADE", name="fk_flight_facts_flight_id"),
                  primary_key=True),
        sa.Column("departure_icao", sa.String(length=16), nullable=True),
        sa.Column("destination_icao", sa.String(length=16), nullable=True),
        sa.Column("departure_lat", sa.Float(), nullable=True),
        sa.Column("departure_lon", sa.Float(), nullable=True),
        sa.Column("destination_lat", sa.Float(), nullable=True),
        sa.Column("destination_lon", sa.Float(), nullable=True),
        sa.Column("route_line_json", sa.Text(), nullable=True),
        sa.Column("distance_nm", sa.Float(), nullable=True),
        sa.Column("forecast_tags_json", sa.Text(), nullable=True),
        sa.Column("forecast_pack_ts", sa.DateTime(timezone=True), nullable=True),
        sa.Column("tags_recorded", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("forecast_frozen_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reported_tags_json", sa.Text(), nullable=True),
        sa.Column("decision", sa.String(length=16), nullable=True),
        sa.Column("debriefed", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("interesting", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("differs_from_forecast", sa.Boolean(), nullable=False, server_default="0"),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )


def downgrade() -> None:
    op.drop_table("flight_facts")
