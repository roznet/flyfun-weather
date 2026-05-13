"""Add usage analytics tables (events, sessions, dims, rollups).

Privacy-first product analytics:

* ``analytics_events`` — raw events keyed by anonymous browser UUID. Retained
  for ``ANALYTICS_RAW_RETENTION_DAYS`` (default 60) days then deleted.
* ``analytics_sessions`` — one row per session, flags first-time vs repeat.
* ``analytics_flights_dim`` — per-flight dimensions snapshot (region,
  distance, route shape, alternate ETD flag). Stable across briefings.
* ``analytics_briefings_dim`` — per-briefing dimensions (lead time at
  creation, model count, refresh sequence). Varies per refresh.
* ``analytics_event_daily`` — daily per-event totals + unique users.
* ``analytics_briefing_feature_daily`` — daily per-feature attachment rate
  computed over briefings opened that day.

Both rollup tables are kept forever — they preserve long-term trends even
after the raw events have been deleted by retention.

All tables are pure ``op.create_table`` (works identically on SQLite + MySQL
without batch mode).

Revision ID: 054
Revises: 053
Create Date: 2026-05-13
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "054"
down_revision: Union[str, None] = "053"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "analytics_events",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column("ts", sa.DateTime(timezone=True), nullable=False),
        sa.Column("anon_id", sa.String(36), nullable=False),
        sa.Column("session_id", sa.String(36), nullable=False),
        # Promoted from props for fast aggregation. Server-derived from
        # briefing_id at ingest; NULL for events outside a briefing.
        sa.Column("flight_id", sa.String(256), nullable=True),
        sa.Column("briefing_id", sa.Integer(), nullable=True),
        sa.Column("event", sa.String(64), nullable=False),
        sa.Column("props", sa.Text(), nullable=True),  # JSON blob
        sa.Column("app_version", sa.String(32), nullable=True),
        sa.Index("ix_ae_event_ts", "event", "ts"),
        sa.Index("ix_ae_briefing", "briefing_id"),
        sa.Index("ix_ae_flight", "flight_id"),
        sa.Index("ix_ae_anon_ts", "anon_id", "ts"),
        sa.Index("ix_ae_ts", "ts"),
    )

    op.create_table(
        "analytics_sessions",
        sa.Column("session_id", sa.String(36), primary_key=True),
        sa.Column("anon_id", sa.String(36), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "is_first_session", sa.Boolean(),
            nullable=False, server_default="0",
        ),
        sa.Column("app_version", sa.String(32), nullable=True),
        sa.Index("ix_as_anon", "anon_id"),
        sa.Index("ix_as_started_at", "started_at"),
    )

    op.create_table(
        "analytics_flights_dim",
        sa.Column("flight_id", sa.String(256), primary_key=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        # 'EU' | 'US' | 'OTHER'
        sa.Column("region", sa.String(8), nullable=True),
        # 'short' | 'medium' | 'long'
        sa.Column("distance_bucket", sa.String(16), nullable=True),
        # Raw count, capped at 10. 2 = direct, 3+ = with waypoints.
        sa.Column("route_points", sa.SmallInteger(), nullable=True),
        sa.Column(
            "has_alternate_etd", sa.Boolean(),
            nullable=False, server_default="0",
        ),
        sa.Index("ix_afd_region", "region"),
        sa.Index("ix_afd_created", "created_at"),
    )

    op.create_table(
        "analytics_briefings_dim",
        sa.Column("briefing_id", sa.Integer(), primary_key=True),
        sa.Column("flight_id", sa.String(256), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        # Position in this flight's briefing sequence (1, 2, ...).
        sa.Column("briefing_seq", sa.SmallInteger(), nullable=False, server_default="1"),
        sa.Column("is_refresh", sa.Boolean(), nullable=False, server_default="0"),
        # 'post_departure' | 'same_day' | '1d' | '2_3d' | '4_7d' | '7d_plus' | 'no_etd'
        sa.Column("lead_time_bucket", sa.String(16), nullable=True),
        sa.Column("model_count", sa.SmallInteger(), nullable=True),
        sa.Index("ix_abd_flight", "flight_id"),
        sa.Index("ix_abd_created", "created_at"),
    )

    op.create_table(
        "analytics_event_daily",
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("event", sa.String(64), nullable=False),
        sa.Column("total_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unique_anons", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("unique_new_anons", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("day", "event", name="pk_aed_day_event"),
        sa.Index("ix_aed_day", "day"),
    )

    op.create_table(
        "analytics_briefing_feature_daily",
        sa.Column("day", sa.Date(), nullable=False),
        # 'forecast_map' | 'skewt' | 'compare' | 'auto_refresh' | 'detailed_mode' | ...
        sa.Column("feature", sa.String(64), nullable=False),
        # Denominator: distinct briefings opened that day.
        sa.Column("briefings_total", sa.Integer(), nullable=False, server_default="0"),
        # Numerator: distinct briefings where the feature was used >= 1x.
        sa.Column(
            "briefings_with_feature", sa.Integer(),
            nullable=False, server_default="0",
        ),
        # Power-user signal: total feature event count across all briefings.
        sa.Column("total_uses", sa.Integer(), nullable=False, server_default="0"),
        sa.PrimaryKeyConstraint("day", "feature", name="pk_abfd_day_feature"),
        sa.Index("ix_abfd_day", "day"),
    )


def downgrade() -> None:
    op.drop_table("analytics_briefing_feature_daily")
    op.drop_table("analytics_event_daily")
    op.drop_table("analytics_briefings_dim")
    op.drop_table("analytics_flights_dim")
    op.drop_table("analytics_sessions")
    op.drop_table("analytics_events")
