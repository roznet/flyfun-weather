"""Read a TAF at one instant, the way a pilot does.

One reading shared by the route briefing (``tasks/route_weather`` — the TAF at
each airport's ETA) and the historical map (``tasks/historical_map`` — the TAF
at the selected past time), so the same TAF yields the same category and wind
in both views.

The interpretation itself is euro_aip's ``WeatherAnalyzer.taf_conditions_at``:
prevailing conditions with BECMG/FM applied, TEMPO/PROB/INTER groups laid over
them, significant weather. This module only picks the values the two callers
display from that result:

- the category: the worse of prevailing and the worst temporary group;
- the ceiling/visibility of whichever report sets that category, so the
  numbers never contradict the category;
- the strongest wind at the instant (prevailing on ties) — a TEMPO gust is
  what a runway crosswind needs to see.

The stored ``verification_observations.taf_*`` columns are *not* this reading:
they come from ``find_applicable_taf`` (last matching group wins), which the
euro_aip docstring itself says not to use for "what does this TAF say at T".
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


@dataclass
class TafReading:
    """What a TAF forecasts at ``check_time`` (``None`` fields = not stated)."""

    check_time: datetime
    flight_category: str | None = None
    prevailing_category: str | None = None
    # Set only when a temporary group is strictly worse than prevailing.
    temporary_category: str | None = None
    temporary_type: str | None = None  # e.g. "TEMPO", "PROB30 TEMPO"
    # The group behind the reading: the worse temporary group, else the latest
    # BECMG/FM change, else None (main body unchanged).
    trend_type: str | None = None
    ceiling_ft: int | None = None
    visibility_m: int | None = None
    wind_dir: int | None = None
    wind_speed_kt: int | None = None
    wind_gust_kt: int | None = None
    significant_weather: list[str] = field(default_factory=list)


def read_taf_at(taf: Any, check_time: datetime) -> TafReading | None:
    """Read a parsed euro_aip TAF ``WeatherReport`` at ``check_time``.

    Returns ``None`` when the TAF's validity does not contain ``check_time``
    (an expired or not-yet-valid TAF says nothing about that instant, #610).
    """
    from euro_aip.briefing.weather.analysis import WeatherAnalyzer

    conditions = WeatherAnalyzer.taf_conditions_at(taf, check_time)
    if conditions is None:
        return None

    reading = TafReading(check_time=check_time)
    prevailing = conditions.prevailing
    governing = prevailing
    if prevailing.flight_category is not None:
        reading.prevailing_category = prevailing.flight_category.value
    # worst_temporary skips groups without a category, so temporary_is_worse
    # implies one; checked anyway so a surprise costs this field, not the
    # caller's whole batch.
    worst = conditions.worst_temporary
    if conditions.temporary_is_worse and worst is not None and worst.flight_category is not None:
        reading.temporary_category = worst.flight_category.value
        reading.temporary_type = WeatherAnalyzer.trend_label(worst)
        reading.trend_type = reading.temporary_type
        governing = worst
    elif conditions.prevailing_change is not None:
        reading.trend_type = conditions.prevailing_change.trend_type
    if conditions.flight_category is not None:
        reading.flight_category = conditions.flight_category.value
    reading.ceiling_ft = governing.ceiling_ft
    reading.visibility_m = governing.visibility_meters
    reading.significant_weather = list(conditions.significant_weather)

    # Strongest wind at the instant (prevailing on ties).
    wind = max(
        [prevailing, *conditions.temporary],
        key=lambda r: max(r.wind_gust or 0, r.wind_speed or 0),
    )
    reading.wind_dir = wind.wind_direction
    reading.wind_speed_kt = wind.wind_speed
    reading.wind_gust_kt = wind.wind_gust
    return reading
