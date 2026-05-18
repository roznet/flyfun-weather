"""Pydantic models for the METAR/TAF verification system."""

from __future__ import annotations

import json
from datetime import datetime

from pydantic import BaseModel, Field


class VerificationObservation(BaseModel):
    """METAR/TAF ground truth for one airport at one time."""

    icao: str
    observation_time: datetime
    collected_at: datetime

    # METAR fields
    metar_raw: str | None = None
    flight_category: str | None = None  # VFR/MVFR/IFR/LIFR
    ceiling_ft: int | None = None
    visibility_m: int | None = None
    wind_dir: int | None = None
    wind_speed_kt: int | None = None
    wind_gust_kt: int | None = None
    temperature_c: int | None = None
    dewpoint_c: int | None = None
    qnh: float | None = None
    weather: list[str] = Field(default_factory=list)

    # TAF fields (active at observation_time)
    taf_raw: str | None = None
    taf_applicable: str | None = None
    taf_issue_time: datetime | None = None
    taf_flight_category: str | None = None
    taf_ceiling_ft: int | None = None
    taf_visibility_m: int | None = None
    taf_wind_dir: int | None = None
    taf_wind_speed_kt: int | None = None
    taf_wind_gust_kt: int | None = None

    def weather_json(self) -> str:
        """Serialize weather list to JSON for DB storage."""
        return json.dumps(self.weather)

    @staticmethod
    def weather_from_json(raw: str | None) -> list[str]:
        """Deserialize weather JSON from DB."""
        if not raw:
            return []
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return []


class VerificationSummary(BaseModel):
    """Aggregated accuracy stats (for export / display)."""

    total_observations: int = 0
    total_scores: int = 0
    flights_tracked: int = 0
    airports_tracked: int = 0


# ---------------------------------------------------------------------------
# Digest models
# ---------------------------------------------------------------------------


class ActivitySummary(BaseModel):
    """High-level counts for a date range."""

    flights_verified: int = 0
    flights_completed: int = 0
    airports_observed: int = 0
    observations_collected: int = 0
    cycles_run: int = 0
    avg_cycle_duration_ms: float | None = None


class CategoryAccuracyRow(BaseModel):
    """One cell in the model × days-out accuracy table."""

    model: str
    days_out: int
    accuracy_pct: float | None = None
    sample_count: int = 0


class NotableMiss(BaseModel):
    """A significant flight-category bust."""

    icao: str
    observation_time: datetime
    model: str
    days_out: int
    obs_category: str
    model_category: str
    ceiling_delta_ft: int | None = None
    direction: str = ""  # "optimistic" or "pessimistic"
    severity: int = 0    # number of category levels off (1, 2, or 3)


class CategoryBiasStats(BaseModel):
    """Per-model category bias breakdown: optimistic vs pessimistic miss rates.

    ``_2`` buckets are "2 or more levels off", collapsing the rare 3-step
    case (VFR↔LIFR) into the 2-step bucket. This matches the storage shape
    of ``verification_daily_stats`` / ``verification_monthly_stats``.
    """

    model: str
    days_out: int
    total_scores: int = 0
    optimistic_1: int = 0   # predicted better by 1 level
    optimistic_2: int = 0   # predicted better by 2 or more levels
    pessimistic_1: int = 0  # predicted worse by 1 level
    pessimistic_2: int = 0  # predicted worse by 2 or more levels


class OptimisticBiasLeaderboardRow(BaseModel):
    """One airport in the optimistic-bias leaderboard.

    Score formula:
        ``(n_cat_opt_1 + 2 * n_cat_opt_2) / n_with_cat``

    where ``n_with_cat`` is the count of scores with valid category pairs
    (``n_cat_match + n_cat_opt_1 + n_cat_opt_2 + n_cat_pess_1 + n_cat_pess_2``).
    Issue #154 spec said ``/ n``; we tightened the denominator so NULL-category
    rows don't deflate the score. See
    ``verification_stats.get_optimistic_bias_leaderboard`` for the rationale.

    ``n`` (this field) is the total sample count including NULL-category rows
    — useful for the ``n < 10`` noise threshold and the public sample display.
    """

    icao: str
    n: int
    n_cat_opt_1: int
    n_cat_opt_2: int
    score: float


class WindAdvisoryStats(BaseModel):
    """Per-model wind advisory match rate."""

    model: str
    accuracy_pct: float | None = None
    sample_count: int = 0


class MissedWarning(BaseModel):
    """A wind WARNING that a model failed to predict."""

    icao: str
    observation_time: datetime
    model: str
    obs_wind_advisory: str
    model_wind_advisory: str


class VerificationDigestData(BaseModel):
    """Complete digest payload for email and web dashboard."""

    period_label: str = ""
    activity: ActivitySummary = Field(default_factory=ActivitySummary)
    category_accuracy_today: list[CategoryAccuracyRow] = Field(default_factory=list)
    category_accuracy_7d: list[CategoryAccuracyRow] = Field(default_factory=list)
    notable_misses: list[NotableMiss] = Field(default_factory=list)
    category_bias: list[CategoryBiasStats] = Field(default_factory=list)
    wind_advisory: list[WindAdvisoryStats] = Field(default_factory=list)
    missed_warnings: list[MissedWarning] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Admin digest models
# ---------------------------------------------------------------------------


class NewUserInfo(BaseModel):
    """Minimal info about a newly registered user."""

    email: str
    display_name: str = ""


class UsersSectionData(BaseModel):
    """User stats for the admin digest."""

    new_users: list[NewUserInfo] = Field(default_factory=list)
    new_user_count: int = 0
    active_user_count: int = 0
    total_user_count: int = 0


class FlightsBriefingsSectionData(BaseModel):
    """Flight and briefing stats for the admin digest."""

    new_flights: int = 0
    total_briefings: int = 0
    manual_briefings: int = 0
    auto_briefings: int = 0
    flights_single_briefing: int = 0
    flights_refreshed: int = 0
    gramet_count: int = 0
    digest_count: int = 0


class PerformanceSectionData(BaseModel):
    """Cost, tokens, disk, and pipeline performance."""

    total_cost_usd: float = 0.0
    total_llm_input_tokens: int = 0
    total_llm_output_tokens: int = 0
    total_disk_bytes: int = 0
    avg_elapsed_seconds: float | None = None
    avg_queue_wait_seconds: float | None = None
    briefing_count_for_perf: int = 0  # sample size for avg stats


class VerificationSectionData(BaseModel):
    """Condensed verification stats for the admin digest."""

    category_accuracy: list[CategoryAccuracyRow] = Field(default_factory=list)
    notable_miss_count: int = 0
    wind_advisory: list[WindAdvisoryStats] = Field(default_factory=list)
    dashboard_url: str = ""


class AdminDigestData(BaseModel):
    """Complete admin digest payload."""

    period_label: str = ""
    users: UsersSectionData = Field(default_factory=UsersSectionData)
    flights_briefings: FlightsBriefingsSectionData = Field(
        default_factory=FlightsBriefingsSectionData
    )
    performance: PerformanceSectionData = Field(default_factory=PerformanceSectionData)
    verification: VerificationSectionData = Field(
        default_factory=VerificationSectionData
    )
