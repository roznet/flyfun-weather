"""Airport weather condition models for departure and arrival."""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field

# Flight category severity order and display colors (single source of truth)
_SEVERITY_ORDER = ("VFR", "MVFR", "IFR", "LIFR")
FLIGHT_CATEGORY_COLORS: dict[str, str] = {
    "VFR": "#0f5132",
    "MVFR": "#b45309",
    "IFR": "#dc2626",
    "LIFR": "#7f1d1d",
}


class FlightCategory(str, Enum):
    """Standard aviation flight category."""

    VFR = "VFR"  # ceiling >= 3000ft AND vis >= 5sm
    MVFR = "MVFR"  # ceiling 1000-3000ft OR vis 3-5sm
    IFR = "IFR"  # ceiling 500-1000ft OR vis 1-3sm
    LIFR = "LIFR"  # ceiling < 500ft OR vis < 1sm

    @classmethod
    def worst(cls, categories: list[FlightCategory]) -> FlightCategory:
        """Return the most restrictive category."""
        result = cls.VFR
        for c in categories:
            if _SEVERITY_ORDER.index(c.value) > _SEVERITY_ORDER.index(result.value):
                result = c
        return result

    @property
    def color(self) -> str:
        """Display color for this flight category."""
        return FLIGHT_CATEGORY_COLORS[self.value]


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
    ceiling_ft: int | None = None  # lowest BKN/OVC base
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

    def condition_for_model(self, model: str) -> AirportModelCondition | None:
        """Look up the condition entry for a specific model."""
        return next((c for c in self.conditions if c.model == model), None)


class AirportConditions(BaseModel):
    """Departure and arrival conditions."""

    departure: AirportConditionsSummary
    arrival: AirportConditionsSummary
