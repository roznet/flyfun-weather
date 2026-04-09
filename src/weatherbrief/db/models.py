"""SQLAlchemy ORM models for weather-specific tables.

Shared models (UserRow, ApiTokenRow, UserPreferencesRow, CostLedgerRow) come
from flyfun_common.db.models. All models share the same Base so that cross-table
ForeignKey references work correctly.
"""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
from sqlalchemy.dialects.mysql import MEDIUMTEXT
from sqlalchemy.orm import Mapped, mapped_column, relationship

# Re-export shared models so existing imports from weatherbrief.db.models still work
from flyfun_common.db.models import (  # noqa: F401
    Base,
    UserRow,
    ApiTokenRow,
    UserPreferencesRow,
    CostLedgerRow,
)


class UserAircraftRow(Base):
    __tablename__ = "user_aircraft"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    icao_type: Mapped[str] = mapped_column(String(4))  # e.g. SR22, C172
    tail_number: Mapped[str | None] = mapped_column(String(10), nullable=True)
    nickname: Mapped[str | None] = mapped_column(String(50), nullable=True)
    is_ifr: Mapped[bool] = mapped_column(Boolean, default=False)
    is_fiki: Mapped[bool] = mapped_column(Boolean, default=False)
    cruise_speed_kt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ceiling_ft: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    user: Mapped[UserRow] = relationship(UserRow)


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
    aircraft_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("user_aircraft.id", ondelete="SET NULL"), nullable=True
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
    aircraft: Mapped[UserAircraftRow | None] = relationship(UserAircraftRow)
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


# ---------------------------------------------------------------------------
# PIREP enums (used by validators; stored as VARCHAR, not sa.Enum)
# ---------------------------------------------------------------------------

ICING_INTENSITIES = ("none", "trace", "light", "moderate", "severe")
ICING_TYPES = ("rime", "clear", "mixed")
TURBULENCE_INTENSITIES = ("none", "light", "moderate", "severe")
TOPS_BASES = ("crossed", "estimated_above", "below_min")
PIREP_SOURCES = ("manual", "inflight", "postflight")


class PirepRow(Base):
    __tablename__ = "pireps"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    client_uuid: Mapped[str | None] = mapped_column(String(36), unique=True, nullable=True)
    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    latitude: Mapped[float] = mapped_column(Float)
    longitude: Mapped[float] = mapped_column(Float)
    gps_altitude_ft: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reported_altitude_ft: Mapped[int | None] = mapped_column(Integer, nullable=True)
    in_cloud: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    icing_intensity: Mapped[str | None] = mapped_column(String(16), nullable=True)
    icing_type: Mapped[str | None] = mapped_column(String(16), nullable=True)
    turbulence_intensity: Mapped[str | None] = mapped_column(String(16), nullable=True)
    ceiling_msl_ft: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tops_msl_ft: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tops_basis: Mapped[str | None] = mapped_column(String(16), nullable=True)
    temp_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    wind_dir: Mapped[int | None] = mapped_column(Integer, nullable=True)
    wind_speed_kt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    remarks: Mapped[str | None] = mapped_column(Text, nullable=True)
    aircraft_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("user_aircraft.id", ondelete="SET NULL"), nullable=True
    )
    pack_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("briefing_packs.id", ondelete="SET NULL"), nullable=True, index=True
    )
    source: Mapped[str] = mapped_column(String(16), default="manual")
    user_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="SET NULL"), nullable=True, index=True
    )

    user: Mapped[UserRow | None] = relationship(UserRow, foreign_keys=[user_id])
    aircraft: Mapped[UserAircraftRow | None] = relationship(UserAircraftRow)
    pack: Mapped[BriefingPackRow | None] = relationship(BriefingPackRow)


class DeviceTokenRow(Base):
    __tablename__ = "device_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token: Mapped[str] = mapped_column(String(200), unique=True)
    environment: Mapped[str] = mapped_column(String(16))
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    user: Mapped[UserRow] = relationship(UserRow)


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
            "icao", "observation_time", "model", "model_init_time", "source",
            name="uq_verif_scores_key",
        ),
        Index("ix_verif_scores_obs", "observation_id"),
        Index("ix_verif_scores_model", "model", "days_out"),
        Index("ix_verif_scores_icao", "icao"),
        Index("ix_verif_scores_lead", "lead_hours"),
        Index("ix_verif_scores_source_model_days", "source", "model", "days_out"),
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
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="flight")

    # Flight category comparison
    obs_flight_category: Mapped[str | None] = mapped_column(String(4), nullable=True)
    model_flight_category: Mapped[str | None] = mapped_column(String(4), nullable=True)
    category_match: Mapped[bool | None] = mapped_column(Boolean, nullable=True)

    # Quantitative deltas (model - observation)
    ceiling_delta_ft: Mapped[float | None] = mapped_column(Float, nullable=True)
    cloud_base_delta_ft: Mapped[float | None] = mapped_column(Float, nullable=True)
    lcl_delta_ft: Mapped[float | None] = mapped_column(Float, nullable=True)
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
            "icao", "observation_time", "taf_issue_time", "source",
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
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="flight")

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


class AirportForecastSnapshotRow(Base):
    """NWP forecast snapshot for a METAR-reporting airport at a sample hour.

    Stores surface fields from Open-Meteo API and ceiling estimates from GRIB.
    Used by the standalone verification pipeline to score models against METARs.
    """

    __tablename__ = "airport_forecast_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "icao", "model", "model_init_time", "forecast_hour",
            name="uq_afs_key",
        ),
        Index("ix_afs_icao_hour", "icao", "forecast_hour"),
        Index("ix_afs_fetched", "fetched_at"),
        Index("ix_afs_model_init", "model", "model_init_time"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    icao: Mapped[str] = mapped_column(String(4), nullable=False)
    model: Mapped[str] = mapped_column(String(20), nullable=False)
    model_init_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    forecast_hour: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    # Surface fields (from Open-Meteo API)
    temperature_2m_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    dewpoint_2m_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    visibility_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    wind_speed_10m_kt: Mapped[float | None] = mapped_column(Float, nullable=True)
    wind_direction_10m_deg: Mapped[float | None] = mapped_column(Float, nullable=True)
    wind_gusts_10m_kt: Mapped[float | None] = mapped_column(Float, nullable=True)
    precipitation_mm: Mapped[float | None] = mapped_column(Float, nullable=True)
    snowfall_cm: Mapped[float | None] = mapped_column(Float, nullable=True)
    cape_jkg: Mapped[float | None] = mapped_column(Float, nullable=True)
    weather_code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cloud_cover_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    cloud_cover_low_pct: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Ceiling estimates (from GRIB + computed)
    nwp_ceiling_ft: Mapped[float | None] = mapped_column(Float, nullable=True)
    cloud_base_ft: Mapped[float | None] = mapped_column(Float, nullable=True)
    lcl_ft: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Sounding-derived fields (from analyze_sounding on pressure levels)
    sounding_ceiling_ft: Mapped[float | None] = mapped_column(Float, nullable=True)
    sounding_cloud_base_ft: Mapped[float | None] = mapped_column(Float, nullable=True)
    freezing_level_ft: Mapped[float | None] = mapped_column(Float, nullable=True)
    sounding_cape_jkg: Mapped[float | None] = mapped_column(Float, nullable=True)
    sounding_cin_jkg: Mapped[float | None] = mapped_column(Float, nullable=True)
    sounding_lifted_index: Mapped[float | None] = mapped_column(Float, nullable=True)
    sounding_convective_risk: Mapped[str | None] = mapped_column(String(16), nullable=True)


class VerificationMonthlyStatsRow(Base):
    """Pre-aggregated monthly verification accuracy per airport/model/days_out.

    Combines both model (GFS, ICON, ECMWF) and TAF scores into a single table.
    For TAF rows, model='taf' and days_out is derived from lead_hours buckets.
    Generated monthly by the retention/rollup job.
    """

    __tablename__ = "verification_monthly_stats"
    __table_args__ = (
        UniqueConstraint(
            "month", "source", "model", "days_out", "icao",
            name="uq_vms_key",
        ),
        Index("ix_vms_month", "month"),
        Index("ix_vms_model_days", "model", "days_out"),
        Index("ix_vms_icao", "icao"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    month: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )  # first day of month
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # flight / standalone
    model: Mapped[str] = mapped_column(String(20), nullable=False)  # gfs / icon / ecmwf / taf
    days_out: Mapped[int] = mapped_column(Integer, nullable=False)
    icao: Mapped[str] = mapped_column(String(4), nullable=False)

    # Sample size
    n_scores: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Flight category direction counts
    n_cat_match: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_cat_optimistic_1: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_cat_optimistic_2: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_cat_pessimistic_1: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_cat_pessimistic_2: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Wind advisory direction counts
    n_advisory_match: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_advisory_optimistic: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_advisory_pessimistic: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Continuous error metrics (MAE = mean absolute error, bias = signed mean)
    ceiling_mae_ft: Mapped[float | None] = mapped_column(Float, nullable=True)
    ceiling_bias_ft: Mapped[float | None] = mapped_column(Float, nullable=True)
    visibility_mae_m: Mapped[float | None] = mapped_column(Float, nullable=True)
    wind_speed_mae_kt: Mapped[float | None] = mapped_column(Float, nullable=True)
    temperature_mae_c: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Precipitation / convection hit rates (model scores only, NULL for TAF)
    n_precip_hit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_precip_miss: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_precip_false_alarm: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_convection_hit: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_convection_miss: Mapped[int | None] = mapped_column(Integer, nullable=True)
    n_convection_false_alarm: Mapped[int | None] = mapped_column(Integer, nullable=True)


class VerificationCacheRow(Base):
    """Pre-computed API responses for verification dashboard and maps.

    Rebuilt after each standalone verification cycle. API endpoints serve
    cached JSON directly instead of running expensive aggregation queries.
    A staleness check (source_max_time vs live MAX) guards against stale data.
    """

    __tablename__ = "verification_cache"
    __table_args__ = (
        UniqueConstraint("cache_key", name="uq_vcache_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    cache_key: Mapped[str] = mapped_column(String(128), nullable=False)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    source_max_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True,
    )
    data_json: Mapped[str] = mapped_column(
        Text().with_variant(MEDIUMTEXT, "mysql"), nullable=False,
    )


class VerificationCycleRow(Base):
    """Performance metrics for each verification collection cycle.

    One row per cycle. source='flight' for flight verification,
    source='standalone' for standalone airport verification.
    """

    __tablename__ = "verification_cycles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[str] = mapped_column(String(24), nullable=False, default="flight")
    phase_finalize_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    phase_find_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    phase_gather_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    phase_fetch_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    phase_store_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    phase_score_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    flights: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    airports: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    observations_stored: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    finalized: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    scored: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)


