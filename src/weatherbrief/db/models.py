"""SQLAlchemy ORM models for weather-specific tables.

Shared models (UserRow, ApiTokenRow, UserPreferencesRow, CostLedgerRow) come
from flyfun_common.db.models. All models share the same Base so that cross-table
ForeignKey references work correctly.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

# Re-export shared models so existing imports from weatherbrief.db.models still work
from flyfun_common.db.models import (  # noqa: F401
    Base,
    UserRow,
    ApiTokenRow,
    UserPreferencesRow,
    CostLedgerRow,
)


class FlightProfileRow(Base):
    __tablename__ = "flight_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100), default="Default")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    settings_json: Mapped[str] = mapped_column(Text, default="{}")
    system_template_key: Mapped[str | None] = mapped_column(
        String(50), nullable=True, default=None
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    user: Mapped[UserRow] = relationship(UserRow)
    flights: Mapped[list[FlightRow]] = relationship(back_populates="profile")


class FlightRow(Base):
    __tablename__ = "flights"

    id: Mapped[str] = mapped_column(String(256), primary_key=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    profile_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("flight_profiles.id", ondelete="SET NULL"), nullable=True
    )
    route_name: Mapped[str] = mapped_column(String(256), default="")
    waypoints_json: Mapped[str] = mapped_column(Text, default="[]")
    departure_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cruise_altitude_ft: Mapped[int] = mapped_column(Integer, default=8000)
    flight_ceiling_ft: Mapped[int] = mapped_column(Integer, default=18000)
    flight_duration_hours: Mapped[float] = mapped_column(default=0.0)
    private: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_refresh: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_refresh_hour: Mapped[int | None] = mapped_column(Integer, nullable=True)
    alt_departure_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    last_auto_refresh_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    user: Mapped[UserRow] = relationship(UserRow)
    profile: Mapped[FlightProfileRow | None] = relationship(back_populates="flights")
    packs: Mapped[list[BriefingPackRow]] = relationship(
        back_populates="flight", cascade="all, delete-orphan"
    )


class BriefingPackRow(Base):
    __tablename__ = "briefing_packs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    flight_id: Mapped[str] = mapped_column(
        String(256), ForeignKey("flights.id", ondelete="CASCADE"), index=True
    )
    fetch_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    days_out: Mapped[int] = mapped_column(Integer)
    has_gramet: Mapped[bool] = mapped_column(Boolean, default=False)
    has_skewt: Mapped[bool] = mapped_column(Boolean, default=False)
    has_digest: Mapped[bool] = mapped_column(Boolean, default=False)
    assessment: Mapped[str | None] = mapped_column(String(16), nullable=True)
    assessment_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact_path: Mapped[str] = mapped_column(Text, default="")
    model_init_times_json: Mapped[str] = mapped_column(Text, default="{}")
    grib_init_times_json: Mapped[str] = mapped_column(Text, default="{}")
    models_skipped_region_json: Mapped[str] = mapped_column(Text, default="[]")
    diagnostics_json: Mapped[str] = mapped_column(Text, default="[]")
    alt_assessment: Mapped[str | None] = mapped_column(String(16), nullable=True)
    alt_assessment_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    has_alt_advisories: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    integrity_hmac: Mapped[str | None] = mapped_column(String(64), nullable=True)

    flight: Mapped[FlightRow] = relationship(back_populates="packs")


class BriefingUsageRow(Base):
    __tablename__ = "briefing_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    flight_id: Mapped[str] = mapped_column(String(100), default="")
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    open_meteo_calls: Mapped[int] = mapped_column(Integer, default=0)
    gramet_fetched: Mapped[bool] = mapped_column(Boolean, default=False)
    gramet_failed: Mapped[bool] = mapped_column(Boolean, default=False)
    llm_digest: Mapped[bool] = mapped_column(Boolean, default=False)
    llm_model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    llm_input_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    llm_output_tokens: Mapped[int | None] = mapped_column(Integer, nullable=True)
    result_size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    elapsed_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    queue_wait_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    triggered_by: Mapped[str | None] = mapped_column(String(16), nullable=True)

    user: Mapped[UserRow] = relationship(UserRow)


class FeedbackRow(Base):
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    flight_id: Mapped[str | None] = mapped_column(String(256), nullable=True, index=True)
    pack_timestamp: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    category: Mapped[str] = mapped_column(String(32))
    comment: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    # Workflow / triage columns
    status: Mapped[str] = mapped_column(
        String(16), nullable=False, server_default="pending", default="pending"
    )
    classification: Mapped[str | None] = mapped_column(String(32), nullable=True)
    ai_analysis: Mapped[str | None] = mapped_column(Text, nullable=True)
    admin_reply: Mapped[str | None] = mapped_column(Text, nullable=True)
    admin_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
    replied_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped[UserRow] = relationship(UserRow)


class CostConfigRow(Base):
    __tablename__ = "cost_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    active_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        index=True,
        default=lambda: datetime.now(timezone.utc),
    )
    active_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None,
    )
    token_cost_per_1k_input: Mapped[float] = mapped_column(Float, default=0.003)
    token_cost_per_1k_output: Mapped[float] = mapped_column(Float, default=0.015)
    droplet_monthly_usd: Mapped[float] = mapped_column(Float, default=24.0)
    misc_monthly_usd: Mapped[float] = mapped_column(Float, default=2.0)
    subscriptions_monthly_usd: Mapped[float] = mapped_column(Float, default=30.0)
    subscription_details_json: Mapped[str] = mapped_column(
        String(1024), default='{"open_meteo": 30}',
    )
    disk_cost_per_gb_monthly: Mapped[float] = mapped_column(Float, default=0.10)
    estimated_monthly_briefings: Mapped[int] = mapped_column(Integer, default=500)
    margin_percent: Mapped[float] = mapped_column(Float, default=30.0)
    usd_per_credit: Mapped[float] = mapped_column(Float, default=0.01)


