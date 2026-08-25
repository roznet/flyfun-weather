"""Pydantic v2 models for weather analysis: routes, forecasts, soundings, advisories."""

from __future__ import annotations

import math
from datetime import datetime, timezone
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

from weatherbrief.models.alternates import RouteAlternates
from weatherbrief.models.observations import RefreshDelta, RouteObservations, RouteSigmets
from weatherbrief.models.observed import ObservedConditions


# euro_aip ``point_type`` values → the abbreviation a pilot reads on a chart.
# Anything unmapped falls through to the raw value rather than being dropped.
_KIND_LABELS: dict[str, str] = {
    "5LNC": "fix",
    "VOR": "VOR",
    "VORDME": "VOR/DME",
    "VORTAC": "VORTAC",
    "NDB": "NDB",
    "NDBDME": "NDB/DME",
    "DME": "DME",
    "TACAN": "TACAN",
}


class Waypoint(BaseModel):
    """An aviation waypoint with coordinates.

    ``name`` is the full airport name for airports (e.g. "Fairoaks Airport")
    and falls back to the code itself for navaids and five-letter fixes —
    euro_aip has no plain-language name for those, the identifier *is* the
    name. Use :attr:`airport_name` to tell the two cases apart rather than
    comparing to ``icao`` at each call site.
    """

    icao: str
    name: str
    lat: float
    lon: float
    # euro_aip ``point_type`` ("VORDME", "5LNC", ...); None for airports and
    # for waypoints resolved before this field existed.
    kind: str | None = None

    @property
    def airport_name(self) -> str | None:
        """The full airport name, or None when we only know the code."""
        if self.name and self.name.upper() != self.icao.upper():
            return self.name
        return None

    @property
    def kind_label(self) -> str | None:
        """Chart abbreviation for a navaid/fix ("VOR/DME", "fix"), else None."""
        if not self.kind:
            return None
        return _KIND_LABELS.get(self.kind, self.kind)

    @property
    def label(self) -> str:
        """Code plus whatever identity we actually know, for section headers.

        "EGTF (Fairoaks Airport)" / "GWC [VOR/DME]" / "ABCDE" — never a name
        invented to fill the gap.
        """
        if self.airport_name:
            return f"{self.icao} ({self.airport_name})"
        if self.kind_label:
            return f"{self.icao} [{self.kind_label}]"
        return self.icao


def bearing_between_coords(lat1_deg: float, lon1_deg: float, lat2_deg: float, lon2_deg: float) -> float:
    """Compute great-circle initial bearing between two lat/lon pairs in degrees [0, 360)."""
    lat1 = math.radians(lat1_deg)
    lat2 = math.radians(lat2_deg)
    dlon = math.radians(lon2_deg - lon1_deg)

    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1) * math.sin(lat2) - math.sin(lat1) * math.cos(lat2) * math.cos(dlon)
    return math.degrees(math.atan2(x, y)) % 360


def bearing_between(wp_a: Waypoint, wp_b: Waypoint) -> float:
    """Compute great-circle initial bearing from wp_a to wp_b in degrees [0, 360)."""
    return bearing_between_coords(wp_a.lat, wp_a.lon, wp_b.lat, wp_b.lon)


def altitude_to_pressure_hpa(altitude_ft: int) -> int:
    """Convert altitude in feet to pressure in hPa using standard atmosphere.

    Uses the barometric formula for the troposphere (valid up to ~36,000 ft).
    """
    altitude_m = altitude_ft * 0.3048
    # Standard atmosphere constants
    P0 = 1013.25  # sea level pressure hPa
    T0 = 288.15  # sea level temperature K
    L = 0.0065  # lapse rate K/m
    g = 9.80665  # gravity m/s^2
    M = 0.0289644  # molar mass of air kg/mol
    R = 8.31447  # gas constant J/(mol·K)

    pressure = P0 * (1 - L * altitude_m / T0) ** (g * M / (R * L))
    return round(pressure)


def pressure_hpa_to_altitude_m(hpa: float) -> float:
    """Convert pressure in hPa to altitude in meters using standard atmosphere.

    Uses the hypsometric formula for the troposphere (valid up to ~36,000 ft).
    Inverse of altitude_to_pressure_hpa.
    """
    P0 = 1013.25  # sea level pressure hPa
    T0 = 288.15  # sea level temperature K
    L = 0.0065  # lapse rate K/m
    g = 9.80665  # gravity m/s^2
    M = 0.0289644  # molar mass of air kg/mol
    R = 8.31447  # gas constant J/(mol·K)

    if hpa <= 0:
        return 0.0
    exp = R * L / (g * M)
    return (T0 / L) * (1 - (hpa / P0) ** exp)


def isa_temperature_c(altitude_ft: float) -> float:
    """ISA standard-atmosphere temperature (°C) at a geopotential altitude (ft).

    Troposphere lapse rate 6.5 K/km (≈1.9812 °C per 1000 ft) up to the
    tropopause at 36,089 ft; isothermal −56.5 °C above. The reference for
    ISA-deviation at cruise (actual − ISA), which pilots use for density
    altitude / true-airspeed / climb performance.

    Note: the live route-graph ISA deviation is computed client-side in
    ``web/ts/visualization/data-extract.ts`` (the temperature is per-model and
    the deviation is a presentation-edge transform). This server-side twin is
    kept for unit-testing the formula and any future server-side deviation;
    keep the two in sync if the standard ever changes.
    """
    if altitude_ft <= 36089.0:
        return 15.0 - 1.9812 * (altitude_ft / 1000.0)
    return -56.5


def temperature_at_pressure(
    hourly: "HourlyForecast", target_pressure_hpa: float
) -> Optional[float]:
    """Interpolate temperature (°C) to *target_pressure_hpa* from a sounding.

    Linear in log-pressure between the two bracketing levels that carry a
    temperature; clamps to the nearest end when the target lies outside the
    reported range. Returns ``None`` when no level has a temperature. Mirrors
    :func:`weatherbrief.analysis.wind.pick_wind_at_pressure` but interpolates
    rather than snapping to the nearest level, since temperature varies
    smoothly and the cruise level rarely lands exactly on a reported level.
    """
    levels = [lvl for lvl in hourly.pressure_levels if lvl.temperature_c is not None]
    if not levels:
        return None
    levels.sort(key=lambda lvl: lvl.pressure_hpa)  # ascending pressure (high→low alt)
    if target_pressure_hpa <= levels[0].pressure_hpa:
        return levels[0].temperature_c
    if target_pressure_hpa >= levels[-1].pressure_hpa:
        return levels[-1].temperature_c
    # Within each adjacent pair `above` has the lower pressure (higher altitude)
    # and `below` the higher pressure (lower altitude); the target sits between.
    for above, below in zip(levels, levels[1:]):
        if above.pressure_hpa <= target_pressure_hpa <= below.pressure_hpa:
            frac = (
                math.log(target_pressure_hpa) - math.log(above.pressure_hpa)
            ) / (math.log(below.pressure_hpa) - math.log(above.pressure_hpa))
            return above.temperature_c + frac * (
                below.temperature_c - above.temperature_c
            )
    return levels[-1].temperature_c  # defensive — unreachable given the clamps above


def pressure_pa_to_altitude_ft(pa: float) -> float:
    """Convert pressure in Pascals to altitude in feet using standard atmosphere."""
    return pressure_hpa_to_altitude_m(pa / 100.0) * 3.28084


class NWPCloudLayerDiag(BaseModel):
    """Cloud diagnostics for a single ICAO layer (low/mid/high)."""

    cover_pct: Optional[float] = None
    base_ft: Optional[float] = None
    top_ft: Optional[float] = None
    top_temp_c: Optional[float] = None


class NWPCloudDiagnostics(BaseModel):
    """GFS cloud layer diagnostics from GRIB2 enrichment.

    All fields are Optional — partial data is normal when some GRIB2
    messages are missing or the model reports no clouds in a layer.
    """

    low: NWPCloudLayerDiag = Field(default_factory=NWPCloudLayerDiag)
    mid: NWPCloudLayerDiag = Field(default_factory=NWPCloudLayerDiag)
    high: NWPCloudLayerDiag = Field(default_factory=NWPCloudLayerDiag)

    convective_cover_pct: Optional[float] = None
    convective_base_ft: Optional[float] = None
    convective_top_ft: Optional[float] = None
    # Native convective-scheme realization + stability (#283 Phase 2). All
    # optional — only some models emit each, and only within their horizon.
    convective_precip_mm_h: Optional[float] = None  # de-accumulated conv precip rate
    k_index: Optional[float] = None                 # model-native K-index (°C)
    total_totals: Optional[float] = None            # model-native Total Totals (°C)
    ml_cape_jkg: Optional[float] = None             # mixed-layer CAPE (J/kg)
    # Mixed-layer CIN (J/kg), NEGATIVE convention: more negative = stronger
    # cap (matches MetPy sounding CIN and the `eff_cin < -200` gate). Provider
    # magnitudes are normalized at decode by `_normalize_model_cin`. (#441)
    ml_cin_jkg: Optional[float] = None
    # ICON-EU parameterized-convection extras (#530). Data availability only —
    # decoded and carried through interpolation, consumed by no grader yet.
    # lpi_con_max is a MAX over the output interval (see
    # NWP_CLOUD_DIAG_RATE_SCALARS); conv_cape is instantaneous.
    lpi_con_max_j_kg: Optional[float] = None        # Lightning Potential Index (J/kg)
    conv_cape_jkg: Optional[float] = None           # convection scheme's own CAPE (J/kg)

    total_cover_pct: Optional[float] = None
    boundary_cover_pct: Optional[float] = None
    ceiling_ft: Optional[float] = None
    freezing_level_ft: Optional[float] = None

    # Capability marker, not a physical scalar (#457, PR #508 review): True
    # when the SOURCE MODEL exposes no convective-realization channel in this
    # diag at all — no convective cover/base/top, no convective precip. HRRR
    # is convection-allowing (no parameterized scheme; realization lives in
    # reflectivity/echo-top products we don't ingest yet), so its generic
    # band covers must not read as "native scheme present but quiet": the
    # NWP convective method routes to the CAPE fallback instead of a false
    # NONE. Unset (None) for GFS/ECMWF/ICON, whose behaviour is unchanged.
    # Listed in NWP_CLOUD_DIAG_META_FIELDS so interpolation carries it
    # through unchanged rather than lerping a boolean.
    convective_scheme_absent: Optional[bool] = None

    # Provenance marker, not a physical scalar (#508 follow-up): names the
    # VERTICAL DEFINITION behind ``low``/``mid``/``high``, so a consumer that
    # has to place a deck in a band knows which rule the amounts were computed
    # under. ``"ncep"`` = LCDC/MCDC/HCDC pressure bands (surface–642 hPa,
    # 642–350 hPa, 350 hPa–top) — GFS and HRRR. Left None for ECMWF (`lcc`/
    # `mcc`/`hcc`) and ICON (`clcl`/`clcm`/`clch`), whose products split much
    # lower (~800/400 hPa) and are NOT interchangeable with the NCEP rule.
    # Consumed by ``build_nwp_cloud_layers_from_condensate``, which may only
    # carve on pressure bands when it knows the amounts are NCEP-defined;
    # anything else falls back to the self-consistent ICAO-carving/bulk-amount
    # pairing. Listed in NWP_CLOUD_DIAG_META_FIELDS so interpolation carries
    # it through unchanged rather than lerping a string.
    band_definition: Optional[str] = None


# --- Interpolation inventory for NWPCloudDiagnostics (issue #485) -----------
#
# These diagnostics are interpolated on two independent axes — spatially
# between route points (analysis/spatial_interpolation.py) and temporally
# across GFS gap hours (fetch/grib/fill.py). The two implementations had
# drifted apart, each silently dropping fields the other carried. Both now
# derive their field list from here, so a field added to the model above
# cannot be handled on only one axis.
#
# The classification below is the one thing the two axes genuinely disagree
# about: on the GFS time axis, values NCEP publishes as a time-AVERAGE over
# the step's window must be aligned on the window midpoint rather than the
# step time, while instantaneous values align on the step time. The spatial
# axis has no such distinction and interpolates every scalar the same way.

NWP_CLOUD_DIAG_LAYER_FIELDS: tuple[str, ...] = ("low", "mid", "high")
"""Sub-model layer fields — interpolated by their own helper, not as scalars.

GFS publishes low/mid/high cover as time-averages, so on the time axis these
are midpoint-aligned along with ``NWP_CLOUD_DIAG_AVERAGED_SCALARS``.
"""

NWP_CLOUD_DIAG_AVERAGED_SCALARS: tuple[str, ...] = ("boundary_cover_pct",)
"""Scalars GFS publishes ONLY as a time-average over the step's window."""

NWP_CLOUD_DIAG_RATE_SCALARS: tuple[str, ...] = (
    "convective_precip_mm_h",
    # ICON `lpi_con_max` is a MAXIMUM over the output interval, not an
    # instantaneous value (#530) — same covering-interval semantics as a
    # de-accumulated rate and as the ECMWF 10fg gust the docstring cites, so
    # it belongs here rather than in the instantaneous default. ICON is
    # fetched hourly for every window hour, so no gap-hour actually exercises
    # this today; classifying it correctly now is what stops a future coarser
    # step from forward-filling a quiet interval over a firing one.
    "lpi_con_max_j_kg",
)
"""Scalars that are a RATE over the window ENDING at their anchor hour.

De-accumulated precipitation rates (ECMWF ``cp``, ICON ``rain_con``) describe
``(N-w, N]``, so a gap hour strictly between anchors N and N+w lies inside the
NEXT anchor's window and must take THAT value — a covering-interval hold, not
persistence and not a lerp. Forward-filling presents the previous window's
rate inside the current one, which can read "dry" through a firing window.

This is the same semantics ``fill.py`` already applies to the ECMWF 10fg gust
(a window maximum); the rate field was simply never classified alongside it.
On the SPATIAL axis the distinction does not exist — neighbouring route points
share a valid time, so rates interpolate like anything else.
"""

NWP_CLOUD_DIAG_META_FIELDS: tuple[str, ...] = (
    "convective_scheme_absent",
    "band_definition",
)
"""Capability/provenance markers — NOT physical scalars.

Constant per (model, point) for a whole enrichment run, so both interpolation
axes carry them through unchanged (persistence) instead of lerping — a
boolean pushed through ``_lerp`` would come back as a float and lose its
None-vs-set distinction.
"""

NWP_CLOUD_DIAG_INSTANT_SCALARS: tuple[str, ...] = tuple(
    name
    for name in NWPCloudDiagnostics.model_fields
    if name not in NWP_CLOUD_DIAG_LAYER_FIELDS
    and name not in NWP_CLOUD_DIAG_AVERAGED_SCALARS
    and name not in NWP_CLOUD_DIAG_RATE_SCALARS
    and name not in NWP_CLOUD_DIAG_META_FIELDS
)
"""Every remaining scalar, treated as instantaneous.

Derived rather than hand-listed so a newly added field is interpolated by
default. If a future field is actually a time-average or a windowed rate, add
it to the corresponding tuple above — the failure mode of forgetting is a
time-alignment error, not a silently dropped field.
"""

NWP_CLOUD_DIAG_SCALARS: tuple[str, ...] = (
    NWP_CLOUD_DIAG_AVERAGED_SCALARS
    + NWP_CLOUD_DIAG_RATE_SCALARS
    + NWP_CLOUD_DIAG_INSTANT_SCALARS
)
"""All non-layer scalars. Both interpolation axes must cover all of these."""


class NWPExplicitConvectiveDiagnostics(BaseModel):
    """Explicit-convection storm diagnostics from a convection-permitting model.

    ICON-D2 runs no deep-convection parameterization (#461), so the
    parameterized diagnostics on :class:`NWPCloudDiagnostics`
    (``convective_base/top_ft``, ``convective_precip_mm_h``) are structurally
    ``None`` on D2-sourced packs. Deep convection in D2 lives in explicit storm
    fields instead — a different KIND of signal, kept in this separate nested
    object so the two tracks can never be conflated (#462).

    Semantics (hard rules from #462):
    - Every value is a **corridor extremum** over a route buffer (~10 NM), not
      a centreline point value — per-point bilinear sampling would let a 2.2 km
      storm cell slip between route points.
    - Interval-max fields describe the hour ENDING at the carrying
      ``HourlyForecast.time`` — the ``(H−1, H]`` window — not the instant.
    - ``echo_top_18dbz_ft`` is the highest altitude with simulated
      reflectivity > 18 dBZ. It sits BELOW the physical storm top (anvil ice
      reflects weakly) and must NEVER be used as a cloud top — in particular
      never for overfly-clearance decisions. Depth/character only.
    - ``None`` ≠ 0 (#421): a ``None`` channel is unknown; the completeness
      flags below say whether ``None`` reflectivity/echo-top means "quiet"
      (channel complete, no echo) or "unavailable" (channel failed).

    Completeness is per-tier and means "valid (unmasked) corridor cells
    decoded", NOT merely "file downloaded": an all-masked corridor is
    UNAVAILABLE; a valid −150 dBZ reflectivity floor is genuinely QUIET.
    """

    source: Literal["icon_d2"]
    reflectivity_hour_max_dbz: Optional[float] = None   # dbz_ctmax corridor max; None = quiet or unavailable (see detection_complete)
    reflectivity_instant_dbz: Optional[float] = None    # dbz_cmax (not fetched in v1)
    echo_top_18dbz_ft: Optional[float] = None           # hourly min pressure over 4 quarter-windows, Pa→ft; NOT a cloud top
    lightning_potential_hour_max_jkg: Optional[float] = None  # lpi_max
    updraft_hour_max_ms: Optional[float] = None         # w_ctmax (0–10 km)
    updraft_helicity_2_8km_hour_max_m2s2: Optional[float] = None  # uh_max, SIGNED (corridor argmax |uh|)

    detection_complete: bool = False   # dbz_ctmax valid over the corridor this hour
    strength_complete: bool = False    # lpi_max + w_ctmax both valid
    echo_top_complete: bool = False    # all 4 quarter windows present and valid


class RouteConfig(BaseModel):
    """A flight route definition loaded from config."""

    name: str
    waypoints: list[Waypoint] = Field(min_length=2)
    cruise_altitude_ft: int = 8000
    flight_ceiling_ft: int = 18000
    flight_duration_hours: float = 0.0

    @model_validator(mode="after")
    def _validate_waypoints(self) -> RouteConfig:
        if len(self.waypoints) < 2:
            raise ValueError("Route must have at least 2 waypoints")
        return self

    @property
    def origin(self) -> Waypoint:
        """First waypoint (departure)."""
        return self.waypoints[0]

    @property
    def destination(self) -> Waypoint:
        """Last waypoint (arrival)."""
        return self.waypoints[-1]

    @property
    def cruise_pressure_hpa(self) -> int:
        """Cruise pressure derived from altitude via standard atmosphere."""
        return altitude_to_pressure_hpa(self.cruise_altitude_ft)

    def leg_bearing(self, leg_index: int) -> float:
        """Bearing for leg N (from waypoint[N] to waypoint[N+1])."""
        return bearing_between(self.waypoints[leg_index], self.waypoints[leg_index + 1])

    def waypoint_track(self, waypoint_icao: str) -> float:
        """Representative track for a waypoint: average of incoming/outgoing leg bearings."""
        idx = next(
            (i for i, wp in enumerate(self.waypoints) if wp.icao == waypoint_icao),
            None,
        )
        if idx is None:
            raise ValueError(f"Waypoint {waypoint_icao} not in route")

        bearings = []
        if idx > 0:
            bearings.append(self.leg_bearing(idx - 1))
        if idx < len(self.waypoints) - 1:
            bearings.append(self.leg_bearing(idx))

        if not bearings:
            return 0.0
        if len(bearings) == 1:
            return bearings[0]

        # Circular mean of two bearings
        rads = [math.radians(b) for b in bearings]
        x = sum(math.cos(r) for r in rads)
        y = sum(math.sin(r) for r in rads)
        return math.degrees(math.atan2(y, x)) % 360


class RoutePoint(BaseModel):
    """A point along a route — either a named waypoint or an interpolated point."""

    lat: float
    lon: float
    distance_from_origin_nm: float
    waypoint_icao: str | None = None  # non-None if this is a named waypoint
    waypoint_name: str | None = None  # full airport name when waypoint_icao is set


class ModelSource(str, Enum):
    """Weather model source identifiers."""

    BEST_MATCH = "best_match"
    GFS = "gfs"
    ECMWF = "ecmwf"
    ICON = "icon"
    UKMO = "ukmo"
    METEOFRANCE = "meteofrance"
    GEM = "gem"


class PressureLevelData(BaseModel):
    """Weather data at a single pressure level for one time step."""

    pressure_hpa: int
    temperature_c: Optional[float] = None
    relative_humidity_pct: Optional[float] = None
    dewpoint_c: Optional[float] = None
    wind_speed_kt: Optional[float] = None
    wind_direction_deg: Optional[float] = None
    geopotential_height_m: Optional[float] = None
    vertical_velocity_pa_s: Optional[float] = None  # omega (Pa/s)
    cloud_liquid_water_kg_kg: Optional[float] = None  # CLWMR from GRIB2
    ice_mixing_ratio_kg_kg: Optional[float] = None  # ICMR from GRIB2
    # PRECIPITATING hydrometeors (#530) — what is falling, as opposed to the
    # suspended cloud species above. ICON-D2 publishes all three (qr/qs/qg);
    # ICON-EU publishes none of them, so an EU-sourced icon slot leaves these
    # None and every consumer falls back exactly as before. Named
    # model-agnostically: AROME's ICE3 scheme carries the same five species
    # (#529) and will fill these same fields.
    rain_water_kg_kg: Optional[float] = None  # qr — liquid precipitation
    snow_water_kg_kg: Optional[float] = None  # qs — snow
    graupel_water_kg_kg: Optional[float] = None  # qg — graupel (frequently 0)
    cloud_area_fraction_pct: Optional[float] = None  # CLC from ICON-EU GRIB2 (0–100%)
    clw_interpolated: bool = False  # True when CLW filled by spatial interpolation


CONDENSATE_LEVEL_FIELDS: tuple[str, ...] = (
    "cloud_liquid_water_kg_kg",
    "ice_mixing_ratio_kg_kg",
    "rain_water_kg_kg",
    "snow_water_kg_kg",
    "graupel_water_kg_kg",
)
"""Per-level hydrometeor mixing ratios, in ANCHOR-FIRST order.

The same single-source-of-truth arrangement as ``NWP_CLOUD_DIAG_SCALARS``
above, for the same reason: these values are carried across two independent
interpolation axes (spatially between route points, temporally between
enriched hours) plus the GRIB merge, and before #530 each site hand-listed
the two cloud species. Adding qr/qs/qg to only some of them would have left a
level with cloud water but no rain water — which the precipitation classifier
reads as "no precipitation species here" and silently demotes to the cloud
proxy.

``cloud_liquid_water_kg_kg`` leads because every carrier keys its "this level
has data" test on it and rides the rest along; the decoders write the whole
set from one fetch, so a level carrying qr without qc does not occur.
"""


class HourlyForecast(BaseModel):
    """Forecast data for one hour at one location."""

    time: datetime

    # Surface variables
    temperature_2m_c: Optional[float] = None
    relative_humidity_2m_pct: Optional[float] = None
    dewpoint_2m_c: Optional[float] = None
    surface_pressure_hpa: Optional[float] = None
    pressure_msl_hpa: Optional[float] = None
    wind_speed_10m_kt: Optional[float] = None
    wind_direction_10m_deg: Optional[float] = None
    wind_gusts_10m_kt: Optional[float] = None
    precipitation_mm: Optional[float] = None
    rain_mm: Optional[float] = None
    showers_mm: Optional[float] = None
    snowfall_cm: Optional[float] = None
    precipitation_probability_pct: Optional[float] = None
    cloud_cover_pct: Optional[float] = None
    cloud_cover_low_pct: Optional[float] = None
    cloud_cover_mid_pct: Optional[float] = None
    cloud_cover_high_pct: Optional[float] = None
    freezing_level_m: Optional[float] = None
    cape_jkg: Optional[float] = None
    convective_inhibition_jkg: Optional[float] = None
    lifted_index_raw: Optional[float] = None
    # Model-native K-index / Total Totals from GRIB enrichment (ECMWF a1
    # kx/totalx — issue #294). Index values (not absolute temperatures), used
    # as-is. None for models without a native field. Copied onto
    # ThermodynamicIndices.nwp_k_index/nwp_total_totals during sounding analysis.
    nwp_k_index: Optional[float] = None
    nwp_total_totals: Optional[float] = None
    visibility_m: Optional[float] = None

    # GFS cloud layer diagnostics from GRIB2 enrichment
    nwp_cloud_diagnostics: Optional[NWPCloudDiagnostics] = None

    # Explicit-convection diagnostics from a convection-permitting model
    # (ICON-D2, #462). Sibling of nwp_cloud_diagnostics, never merged into it:
    # its presence IS the explicit-convection mode signal for the icon slot.
    explicit_convective_diagnostics: Optional[NWPExplicitConvectiveDiagnostics] = None

    # Pressure level data
    pressure_levels: list[PressureLevelData] = Field(default_factory=list)

    def level_at(self, pressure_hpa: int) -> Optional[PressureLevelData]:
        """Get data at a specific pressure level."""
        for lvl in self.pressure_levels:
            if lvl.pressure_hpa == pressure_hpa:
                return lvl
        return None

    def attach_nwp_diagnostics(self, diag: "NWPCloudDiagnostics") -> None:
        """Attach NWP cloud diagnostics and mirror the fields that shadow them.

        ``freezing_level_ft`` on the diagnostics and ``freezing_level_m`` on
        this hour are two views of the same quantity, and sounding analysis
        reads the *hourly* one to populate ``indices.nwp_freezing_level_ft``.
        Setting the diagnostics without mirroring leaves that index blank.

        **Every** write of ``nwp_cloud_diagnostics`` must go through here —
        the GRIB-native application path, the spatial fill, the time-axis
        forward-fill/interp, and the GFS RH/condensate gate. This mirror was
        forgotten on the spatial path (#485 follow-up) and then again on all
        four time-axis/gate sites, each time leaving a freezing level in the
        diagnostics and a stale Open-Meteo value in the field the sounding
        actually reads. ``test_no_direct_diagnostics_assignment`` enforces the
        rule against the source so it stops depending on reviewer memory.

        Open-Meteo's ``cloud_cover_*_pct`` fields are deliberately NOT
        overwritten — they carry hourly-interpolated coverage that is more
        temporally accurate than forward-filled GRIB values on non-native
        hours. The freezing level is different: the model-native value
        (e.g. ECMWF ``deg0l``) is preferred over Open-Meteo's.
        """
        self.nwp_cloud_diagnostics = diag
        if diag.freezing_level_ft is not None:
            self.freezing_level_m = diag.freezing_level_ft / 3.28084

    def nwp_state_snapshot(self) -> tuple:
        """References to every field a staged GRIB commit writes (#508 r5).

        Taken BEFORE a commit loop mutates this hour so a mid-loop failure
        can restore the exact prior state via :meth:`restore_nwp_state` —
        the replacement paths swap whole references, so the snapshot needs
        no copying. Covers ``pressure_levels`` (sounding replacement),
        ``nwp_cloud_diagnostics`` and its ``freezing_level_m`` mirror
        (diagnostics application).
        """
        return (
            self.pressure_levels,
            self.nwp_cloud_diagnostics,
            self.freezing_level_m,
        )

    def restore_nwp_state(self, snap: tuple) -> None:
        """Exact rollback of :meth:`nwp_state_snapshot`.

        Lives on the model (with :meth:`attach_nwp_diagnostics`) because the
        diagnostics/mirror pair must stay consistent and direct assignment of
        ``nwp_cloud_diagnostics`` is banned outside this module — restoring a
        snapshot restores BOTH halves, so the mirror invariant holds.
        """
        self.pressure_levels, self.nwp_cloud_diagnostics, self.freezing_level_m = snap


class WaypointForecast(BaseModel):
    """Complete forecast for one waypoint from one model."""

    waypoint: Waypoint
    model: ModelSource
    fetched_at: datetime
    hourly: list[HourlyForecast] = Field(default_factory=list)

    def at_time(self, target: datetime) -> Optional[HourlyForecast]:
        """Find the forecast hour closest to target time.

        Handles mixed naive/aware datetimes for backward compatibility
        with old pack snapshots that stored naive timestamps.
        """
        if not self.hourly:
            return None
        # Normalize: if one side is naive and the other aware, treat naive as UTC
        t = target if target.tzinfo else target.replace(tzinfo=timezone.utc)
        def _delta(h: HourlyForecast) -> float:
            ht = h.time if h.time.tzinfo else h.time.replace(tzinfo=timezone.utc)
            return abs((ht - t).total_seconds())
        return min(self.hourly, key=_delta)


# --- Analysis result models ---


class WindComponent(BaseModel):
    """Wind broken into headwind/tailwind and crosswind components."""

    wind_speed_kt: float
    wind_direction_deg: float
    track_deg: float
    headwind_kt: float  # positive = headwind, negative = tailwind
    crosswind_kt: float  # positive = from right, negative = from left


class IcingRisk(str, Enum):
    """Icing severity levels."""

    NONE = "none"
    LIGHT = "light"
    MODERATE = "moderate"
    SEVERE = "severe"



class IcingType(str, Enum):
    """Type of icing based on wet-bulb temperature regime."""

    NONE = "none"
    RIME = "rime"
    MIXED = "mixed"
    CLEAR = "clear"


class CloudCoverage(str, Enum):
    """Cloud coverage category derived from dewpoint depression."""

    FEW = "few"
    SCT = "sct"
    BKN = "bkn"
    OVC = "ovc"


class ConvectiveRisk(str, Enum):
    """Convective risk level from thermodynamic indices."""

    NONE = "none"
    MARGINAL = "marginal"
    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    EXTREME = "extreme"


class ConvectiveRegime(str, Enum):
    """Dominant convective regime, used to pick regime-appropriate scoring.

    Discriminates the four physically distinct ways European convection
    initiates, so a single CAPE threshold doesn't misjudge a capped
    "loaded gun" (high CAPE held down by an inversion) or miss thermally
    driven convection that carries little CAPE.
    """

    THERMAL = "thermal"                    # low CAPE, weak cap — surface/orographic
    WEAK_INSTABILITY = "weak_instability"  # moderate CAPE, weak cap
    LOADED_GUN = "loaded_gun"              # high CAPE, strong capping inversion
    ACTIVE = "active"                      # high CAPE, weak/absent cap

    @property
    def label(self) -> str:
        """Human-readable title-case label for digests/UI (e.g. 'Loaded Gun')."""
        return self.value.replace("_", " ").title()


class ConvectiveCharacter(str, Enum):
    """VFR-avoidability character of route convection (per model).

    Orthogonal to :class:`ConvectiveRisk` (severity): severity says how bad an
    individual cell is, character says whether a VFR pilot can operate *around*
    the convection. Drives the digest narrative + a dedicated graded advisory;
    it never changes the severity advisory's colour. See issue #294.
    """

    NONE = "none"              # no convection worth characterising
    ISOLATED = "isolated"      # discrete cells in clear air — circumnavigable VFR
    SCATTERED = "scattered"    # gaps still threadable, but committing
    WIDESPREAD = "widespread"  # high coverage, no reliable gaps to thread
    EMBEDDED = "embedded"      # cells hidden in a stratiform deck — can't see/avoid
    ORGANIZED = "organized"    # frontal / squall-line / forced system
    UNKNOWN = "unknown"        # insufficient data to characterise

    @property
    def label(self) -> str:
        """Human-readable title-case label for digests/UI (e.g. 'Isolated')."""
        return self.value.title()


class VerticalMotionClass(str, Enum):
    """Classification of the vertical motion profile."""

    QUIESCENT = "quiescent"
    SYNOPTIC_ASCENT = "synoptic_ascent"
    SYNOPTIC_SUBSIDENCE = "synoptic_subsidence"
    CONVECTIVE = "convective"
    OSCILLATING = "oscillating"
    UNAVAILABLE = "unavailable"


class CATRiskLevel(str, Enum):
    """Clear-air turbulence risk level from Richardson number."""

    NONE = "none"
    LIGHT = "light"
    MODERATE = "moderate"
    SEVERE = "severe"


class PrecipPhase(str, Enum):
    """Hydrometeor phase at a pressure level."""

    SNOW = "snow"
    MIXED = "mixed"
    RAIN = "rain"
    FREEZING_RAIN = "freezing_rain"
    ICE_PELLETS = "ice_pellets"
    DRY = "dry"


class PrecipIntensity(str, Enum):
    """Precipitation intensity category."""

    NONE = "none"
    LIGHT = "light"       # < 1 mm/h (or < 0.5 cm/h snow)
    MODERATE = "moderate"  # 1-4 mm/h
    HEAVY = "heavy"        # > 4 mm/h


class ParcelPathPoint(BaseModel):
    """A single point on the lifted parcel temperature profile."""

    pressure_hpa: float
    temperature_c: float


class ThermodynamicIndices(BaseModel):
    """Profile-level thermodynamic indices computed via MetPy."""

    lcl_pressure_hpa: Optional[float] = None
    lcl_altitude_ft: Optional[float] = None
    lfc_pressure_hpa: Optional[float] = None
    lfc_altitude_ft: Optional[float] = None
    el_pressure_hpa: Optional[float] = None
    el_altitude_ft: Optional[float] = None
    cape_surface_jkg: Optional[float] = None
    cape_most_unstable_jkg: Optional[float] = None
    cape_mixed_layer_jkg: Optional[float] = None
    cin_surface_jkg: Optional[float] = None
    lifted_index: Optional[float] = None
    showalter_index: Optional[float] = None
    k_index: Optional[float] = None
    total_totals: Optional[float] = None
    precipitable_water_mm: Optional[float] = None
    freezing_level_ft: Optional[float] = None
    minus10c_level_ft: Optional[float] = None
    minus20c_level_ft: Optional[float] = None
    bulk_shear_0_6km_kt: Optional[float] = None
    bulk_shear_0_1km_kt: Optional[float] = None
    sounding_ceiling_ft: Optional[float] = None
    nwp_ceiling_ft: Optional[float] = None
    # Per-source sounding ceilings, so the cloud-source preference (#410) can
    # re-point ``sounding_ceiling_ft`` when ``_resolve_analyses`` swaps the
    # active ``cloud_layers`` slot. ``sounding_ceiling_ft`` always mirrors
    # whichever slot is active; these record the two underlying estimates.
    # Recomputing at resolve time is not an option — ``derived_levels`` is not
    # serialized into the pack, and the LCL floor needs it.
    # ``nwp_sounding_ceiling_ft`` is None both when the model has no native
    # cloud source and when its layers carry no BKN/OVC deck; use
    # ``nwp_cloud_layers is None`` to tell those apart.
    dd_sounding_ceiling_ft: Optional[float] = None
    nwp_sounding_ceiling_ft: Optional[float] = None
    nwp_cape_jkg: Optional[float] = None
    nwp_cape_type: Optional[str] = None  # "sb", "ml", "mu", "unknown"
    nwp_cin_jkg: Optional[float] = None
    nwp_lifted_index: Optional[float] = None
    # Model-native K-index / Total Totals (full model vertical resolution),
    # preferred over the MetPy-derived k_index/total_totals where available
    # (currently ECMWF `kx`/`totalx` via ECPDS GRIB — issue #294). Kept as a
    # separate NWP signal, like nwp_cape_jkg, so the two derivations stay
    # independent (see meteorology-decisions.md §4d).
    nwp_k_index: Optional[float] = None
    nwp_total_totals: Optional[float] = None
    nwp_freezing_level_ft: Optional[float] = None
    cape_raw_vs_calc_divergent: Optional[bool] = None


class DerivedLevel(BaseModel):
    """Per-pressure-level derived values for sounding analysis."""

    pressure_hpa: int
    altitude_ft: Optional[float] = None
    temperature_c: Optional[float] = None
    dewpoint_c: Optional[float] = None
    relative_humidity_pct: Optional[float] = None
    wet_bulb_c: Optional[float] = None
    dewpoint_depression_c: Optional[float] = None
    theta_e_k: Optional[float] = None
    lapse_rate_c_per_km: Optional[float] = None
    icing_index: Optional[float] = None  # Ogimet-DD continuous icing index (0–100)
    icing_index_nwp: Optional[float] = None  # Ogimet-NWP icing index (0–100)
    wind_speed_kt: Optional[float] = None
    wind_direction_deg: Optional[float] = None
    omega_pa_s: Optional[float] = None  # raw model omega (Pa/s)
    w_fpm: Optional[float] = None  # vertical velocity (ft/min)
    richardson_number: Optional[float] = None  # Ri for layer below
    bv_freq_squared_per_s2: Optional[float] = None  # N² for layer below (s⁻²)
    cloud_liquid_water_g_m3: Optional[float] = None  # LWC converted from CLWMR
    cloud_liquid_water_g_kg: Optional[float] = None  # CLW mixing ratio (g/kg) for SFIP
    ice_mixing_ratio_g_kg: Optional[float] = None  # ICE mixing ratio (g/kg) for glaciation factor
    rain_water_g_kg: Optional[float] = None  # qr mixing ratio (g/kg) — PRECIPITATING liquid
    # True when non-trivial rain water coexists with a sub-freezing
    # temperature at this level (#530) — freezing rain / large-droplet
    # regime, physically distinct from the supercooled CLOUD droplets
    # `cloud_liquid_water_g_kg` describes and considerably more hazardous.
    # Only a model publishing qr can set it (ICON-D2 today), so False means
    # "not detected here", never "no rain here" — see meteorology-decisions §24.
    supercooled_rain: bool = False
    sfip_raw: Optional[float] = None  # SFIP index 0.0–1.0
    sfip_100: Optional[float] = None  # SFIP index 0–100
    sfip_severity: Optional[str] = None  # "NONE"/"LIGHT"/"MODERATE"/"SEVERE" (GA mapping)
    sfip_variant: Optional[str] = None  # "full", "proxy", or "interp"
    clw_interpolated: bool = False  # True when CLW came from spatial interpolation
    precip_phase: Optional[str] = None  # PrecipPhase value for this level


class EnhancedCloudLayer(BaseModel):
    """Cloud layer detected from dewpoint depression or NWP diagnostics."""

    base_ft: float
    top_ft: float
    base_pressure_hpa: Optional[int] = None
    top_pressure_hpa: Optional[int] = None
    thickness_ft: Optional[float] = None
    mean_temperature_c: Optional[float] = None
    coverage: CloudCoverage = CloudCoverage.SCT
    mean_dewpoint_depression_c: Optional[float] = None
    mean_cloud_cover_pct: Optional[float] = None  # Mean cloud fraction for nwp_3d, band cover_pct for grib
    theoretical_max_top_ft: Optional[float] = None  # EL (convective) or −20°C (stratiform)
    # How this layer was derived:
    #   "dd"             — dewpoint depression sounding analysis (default)
    #   "grib"           — GRIB2 model diagnostics with explicit base/top (GFS)
    #   "nwp_3d"         — per-level 3D cloud fraction (ECMWF cc / ICON clc)
    #   "nwp_condensate" — per-level CLMR+CIMIXR microphysics (HRRR, #457)
    #   "synthesized"    — Open-Meteo cloud %, narrowed by DD envelope + inversions
    # "grib"/"nwp_3d"/"nwp_condensate" are MODEL-NATIVE — the single source
    # of truth for that classification is NATIVE_NWP_CLOUD_SOURCES below;
    # consumers must use it rather than hand-listing sources (two consumers
    # each missed a source once doing that — PR #508 rounds 3 and 5).
    source: str = "dd"


# EnhancedCloudLayer.source values that are MODEL-NATIVE (independent of the
# DD envelope): GFS band geometry, ECMWF/ICON 3D cloud fraction, HRRR
# condensate microphysics. Consumers that branch on nativeness — the
# cloud-method provenance badge (tasks/advise.py) and the DD-vs-NWP
# agreement advisory (advisories/dd_nwp_agreement.py) — must use this set;
# each previously kept its own hand-written list and each missed a native
# source once (PR #508 rounds 3 and 5).
NATIVE_NWP_CLOUD_SOURCES: frozenset[str] = frozenset(
    {"grib", "nwp_3d", "nwp_condensate"}
)


class InversionLayer(BaseModel):
    """Temperature inversion layer detected from lapse rate analysis."""

    base_ft: float
    top_ft: float
    base_pressure_hpa: Optional[int] = None
    top_pressure_hpa: Optional[int] = None
    strength_c: float  # Total temperature gain through the inversion
    base_temperature_c: Optional[float] = None
    top_temperature_c: Optional[float] = None
    surface_based: bool = False  # True if starts at lowest level


class IcingZone(BaseModel):
    """Grouped icing zone from wet-bulb temperature analysis."""

    base_ft: float
    top_ft: float
    base_pressure_hpa: Optional[int] = None
    top_pressure_hpa: Optional[int] = None
    risk: IcingRisk = IcingRisk.NONE
    icing_type: IcingType = IcingType.NONE
    sld_risk: bool = False
    # True when any level in this zone carries DerivedLevel.supercooled_rain
    # (#530). Reported, NOT graded: it does not enter `risk` and does not make
    # a NONE zone hazardous — see meteorology-decisions §24 for why the grade
    # effect waits on the #411 validation, and `sld_risk` above for the same
    # deliberately-dormant shape.
    supercooled_rain: bool = False
    mean_temperature_c: Optional[float] = None
    mean_wet_bulb_c: Optional[float] = None
    mean_icing_index: Optional[float] = None  # Mean Ogimet icing index for the zone
    mean_rh_pct: Optional[float] = None  # Mean RH of levels in the zone

    @property
    def is_hazardous(self) -> bool:
        """True when this zone represents icing a pilot would actually meet.

        A zone's *existence* is not a hazard predicate. The Ogimet methods emit a
        zone wherever cloud sits in the icing temperature band and stamp it with
        the computed index, so ``risk == NONE`` means "assessed here, index below
        the LIGHT threshold" — the method reporting *no icing*. Consumers that
        tested ``if zone`` / ``if zones`` instead of this graded assessed-but-clean
        points as icing hits.

        ``sld_risk`` is a dormant forward contract, not a live path — nothing
        populates it today. See ``advisories._helpers.hazardous_icing_zones`` for
        why it is kept rather than simplified away.
        """
        return self.risk != IcingRisk.NONE or self.sld_risk


class SfipZone(BaseModel):
    """SFIP icing zone — separate from Ogimet IcingZone for comparison."""

    base_ft: float
    top_ft: float
    base_pressure_hpa: Optional[int] = None
    top_pressure_hpa: Optional[int] = None
    risk: IcingRisk = IcingRisk.NONE
    icing_type: IcingType = IcingType.NONE
    mean_sfip_100: Optional[float] = None
    mean_temperature_c: Optional[float] = None
    mean_rh_pct: Optional[float] = None
    variant: str = "full"  # "full" or "proxy"


class SldZone(BaseModel):
    """Supercooled Large Droplet detection zone.

    Two physical formation mechanisms:
    - ``warm_nose``: Freezing rain — rain melts in a warm layer aloft then
      refreezes in the cold layer below.  The SLD zone is the subfreezing
      layer where large freezing drops fall.
    - ``coalescence``: Collision-coalescence in a deep cloud with a warm top
      (> −12 °C).  Insufficient ice nuclei allow droplets to grow large via
      the warm-rain process within the supercooled portion of the cloud.
    """

    base_ft: float
    top_ft: float
    base_pressure_hpa: Optional[int] = None
    top_pressure_hpa: Optional[int] = None
    risk: IcingRisk = IcingRisk.NONE
    mechanism: str = "unknown"  # "warm_nose" or "coalescence"
    mean_temperature_c: Optional[float] = None


class PrecipitationZone(BaseModel):
    """A vertical zone with uniform precipitation phase."""

    base_ft: float
    top_ft: float
    base_pressure_hpa: Optional[int] = None
    top_pressure_hpa: Optional[int] = None
    phase: PrecipPhase = PrecipPhase.DRY
    mean_wet_bulb_c: Optional[float] = None
    ice_fraction: Optional[float] = None  # ICMR/(ICMR+CLWMR) when available


class PrecipitationAssessment(BaseModel):
    """Precipitation type and intensity assessment for a sounding."""

    surface_phase: PrecipPhase = PrecipPhase.DRY
    surface_intensity: PrecipIntensity = PrecipIntensity.NONE
    precipitation_zones: list[PrecipitationZone] = Field(default_factory=list)
    freezing_rain_risk: bool = False
    warm_nose_base_ft: Optional[float] = None
    warm_nose_top_ft: Optional[float] = None
    rain_mm: Optional[float] = None
    snow_cm: Optional[float] = None
    total_mm: Optional[float] = None


class ConvectiveAssessment(BaseModel):
    """Convective risk assessment from thermodynamic indices."""

    risk_level: ConvectiveRisk = ConvectiveRisk.NONE
    cape_jkg: Optional[float] = None
    cin_jkg: Optional[float] = None
    lcl_altitude_ft: Optional[float] = None
    lfc_altitude_ft: Optional[float] = None
    el_altitude_ft: Optional[float] = None
    bulk_shear_0_6km_kt: Optional[float] = None
    lifted_index: Optional[float] = None
    k_index: Optional[float] = None
    total_totals: Optional[float] = None
    severe_modifiers: list[str] = Field(default_factory=list)
    # Regime discrimination + transparent reasoning (thermo method)
    regime: Optional[ConvectiveRegime] = None  # dominant regime used for scoring
    drivers: list[str] = Field(default_factory=list)      # factors raising risk
    suppressors: list[str] = Field(default_factory=list)  # factors holding risk down
    elevated_convection: bool = False  # MU parcel well above surface (convection aloft)
    # Unified interface fields (populated by both thermo and NWP methods)
    base_ft: Optional[float] = None  # thermo: lfc_altitude_ft (or lcl fallback); NWP: convective_base_ft
    top_ft: Optional[float] = None  # thermo: el_altitude_ft; NWP: convective_top_ft. ALWAYS None for
    # method="nwp_explicit": D2 cells have unresolved vertical geometry for
    # clearance purposes — the 18 dBZ echo top must never flow into the
    # overfly-clearance filter through this slot (#462).
    cover_pct: Optional[float] = None  # NWP only; thermo: None
    convective_precip_mm_h: Optional[float] = None  # NWP native firing signal (#283); thermo: None
    method: str = "thermo"  # "thermo", "nwp", "nwp_hybrid", "nwp_lcl_top", "nwp_precip", "nwp_cape_fallback", "nwp_explicit"
    # Explicit-convection provenance + detail fields (#462, method="nwp_explicit"
    # only). Corridor maxima from the convection-permitting model's storm
    # fields. echo_top_18dbz_ft is a depth/character detail — deliberately a
    # separate field from top_ft so the clearance filter structurally cannot
    # consume it as a cloud top.
    explicit_source: Optional[str] = None            # "icon_d2"
    reflectivity_hour_max_dbz: Optional[float] = None
    echo_top_18dbz_ft: Optional[float] = None


class CATRiskLayer(BaseModel):
    """A layer of clear-air turbulence risk identified by low Richardson number."""

    base_ft: float
    top_ft: float
    base_pressure_hpa: Optional[int] = None
    top_pressure_hpa: Optional[int] = None
    richardson_number: Optional[float] = None  # minimum Ri in layer
    risk: CATRiskLevel = CATRiskLevel.NONE
    # True when the layer lies wholly inside the boundary layer (#533). Such a
    # layer is still graded, but a SEVERE one does not bypass the route-
    # percentage gate the way free-atmosphere severe CAT does: low-level shear
    # is real, yet it is a local, short-lived feature rather than the
    # route-wide hazard "severe CAT anywhere → RED" was written for.
    boundary_layer: bool = False


class VerticalMotionAssessment(BaseModel):
    """Vertical motion and turbulence assessment for a sounding."""

    classification: VerticalMotionClass = VerticalMotionClass.UNAVAILABLE
    max_omega_pa_s: Optional[float] = None
    max_w_fpm: Optional[float] = None
    max_w_level_ft: Optional[float] = None
    cat_risk_layers: list[CATRiskLayer] = Field(default_factory=list)
    e_shear_layers: list[CATRiskLayer] = Field(default_factory=list)
    convective_contamination: bool = False
    # Top of the surface well-mixed layer (#533), when one is detected. CAT
    # layers below it are suppressed — reported so that suppression is
    # inspectable. None when the surface layer is not well mixed.
    mixed_layer_top_ft: Optional[float] = None


class SoundingAnalysis(BaseModel):
    """Complete sounding analysis for one model at one waypoint/time."""

    indices: Optional[ThermodynamicIndices] = None
    parcel_path: list[ParcelPathPoint] = Field(default_factory=list)
    derived_levels: list[DerivedLevel] = Field(default_factory=list)
    cloud_layers: list[EnhancedCloudLayer] = Field(default_factory=list)
    nwp_cloud_layers: list[EnhancedCloudLayer] | None = None
    icing_zones: list[IcingZone] = Field(default_factory=list)
    icing_ogimet_nwp_zones: list[IcingZone] = Field(default_factory=list)
    sfip_zones: list[SfipZone] = Field(default_factory=list)
    ieng_icing_zones: list[IcingZone] = Field(default_factory=list)
    sld_zones: list[SldZone] = Field(default_factory=list)
    inversion_layers: list[InversionLayer] = Field(default_factory=list)
    convective: Optional[ConvectiveAssessment] = None
    convective_thermo: Optional[ConvectiveAssessment] = None
    convective_nwp: Optional[ConvectiveAssessment] = None
    # Explicit-convection unavailability marker (#462). True when this model's
    # hour carried an explicit-convection payload (ICON-D2) whose detection
    # channel was incomplete: the explicit assessment could not run, so
    # ``convective_nwp`` is None — deliberately NOT a quiet NONE assessment
    # (unknown must never read as "scheme quiet") and NOT a CAPE fallback
    # presented as D2's explicit verdict. Grading falls back to the thermo
    # track (badged truthfully by convective_method_effective); this flag keeps
    # "explicit track unavailable" distinguishable from "Open-Meteo-only model"
    # for details/UI. Sibling of ``active_icing_available`` (#391 pattern).
    convective_explicit_unavailable: bool = False
    # NWP-convective fallback marker (#568). True when the model-native NWP
    # convective track was REQUESTED (``convective_method == "nwp"``) but absent
    # for this model at this point, so grading silently fell back to thermo. Set
    # in ``_resolve_analyses``, the only place that knows the *requested* method.
    #
    # Deliberately distinct from ``convective_method_effective == "thermo"``,
    # which is ambiguous by construction: that value also means "the user
    # explicitly asked for thermo". And distinct from ``convective_nwp is None``,
    # which is False under an explicit-thermo request (the NWP assessment is
    # always computed and stored, it is just not swapped in).
    #
    # ``grade_convective_model`` keys the §18 DD-amber cap on this: a model with
    # no native track is graded on MetPy parcel CAPE while its siblings are
    # graded on their own convective schemes, so it must not be red-eligible on
    # that thermodynamic signal alone. Sibling of ``active_icing_available``
    # (#391) and ``convective_explicit_unavailable`` (#462).
    convective_nwp_fallback: bool = False
    precipitation: Optional[PrecipitationAssessment] = None
    vertical_motion: Optional[VerticalMotionAssessment] = None
    # Bulk Open-Meteo 3-level cloud-cover summary. NOT the native NWP cloud
    # envelope (that's `nwp_cloud_layers`): this low/mid/high triple is a coarse
    # per-model summary now populated for ECMWF too, so it must not be surfaced
    # as "NWP cloud" — the low/mid/high framing is the GFS-native paradigm and
    # misrepresents ECMWF's per-level 3-D fraction. See digest/prompt_builder.py.
    cloud_cover_low_pct: Optional[float] = None
    cloud_cover_mid_pct: Optional[float] = None
    cloud_cover_high_pct: Optional[float] = None
    # Surface-level fields used for surface obscuration (fog/LIFR) viz.
    # All optional — older snapshots omit them and the cross-section
    # falls back to "no obscuration band".
    visibility_m: Optional[float] = None
    temperature_2m_c: Optional[float] = None
    dewpoint_2m_c: Optional[float] = None
    # GFS cloud layer diagnostics from GRIB2 enrichment
    nwp_cloud_diagnostics: Optional[NWPCloudDiagnostics] = None

    # Which cloud *source* was actually applied by _resolve_analyses.
    # "dd" (default or fallback), "nwp" (GRIB diagnostics available),
    # "nwp_synthesized" (synthesized from Open-Meteo + DD heuristics). Since the
    # #410 split this is a pure grading source — the render style is a client-only
    # concern (``vizSettings.cloudStyle``) and no longer travels with the grade.
    # The field name is kept stable (the #408 method-badge machinery and the iOS
    # client read it) even though it now carries a source, not a fused method.
    cloud_method_effective: Optional[str] = None

    # Which icing / convective method _resolve_analyses actually graded on — the
    # EFFECTIVE method, which diverges from the requested one exactly where a
    # fallback fired (#408), the sibling of ``cloud_method_effective``. The
    # icing/cloud evidence regions and ``primary_method_id`` source their method
    # badge from these, so a chip can tell a pilot the truth under fallback
    # ("graded on thermo, though you asked for NWP") instead of the requested
    # label that lies at the point it matters.
    #   ``icing_method_effective`` — "ogimet_nwp" / "sfip_nwp" / "ogimet_dd". Left
    #     None ONLY when the method could not run at all (pairs with
    #     ``active_icing_available=False``: an unavailable method has no honest
    #     label to badge). The no-swap DD path stamps "ogimet_dd" rather than
    #     nothing — a grade produced BY a method must say so, else "graded on DD"
    #     reads the same as "this advisory has no method axis".
    #   ``convective_method_effective`` — "nwp" / "thermo"; "thermo" both when the
    #     model-native NWP convective was absent and it silently fell back, and
    #     when thermo was explicitly requested (no swap, still badged).
    icing_method_effective: Optional[str] = None
    convective_method_effective: Optional[str] = None

    # Whether the *active* icing method (the one resolved into ``icing_zones`` by
    # _resolve_analyses) could actually run at this point. Ogimet-NWP requires a
    # model-native cloud envelope; on a model without one it returns [] — which
    # is indistinguishable from "ran, found no icing". This flag carries the
    # distinction to the icing evaluators so absent icing is graded UNAVAILABLE,
    # not clear-by-absence (#391). Default True: the DD method (and old packs)
    # can always run.
    active_icing_available: bool = True

    # Immutable DD source fields — populated at construction, preserved
    # through serialization.  The validator reconstructs them from
    # cloud_layers / icing_zones when loading old JSON that lacks them.
    dd_cloud_layers: list[EnhancedCloudLayer] = Field(default_factory=list)
    icing_ogimet_dd_zones: list[IcingZone] = Field(default_factory=list)

    @model_validator(mode="after")
    def _sync_dd_sources(self) -> "SoundingAnalysis":
        """Backward compat: populate source fields from resolved fields when absent."""
        if not self.dd_cloud_layers and self.cloud_layers:
            self.dd_cloud_layers = list(self.cloud_layers)
        if not self.icing_ogimet_dd_zones and self.icing_zones:
            self.icing_ogimet_dd_zones = list(self.icing_zones)
        if self.convective_thermo is None and self.convective is not None:
            self.convective_thermo = self.convective
        return self


class VerticalRegime(BaseModel):
    """A vertical slice with uniform conditions, derived from weather data."""

    floor_ft: float
    ceiling_ft: float
    in_cloud: bool
    icing_risk: IcingRisk = IcingRisk.NONE
    icing_type: IcingType = IcingType.NONE
    inversion: bool = False  # True if within a temperature inversion
    cloud_cover_pct: Optional[float] = None  # NWP cloud % for this regime's ICAO band
    cat_risk: Optional[str] = None  # CAT turbulence risk level at this regime
    strong_vertical_motion: bool = False  # |w| > 200 fpm
    label: str  # e.g. "Clear", "In cloud 95%", "In cloud, icing MOD (mixed)"
    # Cloud diagnostics (from EnhancedCloudLayer)
    cloud_coverage: Optional[str] = None  # "sct", "bkn", "ovc"
    mean_temperature_c: Optional[float] = None
    mean_dewpoint_depression_c: Optional[float] = None
    # Icing diagnostics (from IcingZone)
    sld_risk: bool = False
    mean_wet_bulb_c: Optional[float] = None
    mean_rh_pct: Optional[float] = None
    mean_icing_index: Optional[float] = None
    # Inversion diagnostics (from InversionLayer)
    inversion_strength_c: Optional[float] = None
    inversion_surface_based: bool = False


class AltitudeAdvisory(BaseModel):
    """An actionable altitude recommendation, aggregated across models."""

    advisory_type: str  # "descend_below_icing", "climb_above_icing", etc.
    altitude_ft: Optional[float] = None  # worst-case across models
    feasible: bool = True  # achievable within constraints
    reason: str = ""  # human-readable explanation
    per_model_ft: dict[str, Optional[float]] = Field(default_factory=dict)


class AltitudeAdvisories(BaseModel):
    """Complete altitude picture for a waypoint."""

    regimes: dict[str, list[VerticalRegime]] = Field(default_factory=dict)
    advisories: list[AltitudeAdvisory] = Field(default_factory=list)
    cruise_in_icing: bool = False
    cruise_icing_risk: IcingRisk = IcingRisk.NONE


class AgreementLevel(str, Enum):
    """How well models agree on a variable."""

    GOOD = "good"
    MODERATE = "moderate"
    POOR = "poor"


class ModelDivergence(BaseModel):
    """Comparison of a single variable across models.

    ``model_values`` may carry ``None`` for a model that lacks the metric at a
    point, and ``mean`` is ``None`` when no model supplied a value. The artifact
    is *written* with these nulls, so the schema must read them back — consumers
    treat ``None`` as "metric absent for that model" (skip, don't average).

    Note: an all-absent metric reads as ``mean=None`` **and**
    ``agreement=GOOD`` — ``GOOD`` here means "nothing to disagree about", not
    "models agreed". ``mean is None`` is the canonical "metric absent" signal:
    a consumer that branches on ``agreement`` alone must also check ``mean`` to
    avoid mistaking an absent metric for unanimous agreement.
    """

    variable: str
    model_values: dict[str, float | None]
    mean: float | None = None
    spread: float
    agreement: AgreementLevel


class WaypointAnalysis(BaseModel):
    """Analysis results for a single waypoint at a target time."""

    waypoint: Waypoint
    target_time: datetime
    wind_components: dict[str, WindComponent] = Field(default_factory=dict)
    sounding: dict[str, SoundingAnalysis] = Field(default_factory=dict)
    altitude_advisories: Optional[AltitudeAdvisories] = None
    model_divergence: list[ModelDivergence] = Field(default_factory=list)
    # model -> temperature (°C) at the elected cruise level (see RoutePointAnalysis).
    cruise_temperature_c: dict[str, float] = Field(default_factory=dict)


class RoutePointAnalysis(BaseModel):
    """Analysis for one route point (waypoint or interpolated)."""

    point_index: int
    lat: float
    lon: float
    distance_from_origin_nm: float
    waypoint_icao: str | None = None
    waypoint_name: str | None = None
    interpolated_time: datetime
    forecast_hour: datetime
    track_deg: float
    wind_components: dict[str, WindComponent] = Field(default_factory=dict)
    sounding: dict[str, SoundingAnalysis] = Field(default_factory=dict)
    altitude_advisories: Optional[AltitudeAdvisories] = None
    model_divergence: list[ModelDivergence] = Field(default_factory=list)
    # model -> temperature (°C) at the elected cruise level, interpolated from
    # that model's sounding. The route graph derives ISA deviation from it.
    cruise_temperature_c: dict[str, float] = Field(default_factory=dict)


class NightInterval(BaseModel):
    """A contiguous twilight or night run along the route, for cross-section shading."""

    start_distance_nm: float
    end_distance_nm: float
    start_time: datetime
    end_time: datetime
    phase: Literal["twilight", "night"]  # civil twilight (0..-6 deg) vs night (< -6 deg)


class SunSideSegment(BaseModel):
    """A stretch of route where the sun sits in one sector relative to the aircraft.

    ``left``/``right`` are the 120-deg flanks; ``ahead`` (flying into the sun) and
    ``behind`` are the +/-30-deg cones off the nose and tail.
    """

    side: Literal["left", "right", "ahead", "behind"]
    start_distance_nm: float
    end_distance_nm: float


class SunSideSummary(BaseModel):
    """Which sector the sun favours over the route (passenger-seating + glare note).

    ``dominant_side`` is the longest-running sector: ``left``/``right`` for seating
    and shade, ``ahead``/``behind`` for into-the-sun / sun-at-your-back.
    """

    dominant_side: Literal["left", "right", "ahead", "behind", "none"]
    dominant_side_pct: float  # the "X%" in the note
    segments: list[SunSideSegment] = Field(default_factory=list)


class GlareAssessment(BaseModel):
    """Sun-vs-runway glare check for one takeoff or landing on the wind-best runway."""

    phase: Literal["takeoff", "landing"]
    airport_icao: str
    runway_ident: str | None = None  # wind-best runway, e.g. "27"
    runway_heading_true: float | None = None
    sun_azimuth_true: float | None = None
    sun_elevation_deg: float | None = None
    relative_bearing_deg: float | None = None  # signed sun-vs-runway, normalized +/-180
    into_sun: bool = False  # glare condition met
    is_dark: bool = False  # sun below horizon at that time


class SunPoint(BaseModel):
    """Per-route-point sun geometry, for the cross-section hover readout (#227)."""

    distance_nm: float
    elevation_deg: float  # above horizon; negative = below
    azimuth_deg: float  # true degrees, clockwise from north
    relative_bearing_deg: float  # signed sun az - track, +/-180; + = right of track


class RouteSunAnalysis(BaseModel):
    """Precomputed solar readouts for the route: night shading, sun side, dep/arr glare."""

    night_intervals: list[NightInterval] = Field(default_factory=list)
    sun_side: SunSideSummary
    points: list[SunPoint] = Field(default_factory=list)
    takeoff: GlareAssessment | None = None
    landing: GlareAssessment | None = None


class RouteAnalysesManifest(BaseModel):
    """Container for all route point analyses, saved as route_analyses.json."""

    route_name: str
    target_date: str
    departure_time: datetime
    flight_duration_hours: float
    total_distance_nm: float
    cruise_altitude_ft: int
    models: list[str]
    analyses: list[RoutePointAnalysis]
    # Optional solar analysis (issue #227) — old packs deserialize fine without it.
    sun: RouteSunAnalysis | None = None


class RoutePointWindOverlay(BaseModel):
    """Per-point recomputed wind components at an override altitude.

    Used to refresh route-graph/route-map headwind without regenerating
    the full analysis manifest.
    """

    point_index: int
    wind_components: dict[str, WindComponent] = Field(default_factory=dict)


class RouteWindOverlay(BaseModel):
    """Container for per-route-point wind overlays at an override altitude."""

    cruise_altitude_ft: int
    points: list[RoutePointWindOverlay] = Field(default_factory=list)


class ElevationPoint(BaseModel):
    """Single elevation sample along the route."""

    distance_nm: float
    elevation_ft: float
    lat: float
    lon: float


class ElevationProfile(BaseModel):
    """High-resolution terrain profile along a route."""

    route_name: str
    points: list[ElevationPoint]
    max_elevation_ft: float
    total_distance_nm: float


class RouteCrossSection(BaseModel):
    """Cross-section forecast data along the full route for one model."""

    model: ModelSource
    route_points: list[RoutePoint]
    fetched_at: datetime
    point_forecasts: list[WaypointForecast]


class ForecastSnapshot(BaseModel):
    """Root object: complete snapshot of one fetch run."""

    route: RouteConfig
    target_date: str  # ISO date string YYYY-MM-DD
    fetch_date: str  # ISO date string
    days_out: int  # D-N
    departure_time: Optional[datetime] = None  # aware UTC; None for old packs
    forecasts: list[WaypointForecast] = Field(default_factory=list)
    analyses: list[WaypointAnalysis] = Field(default_factory=list)
    cross_sections: list[RouteCrossSection] = Field(default_factory=list)
    route_observations: RouteObservations | None = None
    route_sigmets: RouteSigmets | None = None
    # Observed conditions along the corridor (#574): radar, lightning and
    # satellite cloud tops, sampled from locally-collected frames. D-0 only,
    # and only where the observed collector is enabled — None otherwise, so
    # the web section stays hidden rather than rendering an empty panel.
    observed_conditions: ObservedConditions | None = None
    # Weather-based divert candidates (D-2 inward, opt-in via compute_alternates).
    # None outside that window so the web section stays hidden (#210).
    alternates: RouteAlternates | None = None
    # Worsening summary from the last cheap real-time refresh (no digest re-run);
    # None after a full pipeline run (clean slate — the digest covers changes).
    last_refresh_delta: RefreshDelta | None = None
