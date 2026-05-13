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

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
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
    lead_time_bucket: Mapped[str | None] = mapped_column(String(16), nullable=True)
    model_count: Mapped[int | None] = mapped_column(SmallInteger, nullable=True)


class AnalyticsEventDailyRow(Base):
    __tablename__ = "analytics_event_daily"
    __table_args__ = (
        PrimaryKeyConstraint("day", "event", name="pk_aed_day_event"),
        Index("ix_aed_day", "day"),
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
        Index("ix_abfd_day", "day"),
    )

    day: Mapped[date_t] = mapped_column(Date, nullable=False)
    feature: Mapped[str] = mapped_column(String(64), nullable=False)
    briefings_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    briefings_with_feature: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_uses: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
