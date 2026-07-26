"""SQLAlchemy ORM models for weather-specific tables.

Shared models (UserRow, ApiTokenRow, UserPreferencesRow, CostLedgerRow) come
from flyfun_common.db.models. All models share the same Base so that cross-table
ForeignKey references work correctly.
"""

from __future__ import annotations

from datetime import date as date_t, datetime, timezone

from sqlalchemy import BigInteger, Boolean, Date, DateTime, Double, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint
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
from flyfun_common.oauth import (  # noqa: F401 — register OAuth tables on Base
    OAuthClientRow,
    OAuthAuthorizationCodeRow,
    OAuthRefreshTokenRow,
)

# Register analytics tables on Base so init_shared_db picks them up in dev.
from weatherbrief.analytics.models import (  # noqa: F401
    AnalyticsBriefingDimRow,
    AnalyticsBriefingFeatureDailyRow,
    AnalyticsEventDailyRow,
    AnalyticsEventRow,
    AnalyticsFlightDimRow,
    AnalyticsSessionRow,
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
    # Timing-scenario flexibility mode (timing-scenario-plan.md):
    # none | alternate | same_day | prev_day | next_day. "alternate" grades the
    # single alt_departure_time; the day modes run the departure-window scan.
    flexibility: Mapped[str] = mapped_column(String(16), default="none")
    # Per-flight briefing-notification override (ios-app-briefing-notifications.md):
    # default (follow the global scope) | notify (push/email on ANY completion of
    # this flight) | mute (never notify for this flight). Independent of
    # auto_refresh — a "notify" flight with auto_refresh off is the Siri-loop case.
    notify_override: Mapped[str] = mapped_column(String(16), default="default")
    last_auto_refresh_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    verification_status: Mapped[str | None] = mapped_column(
        String(16), nullable=True, default=None
    )
    # Original Field-15 input the pilot typed (see weatherbrief.models.Flight).
    raw_route: Mapped[str | None] = mapped_column(String(4000), nullable=True)
    parser_version: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # Random 8-char base62 token for short share links (/s/{code}).
    # Nullable for legacy/test rows; create_flight always sets one.
    share_code: Mapped[str | None] = mapped_column(
        String(16), nullable=True, unique=True, index=True
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
    debrief: Mapped["FlightDebriefRow | None"] = relationship(
        back_populates="flight", uselist=False, cascade="all, delete-orphan"
    )


class FlightDebriefRow(Base):
    """Pilot post-flight judgement (cancelled / flown), one per flight.

    Sidecar table — kept independent from FlightRow so the row can be
    upserted without touching the flight definition. Cascade-deletes when
    the flight is removed; the presence of any row exempts all of the
    flight's packs from T2 retention (see tasks/retention.py).
    """

    __tablename__ = "flight_debriefs"

    flight_id: Mapped[str] = mapped_column(
        String(256), ForeignKey("flights.id", ondelete="CASCADE"), primary_key=True
    )
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    reasons_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    outcomes_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    flight: Mapped[FlightRow] = relationship(back_populates="debrief")


class FlightSubscriptionRow(Base):
    """Read-only subscription to another user's flight.

    Owners cannot subscribe to their own flights. Privacy flip on the parent
    flight filters it out of subscriber list queries (see storage/flights.py).
    """

    __tablename__ = "flight_subscriptions"
    __table_args__ = (
        UniqueConstraint("flight_id", "user_id", name="uq_flight_subs_flight_user"),
        Index("ix_flight_subs_flight", "flight_id"),
        Index("ix_flight_subs_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    flight_id: Mapped[str] = mapped_column(
        String(256), ForeignKey("flights.id", ondelete="CASCADE"), nullable=False
    )
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(timezone.utc),
    )

    flight: Mapped[FlightRow] = relationship(FlightRow)
    user: Mapped[UserRow] = relationship(UserRow)


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
    # Profile AI toggle for this pack. server_default="1" so pre-existing rows
    # read as "digest was requested" — preserves the old has_digest semantics
    # for legacy packs (see BriefingPackMeta.llm_digest_requested).
    llm_digest_requested: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="1",
    )
    assessment: Mapped[str | None] = mapped_column(String(16), nullable=True)
    assessment_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Compact RED/AMBER advisory breakdown (AdvisorySummary JSON) denormalized
    # at briefing-build time for the flights-list card chips. NULL for packs
    # created before issue #276 / before their next refresh.
    advisory_summary_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Long-range outlook (beyond the GRIB horizon), shown instead of the
    # traffic-light assessment. Mutually exclusive with ``assessment``.
    outlook: Mapped[str | None] = mapped_column(String(32), nullable=True)
    outlook_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # LangSmith root run id of the digest LLM call (issue #244). NULL for
    # provisional rows (digest not yet run) and packs created before #244.
    # The feedback endpoint reads this to attach thumb ratings to the run.
    digest_trace_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    artifact_path: Mapped[str] = mapped_column(Text, default="")
    model_init_times_json: Mapped[str] = mapped_column(Text, default="{}")
    grib_init_times_json: Mapped[str] = mapped_column(Text, default="{}")
    model_sources_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    models_skipped_region_json: Mapped[str] = mapped_column(Text, default="[]")
    diagnostics_json: Mapped[str] = mapped_column(Text, default="[]")
    alt_assessment: Mapped[str | None] = mapped_column(String(16), nullable=True)
    alt_assessment_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    has_alt_advisories: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # DWD Surface Analysis & Forecast section.
    # Bytes live in the shared DATA_DIR/dwd_charts/<run_cycle>/ cache; this
    # row only stores references. NULL run_cycle = section unavailable for
    # this pack (out of coverage, beyond horizon, or refresh failed).
    dwd_charts_run_cycle: Mapped[str | None] = mapped_column(String(32), nullable=True)
    dwd_charts_default_id: Mapped[str | None] = mapped_column(String(8), nullable=True)
    dwd_charts_in_coverage: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0",
    )
    dwd_charts_within_horizon: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0",
    )
    # Met Office surface-pressure charts — second front-chart source.
    # Bytes live in DATA_DIR/metoffice_charts/<run_cycle>/.
    metoffice_charts_run_cycle: Mapped[str | None] = mapped_column(String(32), nullable=True)
    metoffice_charts_default_id: Mapped[str | None] = mapped_column(String(8), nullable=True)
    metoffice_charts_in_coverage: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0",
    )
    metoffice_charts_within_horizon: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="0",
    )
    integrity_hmac: Mapped[str | None] = mapped_column(String(64), nullable=True)

    flight: Mapped[FlightRow] = relationship(back_populates="packs")


class BriefingUsageRow(Base):
    __tablename__ = "briefing_usage"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    flight_id: Mapped[str] = mapped_column(String(256), default="")
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


class BriefingRefreshJobRow(Base):
    """Durable mirror of an in-flight briefing refresh (issue #499).

    The in-memory ``_RefreshRegistry`` is the single choke point every refresh
    path goes through; this table is its write-through mirror, so a refresh
    that is killed mid-flight (OOM, deploy, crash) leaves a record instead of
    evaporating. A non-terminal row present at process boot is by definition an
    orphan — the deployment runs a single uvicorn worker, so there is no other
    instance that could still own it, and no lease is needed.

    ``flight_id`` is intentionally not a foreign key: the row must outlive the
    flight so reconciliation can tell "flight deleted, don't resume" apart from
    "no record". See ``weatherbrief.tasks.refresh_resume``.
    """

    __tablename__ = "briefing_refresh_jobs"
    __table_args__ = (
        Index("ix_refresh_jobs_status", "status"),
        Index("ix_refresh_jobs_flight", "flight_id", "created_at"),
    )

    #: Statuses a job can end in. Anything else means "still in flight" and, at
    #: process boot, means "interrupted".
    TERMINAL = ("succeeded", "failed", "abandoned")

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    flight_id: Mapped[str] = mapped_column(String(256), nullable=False)
    user_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=True
    )
    # Registry queue-cap class: "user" | "scheduler" | "resume".
    triggered_by: Mapped[str] = mapped_column(String(16), default="user")
    # Client-declared attribution ("user" | "siri" | "mcp"), preserved so a
    # resumed refresh is still accounted to the surface that asked for it.
    source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    as_of_date: Mapped[date_t | None] = mapped_column(Date, nullable=True)
    # queued | running | succeeded | failed | abandoned
    status: Mapped[str] = mapped_column(String(16), default="queued")
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    heartbeat_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Last pipeline stage the registry saw — the "where did it die" half of a
    # post-mortem for an interrupted refresh.
    stage: Mapped[str | None] = mapped_column(String(64), nullable=True)
    pack_path: Mapped[str | None] = mapped_column(String(512), nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


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
    # 'up'/'down' for quick thumb ratings; NULL for the traditional form
    sentiment: Mapped[str | None] = mapped_column(String(8), nullable=True)
    # 'digest' for thumb ratings; NULL/'general' for the traditional form
    target: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # User is OK to receive an email reply about this feedback. Default-on:
    # a reply answers feedback the user initiated, and the opt-out exists so
    # a reply in their inbox isn't a surprise. Legacy rows backfill to
    # contactable via server_default.
    contact_ok: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="1", default=True
    )
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

    triage_prompt: Mapped[str | None] = mapped_column(
        Text().with_variant(MEDIUMTEXT, "mysql"), nullable=True
    )
    triage_raw_response: Mapped[str | None] = mapped_column(
        Text().with_variant(MEDIUMTEXT, "mysql"), nullable=True
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


class FlightBriefingSeenRow(Base):
    """Per-(user, flight) briefing seen/notified state for cross-surface badge sync.

    Mirrors the system-message unseen pattern (``messages_last_seen_id``) but
    per-flight and server-derived, so the app badge stays accurate across web ↔
    app ↔ multiple devices (see ios-app-briefing-notifications.md → Badge).

    A flight counts toward the badge at most once: it is **unseen** iff
    ``last_notified_ts`` (the pack ts of the most recent notify-qualifying
    refresh) is strictly newer than ``last_seen_ts`` (set to the flight's
    current latest pack ts when the pilot opens the briefing, on web or app).
    Opening clears the flight no matter how many packs piled up; an unchanged
    later refresh never re-lights a flight already cleared.
    """

    __tablename__ = "flight_briefing_seen"
    __table_args__ = (
        UniqueConstraint("user_id", "flight_id", name="uq_flight_seen_user_flight"),
        Index("ix_flight_seen_user", "user_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    flight_id: Mapped[str] = mapped_column(
        String(256), ForeignKey("flights.id", ondelete="CASCADE"), nullable=False
    )
    # Pack ts of the most recent notify-qualifying refresh (scope + change-filter
    # + not muted). NULL until the flight first qualifies.
    last_notified_ts: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Flight's latest pack ts at the moment the pilot last opened the briefing.
    # NULL until first opened.
    last_seen_ts: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    user: Mapped[UserRow] = relationship(UserRow)


class SystemMessageRow(Base):
    __tablename__ = "system_messages"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[str] = mapped_column(String(10), index=True)  # YYYY-MM-DD
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(20), default="feature")  # feature, change, fix
    # When true, counts toward the unseen badge / notification dot. When false,
    # the message still appears in the stream but never lights the dot.
    highlight: Mapped[bool] = mapped_column(Boolean, default=False)
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
        # Created by migration 038; load-bearing since #448 — the activity
        # COUNT(DISTINCT) queries FORCE INDEX it by name (hard SQL error on
        # MySQL if dropped), so the model must declare it. See
        # verification_stats._SCORES_SOURCE_TIME_HINT.
        Index("ix_verif_scores_source_time", "source", "observation_time"),
        Index(
            "ix_verif_scores_source_days_time",
            "source", "days_out", "observation_time",
        ),
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
        # TAF pseudo-model stats are aggregated at query time from this table
        # (no rollup — key shape differs); without a (source, observation_time)
        # index every digest/dashboard TAF aggregate was a full scan (#448).
        Index("ix_taf_verif_source_time", "source", "observation_time"),
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
        # Forecast-hour-leading, for the map's "which hours/models exist per
        # day" scan. Without it that DISTINCT has no usable index and reads the
        # whole table on every map page load (#415).
        Index("ix_afs_hour_model", "forecast_hour", "model"),
        # Region-leading variant: once the map serving query filters by region
        # (EU/US seam), this covers "which hours/models exist per day, in this
        # region" as an index-only scan. Added alongside ix_afs_hour_model;
        # the latter is dropped in a follow-up once serving is region-aware.
        Index("ix_afs_region_hour_model", "region", "forecast_hour", "model"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    icao: Mapped[str] = mapped_column(String(4), nullable=False)
    # Region scope: 'eu' (default, all pre-seam rows) or 'us'. Carried by the
    # snapshot artifact so an off-box region cycle ingests without collision,
    # and used to split the forecast-map cache per region.
    region: Mapped[str] = mapped_column(
        String(2), nullable=False, default="eu", server_default="eu"
    )
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
    # Window that precipitation_mm / snowfall_cm accumulate over. Open-Meteo
    # (GFS, ICON) reports hourly, so 1. ECMWF is decoded from accumulated-since-
    # init GRIB fields and thins its step cadence with lead time, so a far-out
    # row covers 3 h or 6 h. NULL on rows written before this was tracked, and
    # on the first delivered step (nothing to diff against).
    precip_period_h: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cape_jkg: Mapped[float | None] = mapped_column(Float, nullable=True)
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


def snapshot_natural_key(
    icao: str, model: str, model_init_time: datetime, forecast_hour: datetime,
) -> tuple:
    """Canonical natural key for `airport_forecast_snapshots` (`uq_afs_key`).

    Strips tzinfo so tz-aware and SQLite's naive datetimes compare equal. Single
    source of truth for dedup, shared by the standalone cycle's inserter and the
    artifact importer so their idempotency logic can't drift apart.
    """
    init_naive = (
        model_init_time.replace(tzinfo=None) if model_init_time.tzinfo else model_init_time
    )
    fhour_naive = (
        forecast_hour.replace(tzinfo=None) if forecast_hour.tzinfo else forecast_hour
    )
    return (icao, model, init_naive, fhour_naive)


def snapshot_insert_ignore(db, rows: list[dict], *, chunk: int = 1000) -> None:
    """Bulk-insert ``airport_forecast_snapshots`` rows, ignoring ``uq_afs_key`` conflicts.

    Dialect-aware INSERT-OR-IGNORE **scoped to the natural-key conflict only** —
    SQLite ``ON CONFLICT (uq_afs_key) DO NOTHING``, MySQL ``ON DUPLICATE KEY
    UPDATE id=id`` (a no-op that fires solely on a duplicate key). A second writer
    on the same natural key (e.g. a droplet fallback cycle racing an artifact
    ingest) becomes a clean no-op instead of an ``IntegrityError``, so concurrent
    writes are safe and the old single-writer assumption is dropped. Shared by the
    standalone cycle's ``_store_snapshots`` and the importer's ``_idempotent_insert``
    so their write paths can't drift.

    Deliberately NOT MySQL bare ``INSERT IGNORE``: that downgrades *every*
    insert-time error (truncation, out-of-range, NOT NULL) to a warning and skips
    the row, which would let this prod-only hot path silently swallow unrelated
    data bugs that SQLite tests can't catch. ``ON DUPLICATE KEY UPDATE`` narrows it
    to duplicate-key conflicts only, matching the SQLite branch's scope so other
    errors still raise loudly.

    Callers still pre-filter existing keys for an accurate inserted-count and to
    cut write load; this is the last-line safety net against the check-then-insert
    race, not a replacement for that pre-filter.
    """
    if not rows:
        return
    table = AirportForecastSnapshotRow.__table__
    dialect = db.get_bind().dialect.name
    if dialect == "sqlite":
        from sqlalchemy.dialects.sqlite import insert as _ins
        stmt = _ins(table).on_conflict_do_nothing(
            index_elements=["icao", "model", "model_init_time", "forecast_hour"],
        )
    elif dialect == "mysql":
        from sqlalchemy.dialects.mysql import insert as _ins
        stmt = _ins(table)
        # No-op self-assign to the EXISTING row's PK (``id = id``): fires ONLY on
        # a duplicate uq_afs_key, so unrelated insert errors still raise (unlike
        # bare INSERT IGNORE). Must reference the table column, not
        # ``stmt.inserted.id`` — ``id`` is auto-increment and absent from the
        # INSERT column list, so ``new.id`` would be an unknown column.
        stmt = stmt.on_duplicate_key_update(id=table.c.id)
    else:  # pragma: no cover - other dialects fall back to a plain insert
        from sqlalchemy import insert as _ins
        stmt = _ins(table)
    for i in range(0, len(rows), chunk):
        db.execute(stmt, rows[i : i + chunk])


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


class VerificationDailyStatsRow(Base):
    """Daily pre-aggregation of NWP verification scores per airport/model/days_out.

    One row per (date, source, model, days_out, icao). Powers the model
    accuracy dashboard and optimistic-bias leaderboard. Rolled up after
    each standalone verification cycle; idempotent (DELETE+INSERT per day).

    SUM columns (not averages) so periods compose by addition. Per-field
    non-NULL counts (``n_ceiling``, ``n_wind``, ``n_temp``, ``n_vis``) are
    stored so MAE can be computed correctly as ``sum_abs / n_<field>`` —
    same semantics as ``func.avg(...)`` over raw scores, which only counts
    rows with a non-NULL delta.

    TAF rollup is out of scope; TAF queries continue to hit
    ``taf_verification_scores`` directly.
    """

    __tablename__ = "verification_daily_stats"
    __table_args__ = (
        UniqueConstraint(
            "date", "source", "model", "days_out", "icao", name="uq_vds_key",
        ),
        Index("ix_vds_date_model", "date", "source", "model", "days_out"),
        Index("ix_vds_icao_model", "icao", "source", "model", "days_out"),
        # The bias-leaderboard query filters (source, model, days_out) as
        # constants with a date *range*. The date-leading index only works for
        # narrow ranges; at 30d/90d MySQL fell back to full scans (~9.5 s per
        # key × 18 keys per rebuild, #448). Equality columns first, range last.
        Index("ix_vds_source_model_days_date", "source", "model", "days_out", "date"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[date_t] = mapped_column(Date, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)
    model: Mapped[str] = mapped_column(String(20), nullable=False)
    days_out: Mapped[int] = mapped_column(Integer, nullable=False)
    icao: Mapped[str] = mapped_column(String(4), nullable=False)

    # Sample size: total scores and per-delta-field non-NULL counts
    n: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_ceiling: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_wind: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_temp: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_vis: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Category direction counts
    n_cat_match: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_cat_opt_1: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_cat_opt_2: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_cat_pess_1: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_cat_pess_2: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Continuous delta sums (signed and absolute). NULL when n_<field>=0.
    sum_abs_ceiling_delta_ft: Mapped[float | None] = mapped_column(Float, nullable=True)
    sum_ceiling_delta_ft: Mapped[float | None] = mapped_column(Float, nullable=True)
    sum_abs_wind_delta_kt: Mapped[float | None] = mapped_column(Float, nullable=True)
    sum_abs_temp_delta_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    sum_abs_vis_delta_m: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Advisory direction counts
    n_advisory_match: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_advisory_opt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_advisory_pess: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Precipitation / convection contingency counts. Both obs and fcst must
    # be non-NULL for a score to count toward any of these.
    n_precip_hit: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_precip_miss: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_precip_false_alarm: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_convection_hit: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_convection_miss: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_convection_false_alarm: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class AirportMonthlySummaryRow(Base):
    """Pre-aggregated monthly observation climatology per airport.

    One row per (month, icao). Powers the public Patterns map and Spotlight
    leaderboards. Rolled up nightly from ``verification_observations`` once
    a calendar month is complete; idempotent (DELETE+INSERT per month).

    Generous column set: per-phenomenon counts, multiple percentiles, and
    hours-below-threshold derivations are stored eagerly so leaderboard
    queries are single-table reads against ~830 rows per month.
    """

    __tablename__ = "airport_monthly_summary"
    __table_args__ = (
        UniqueConstraint("month", "icao", name="uq_ams_key"),
        Index("ix_ams_month", "month"),
        Index("ix_ams_icao", "icao"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    month: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )  # first day of month, UTC
    icao: Mapped[str] = mapped_column(String(4), nullable=False)

    # Sample size
    n_obs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Flight category hour counts (drives IR usefulness, VFR rate)
    n_vfr: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_mvfr: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_ifr: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_lifr: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Phenomena hour counts. Each counts METARs whose ``weather`` list
    # contains the relevant code (e.g. n_ts captures TS, +TSRA, VCTS, ...).
    n_obscuration: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_fg: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_br: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_haze: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_precip: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_ra: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_sn: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_ts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_freezing: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Ceiling percentiles (ft). NULL for airports with no METAR ceiling data.
    ceiling_p10_ft: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ceiling_p25_ft: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ceiling_p50_ft: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ceiling_p75_ft: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ceiling_p90_ft: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Visibility percentiles (m).
    visibility_p10_m: Mapped[int | None] = mapped_column(Integer, nullable=True)
    visibility_p25_m: Mapped[int | None] = mapped_column(Integer, nullable=True)
    visibility_p50_m: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Wind aggregates (kt).
    wind_mean_kt: Mapped[float | None] = mapped_column(Float, nullable=True)
    wind_p50_kt: Mapped[float | None] = mapped_column(Float, nullable=True)
    wind_p95_kt: Mapped[float | None] = mapped_column(Float, nullable=True)
    gust_max_kt: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Hours-below-threshold (handy for "approach minimums" / "low vis"
    # rankings without re-deriving from percentiles).
    n_ceiling_below_500: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_ceiling_below_1500: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_visibility_below_5km: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_wind_over_25kt: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Temperature extremes (deg C).
    temp_min_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    temp_max_c: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Volatility — count of category transitions between consecutive
    # observations within the same UTC day, summed across the month.
    category_changes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Per-hour-of-day breakdown stored as JSON. Keys are zero-padded HH
    # strings (00..23); values are dicts {n, n_ifr, n_ts, n_fog, n_precip,
    # ceiling_p50}. Hours with no observations are omitted.
    diurnal_json: Mapped[str | None] = mapped_column(Text, nullable=True)


class AirportDailySummaryRow(Base):
    """Daily observation climatology per airport.

    One row per (date, icao). Powers Spotlight calendar heatmaps and
    "worst day of the month" leaderboards. Rolled up nightly from
    ``verification_observations`` for completed UTC days; idempotent.

    Cannot be backfilled past raw retention — must accumulate from day 1.
    """

    __tablename__ = "airport_daily_summary"
    __table_args__ = (
        UniqueConstraint("date", "icao", name="uq_ads_key"),
        Index("ix_ads_date", "date"),
        Index("ix_ads_icao", "icao"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    date: Mapped[date_t] = mapped_column(Date, nullable=False)  # UTC date
    icao: Mapped[str] = mapped_column(String(4), nullable=False)

    n_obs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Worst category seen during the day (VFR/MVFR/IFR/LIFR). Drives the
    # calendar heatmap cell color.
    worst_category: Mapped[str | None] = mapped_column(String(4), nullable=True)

    n_vfr: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_mvfr: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_ifr: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_lifr: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    n_obscuration: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_fg: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_br: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_precip: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_ts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    n_freezing: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    ceiling_min_ft: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ceiling_p10_ft: Mapped[int | None] = mapped_column(Integer, nullable=True)
    ceiling_p50_ft: Mapped[int | None] = mapped_column(Integer, nullable=True)
    visibility_min_m: Mapped[int | None] = mapped_column(Integer, nullable=True)
    visibility_p50_m: Mapped[int | None] = mapped_column(Integer, nullable=True)
    wind_max_kt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    gust_max_kt: Mapped[int | None] = mapped_column(Integer, nullable=True)
    temp_min_c: Mapped[float | None] = mapped_column(Float, nullable=True)
    temp_max_c: Mapped[float | None] = mapped_column(Float, nullable=True)

    # Volatility — category transitions between consecutive obs within
    # the UTC day. Summed across a month equals the monthly
    # ``category_changes`` column.
    n_category_changes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


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


class ApiUsageRow(Base):
    """Tracks external API calls across all pipelines.

    One row per pipeline run per service. Used for monthly subscription
    limit monitoring (across all users) and per-user usage breakdown.
    """

    __tablename__ = "api_usage_log"
    __table_args__ = (
        Index("ix_api_usage_timestamp", "timestamp"),
        Index("ix_api_usage_service", "service"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    service: Mapped[str] = mapped_column(String(32), nullable=False)  # open_meteo, ecmwf, ...
    pipeline: Mapped[str] = mapped_column(String(32), nullable=False)  # briefing, verification, frontal, ...
    api_calls: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    user_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    flight_id: Mapped[str | None] = mapped_column(String(256), nullable=True)


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
    # Peak parent uvicorn RSS during the cycle. Nullable: legacy rows
    # predate sampling. See migration 051 / issue #137.
    peak_rss_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Peak total cgroup memory (parent + GRIB decode workers). Same caveat.
    peak_cgroup_mb: Mapped[int | None] = mapped_column(Integer, nullable=True)


class EdrCalibrationAccumulatorRow(Base):
    """Streaming ln-moment accumulator for the Sharman & Pearson (2017) EDR remap.

    One row per ``(model, diagnostic, band)``. Holds running two-pass sums
    (n, Σ ln D, Σ (ln D)²) of ``ln(D)`` for a turbulence diagnostic ``D`` (for
    ``diagnostic=richardson``,
    ``D = 1/max(Ri, RI_FLOOR)``), accumulated over standalone-verification
    soundings. The remap coefficients ``a, b`` for ``EDR = exp(a + b·ln D)`` are
    derived offline from these moments plus the published C1/C2 climatology;
    individual samples are never stored. See
    :mod:`weatherbrief.analysis.sounding.edr`.

    ``sum_ln`` / ``sum_ln2`` are DOUBLE (not single-precision FLOAT): these sums
    accumulate across every cycle indefinitely, so they outgrow FLOAT's ~7
    significant digits on MySQL.
    """

    __tablename__ = "edr_calibration_accumulator"
    __table_args__ = (
        UniqueConstraint("model", "diagnostic", "band", name="uq_edr_calib_key"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model: Mapped[str] = mapped_column(String(20), nullable=False)  # gfs / icon / ecmwf
    diagnostic: Mapped[str] = mapped_column(String(20), nullable=False)  # richardson (room for e_shear)
    band: Mapped[str] = mapped_column(String(16), nullable=False)  # 0_10kft / 10_20kft / 20_45kft / all
    n: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    sum_ln: Mapped[float] = mapped_column(Double, nullable=False, default=0.0)
    sum_ln2: Mapped[float] = mapped_column(Double, nullable=False, default=0.0)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


