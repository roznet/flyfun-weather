"""SQLAlchemy ORM models for weather-specific tables.

Shared models (UserRow, ApiTokenRow, UserPreferencesRow, CostLedgerRow) come
from flyfun_common.db.models. All models share the same Base so that cross-table
ForeignKey references work correctly.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
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
    verification_status: Mapped[str | None] = mapped_column(
        String(16), nullable=True, default=None
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
    config_json: Mapped[str] = mapped_column(Text, nullable=False)


class SystemMessageRow(Base):
    __tablename__ = "system_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(20), default="feature")  # feature, change, fix
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )


# ---------------------------------------------------------------------------
# Verification tables — standalone observation archive + flight linkage
# ---------------------------------------------------------------------------


class VerificationObservationRow(Base):
    """Ground truth METAR/TAF archive, keyed by (icao, observation_time)."""

    __tablename__ = "verification_observations"
    __table_args__ = (
        UniqueConstraint("icao", "observation_time", name="uq_verif_obs_icao_time"),
        Index("ix_verif_obs_icao", "icao"),
        Index("ix_verif_obs_time", "observation_time"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    icao: Mapped[str] = mapped_column(String(4), nullable=False)
    observation_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    collected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # METAR fields
    metar_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    flight_category: Mapped[str | None] = mapped_column(String(4), nullable=True)
    ceiling_ft: Mapped[int | None] = mapped_column(Integer, nullable=True)
    visibility_m: Mapped[int | None] = mapped_column(Integer, nullable=True)
    wind_dir: Mapped[int | None] = mapped_column(Integer, nullable=True)
    wind_speed_kt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    wind_gust_kt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    temperature_c: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dewpoint_c: Mapped[int | None] = mapped_column(Integer, nullable=True)
    qnh: Mapped[float | None] = mapped_column(Float, nullable=True)
    weather: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON list

    # TAF fields (active at observation_time)
    taf_raw: Mapped[str | None] = mapped_column(Text, nullable=True)
    taf_applicable: Mapped[str | None] = mapped_column(Text, nullable=True)
    taf_issue_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    taf_flight_category: Mapped[str | None] = mapped_column(String(4), nullable=True)
    taf_ceiling_ft: Mapped[int | None] = mapped_column(Integer, nullable=True)
    taf_visibility_m: Mapped[int | None] = mapped_column(Integer, nullable=True)
    taf_wind_dir: Mapped[int | None] = mapped_column(Integer, nullable=True)
    taf_wind_speed_kt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    taf_wind_gust_kt: Mapped[int | None] = mapped_column(Integer, nullable=True)


class VerificationScoreRow(Base):
    """Model-vs-METAR accuracy record."""

    __tablename__ = "verification_scores"
    __table_args__ = (
        UniqueConstraint(
            "icao", "observation_time", "model", "model_init_time",
            name="uq_verif_scores_key",
        ),
        Index("ix_verif_scores_obs", "observation_id"),
        Index("ix_verif_scores_model", "model", "days_out"),
        Index("ix_verif_scores_icao", "icao"),
        Index("ix_verif_scores_lead", "lead_hours"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    observation_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("verification_observations.id", ondelete="CASCADE"), nullable=False
    )
    icao: Mapped[str] = mapped_column(String(4), nullable=False)
    observation_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    model: Mapped[str] = mapped_column(String(20), nullable=False)
    model_init_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    lead_hours: Mapped[int] = mapped_column(Integer, nullable=False)
    days_out: Mapped[int] = mapped_column(Integer, nullable=False)

    # Flight category comparison
    obs_flight_category: Mapped[str | None] = mapped_column(String(4), nullable=True)
    model_flight_category: Mapped[str | None] = mapped_column(String(4), nullable=True)
    category_match: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Quantitative deltas (model - observation)
    ceiling_delta_ft: Mapped[float | None] = mapped_column(Float, nullable=True)
    visibility_delta_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    wind_speed_delta_kt: Mapped[float | None] = mapped_column(Float, nullable=True)
    wind_dir_delta_deg: Mapped[float | None] = mapped_column(Float, nullable=True)
    temperature_delta_c: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Wind advisory comparison
    obs_wind_advisory: Mapped[str | None] = mapped_column(String(10), nullable=True)
    model_wind_advisory: Mapped[str | None] = mapped_column(String(10), nullable=True)
    advisory_match: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Significant weather hit/miss
    obs_has_precipitation: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    model_has_precipitation: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    obs_has_convection: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    model_has_convection: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    observation: Mapped[VerificationObservationRow] = relationship(
        VerificationObservationRow
    )


class TafVerificationScoreRow(Base):
    """TAF-vs-METAR accuracy record."""

    __tablename__ = "taf_verification_scores"
    __table_args__ = (
        UniqueConstraint(
            "icao", "observation_time", "taf_issue_time",
            name="uq_taf_verif_key",
        ),
        Index("ix_taf_verif_obs", "observation_id"),
        Index("ix_taf_verif_icao", "icao"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    observation_id: Mapped[int] = mapped_column(
        Integer, ForeignKey("verification_observations.id", ondelete="CASCADE"), nullable=False
    )
    icao: Mapped[str] = mapped_column(String(4), nullable=False)
    observation_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    taf_issue_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    lead_hours: Mapped[int] = mapped_column(Integer, nullable=False)

    # Flight category
    obs_flight_category: Mapped[str | None] = mapped_column(String(4), nullable=True)
    taf_flight_category: Mapped[str | None] = mapped_column(String(4), nullable=True)
    category_match: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Deltas (TAF - observation)
    ceiling_delta_ft: Mapped[float | None] = mapped_column(Float, nullable=True)
    visibility_delta_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    wind_speed_delta_kt: Mapped[float | None] = mapped_column(Float, nullable=True)
    wind_dir_delta_deg: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Wind advisory comparison
    obs_wind_advisory: Mapped[str | None] = mapped_column(String(10), nullable=True)
    taf_wind_advisory: Mapped[str | None] = mapped_column(String(10), nullable=True)
    advisory_match: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    observation: Mapped[VerificationObservationRow] = relationship(
        VerificationObservationRow
    )


class FlightVerificationMapRow(Base):
    """Thin linkage between flights and verification observations.

    Populated on first collection cycle to cache corridor airports.
    observation_id is nullable — set when observations arrive.
    CASCADE on flight delete removes linkage but not observations.
    """

    __tablename__ = "flight_verification_map"
    __table_args__ = (
        UniqueConstraint(
            "flight_id", "icao", "observation_id",
            name="uq_fvm_flight_icao_obs",
        ),
        Index("ix_fvm_flight", "flight_id"),
        Index("ix_fvm_icao", "icao"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    flight_id: Mapped[str] = mapped_column(
        String(256), ForeignKey("flights.id", ondelete="CASCADE"), nullable=False
    )
    icao: Mapped[str] = mapped_column(String(4), nullable=False)
    observation_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("verification_observations.id", ondelete="CASCADE"), nullable=True
    )
    distance_from_route_nm: Mapped[float | None] = mapped_column(Float, nullable=True)

    flight: Mapped[FlightRow] = relationship(FlightRow)
    observation: Mapped[VerificationObservationRow | None] = relationship(
        VerificationObservationRow
    )


