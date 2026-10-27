"""Frost-at-departure advisory (issue #949) — informational, never graded.

"Will an aircraft parked outside overnight at the departure airport likely
have frost on it at departure?" A v1 heuristic from fields every model already
carries (2 m temperature and dewpoint, total cloud, 10 m wind) over the night
before departure, read from :attr:`RouteContext.departure_frost`.

Like the sun advisory it is advice only, but stricter: its catalog entry sets
``affects_grade=False``, so an AMBER card never moves the briefing's overall
assessment (sun's AMBER does; see ``registry.grade_neutral_ids``). Never RED.

Per model (meteorology-decisions §69):

* **Out of scope** (departure after sunrise + ``clear_after_sunrise_min`` and
  before sunset): GREEN, no text.
* **UNAVAILABLE**: the model has fewer than 3 real hours in the night window
  or none within an hour of departure (GRIB-primary slots decode only the
  flight window). Never graded off a clamped hour.
* **AMBER "frost likely"**: at least ``min_radiative_hours`` *radiative* hours
  (T2m ≤ ``t_max_c``, cloud < ``cloud_max_pct``, wind < ``wind_max_kt``,
  T−Td ≤ ``spread_max_c``, Td ≤ ``td_max_c``).
* **GREEN "frost possible"**: one radiative hour, or T2m ≤ 0 °C with a moist
  spread (ground frost without the radiative boost).
* **GREEN**: otherwise, with the night minimum and, when it was cold enough,
  the ingredient that was missing.
"""

from __future__ import annotations

from collections import Counter

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories.registry import register
from weatherbrief.analysis.advisories.strings import adv_t
from weatherbrief.analysis.frost import DepartureFrostAnalysis, FrostHour, ModelNight
from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AdvisoryParameterDef,
    AdvisoryStatus,
    ModelAdvisoryResult,
    RouteAdvisoryResult,
)

ADVISORY_ID = "frost"

_DEFAULTS: dict[str, float] = {
    "t_max_c": 3,
    "cloud_max_pct": 30,
    "wind_max_kt": 5,
    "spread_max_c": 5,
    "td_max_c": 1,
    "min_radiative_hours": 2,
    "clear_after_sunrise_min": 90,
}


def _p(params: dict[str, float], key: str) -> float:
    return float(params.get(key, _DEFAULTS[key]))


def _fmt_c(value: float) -> str:
    """Signed whole degrees: +1, 0, -2."""
    v = round(value)
    return "0" if v == 0 else f"{v:+d}"


def _hz(hour: FrostHour) -> str:
    return hour.time.strftime("%H")


def _is_radiative(h: FrostHour, params: dict[str, float]) -> bool:
    spread = h.spread_c
    return (
        h.temperature_c <= _p(params, "t_max_c")
        and h.cloud_cover_pct is not None and h.cloud_cover_pct < _p(params, "cloud_max_pct")
        and h.wind_speed_kt is not None and h.wind_speed_kt < _p(params, "wind_max_kt")
        and spread is not None and spread <= _p(params, "spread_max_c")
        and h.dewpoint_c is not None and h.dewpoint_c <= _p(params, "td_max_c")
    )


def _failed_ingredient(h: FrostHour, params: dict[str, float]) -> str:
    """The first ingredient a cold hour lacks: cloudy, windy, dew or dry."""
    if h.cloud_cover_pct is None or h.cloud_cover_pct >= _p(params, "cloud_max_pct"):
        return "cloudy"
    if h.wind_speed_kt is None or h.wind_speed_kt >= _p(params, "wind_max_kt"):
        return "windy"
    if h.dewpoint_c is not None and h.dewpoint_c > _p(params, "td_max_c"):
        return "dew"
    return "dry"


def _missing_ingredient(
    hours: list[FrostHour], params: dict[str, float], loc: str | None,
) -> str:
    """Why the cold hours were not frost hours, in a few words.

    The ingredient most of the cold hours (T2m ≤ ``t_max_c``) lacked, not just
    the coldest hour's: a night that was clear and calm but dry should not read
    "but cloudy" because one hour clouded over. Ties go to the order cloud,
    wind, dew, dry.
    """
    cold = [h for h in hours if h.temperature_c <= _p(params, "t_max_c")]
    counts = Counter(_failed_ingredient(h, params) for h in cold)
    order = ["cloudy", "windy", "dew", "dry"]
    why = max(order, key=lambda k: (counts.get(k, 0), -order.index(k)))
    return adv_t(f"frost.why_{why}", loc)


def grade_model_night(
    night: ModelNight,
    frost: DepartureFrostAnalysis,
    params: dict[str, float],
    loc: str | None,
) -> tuple[AdvisoryStatus, str]:
    """One model's frost status and detail for an in-scope departure."""
    icao = frost.icao or "?"
    if not night.gradeable(frost.departure_time):
        return AdvisoryStatus.UNAVAILABLE, adv_t("frost.no_night", loc, icao=icao)

    hours = night.hours
    coldest = min(hours, key=lambda h: h.temperature_c)
    radiative = [h for h in hours if _is_radiative(h, params)]

    # The window can start later than the night (Open-Meteo slots begin at 00Z
    # of the departure date): say which part of the night was seen.
    partial = ""
    if hours[0].time.timestamp() - frost.window_start.timestamp() > 3600:
        partial = " " + adv_t("frost.partial", loc, start=_hz(hours[0]))

    if len(radiative) >= _p(params, "min_radiative_hours"):
        coldest_rad = min(radiative, key=lambda h: h.temperature_c)
        return AdvisoryStatus.AMBER, adv_t(
            "frost.likely", loc,
            start=_hz(radiative[0]), end=_hz(radiative[-1]),
            tmin=_fmt_c(coldest_rad.temperature_c),
            td=_fmt_c(coldest_rad.dewpoint_c),  # radiative ⇒ dewpoint known
        ) + partial

    spread_max = _p(params, "spread_max_c")
    ground_frost = any(
        h.temperature_c <= 0 and h.spread_c is not None and h.spread_c <= spread_max
        for h in hours
    )
    if radiative or ground_frost:
        return AdvisoryStatus.GREEN, adv_t(
            "frost.possible", loc, icao=icao, tmin=_fmt_c(coldest.temperature_c),
        ) + partial

    if coldest.temperature_c <= _p(params, "t_max_c"):
        return AdvisoryStatus.GREEN, adv_t(
            "frost.unlikely_cold", loc, icao=icao, tmin=_fmt_c(coldest.temperature_c),
            why=_missing_ingredient(hours, params, loc),
        ) + partial
    return AdvisoryStatus.GREEN, adv_t(
        "frost.none", loc, icao=icao, tmin=_fmt_c(coldest.temperature_c),
    ) + partial


@register
class FrostEvaluator:
    """Frost on a parked aircraft at departure — informational, not in the grade."""

    @staticmethod
    def catalog_entry() -> AdvisoryCatalogEntry:
        return AdvisoryCatalogEntry(
            id=ADVISORY_ID,
            name="Frost at Departure",
            short_description="Frost likely on an aircraft parked outside overnight at the departure airport",
            description=(
                "Informational only: it never changes the briefing's overall grade. "
                "For an early-morning or night departure, looks at the night before at "
                "the departure airport. Frost forms on a clear, calm night when the air "
                "near the ground gets to about +3 °C or colder with moist air (the aircraft "
                "skin radiates and ends up a few degrees colder than the 2 m air). AMBER "
                "when a model has a few clear, calm, cold and moist hours before departure, "
                "meaning: allow time to clear the frost before flight. Departures more than "
                "the clear-off margin after sunrise are not assessed. Models whose forecast "
                "does not cover the night show as unavailable."
            ),
            category="airport",
            timing_class="cheap",
            default_enabled=True,
            altitude_dependent=False,
            affects_grade=False,
            parameters=[
                AdvisoryParameterDef(
                    key="t_max_c",
                    label="Cold enough at",
                    description="2 m temperature at or below this counts toward frost (the skin runs colder than the air)",
                    type="number", unit="°C",
                    default=_DEFAULTS["t_max_c"], min=-5, max=6, step=0.5,
                ),
                AdvisoryParameterDef(
                    key="cloud_max_pct",
                    label="Clear sky below",
                    description="Total cloud cover below this lets the aircraft radiate and cool",
                    type="number", unit="%",
                    default=_DEFAULTS["cloud_max_pct"], min=0, max=100, step=5,
                ),
                AdvisoryParameterDef(
                    key="wind_max_kt",
                    label="Calm below",
                    description="10 m wind below this; stronger wind mixes and keeps the skin near air temperature",
                    type="number", unit="kt",
                    default=_DEFAULTS["wind_max_kt"], min=0, max=20, step=1,
                ),
                AdvisoryParameterDef(
                    key="spread_max_c",
                    label="Moist air spread",
                    description="Temperature minus dewpoint at or below this; drier air deposits little frost",
                    type="number", unit="°C",
                    default=_DEFAULTS["spread_max_c"], min=0, max=15, step=0.5,
                ),
                AdvisoryParameterDef(
                    key="td_max_c",
                    label="Dewpoint at most",
                    description="Dewpoint at or below this; well above 0 °C gives dew rather than frost",
                    type="number", unit="°C",
                    default=_DEFAULTS["td_max_c"], min=-10, max=5, step=0.5,
                ),
                AdvisoryParameterDef(
                    key="min_radiative_hours",
                    label="Frost hours needed",
                    description="Clear, calm, cold, moist hours before departure needed for AMBER",
                    type="number", unit="h",
                    default=_DEFAULTS["min_radiative_hours"], min=1, max=8, step=1,
                ),
                AdvisoryParameterDef(
                    key="clear_after_sunrise_min",
                    label="Clear-off after sunrise",
                    description="Departures later than this after sunrise are not assessed (the sun has cleared the wings)",
                    type="number", unit="min",
                    default=_DEFAULTS["clear_after_sunrise_min"], min=0, max=240, step=15,
                ),
            ],
        )

    @staticmethod
    def evaluate(ctx: RouteContext, params: dict[str, float]) -> RouteAdvisoryResult:
        loc = ctx.locale
        frost = ctx.departure_frost

        def _row(model: str, status: AdvisoryStatus, detail: str) -> ModelAdvisoryResult:
            return ModelAdvisoryResult.build(
                model=model, status=status, detail=detail,
                affected=1 if status == AdvisoryStatus.AMBER else 0,
                total=0 if status == AdvisoryStatus.UNAVAILABLE else 1,
                total_distance_nm=ctx.total_distance_nm,
            )

        if frost is None:
            per_model = [
                _row(m, AdvisoryStatus.UNAVAILABLE, adv_t("frost.no_data", loc))
                for m in ctx.models
            ]
            return RouteAdvisoryResult.from_per_model(ADVISORY_ID, per_model, params)

        if not frost.in_scope(_p(params, "clear_after_sunrise_min")):
            # Daylight departure: nothing to say, for every model alike.
            per_model = [_row(m, AdvisoryStatus.GREEN, "") for m in ctx.models]
            return RouteAdvisoryResult.from_per_model(ADVISORY_ID, per_model, params)

        per_model = []
        for model in ctx.models:
            night = frost.models.get(model) or ModelNight(model=model)
            status, detail = grade_model_night(night, frost, params, loc)
            per_model.append(_row(model, status, detail))
        return RouteAdvisoryResult.from_per_model(ADVISORY_ID, per_model, params)
