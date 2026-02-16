"""Airport weather condition models for departure and arrival."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class FlightCategory(str, Enum):
    """Standard aviation flight category."""

    VFR = "vfr"  # ceiling >= 3000ft AND vis >= 5sm
    MVFR = "mvfr"  # ceiling 1000-3000ft OR vis 3-5sm
    IFR = "ifr"  # ceiling 500-1000ft OR vis 1-3sm
    LIFR = "lifr"  # ceiling < 500ft OR vis < 1sm

    @classmethod
    def worst(cls, categories: list[FlightCategory]) -> FlightCategory:
        """Return the most restrictive category."""
        _ORDER = [cls.VFR, cls.MVFR, cls.IFR, cls.LIFR]
        result = cls.VFR
        for c in categories:
            if _ORDER.index(c) > _ORDER.index(result):
                result = c
        return result


class RunwayEnd(BaseModel):
    """One end of a runway."""

    id: str  # "09L", "27R"
    heading_deg: float  # true heading


class RunwayWind(BaseModel):
    """Wind components relative to a runway."""

    runway_id: str
    heading_deg: float
    crosswind_kt: float  # absolute value
    headwind_kt: float  # positive = headwind


class AirportModelCondition(BaseModel):
    """Conditions at one airport for one model at the expected time."""

    model: str
    flight_category: FlightCategory
    ceiling_ft: float | None = None  # lowest BKN/OVC base
    visibility_sm: float | None = None  # statute miles
    wind_speed_kt: float | None = None
    wind_direction_deg: float | None = None
    wind_gust_kt: float | None = None
    best_runway: RunwayWind | None = None
    all_runways: list[RunwayWind] = Field(default_factory=list)


class AirportConditionsSummary(BaseModel):
    """Conditions at one airport across all models."""

    icao: str
    name: str
    runway_ends: list[RunwayEnd] = Field(default_factory=list)
    conditions: list[AirportModelCondition] = Field(default_factory=list)


class AirportConditions(BaseModel):
    """Departure and arrival conditions."""

    departure: AirportConditionsSummary
    arrival: AirportConditionsSummary
