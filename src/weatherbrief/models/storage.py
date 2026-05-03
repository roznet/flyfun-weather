"""Pydantic v2 models for flights, briefing packs, and flight profiles (API/storage layer)."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator

from weatherbrief.debriefs.taxonomy import (
    OUTCOME_CATEGORIES,
    ConditionTag,
    Decision,
    OutcomeValue,
)
from weatherbrief.models.diagnostic import Diagnostic


class FlightProfile(BaseModel):
    """A named set of flight parameters and advisory settings."""

    id: int
    user_id: str = ""
    name: str = "Default"
    is_default: bool = False
    settings: dict = Field(default_factory=dict)
    system_template_key: str | None = None
    created_at: datetime
    updated_at: datetime


class Flight(BaseModel):
    """A saved briefing target — route + date/time specifics."""

    id: str  # slug: "{route_name}-{YYYY-MM-DD}-{hash}"
    user_id: str = ""  # owner; empty in single-user / dev mode
    profile_id: int | None = None  # associated flight profile
    aircraft_id: int | None = None  # associated user aircraft
    route_name: str  # user-assigned name or derived from waypoints
    waypoints: list[str] = Field(default_factory=list)  # airports, navaids, or fixes
    departure_time: datetime  # aware UTC datetime
    cruise_altitude_ft: int = 8000
    flight_ceiling_ft: int = 18000
    flight_duration_hours: float = 0.0
    private: bool = False
    alt_departure_time: datetime | None = None  # optional same-day alt departure
    auto_refresh: bool = False
    auto_refresh_hour: int | None = None
    last_auto_refresh_at: datetime | None = None
    # Original Field-15 input the pilot typed, when captured. NULL for
    # iOS/MCP-created flights and for flights that pre-date the column —
    # no input string was ever recorded for those, distinct from "input
    # was empty." Stays in sync with ``waypoints``: cleared when waypoints
    # are edited directly, overwritten when a new raw string is parsed.
    raw_route: str | None = None
    # euro_aip version that derived ``waypoints`` from ``raw_route``
    # (e.g. ``"euro_aip/0.9.0"`` — the ``library/version`` shape comes
    # from ``_euro_aip_parser_version`` in the API layer). Lets a future
    # re-derive job spot flights that would benefit from a newer parser.
    # NULL whenever raw_route is NULL.
    parser_version: str | None = None
    # Short share token for /s/{code} redirect. Generated at save_flight
    # time when missing — only ever NULL for direct FlightRow construction
    # in tests.
    share_code: str | None = None
    created_at: datetime

    @computed_field  # type: ignore[prop-decorator]
    @property
    def target_date(self) -> str:
        """YYYY-MM-DD derived from departure_time (backward compat)."""
        return self.departure_time.strftime("%Y-%m-%d")

    @computed_field  # type: ignore[prop-decorator]
    @property
    def target_time_utc(self) -> int:
        """Departure hour derived from departure_time (backward compat)."""
        return self.departure_time.hour


class BriefingPackMeta(BaseModel):
    """Metadata for one fetch — lightweight index for history listing."""

    id: int | None = None  # DB primary key (auto-generated)
    flight_id: str
    fetch_timestamp: datetime  # aware UTC datetime
    days_out: int
    has_gramet: bool = False
    has_skewt: bool = False
    has_digest: bool = False
    assessment: Optional[str] = None  # GREEN/AMBER/RED from digest
    assessment_reason: Optional[str] = None
    artifact_path: str = ""  # path to pack directory
    model_init_times: dict[str, int] = Field(default_factory=dict)
    grib_init_times: dict[str, int] = Field(default_factory=dict)
    # Maps logical model name → freshness source key (e.g. "ecmwf" → "ecmwf:direct"
    # or "ecmwf:openmeteo").  Set at enrichment time so the freshness marker
    # store knows which source to compare each pack's init against.  Empty for
    # legacy packs created before issue #108 — the freshness check infers from
    # ``grib_init_times`` presence in that case.
    model_sources: dict[str, str] = Field(default_factory=dict)
    models_skipped_region: list[str] = Field(default_factory=list)
    diagnostics: list[Diagnostic] = Field(default_factory=list)
    alt_assessment: Optional[str] = None  # GREEN/AMBER/RED for alt departure
    alt_assessment_reason: Optional[str] = None
    has_alt_advisories: bool = False

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_historical(self) -> bool:
        """True when the briefing was generated for a past departure date."""
        return self.days_out < 0


NOTE_MAX_LEN = 300


class FlightDebrief(BaseModel):
    """Pilot post-flight judgement against one flight.

    Outcomes-dict keys are the categories that were *queried* at debrief
    time (those with an advisory raised on the briefing); values default to
    ``CONSISTENT``. Categories absent from the dict were not queried — they
    do not count toward per-category accuracy stats.
    """

    flight_id: str
    decision: Decision
    reasons: list[ConditionTag] = Field(default_factory=list)
    outcomes: dict[ConditionTag, OutcomeValue] = Field(default_factory=dict)
    note: str | None = None
    created_at: datetime
    updated_at: datetime

    @field_validator("reasons", mode="before")
    @classmethod
    def _dedupe_reasons(cls, v):
        if isinstance(v, list):
            seen: set = set()
            out: list = []
            for item in v:
                key = item.value if isinstance(item, ConditionTag) else item
                if key not in seen:
                    seen.add(key)
                    out.append(item)
            return out
        return v

    @field_validator("note")
    @classmethod
    def _note_length(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            return None
        if len(v) > NOTE_MAX_LEN:
            raise ValueError(f"note must be at most {NOTE_MAX_LEN} characters")
        return v

    @model_validator(mode="after")
    def _decision_shape(self) -> FlightDebrief:
        if self.decision is Decision.FLOWN and self.reasons:
            raise ValueError("reasons must be empty when decision is 'flown'")
        if self.decision is Decision.CANCELLED and self.outcomes:
            raise ValueError("outcomes must be empty when decision is 'cancelled'")
        if self.decision is Decision.MONITORING:
            # Monitoring flights aren't real go/no-go decisions, so neither
            # field has meaning. Note is still allowed (pilot may want to
            # remember why they set it up).
            if self.reasons:
                raise ValueError("reasons must be empty when decision is 'monitoring'")
            if self.outcomes:
                raise ValueError("outcomes must be empty when decision is 'monitoring'")
        for tag in self.outcomes:
            if tag not in OUTCOME_CATEGORIES:
                raise ValueError(f"{tag.value} is not a valid outcome category")
        return self
