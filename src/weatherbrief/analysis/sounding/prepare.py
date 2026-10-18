"""Convert forecast data into pint-wrapped arrays for MetPy.

Pint arrays stay within the sounding subpackage — callers pass PressureLevelData
in and get plain-number Pydantic models back.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np
from metpy.calc import dewpoint_from_relative_humidity
from metpy.units import units

if TYPE_CHECKING:
    from pint import Quantity

    from weatherbrief.models import HourlyForecast, PressureLevelData

logger = logging.getLogger(__name__)

MIN_LEVELS = 3

# Below-ground clipping (#807). Same plausibility window as §29's
# ``vertical_motion._SURFACE_PRESSURE_{MIN,MAX}_HPA``: outside it the surface
# pressure is a decode error, not terrain, and the column is left unclipped.
_SURFACE_PRESSURE_MIN_HPA = 300.0
_SURFACE_PRESSURE_MAX_HPA = 1100.0

# A level is kept while it sits no more than this far *below* the model ground
# (p <= surface + tolerance). ~40 m: absorbs integer-hPa level labels, the
# hourly-vs-GRIB surface-pressure mismatch, and a sea-level 1000 hPa level in
# a shallow low (sp 996) — an extrapolation that short is negligible, and
# dropping the level would lose the near-surface parcel for nothing.
_BELOW_GROUND_TOLERANCE_HPA = 5.0

# After a clip, the 2 m surface observation is inserted as the lowest level
# only when the first kept level sits more than this far above the ground
# (~90 m). Closer, that level already *is* the near-surface parcel (as the
# 1000 hPa level is at sea level) and a second level a few tens of metres away
# would only manufacture a skin-layer lapse rate / inversion from the 2 m
# diagnostic.
_SURFACE_LEVEL_MIN_GAP_HPA = 10.0


@dataclass
class PreparedProfile:
    """Pint-wrapped sounding arrays ready for MetPy calculations."""

    pressure: Quantity  # hPa, descending (surface → altitude)
    temperature: Quantity  # degC
    dewpoint: Quantity  # degC
    wind_speed: Quantity | None  # knot
    wind_direction: Quantity | None  # degree
    height: Quantity | None  # meter (geopotential)
    omega: Quantity | None  # Pa/s (NaN for missing levels)
    # Surface observations (from HourlyForecast)
    surface_pressure: Quantity | None  # hPa
    surface_temperature: Quantity | None  # degC
    surface_dewpoint: Quantity | None  # degC
    # Number of delivered levels dropped as below the model ground (#807), and
    # whether the 2 m surface observation was inserted as level 0. Diagnostic
    # only — nothing grades on them.
    clipped_levels: int = 0
    surface_level_inserted: bool = False
    # (metpy_mode, backend) cached by thermodynamics._parcel_backend (#704).
    parcel_backend: tuple | None = field(default=None, repr=False, compare=False)


def _derive_dewpoint(temperature_c: float, relative_humidity_pct: float) -> float:
    """Derive dewpoint (degC) from temperature and RH.

    Delegates to MetPy's ``dewpoint_from_relative_humidity`` (Bolton 1980,
    constants 17.67/243.5) so the derivation matches the rest of the sounding
    pipeline and the documented Magnus variant. Replaces a hand-rolled
    Tetens (17.27/237.7) formula that differed from MetPy by ~0.1 degC.
    """
    dewpoint = dewpoint_from_relative_humidity(
        temperature_c * units.degC,
        relative_humidity_pct * units.percent,
    )
    return float(dewpoint.to("degC").magnitude)


def prepare_profile(
    levels: list[PressureLevelData],
    hourly: HourlyForecast | None = None,
) -> PreparedProfile | None:
    """Convert pressure level data to pint-wrapped arrays for MetPy.

    Filters out levels missing temperature, derives dewpoint from RH where
    needed, sorts by descending pressure. Returns None if fewer than
    MIN_LEVELS valid levels remain.

    **Below-ground clip (#807).** Pressure-level products (Open-Meteo, the
    GFS/ECMWF/HRRR GRIBs) deliver every requested level whatever the terrain;
    under 850 hPa ground the 1000/975/950/925 hPa levels are the model's
    downward extrapolation, often near-saturated. When ``hourly`` carries a
    plausible ``surface_pressure_hpa``, levels more than
    ``_BELOW_GROUND_TOLERANCE_HPA`` below it are dropped, so parcels, DD cloud,
    inversions and icing only see air. Where the clip leaves the first real
    level more than ``_SURFACE_LEVEL_MIN_GAP_HPA`` above the ground, the 2 m
    observation (surface p, T, Td, 10 m wind, model orography height) is
    inserted as level 0 so the surface-based parcel launches from the model's
    ground rather than from an elevated level (with ECMWF's 1000/925/850/700
    set, 840 hPa terrain would otherwise launch from 700 hPa). No surface
    pressure → the pre-#807 unclipped behaviour. A flat column whose ground is
    under its lowest level is untouched. See meteorology-decisions (#807).
    """
    valid = []
    for lv in levels:
        if lv.temperature_c is None:
            logger.debug("Level %s hPa: skipped (no temperature)", lv.pressure_hpa)
            continue
        # Derive dewpoint if not directly available
        dp = lv.dewpoint_c
        if dp is None and lv.relative_humidity_pct is not None:
            dp = _derive_dewpoint(lv.temperature_c, lv.relative_humidity_pct)
        if dp is None:
            logger.debug("Level %s hPa: skipped (no dewpoint, T=%.1f, RH=%s)",
                         lv.pressure_hpa, lv.temperature_c, lv.relative_humidity_pct)
            continue
        valid.append((lv, dp))

    # Sort by descending pressure (surface first)
    valid.sort(key=lambda pair: pair[0].pressure_hpa, reverse=True)

    # Below-ground clip (#807) — before the MIN_LEVELS gate, so a column that
    # is mostly inside the mountain is rejected like any other thin column
    # rather than analysed on its extrapolation.
    sp_hpa = _usable_surface_pressure(hourly)
    clipped = 0
    if sp_hpa is not None:
        kept = [
            pair for pair in valid
            if pair[0].pressure_hpa <= sp_hpa + _BELOW_GROUND_TOLERANCE_HPA
        ]
        clipped = len(valid) - len(kept)
        valid = kept

    surface_level = None
    if clipped and valid and sp_hpa - valid[0][0].pressure_hpa > _SURFACE_LEVEL_MIN_GAP_HPA:
        surface_level = _surface_level(levels, hourly, sp_hpa, valid)

    n_levels = len(valid) + (1 if surface_level is not None else 0)
    if n_levels < MIN_LEVELS:
        logger.warning(
            "Sounding rejected: only %d/%d levels valid out of %d total"
            " (%d below model ground)",
            n_levels, MIN_LEVELS, len(levels), clipped,
        )
        return None

    # Level 0 is the inserted surface observation when there is one; every
    # per-level array below is built from ``sfc + ...`` so they stay aligned.
    sfc = [surface_level] if surface_level is not None else []
    pressure = np.array(
        [s.pressure_hpa for s in sfc] + [lv.pressure_hpa for lv, _ in valid],
        dtype=float,
    ) * units.hPa
    temperature = np.array(
        [s.temperature_c for s in sfc] + [lv.temperature_c for lv, _ in valid],
    ) * units.degC
    dewpoint = np.array(
        [s.dewpoint_c for s in sfc] + [dp for _, dp in valid],
    ) * units.degC

    # Wind — per-level, NaN for missing levels (mirrors the omega branch below,
    # NOT all-or-nothing). A partial GRIB fetch can leave SOME levels without
    # u/v: DWD publishes one file per (variable, level), so a handful of failed
    # downloads — or a failure at the top of the column — yields temperature
    # without wind there. An all-or-nothing gate would then discard wind for the
    # ENTIRE profile, including the flyable levels that did arrive, leaving
    # Richardson/CAT with nothing to grade — so turbulence reads GREEN "smooth"
    # rather than assessing the levels that DO have wind. That is the #391/#393
    # "absence reads as clear" failure, reachable from a single failed download
    # (#478). With NaN fill the flyable column keeps real wind; downstream MetPy
    # calls propagate NaN per element, and the per-level guards (stability
    # indicators, bulk shear, derived-level wind) already skip NaN.
    has_any_wind = any(
        lv.wind_speed_kt is not None and lv.wind_direction_deg is not None
        for lv, _ in valid
    )
    wind_speed = None
    wind_direction = None
    if has_any_wind:
        wind_speed = np.array([s.wind_speed_kt for s in sfc] + [
            lv.wind_speed_kt if lv.wind_speed_kt is not None else np.nan
            for lv, _ in valid
        ]) * units.knot
        wind_direction = np.array([s.wind_direction_deg for s in sfc] + [
            lv.wind_direction_deg if lv.wind_direction_deg is not None else np.nan
            for lv, _ in valid
        ]) * units.degree

    # Height — only if all valid levels have geopotential
    has_height = all(lv.geopotential_height_m is not None for lv, _ in valid)
    height = None
    if has_height:
        # _surface_level only returns a level carrying a height when the column
        # has heights, so the surface entry is never None here.
        height = np.array(
            [s.height_m for s in sfc]
            + [lv.geopotential_height_m for lv, _ in valid],
        ) * units.meter

    # Omega — use NaN for missing levels (same per-level NaN-fill as wind above)
    has_any_omega = any(lv.vertical_velocity_pa_s is not None for lv, _ in valid)
    omega = None
    if has_any_omega:
        omega_vals = [np.nan for _ in sfc] + [
            lv.vertical_velocity_pa_s if lv.vertical_velocity_pa_s is not None else np.nan
            for lv, _ in valid
        ]
        omega = np.array(omega_vals) * units("Pa/s")

    # Surface values from HourlyForecast
    sfc_pressure = None
    sfc_temperature = None
    sfc_dewpoint = None
    if hourly is not None:
        if hourly.surface_pressure_hpa is not None:
            sfc_pressure = hourly.surface_pressure_hpa * units.hPa
        if hourly.temperature_2m_c is not None:
            sfc_temperature = hourly.temperature_2m_c * units.degC
        if hourly.dewpoint_2m_c is not None:
            sfc_dewpoint = hourly.dewpoint_2m_c * units.degC

    return PreparedProfile(
        pressure=pressure,
        temperature=temperature,
        dewpoint=dewpoint,
        wind_speed=wind_speed,
        wind_direction=wind_direction,
        height=height,
        omega=omega,
        surface_pressure=sfc_pressure,
        surface_temperature=sfc_temperature,
        surface_dewpoint=sfc_dewpoint,
        clipped_levels=clipped,
        surface_level_inserted=surface_level is not None,
    )


@dataclass(frozen=True)
class _SurfaceLevel:
    """The 2 m observation, shaped as the profile's lowest level."""

    pressure_hpa: float
    temperature_c: float
    dewpoint_c: float
    wind_speed_kt: float  # NaN when the hour has no 10 m wind
    wind_direction_deg: float
    height_m: float | None


def levels_above_ground(
    levels: list[PressureLevelData], hourly: HourlyForecast | None,
) -> list[PressureLevelData]:
    """``levels`` without those below the model ground, by ``prepare_profile``'s rule.

    For readers of the raw level list (cloud-fraction NWP layers) that would
    otherwise see the extrapolated sub-surface levels the profile drops (#807).
    No usable surface pressure → the list unchanged.
    """
    sp_hpa = _usable_surface_pressure(hourly)
    if sp_hpa is None:
        return levels
    return [
        lv for lv in levels
        if lv.pressure_hpa <= sp_hpa + _BELOW_GROUND_TOLERANCE_HPA
    ]


def _usable_surface_pressure(hourly: HourlyForecast | None) -> float | None:
    """The hour's model surface pressure, or None when absent / implausible."""
    if hourly is None or hourly.surface_pressure_hpa is None:
        return None
    sp = float(hourly.surface_pressure_hpa)
    if not (_SURFACE_PRESSURE_MIN_HPA <= sp <= _SURFACE_PRESSURE_MAX_HPA):
        return None
    return sp


def _surface_level(
    levels: list[PressureLevelData],
    hourly: HourlyForecast,
    sp_hpa: float,
    valid: list[tuple[PressureLevelData, float]],
) -> _SurfaceLevel | None:
    """Build the surface level from the hour's 2 m fields, or None.

    Needs 2 m temperature and a dewpoint (or 2 m RH to derive one). When the
    kept column carries geopotential heights the surface needs one too, in the
    same datum: the model's orography via ``model_surface_height_m`` — the
    function that already normalizes ``deg0l`` / ``blh`` AGL→MSL (§21, §31),
    read against the *unclipped* column so it brackets the ground exactly as
    the model's own extrapolation does (§29 reads the same anchor). If that
    height is unavailable or not below the first kept level, the surface is
    not inserted rather than published with a broken height axis.
    """
    t_c = hourly.temperature_2m_c
    if t_c is None:
        return None
    td_c = hourly.dewpoint_2m_c
    if td_c is None and hourly.relative_humidity_2m_pct is not None:
        td_c = _derive_dewpoint(t_c, hourly.relative_humidity_2m_pct)
    if td_c is None:
        return None
    td_c = min(td_c, t_c)  # 2 m diagnostics can disagree by a few tenths

    height_m = None
    if all(lv.geopotential_height_m is not None for lv, _ in valid):
        from weatherbrief.fetch.grib.decode import model_surface_height_m

        height_m = model_surface_height_m(levels, sp_hpa)
        if height_m is None or height_m >= valid[0][0].geopotential_height_m:
            logger.debug(
                "Surface level not inserted: orography %s m vs first level %s m",
                height_m, valid[0][0].geopotential_height_m,
            )
            return None

    ws = hourly.wind_speed_10m_kt
    wd = hourly.wind_direction_10m_deg
    if ws is None or wd is None:
        ws = wd = np.nan

    return _SurfaceLevel(
        pressure_hpa=sp_hpa,
        temperature_c=float(t_c),
        dewpoint_c=float(td_c),
        wind_speed_kt=float(ws),
        wind_direction_deg=float(wd),
        height_m=height_m,
    )
