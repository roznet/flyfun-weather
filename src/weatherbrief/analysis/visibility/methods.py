"""Visibility and fog methods, run side by side on snapshot inputs (#979 phase 1).

Every method is a pure function of :class:`VisibilityInputs`, which any
snapshot row (or anything with the same attribute names) fills. A method
whose inputs are missing returns None: it is *skipped*, never zero-filled, so
a model that does not publish a field is simply not scored on that method.

Visibility methods (metres, capped at :data:`VIS_CAP_M`):

- ``native``: the model's own visibility (DWD ICON VIS until 2026-11-30, ECMWF
  ``vis``, GFS ``VIS``), passed through so it is scored in the same table.
- ``sw99``: hydrometeor extinction at the lowest model level, vis = 3.912/β.
  Cloud water after Kunkel (1984), ice, rain and snow after Stoelinga and
  Warner (1999), the NCEP/UPP coefficients. Rain and snow from the lowest-level
  mixing ratios where the model has them (ICON-D2), else from the surface
  rates (see :func:`_rain_content`, :func:`_snow_content`).
- ``gultepe06``: fog extinction from cloud water and an assumed droplet number
  (Gultepe et al. 2006), vis = 1.002 / (LWC · Nd)^0.6473 km.
- ``rh_haze``: clear-air visibility falling with 2 m RH, the RUC/RAP humidity
  term, vis = 60 · exp(-2.5 (RH - 15) / 80) km.
- ``rap_blend``: min(sw99, rh_haze), as RAP/HRRR blend the two.
- ``rh_dwd``: DWD's documented RH cubic (5 km at 100 %, 20 km at 90 %,
  40 km at 60 %, flat below). Never below 5 km, so it cannot forecast mist on
  its own; scored to show that.
- ``rh_icon``: the RH curve ICON's own VIS follows on dry air, fitted to it
  (:data:`RH_ICON_CURVE`). ICON goes below 1 km at 2 m RH 98-99 % with no
  cloud water at all, far steeper than either curve above.
- ``icon_blend``: min(sw99, rh_icon), the candidate to replace ICON's VIS
  after 2026-11-30.

Fog rules (True / False) and the three-tier fog risk:

- ``fog_model``: the model makes fog at its lowest level (cover or cloud water).
- ``fog_radiation``: night, T - Td small, light wind, little low cloud.
- ``fog_fsi``: Fog Stability Index below its "high" threshold.
- ``fog_tier``: LIKELY / POSSIBLE / NONE (:func:`fog_tier`).

Every constant is a module-level tunable recorded in meteorology-decisions
§80; :data:`METHODS_VERSION` changes whenever one does, and is stored with
each score so a re-tuned method is never mixed with its old self.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, fields
from datetime import datetime
from typing import Any, Callable

#: Stored with every score. Bump on any change to a formula or constant below.
METHODS_VERSION = "2"

#: Upper bound of every derived visibility (UPP caps at ~24 km; METARs stop
#: at 9999 m, so anything above 10 km scores the same).
VIS_CAP_M = 20000.0

_RD = 287.05            # J kg-1 K-1
_KOSCHMIEDER = 3.912    # -ln(0.02): 2 % contrast threshold

# sw99 extinction coefficients, β [km-1] = a · C^b with C in g m-3.
SW99_CLOUD = (144.7, 0.88)    # Kunkel 1984, cloud water
SW99_ICE = (163.9, 1.00)      # cloud ice
SW99_RAIN = (2.24, 0.75)
SW99_SNOW = (10.36, 0.7778)

# Rain / snow content from surface rates where the model has no mixing ratio
# (ICON-EU, GFS, ECMWF): W = a · R^b, W in g m-3, R in mm h-1 of water.
# Rain: Marshall-Palmer drop-size integral. Snow: a first guess to be fitted
# on the verification data (meteorology-decisions §80).
RAIN_CONTENT_FROM_RATE = (0.089, 0.84)
SNOW_CONTENT_FROM_RATE = (0.25, 0.86)

# Gultepe 2006: assumed fog droplet number concentration (cm-3), continental.
GULTEPE_ND_CM3 = 100.0
# Below this LWC (g m-3) there is no fog to speak of: vis = cap.
GULTEPE_MIN_LWC = 0.001

# Fog rules.
FOG_MODEL_CLC_PCT = 90.0          # lowest-level cover at or above this
FOG_MODEL_LWC = 0.02              # or lowest-level cloud water (g m-3)
FOG_RAD_SPREAD_C = 1.0            # T2m - Td2m at or below
FOG_RAD_WIND_KT = 6.0             # 10 m wind below (~3 m/s)
FOG_RAD_LOW_CLOUD_PCT = 30.0      # low cloud cover at or below
FOG_FSI_HIGH = 31.0               # FSI below: high fog likelihood
FOG_LIKELY_VIS_M = 1000.0         # a derived vis below this is model fog
FOG_POSSIBLE_VIS_M = 5000.0       # 1-5 km with RH >= FOG_POSSIBLE_RH
FOG_POSSIBLE_RH = 95.0

# rh_dwd: v(x) = 5 + a·x + b·x² + c·x³ km with x = 100 - RH (%), through
# v(0) = 5, v(10) = 20, v(40) = 40 and flat at x = 40 (RH 60 %).
RH_DWD_COEFFS = (61 / 36, -11 / 576, -1 / 28800)
RH_DWD_AT_100_KM = 5.0
RH_DWD_FLAT_BELOW_RH = 60.0

#: rh_icon: (2 m RH %, visibility m), interpolated linearly in log10(vis).
#: The 35th percentile of ICON's own VIS per RH bin, on ICON D0-D2 rows with
#: zero lowest-level cloud water and no precipitation, 2026-11-09..11
#: (~4,600 rows; meteorology-decisions §80). The 35th percentile, not the
#: median, keeps the frequency bias near ICON's: the curve is deterministic
#: where ICON's own VIS spreads within a bin. Thin above 98.5 % RH: floored
#: at 400 m. Below the first point: the cap.
RH_ICON_CURVE = (
    (90.0, 20000.0),
    (91.0, 17600.0),
    (92.5, 14800.0),
    (93.5, 11800.0),
    (94.5, 8000.0),
    (95.5, 4800.0),
    (96.5, 2850.0),
    (97.25, 1550.0),
    (97.75, 1200.0),
    (98.25, 950.0),
    (98.75, 450.0),
    (100.0, 400.0),
)

#: Visibility methods in report order.
VISIBILITY_METHODS = ("native", "sw99", "gultepe06", "rh_haze", "rap_blend",
                      "rh_dwd", "rh_icon", "icon_blend")
#: Fog outputs in report order.
FOG_RULES = ("fog_model", "fog_radiation", "fog_fsi")
FOG_TIERS = ("LIKELY", "POSSIBLE", "NONE")


@dataclass
class VisibilityInputs:
    """The model-agnostic inputs; every field optional (None = unknown)."""

    visibility_m: float | None = None              # model-native visibility
    temperature_2m_c: float | None = None
    dewpoint_2m_c: float | None = None
    wind_speed_10m_kt: float | None = None
    precipitation_mm: float | None = None          # total over precip_period_h
    snowfall_cm: float | None = None               # over precip_period_h
    precip_period_h: float | None = None
    cloud_cover_low_pct: float | None = None
    surface_pressure_hpa: float | None = None
    lowest_level_cloud_water_kg_kg: float | None = None
    lowest_level_cloud_ice_kg_kg: float | None = None
    lowest_level_rain_kg_kg: float | None = None
    lowest_level_snow_kg_kg: float | None = None
    lowest_level_cloud_cover_pct: float | None = None
    t850_c: float | None = None
    td850_c: float | None = None
    wind_speed_850_kt: float | None = None
    is_night: bool | None = None

    @classmethod
    def from_snapshot(cls, snap: Any, is_night: bool | None = None) -> "VisibilityInputs":
        """From an ``AirportForecastSnapshotRow`` (or a dict / object with the
        same names). Missing attributes read None."""
        get = snap.get if isinstance(snap, dict) else (lambda k: getattr(snap, k, None))
        values = {f.name: get(f.name) for f in fields(cls) if f.name != "is_night"}
        return cls(**values, is_night=is_night)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def is_night(lat: float, lon: float, when: datetime) -> bool:
    """Sun below the horizon at the airport (radiation-fog rule)."""
    from euro_aip.utils.solar import solar_elevation

    return solar_elevation(lat, lon, when) < 0.0


def relative_humidity_pct(t_c: float | None, td_c: float | None) -> float | None:
    """RH over water from T and Td (Magnus), clamped to 0-100 %."""
    if t_c is None or td_c is None:
        return None
    a, b = 17.625, 243.04
    rh = 100.0 * math.exp(a * td_c / (b + td_c) - a * t_c / (b + t_c))
    return min(100.0, max(0.0, rh))


def air_density(inp: VisibilityInputs) -> float | None:
    """kg m-3 from surface pressure (1013.25 hPa if unknown) and T2m."""
    if inp.temperature_2m_c is None:
        return None
    p_pa = 100.0 * (inp.surface_pressure_hpa if inp.surface_pressure_hpa is not None else 1013.25)
    return p_pa / (_RD * (inp.temperature_2m_c + 273.15))


def _content(q_kg_kg: float | None, rho: float) -> float | None:
    """Mixing ratio kg/kg → mass content g m-3."""
    if q_kg_kg is None:
        return None
    return max(0.0, q_kg_kg) * rho * 1000.0


def _hourly(amount: float | None, period_h: float | None) -> float | None:
    if amount is None:
        return None
    return max(0.0, amount) / (period_h if period_h and period_h > 0 else 1.0)


def _snow_water_rate(inp: VisibilityInputs) -> float | None:
    """Snow as mm h-1 of water, from snowfall in cm."""
    from weatherbrief.units import SNOW_CM_PER_MM_WATER

    rate_cm = _hourly(inp.snowfall_cm, inp.precip_period_h)
    return None if rate_cm is None else rate_cm / SNOW_CM_PER_MM_WATER


def _rain_content(inp: VisibilityInputs, rho: float) -> float | None:
    if inp.lowest_level_rain_kg_kg is not None:
        return _content(inp.lowest_level_rain_kg_kg, rho)
    total = _hourly(inp.precipitation_mm, inp.precip_period_h)
    if total is None:
        return None
    rain = max(0.0, total - (_snow_water_rate(inp) or 0.0))
    a, b = RAIN_CONTENT_FROM_RATE
    return a * rain ** b if rain > 0 else 0.0


def _snow_content(inp: VisibilityInputs, rho: float) -> float | None:
    if inp.lowest_level_snow_kg_kg is not None:
        return _content(inp.lowest_level_snow_kg_kg, rho)
    rate = _snow_water_rate(inp)
    if rate is None:
        return None
    a, b = SNOW_CONTENT_FROM_RATE
    return a * rate ** b if rate > 0 else 0.0


def _vis_from_extinction(beta_km: float) -> float:
    if beta_km <= 0:
        return VIS_CAP_M
    return min(VIS_CAP_M, 1000.0 * _KOSCHMIEDER / beta_km)


# ---------------------------------------------------------------------------
# Visibility methods
# ---------------------------------------------------------------------------


def vis_native(inp: VisibilityInputs) -> float | None:
    if inp.visibility_m is None or inp.visibility_m < 0:
        return None
    return min(VIS_CAP_M, float(inp.visibility_m))


def vis_sw99(inp: VisibilityInputs) -> float | None:
    """Needs lowest-level cloud water and T2m; rain/snow optional."""
    rho = air_density(inp)
    cw = _content(inp.lowest_level_cloud_water_kg_kg, rho) if rho else None
    if cw is None:
        return None
    beta = SW99_CLOUD[0] * cw ** SW99_CLOUD[1] if cw > 0 else 0.0
    ci = _content(inp.lowest_level_cloud_ice_kg_kg, rho)
    if ci:
        beta += SW99_ICE[0] * ci ** SW99_ICE[1]
    cr = _rain_content(inp, rho)
    if cr:
        beta += SW99_RAIN[0] * cr ** SW99_RAIN[1]
    cs = _snow_content(inp, rho)
    if cs:
        beta += SW99_SNOW[0] * cs ** SW99_SNOW[1]
    return _vis_from_extinction(beta)


def vis_gultepe06(inp: VisibilityInputs) -> float | None:
    """Needs lowest-level cloud water and T2m."""
    rho = air_density(inp)
    lwc = _content(inp.lowest_level_cloud_water_kg_kg, rho) if rho else None
    if lwc is None:
        return None
    if lwc < GULTEPE_MIN_LWC:
        return VIS_CAP_M
    return min(VIS_CAP_M, 1000.0 * 1.002 / (lwc * GULTEPE_ND_CM3) ** 0.6473)


def vis_rh_haze(inp: VisibilityInputs) -> float | None:
    """Needs T2m and Td2m."""
    rh = relative_humidity_pct(inp.temperature_2m_c, inp.dewpoint_2m_c)
    if rh is None:
        return None
    return min(VIS_CAP_M, 60000.0 * math.exp(-2.5 * (rh - 15.0) / 80.0))


def vis_rap_blend(inp: VisibilityInputs) -> float | None:
    """min(sw99, rh_haze); needs both."""
    a, b = vis_sw99(inp), vis_rh_haze(inp)
    if a is None or b is None:
        return None
    return min(a, b)


def vis_rh_dwd(inp: VisibilityInputs) -> float | None:
    """DWD's RH cubic; needs T2m and Td2m."""
    rh = relative_humidity_pct(inp.temperature_2m_c, inp.dewpoint_2m_c)
    if rh is None:
        return None
    x = 100.0 - max(rh, RH_DWD_FLAT_BELOW_RH)
    a, b, c = RH_DWD_COEFFS
    return min(VIS_CAP_M, 1000.0 * (RH_DWD_AT_100_KM + a * x + b * x * x + c * x ** 3))


def vis_rh_icon(inp: VisibilityInputs) -> float | None:
    """ICON's own dry-air RH curve (:data:`RH_ICON_CURVE`); needs T2m, Td2m."""
    rh = relative_humidity_pct(inp.temperature_2m_c, inp.dewpoint_2m_c)
    if rh is None:
        return None
    if rh <= RH_ICON_CURVE[0][0]:
        return min(VIS_CAP_M, RH_ICON_CURVE[0][1])
    for (r0, v0), (r1, v1) in zip(RH_ICON_CURVE, RH_ICON_CURVE[1:]):
        if rh <= r1:
            f = (rh - r0) / (r1 - r0)
            return min(VIS_CAP_M, 10.0 ** (math.log10(v0) + f * (math.log10(v1) - math.log10(v0))))
    return min(VIS_CAP_M, RH_ICON_CURVE[-1][1])


def vis_icon_blend(inp: VisibilityInputs) -> float | None:
    """min(sw99, rh_icon); needs both."""
    a, b = vis_sw99(inp), vis_rh_icon(inp)
    if a is None or b is None:
        return None
    return min(a, b)


VISIBILITY_FUNCS: dict[str, Callable[[VisibilityInputs], float | None]] = {
    "native": vis_native,
    "sw99": vis_sw99,
    "gultepe06": vis_gultepe06,
    "rh_haze": vis_rh_haze,
    "rap_blend": vis_rap_blend,
    "rh_dwd": vis_rh_dwd,
    "rh_icon": vis_rh_icon,
    "icon_blend": vis_icon_blend,
}


# ---------------------------------------------------------------------------
# Fog rules and tier
# ---------------------------------------------------------------------------


def fog_model(inp: VisibilityInputs) -> bool | None:
    """Lowest-level cover >= 90 % or cloud water >= 0.02 g m-3."""
    clc = inp.lowest_level_cloud_cover_pct
    rho = air_density(inp)
    lwc = _content(inp.lowest_level_cloud_water_kg_kg, rho) if rho else None
    if clc is None and lwc is None:
        return None
    return bool(
        (clc is not None and clc >= FOG_MODEL_CLC_PCT)
        or (lwc is not None and lwc >= FOG_MODEL_LWC)
    )


def fog_radiation(inp: VisibilityInputs) -> bool | None:
    """Night, T-Td <= 1 C, wind < 6 kt, low cloud <= 30 %."""
    needed = (inp.is_night, inp.temperature_2m_c, inp.dewpoint_2m_c,
              inp.wind_speed_10m_kt, inp.cloud_cover_low_pct)
    if any(v is None for v in needed):
        return None
    return bool(
        inp.is_night
        and inp.temperature_2m_c - inp.dewpoint_2m_c <= FOG_RAD_SPREAD_C
        and inp.wind_speed_10m_kt < FOG_RAD_WIND_KT
        and inp.cloud_cover_low_pct <= FOG_RAD_LOW_CLOUD_PCT
    )


def fog_stability_index(inp: VisibilityInputs) -> float | None:
    """Petterssen FSI = 4 T2m - 2 (T850 + Td2m) + wind850 [kt].

    Written as 2 (T2m - T850) + 2 (T2m - Td2m) + wind850, the same thing.
    """
    t, td, t850, w850 = (inp.temperature_2m_c, inp.dewpoint_2m_c,
                         inp.t850_c, inp.wind_speed_850_kt)
    if t is None or td is None or t850 is None or w850 is None:
        return None
    return 2.0 * (t - t850) + 2.0 * (t - td) + w850


def fog_fsi(inp: VisibilityInputs) -> bool | None:
    fsi = fog_stability_index(inp)
    return None if fsi is None else fsi < FOG_FSI_HIGH


FOG_FUNCS: dict[str, Callable[[VisibilityInputs], bool | None]] = {
    "fog_model": fog_model,
    "fog_radiation": fog_radiation,
    "fog_fsi": fog_fsi,
}


def fog_tier(inp: VisibilityInputs, rules: dict[str, bool | None] | None = None,
             derived_vis_m: float | None = None) -> str | None:
    """LIKELY / POSSIBLE / NONE, or None when the tier cannot see fog.

    - LIKELY: the model makes fog (``fog_model`` or a derived vis < 1 km), or
      the radiation setup holds and at least one other rule agrees.
    - POSSIBLE: any other rule fires (today that is one of fog_radiation /
      fog_fsi alone), or a derived vis of 1-5 km with RH 2 m >= 95 %.
    - NONE: nothing fires.

    ``derived_vis_m`` is the lowest of the hydrometeor methods (sw99,
    gultepe06); ``native`` is deliberately not used, so the tier means the
    same on a model with and without its own visibility.

    None unless ``fog_model`` or a derived vis is known: without the model's
    own fog, NONE would only mean "the radiation / FSI rules did not fire",
    and scoring it as NONE turns every fog morning on such a row into a
    confident miss (ECMWF and GFS rows, and runs logged before #980).
    """
    rules = rules if rules is not None else {k: f(inp) for k, f in FOG_FUNCS.items()}
    known = {k: v for k, v in rules.items() if v is not None}
    if rules.get("fog_model") is None and derived_vis_m is None:
        return None
    if rules.get("fog_model") or (derived_vis_m is not None and derived_vis_m < FOG_LIKELY_VIS_M):
        return "LIKELY"
    fired = [k for k, v in known.items() if v]
    if rules.get("fog_radiation") and len(fired) >= 2:
        return "LIKELY"
    if fired:
        return "POSSIBLE"
    rh = relative_humidity_pct(inp.temperature_2m_c, inp.dewpoint_2m_c)
    if (
        derived_vis_m is not None and derived_vis_m < FOG_POSSIBLE_VIS_M
        and rh is not None and rh >= FOG_POSSIBLE_RH
    ):
        return "POSSIBLE"
    return "NONE"


def run_all(inp: VisibilityInputs) -> dict[str, Any]:
    """Every applicable method: ``{method: value}``, skipped methods absent.

    Visibility methods give metres (rounded to 1 m), fog rules booleans,
    ``fog_tier`` a tier string.
    """
    out: dict[str, Any] = {}
    for name, func in VISIBILITY_FUNCS.items():
        value = func(inp)
        if value is not None:
            out[name] = round(value)
    rules = {name: func(inp) for name, func in FOG_FUNCS.items()}
    out.update({k: v for k, v in rules.items() if v is not None})
    derived = [out[k] for k in ("sw99", "gultepe06") if k in out]
    tier = fog_tier(inp, rules, min(derived) if derived else None)
    if tier is not None:
        out["fog_tier"] = tier
    return out
