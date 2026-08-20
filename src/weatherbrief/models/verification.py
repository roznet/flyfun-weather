"""Pydantic models for the METAR/TAF verification system."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from pydantic import BaseModel, Field, field_validator


class VerificationObservation(BaseModel):
    """METAR/TAF ground truth for one airport at one time.

    Datetimes are normalised to aware UTC **at construction**: the euro_aip
    METAR/TAF parser's awareness is not guaranteed, and the DB columns these
    feed are ``TZDateTime`` (issue #520), which rejects naive writes.

    This covers the constructor only. Pydantic runs field validators on
    attribute *assignment* just when ``validate_assignment=True``, which this
    model deliberately does not set — turning it on would validate every
    post-construction assignment, including the ``int | None`` TAF fields that
    are currently stored as the parser hands them over, converting a latent
    type sloppiness into a live ingestion crash. So a caller assigning a
    datetime after construction must normalise it itself; see
    ``tasks/verification.py`` where ``taf_issue_time`` is set.
    """

    icao: str
    observation_time: datetime
    collected_at: datetime

    @field_validator(
        "observation_time", "collected_at", "taf_issue_time", mode="after",
    )
    @classmethod
    def _aware_utc(cls, v: datetime | None) -> datetime | None:
        if v is None:
            return None
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc)

    # METAR fields
    metar_raw: str | None = None
    # 'METAR' (routine) or 'SPECI' (special, issued off-cycle on significant
    # change). Defaults to routine so callers that predate SPECI handling —
    # and tests constructing observations by hand — keep their old meaning.
    report_type: str = "METAR"
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


class GustAccuracyStats(BaseModel):
    """Per-model gust accuracy, reported under both conditionings (#491).

    The two halves select different, mostly non-overlapping samples and must
    never be blended into one number — see ``tasks/verification_gust`` for the
    definitions.

    *Forecast-flagged* (the "why does the gust layer sit above the TAFs?"
    view): ``n_flagged`` hours where the forecast called a gust,
    ``flagged_over_peak_kt`` = mean(forecast gust − realised peak) on those
    hours, ``over_warn_ratio`` = flagged hours ÷ hours the airport gusted,
    ``n_flag_hit`` = flagged hours that did gust.

    *Obs-flagged* (the extreme-day view): ``n_gust`` hours where the airport
    gusted and the forecast had a gust value, with ``gust_mae_kt`` /
    ``gust_bias_kt`` over those hours.
    """

    model: str
    days_out: int = 0
    n: int = 0
    # Obs-flagged conditioning
    n_gust: int = 0
    gust_mae_kt: float | None = None
    gust_bias_kt: float | None = None
    # Forecast-flagged conditioning
    n_flagged: int = 0
    flagged_over_peak_kt: float | None = None
    # Occurrence
    n_obs_gust: int = 0
    n_flag_hit: int = 0
    over_warn_ratio: float | None = None


class MissedWarning(BaseModel):
    """An observed ``red`` wind advisory that a model called something milder."""

    icao: str
    observation_time: datetime
    model: str
    days_out: int
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
    gust_accuracy: list[GustAccuracyStats] = Field(default_factory=list)
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


class DonationInfo(BaseModel):
    """A single donation made during the digest period."""

    amount_usd: float = 0.0
    amount: float = 0.0  # charged amount in `currency`
    currency: str = "USD"  # ISO 4217 as charged
    recurring: bool = False
    donor: str = ""  # donor email if attributed, else "anonymous"


class DonationsSectionData(BaseModel):
    """Donations received during the admin digest period."""

    count: int = 0
    total_usd: float = 0.0
    donations: list[DonationInfo] = Field(default_factory=list)


class DebriefInfo(BaseModel):
    """A single flight debrief filed during the digest period."""

    route_name: str = ""
    decision: str = ""  # cancelled | flown | monitoring
    summary: str = ""  # reasons (cancelled) or worse-outcome categories (flown)
    note: str = ""


class DebriefsSectionData(BaseModel):
    """Flight debriefs filed during the admin digest period."""

    total_count: int = 0
    flown_count: int = 0
    cancelled_count: int = 0
    monitoring_count: int = 0
    recent: list[DebriefInfo] = Field(default_factory=list)  # last 5, newest first


class AdminDigestData(BaseModel):
    """Complete admin digest payload."""

    period_label: str = ""
    users: UsersSectionData = Field(default_factory=UsersSectionData)
    flights_briefings: FlightsBriefingsSectionData = Field(
        default_factory=FlightsBriefingsSectionData
    )
    performance: PerformanceSectionData = Field(default_factory=PerformanceSectionData)
    donations: DonationsSectionData = Field(default_factory=DonationsSectionData)
    debriefs: DebriefsSectionData = Field(default_factory=DebriefsSectionData)
