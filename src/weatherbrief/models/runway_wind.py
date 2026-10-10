"""Runway + wind picture (#758): an airport's runways and the winds on them.

The shared data contract behind the runway + wind widget. The server builds it
(``analysis/runway_wind.py``) so web, iOS and agents draw the same thing and
none of them derives a component or picks a runway itself.

Everything is **true** north: runway headings are euro_aip's ``heading_degT``
and METAR/TAF winds are reported true. Runway *idents* are the painted
(magnetic) numbers. No magnetic variation is applied anywhere, on purpose.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class RunwayEndInfo(BaseModel):
    """One landing direction: the painted ident and the true heading flown."""

    ident: str  # "09", "27L"
    heading_true: float


class RunwayInfo(BaseModel):
    """One physical (open) runway with the ends euro_aip knows a heading for."""

    id: str  # "09/27", "05L/23R"
    length_ft: int | None = None
    surface: str | None = None  # raw euro_aip surface code
    # euro_aip's surface classification — the same one behind
    # Airport.has_hard_runway, which the alternates use. None when the
    # surface is unknown.
    hard: bool | None = None
    ends: list[RunwayEndInfo] = Field(default_factory=list)


class WindSample(BaseModel):
    """One reported wind: a METAR now, a TAF at ETA, or (later) a model hour."""

    source: Literal["metar", "taf", "model"]
    # METAR observation time / the ETA the TAF was read at / model valid time.
    time: datetime | None = None
    # None when VRB. Calm keeps what was reported (000): the table's
    # best-runway pick reads it, so the picture must too. Read ``calm`` first.
    direction_true: int | None = None
    speed_kt: int | None = None
    gust_kt: int | None = None
    variable: bool = False  # VRB: speed but no direction
    variable_from: int | None = None  # dddVddd
    variable_to: int | None = None
    calm: bool = False


class EndComponents(BaseModel):
    """A wind's components on one runway end (euro_aip ``WindComponents``)."""

    ident: str
    headwind_kt: float  # negative = tailwind
    crosswind_kt: float  # signed, positive = from the right
    side: Literal["left", "right", ""] = ""
    gust_headwind_kt: float | None = None
    gust_crosswind_kt: float | None = None
    # Worst case over the variable range and the gust.
    max_crosswind_kt: float


class WindAtAirport(BaseModel):
    """One wind sample laid over the airport's runways."""

    wind: WindSample
    ends: list[EndComponents] = Field(default_factory=list)
    # The wind-best end and its advisory tier ("green"/"amber"/"red"), from
    # the same ``compute_wind_advisory`` that fills the METAR/TAF table's
    # ``metar_best_runway_id`` / ``metar_wind_advisory`` — one picker, so the
    # dial and the table can never disagree. None for VRB (no direction to
    # pick by) or when the airport has no runway data.
    best_end: str | None = None
    advisory: str | None = None


class RunwayWindPicture(BaseModel):
    """An airport's runways and every wind sample known for it."""

    icao: str
    runways: list[RunwayInfo] = Field(default_factory=list)  # closed excluded
    winds: list[WindAtAirport] = Field(default_factory=list)
