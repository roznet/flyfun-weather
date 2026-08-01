"""SQLAlchemy models for the usage-analytics tables.

Mirrors migration ``054_analytics_tables.py``. Tables are intentionally
free of foreign keys back to ``flights`` / ``briefing_packs`` so analytics
data survives flight/briefing deletion (snapshot semantics).
"""

from __future__ import annotations

from datetime import date as date_t, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    Index,
    Integer,
    PrimaryKeyConstraint,
    SmallInteger,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column

from flyfun_common.db.models import Base


class AnalyticsEventRow(Base):
    __tablename__ = "analytics_events"
    __table_args__ = (
        Index("ix_ae_event_ts", "event", "ts"),
        Index("ix_ae_briefing", "briefing_id"),
        Index("ix_ae_flight", "flight_id"),
        Index("ix_ae_anon_ts", "anon_id", "ts"),
        Index("ix_ae_ts", "ts"),
    )

    # BigInteger().with_variant(Integer, "sqlite") so SQLite emits
    # ``INTEGER PRIMARY KEY`` — the only form SQLite treats as a rowid alias
    # with automatic ID assignment. Plain BIGINT PRIMARY KEY is a normal
    # NOT NULL column with no default and inserts fail.
    id: Mapped[int] = mapped_column(
        BigInteger().with_variant(Integer, "sqlite"),
        primary_key=True,
        autoincrement=True,
    )
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    anon_id: Mapped[str] = mapped_column(String(36), nullable=False)
    session_id: Mapped[str] = mapped_column(String(36), nullable=False)
    flight_id: Mapped[str | None] = mapped_column(String(256), nullable=True)
    briefing_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    props: Mapped[str | None] = mapped_column(Text, nullable=True)
    app_version: Mapped[str | None] = mapped_column(String(32), nullable=True)


class AnalyticsSessionRow(Base):
    __tablename__ = "analytics_sessions"
    __table_args__ = (
        Index("ix_as_anon", "anon_id"),
        Index("ix_as_started_at", "started_at"),
    )

    session_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    anon_id: Mapped[str] = mapped_column(String(36), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    is_first_session: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    app_version: Mapped[str | None] = mapped_column(String(32), nullable=True)


class AnalyticsFlightDimRow(Base):
    __tablename__ = "analytics_flights_dim"
    __table_args__ = (
        Index("ix_afd_region", "region"),
        Index("ix_afd_created", "created_at"),
    )

    flight_id: Mapped[str] = mapped_column(String(256), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    region: Mapped[str | None] = mapped_column(String(8), nullable=True)
    distance_bucket: Mapped[str | None] = mapped_column(String(16), nullable=True)
    route_points: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)
    has_alternate_etd: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


class AnalyticsBriefingDimRow(Base):
    __tablename__ = "analytics_briefings_dim"
    __table_args__ = (
        Index("ix_abd_flight", "flight_id"),
        Index("ix_abd_created", "created_at"),
    )

    briefing_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    flight_id: Mapped[str] = mapped_column(String(256), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    briefing_seq: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=1)
    is_refresh: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    # One of: 'post_departure' | 'same_day' | '1d' | '2_3d' | '4_7d' |
    # '7d_plus' | 'no_etd'. See ``enrich._lead_time_bucket``.
    lead_time_bucket: Mapped[str | None] = mapped_column(String(16), nullable=True)
    model_count: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)


class AnalyticsEventDailyRow(Base):
    __tablename__ = "analytics_event_daily"
    __table_args__ = (
        PrimaryKeyConstraint("day", "event", name="pk_aed_day_event"),
    )

    day: Mapped[date_t] = mapped_column(Date, nullable=False)
    event: Mapped[str] = mapped_column(String(64), nullable=False)
    total_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unique_anons: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unique_new_anons: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class AnalyticsBriefingFeatureDailyRow(Base):
    __tablename__ = "analytics_briefing_feature_daily"
    __table_args__ = (
        PrimaryKeyConstraint("day", "feature", name="pk_abfd_day_feature"),
    )

    day: Mapped[date_t] = mapped_column(Date, nullable=False)
    feature: Mapped[str] = mapped_column(String(64), nullable=False)
    briefings_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    briefings_with_feature: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_uses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class AnalyticsXsectionConfigDailyRow(Base):
    """Daily per-dimension breakdown of cross-section display config.

    Rolled up from ``xsection.viewed`` snapshot events. Each row is one
    ``(day, dimension, value)`` bucket:

    * scalar dimensions (``theme``, ``preset``, ``layout``, …) — ``views`` is
      the count of cross-section views that had that value;
    * the ``"layer"`` dimension — one row per enabled layer id, ``views`` is
      the count of views with that layer enabled (per-layer attachment).

    The denominator (total xsection views / unique viewers that day) comes
    from ``analytics_event_daily`` for ``xsection.viewed``; the client divides
    to get shares and attachment percentages. Kept forever, like the other
    ``*_daily`` rollups.
    """

    __tablename__ = "analytics_xsection_config_daily"
    __table_args__ = (
        PrimaryKeyConstraint("day", "dimension", "value", name="pk_axcd_day_dim_val"),
    )

    day: Mapped[date_t] = mapped_column(Date, nullable=False)
    dimension: Mapped[str] = mapped_column(String(32), nullable=False)
    value: Mapped[str] = mapped_column(String(64), nullable=False)
    views: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    unique_anons: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
