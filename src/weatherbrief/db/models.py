"""SQLAlchemy ORM models for all persistent tables."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class UserRow(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), default="local")
    provider_sub: Mapped[str] = mapped_column(String(256), default="")
    email: Mapped[str] = mapped_column(String(256), default="")
    display_name: Mapped[str] = mapped_column(String(256), default="")
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    last_login_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )

    preferences: Mapped[UserPreferencesRow | None] = relationship(
        back_populates="user", uselist=False, cascade="all, delete-orphan"
    )
    profiles: Mapped[list[FlightProfileRow]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    flights: Mapped[list[FlightRow]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    briefing_usage: Mapped[list[BriefingUsageRow]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    api_tokens: Mapped[list[ApiTokenRow]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )
    credit_balance: Mapped[float] = mapped_column(Float, default=500.0, server_default="500.0")
    credit_ledger: Mapped[list[CreditLedgerRow]] = relationship(
        back_populates="user", cascade="all, delete-orphan"
    )


class UserPreferencesRow(Base):
    __tablename__ = "user_preferences"

    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), primary_key=True
    )
    defaults_json: Mapped[str] = mapped_column(Text, default="{}")
    encrypted_autorouter_creds: Mapped[str] = mapped_column(Text, default="")
    digest_config_json: Mapped[str] = mapped_column(Text, default="{}")
    setup_completed: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped[UserRow] = relationship(back_populates="preferences")


class FlightProfileRow(Base):
    __tablename__ = "flight_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    name: Mapped[str] = mapped_column(String(100), default="Default")
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    settings_json: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )

    user: Mapped[UserRow] = relationship(back_populates="profiles")
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
    target_date: Mapped[str] = mapped_column(String(10))
    target_time_utc: Mapped[int] = mapped_column(Integer, default=9)
    cruise_altitude_ft: Mapped[int] = mapped_column(Integer, default=8000)
    flight_ceiling_ft: Mapped[int] = mapped_column(Integer, default=18000)
    flight_duration_hours: Mapped[float] = mapped_column(default=0.0)
    private: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_refresh: Mapped[bool] = mapped_column(Boolean, default=False)
    auto_refresh_hour: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_auto_refresh_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )

    user: Mapped[UserRow] = relationship(back_populates="flights")
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
    fetch_timestamp: Mapped[str] = mapped_column(String(64))
    days_out: Mapped[int] = mapped_column(Integer)
    has_gramet: Mapped[bool] = mapped_column(Boolean, default=False)
    has_skewt: Mapped[bool] = mapped_column(Boolean, default=False)
    has_digest: Mapped[bool] = mapped_column(Boolean, default=False)
    assessment: Mapped[str | None] = mapped_column(String(16), nullable=True)
    assessment_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    artifact_path: Mapped[str] = mapped_column(Text, default="")
    model_init_times_json: Mapped[str] = mapped_column(Text, default="{}")
    grib_init_times_json: Mapped[str] = mapped_column(Text, default="{}")

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

    user: Mapped[UserRow] = relationship(back_populates="briefing_usage")


class ApiTokenRow(Base):
    __tablename__ = "api_tokens"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    name: Mapped[str] = mapped_column(String(256), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    last_used_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, default=None
    )
    revoked: Mapped[bool] = mapped_column(Boolean, default=False)

    user: Mapped[UserRow] = relationship(back_populates="api_tokens")


class FeedbackRow(Base):
    __tablename__ = "feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True
    )
    flight_id: Mapped[str] = mapped_column(String(256), index=True)
    pack_timestamp: Mapped[str] = mapped_column(String(64), default="")
    category: Mapped[str] = mapped_column(String(32))
    comment: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc)
    )
    user: Mapped[UserRow] = relationship()


class CostConfigRow(Base):
    __tablename__ = "cost_config"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    active_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), index=True,
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


class CreditLedgerRow(Base):
    __tablename__ = "credit_ledger"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("users.id", ondelete="CASCADE"), index=True,
    )
    timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(timezone.utc),
    )
    amount: Mapped[float] = mapped_column(Float)
    balance_after: Mapped[float] = mapped_column(Float)
    category: Mapped[str] = mapped_column(String(32))
    description: Mapped[str] = mapped_column(String(256), default="")
    breakdown_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    briefing_usage_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("briefing_usage.id", ondelete="SET NULL"), nullable=True,
    )
    cost_config_id: Mapped[int | None] = mapped_column(
        Integer, ForeignKey("cost_config.id", ondelete="SET NULL"), nullable=True,
    )

    user: Mapped[UserRow] = relationship(back_populates="credit_ledger")
