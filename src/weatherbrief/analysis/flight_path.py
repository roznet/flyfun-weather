"""Climb, cruise and descent flight path from the aircraft (#1005).

A pure library function: no ``tasks/`` imports, no I/O. The briefing pipeline
computes one path per briefing (not per model) and the phase-1 advisories
(aircraft icing, cloud / VMC) read the weather where and when the aircraft
actually is, instead of treating every route point as being at cruise.

Model (still air in v1, ISA):

* **Climb** at the preset's climb IAS (jets: 250 kt below FL100, then CAS until
  the Mach cap). Rate of climb falls linearly from ``climb_rate_low_fpm`` at
  sea level to ``climb_rate_high_fpm`` at the ceiling; a turbo piston holds
  the low rate up to its critical altitude, then falls linearly.
* **Cruise** at the aircraft's cruise IAS converted to TAS at cruise altitude
  (jets: the lower of that and the preset's cruise Mach).
* **Descent** at a fixed rate (pistons) or on a fixed path angle (turbine),
  flown at cruise speed (jets: Mach, then descent IAS, 250 kt below FL100).

Integrated in ``STEP_FT`` altitude steps and interpolated onto the route
points. A short leg where the climb and the descent meet before cruise flies
up to ``peak_ft`` (``cruise_reached=False``).

Wind: ``wind_at(distance_nm, altitude_ft) -> headwind_kt`` (default still air)
lets a consensus wind be added later without reshaping the API.

Every number below is a typical POH-order figure, not checked type by type,
and is meant to be tuned (decision of 2026-11-17: "we can tweak rates later").
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence

from weatherbrief.atmo import cas_to_mach_isa, cas_to_tas_isa, ias_to_tas_isa, mach_to_tas_isa
from weatherbrief.models.flight_path import AircraftPerformance, FlightPath, FlightPathPoint

# Integration step (ft) and the floor below which the groundspeed never falls.
STEP_FT = 100.0
MIN_GROUNDSPEED_KT = 30.0
# Never let a rate fall to zero near or above the ceiling (the path would never end).
MIN_CLIMB_RATE_FPM = 100.0
# Dense profile kept on the stored path: one sample every this many feet.
PROFILE_KEEP_FT = 500.0
# A level stretch with wind is integrated in steps of this many nm.
LEVEL_STEP_NM = 2.0

#: Rate factor for a non-FIKI aircraft climbing or descending in icing (#1005
#: decision 5): the iced path is flown at the clean rate × this. FIKI aircraft
#: fly the clean rate. Read by the aircraft-icing card.
ICED_RATE_FACTOR = 0.7

#: Cruise IAS below which an SEP aircraft gets the trainer preset.
TRAINER_MAX_CRUISE_IAS_KT = 120.0

HeadwindFn = Callable[[float, float], float]


# --- presets ----------------------------------------------------------------

#: The class presets of #1005. Keys are stable ids stored on the pack.
PRESETS: dict[str, AircraftPerformance] = {
    "trainer_piston": AircraftPerformance(
        preset="trainer_piston", climb_ias_kt=75, climb_rate_low_fpm=700,
        climb_rate_high_fpm=100, ceiling_ft=13000,
        descent_mode="rate", descent_rate_fpm=500,
    ),
    "hp_piston": AircraftPerformance(
        preset="hp_piston", climb_ias_kt=110, climb_rate_low_fpm=1000,
        climb_rate_high_fpm=100, ceiling_ft=17500,
        descent_mode="rate", descent_rate_fpm=500,
    ),
    "turbo_piston": AircraftPerformance(
        preset="turbo_piston", climb_ias_kt=120, climb_rate_low_fpm=1000,
        climb_rate_high_fpm=300, ceiling_ft=25000, critical_alt_ft=18000,
        descent_mode="rate", descent_rate_fpm=700, turbo=True,
    ),
    "twin_piston": AircraftPerformance(
        preset="twin_piston", climb_ias_kt=120, climb_rate_low_fpm=1300,
        climb_rate_high_fpm=100, ceiling_ft=20000,
        descent_mode="rate", descent_rate_fpm=700,
    ),
    "single_turboprop": AircraftPerformance(
        preset="single_turboprop", climb_ias_kt=150, climb_rate_low_fpm=2000,
        climb_rate_high_fpm=500, ceiling_ft=31000,
        descent_mode="angle", descent_angle_deg=3.0, descent_rate_cap_fpm=2500,
    ),
    "twin_turboprop": AircraftPerformance(
        preset="twin_turboprop", climb_ias_kt=160, climb_rate_low_fpm=2500,
        climb_rate_high_fpm=500, ceiling_ft=35000,
        descent_mode="angle", descent_angle_deg=3.0, descent_rate_cap_fpm=3000,
    ),
    # Not advertised (#1005 decision 8): present so the code handles Mach.
    "light_jet": AircraftPerformance(
        preset="light_jet", climb_ias_kt=260, climb_rate_low_fpm=3500,
        climb_rate_high_fpm=500, ceiling_ft=45000,
        descent_mode="angle", descent_angle_deg=3.0, descent_rate_cap_fpm=4000,
        low_alt_ias_kt=250, low_alt_cap_ft=10000, climb_mach=0.70, cruise_mach=0.70,
    ),
}

#: Typical cruise IAS (kt) per preset, used only when the aircraft has none.
DEFAULT_CRUISE_IAS_KT: dict[str, float] = {
    "trainer_piston": 100, "hp_piston": 150, "turbo_piston": 150, "twin_piston": 160,
    "single_turboprop": 200, "twin_turboprop": 220, "light_jet": 260,
}

_CATEGORY_PRESET = {
    "MEP": "twin_piston",
    "SET": "single_turboprop",
    "MET": "twin_turboprop",
    "JET": "light_jet",
}


def preset_for(category: str | None, cruise_ias_kt: float | None, turbo: bool = False) -> str:
    """Class preset id for an ICAO category (SEP / MEP / SET / MET / JET).

    SEP (and an unknown category, the unknown-aircraft default): turbo piston
    with the turbo flag, trainer below ``TRAINER_MAX_CRUISE_IAS_KT`` cruise
    IAS, high-performance piston otherwise.
    """
    cat = (category or "").upper()
    if cat in _CATEGORY_PRESET:
        return _CATEGORY_PRESET[cat]
    if turbo:
        return "turbo_piston"
    if cruise_ias_kt is not None and 0 < cruise_ias_kt < TRAINER_MAX_CRUISE_IAS_KT:
        return "trainer_piston"
    return "hp_piston"


def resolve_performance(
    *,
    category: str | None,
    cruise_ias_kt: float | None,
    known: bool = True,
    turbo: bool = False,
    ceiling_ft: float | None = None,
    climb_ias_kt: float | None = None,
    climb_rate_fpm: float | None = None,
    descent_rate_fpm: float | None = None,
    descent_angle_deg: float | None = None,
) -> AircraftPerformance:
    """The aircraft's performance: its overrides, over its class preset.

    ``known=False`` (no aircraft, or the placeholder) gives the unknown-aircraft
    default, same pattern as ``AircraftEquipage``: high-performance piston, or
    trainer when the cruise IAS is below 120 kt.

    ``climb_rate_fpm`` replaces the sea-level rate; the near-ceiling rate keeps
    the preset's proportion. A descent angle wins over a descent rate.
    """
    preset_id = preset_for(category if known else None, cruise_ias_kt, turbo and known)
    perf = PRESETS[preset_id]
    update: dict = {"source": "category" if known and category else "default"}
    overridden = False
    if known and ceiling_ft and ceiling_ft > 0:
        update["ceiling_ft"] = float(ceiling_ft)
        if perf.critical_alt_ft is not None and perf.critical_alt_ft >= ceiling_ft:
            update["critical_alt_ft"] = float(ceiling_ft) * 0.8
    if known and climb_ias_kt and climb_ias_kt > 0:
        update["climb_ias_kt"] = float(climb_ias_kt)
        overridden = True
    if known and climb_rate_fpm and climb_rate_fpm > 0:
        ratio = perf.climb_rate_high_fpm / perf.climb_rate_low_fpm
        update["climb_rate_low_fpm"] = float(climb_rate_fpm)
        update["climb_rate_high_fpm"] = float(climb_rate_fpm) * ratio
        overridden = True
    if known and descent_angle_deg and descent_angle_deg > 0:
        update.update(descent_mode="angle", descent_angle_deg=float(descent_angle_deg))
        overridden = True
    elif known and descent_rate_fpm and descent_rate_fpm > 0:
        update.update(descent_mode="rate", descent_rate_fpm=float(descent_rate_fpm))
        overridden = True
    if overridden:
        update["source"] = "aircraft"
    return perf.model_copy(update=update)


# --- speeds and rates ---------------------------------------------------------


def climb_rate_fpm(perf: AircraftPerformance, alt_ft: float) -> float:
    """Clean rate of climb (fpm) at an altitude."""
    low, high, ceiling = perf.climb_rate_low_fpm, perf.climb_rate_high_fpm, perf.ceiling_ft
    start = perf.critical_alt_ft if perf.critical_alt_ft is not None else 0.0
    if alt_ft <= start:
        rate = low
    elif ceiling <= start:
        rate = high
    else:
        frac = min((alt_ft - start) / (ceiling - start), 1.0)
        rate = low + (high - low) * frac
    return max(rate, MIN_CLIMB_RATE_FPM)


def _scheduled_tas(perf: AircraftPerformance, ias_kt: float, alt_ft: float) -> float:
    """TAS (kt) flying ``ias_kt`` at an altitude, under the jet schedule if any."""
    if perf.low_alt_ias_kt is not None and alt_ft < perf.low_alt_cap_ft:
        ias_kt = min(ias_kt, perf.low_alt_ias_kt)
    if perf.climb_mach is None:
        return ias_to_tas_isa(ias_kt, alt_ft)
    # Jets: compressible CAS, capped at the Mach crossover.
    if cas_to_mach_isa(ias_kt, alt_ft) > perf.climb_mach:
        return mach_to_tas_isa(perf.climb_mach, alt_ft)
    return cas_to_tas_isa(ias_kt, alt_ft)


def climb_tas_kt(perf: AircraftPerformance, alt_ft: float) -> float:
    return _scheduled_tas(perf, perf.climb_ias_kt, alt_ft)


def cruise_tas_kt(perf: AircraftPerformance, cruise_ias_kt: float, alt_ft: float) -> float:
    if perf.cruise_mach is not None:
        return min(_scheduled_tas(perf, cruise_ias_kt, alt_ft), mach_to_tas_isa(perf.cruise_mach, alt_ft))
    return ias_to_tas_isa(cruise_ias_kt, alt_ft)


def descent_tas_kt(perf: AircraftPerformance, cruise_ias_kt: float, alt_ft: float) -> float:
    """Descent speed: the cruise speed schedule (250 kt below FL100 for jets)."""
    return cruise_tas_kt(perf, cruise_ias_kt, alt_ft)


def descent_rate_fpm(perf: AircraftPerformance, groundspeed_kt: float) -> float:
    """Descent rate (fpm): fixed, or the path angle at this groundspeed."""
    if perf.descent_mode == "angle" and perf.descent_angle_deg:
        rate = groundspeed_kt * 6076.12 / 60.0 * math.tan(math.radians(perf.descent_angle_deg))
    else:
        rate = perf.descent_rate_fpm or 500.0
    if perf.descent_rate_cap_fpm:
        rate = min(rate, perf.descent_rate_cap_fpm)
    return max(rate, MIN_CLIMB_RATE_FPM)


# --- integration ----------------------------------------------------------------


def _no_wind(_d: float, _a: float) -> float:
    return 0.0


def _integrate_vertical(
    start_ft: float, end_ft: float, rate_fn: Callable[[float, float], float],
    tas_fn: Callable[[float], float], headwind_fn: Callable[[float, float], float],
) -> list[tuple[float, float, float]]:
    """Samples ``(distance_nm, altitude_ft, time_s)`` from ``start_ft`` to ``end_ft``.

    Distance runs away from the start airport. ``rate_fn(alt, gs)`` is the
    vertical rate, ``headwind_fn(distance, alt)`` the headwind.
    """
    out = [(0.0, start_ft, 0.0)]
    if end_ft <= start_ft:
        return out
    d = t = 0.0
    alt = start_ft
    while alt < end_ft - 1e-6:
        step = min(STEP_FT, end_ft - alt)
        mid = alt + step / 2
        gs = max(tas_fn(mid) - headwind_fn(d, mid), MIN_GROUNDSPEED_KT)
        rate = rate_fn(mid, gs)
        dt_min = step / rate
        d += gs * dt_min / 60.0
        t += dt_min * 60.0
        alt += step
        out.append((d, alt, t))
    return out


def _alt_at(samples: list[tuple[float, float, float]], alt_ft: float) -> tuple[float, float]:
    """(distance, time) along a vertical profile where it reaches ``alt_ft``."""
    for (d0, a0, t0), (d1, a1, t1) in zip(samples, samples[1:]):
        if a1 >= alt_ft:
            f = 0.0 if a1 == a0 else (alt_ft - a0) / (a1 - a0)
            return d0 + (d1 - d0) * f, t0 + (t1 - t0) * f
    d, _, t = samples[-1]
    return d, t


def _truncate(samples, alt_ft):
    d, t = _alt_at(samples, alt_ft)
    kept = [s for s in samples if s[1] < alt_ft]
    kept.append((d, alt_ft, t))
    return kept


def _thin(samples: list[tuple[float, float, float]]) -> list[tuple[float, float, float]]:
    """Keep every ``PROFILE_KEEP_FT`` of a vertical profile, plus both ends."""
    if len(samples) <= 2:
        return list(samples)
    kept = [samples[0]]
    last = samples[0][1]
    for s in samples[1:-1]:
        if abs(s[1] - last) >= PROFILE_KEEP_FT - 1e-6:
            kept.append(s)
            last = s[1]
    kept.append(samples[-1])
    return kept


def compute_flight_path(
    distances_nm: Sequence[float],
    cruise_ft: float,
    performance: AircraftPerformance,
    dep_elev_ft: float = 0.0,
    arr_elev_ft: float = 0.0,
    *,
    cruise_ias_kt: float | None = None,
    point_indices: Sequence[int] | None = None,
    planned_duration_h: float | None = None,
    wind_at: HeadwindFn | None = None,
) -> FlightPath:
    """The flight path over the route points at ``distances_nm`` (route order).

    ``point_indices`` (default ``0..n-1``) label the points. ``planned_duration_h``
    (the flight's duration, the pilot's when overwritten) scales the path's
    times so it ends at the planned arrival; ``None`` or ``<= 0`` keeps the
    path's own time. ``wind_at(distance_nm, altitude_ft)`` returns the
    headwind (kt), default still air.
    """
    headwind = wind_at or _no_wind
    perf = performance
    ias = cruise_ias_kt if cruise_ias_kt and cruise_ias_kt > 0 else DEFAULT_CRUISE_IAS_KT.get(perf.preset, 150)
    total = float(distances_nm[-1]) if distances_nm else 0.0
    dep = max(float(dep_elev_ft or 0.0), 0.0)
    arr = max(float(arr_elev_ft or 0.0), 0.0)
    cruise = max(float(cruise_ft), dep, arr)

    climb = _integrate_vertical(
        dep, cruise, lambda a, gs: climb_rate_fpm(perf, a),
        lambda a: climb_tas_kt(perf, a), headwind,
    )
    # The descent is integrated upward from the destination, distance measured
    # back from it; the wind is read at the matching route distance.
    descent = _integrate_vertical(
        arr, cruise, lambda a, gs: descent_rate_fpm(perf, gs),
        lambda a: descent_tas_kt(perf, ias, a),
        lambda d, a: headwind(total - d, a),
    )

    toc, t_climb = climb[-1][0], climb[-1][2]
    tod_back, t_desc = descent[-1][0], descent[-1][2]
    peak = cruise
    reached = True
    if toc + tod_back > total and total > 0:
        # Short leg: climb and descent meet below cruise. Find the highest
        # altitude both can reach within the route.
        reached = False
        lo, hi = max(dep, arr), cruise
        for _ in range(40):
            mid = (lo + hi) / 2
            dc, _ = _alt_at(climb, mid) if mid > dep else (0.0, 0.0)
            dd, _ = _alt_at(descent, mid) if mid > arr else (0.0, 0.0)
            if dc + dd <= total:
                lo = mid
            else:
                hi = mid
        peak = lo
        climb = _truncate(climb, peak) if peak > dep else [climb[0]]
        descent = _truncate(descent, peak) if peak > arr else [descent[0]]
        toc, t_climb = climb[-1][0], climb[-1][2]
        tod_back, t_desc = descent[-1][0], descent[-1][2]

    tod = max(total - tod_back, toc)

    # Level stretch at the peak (cruise or the short-leg peak).
    profile: list[tuple[float, float, float]] = list(_thin(climb))
    t = t_climb
    level_nm = tod - toc
    if level_nm > 0:
        tas = cruise_tas_kt(perf, ias, peak)
        if wind_at is None:
            t += level_nm / max(tas, MIN_GROUNDSPEED_KT) * 3600.0
        else:
            d = toc
            while d < tod - 1e-9:
                step = min(LEVEL_STEP_NM, tod - d)
                gs = max(tas - headwind(d + step / 2, peak), MIN_GROUNDSPEED_KT)
                t += step / gs * 3600.0
                d += step
                if d < tod - 1e-9:
                    profile.append((d, peak, t))
        profile.append((tod, peak, t))
    t_tod = t
    for d_back, alt, t_back in reversed(_thin(descent)[:-1]):
        profile.append((total - d_back, alt, t_tod + (t_desc - t_back)))
    if profile[-1][0] < total:
        profile.append((total, arr, t_tod + t_desc))
    duration_s = profile[-1][2]

    # Drop duplicate distances (zero-length level stretch), keeping order.
    dense: list[list[float]] = []
    for d, a, ts in profile:
        if dense and abs(d - dense[-1][0]) < 1e-9 and abs(a - dense[-1][1]) < 1e-9:
            continue
        dense.append([round(d, 3), round(a, 1), round(ts, 1)])

    scale = 1.0
    if planned_duration_h and planned_duration_h > 0 and duration_s > 0:
        scale = planned_duration_h * 3600.0 / duration_s

    path = FlightPath(
        performance=perf, cruise_altitude_ft=float(cruise_ft), cruise_ias_kt=float(ias),
        departure_elevation_ft=dep, arrival_elevation_ft=arr,
        total_distance_nm=total, toc_nm=round(toc, 3), tod_nm=round(tod, 3),
        cruise_reached=reached, peak_ft=round(peak, 1),
        duration_h=duration_s / 3600.0, time_scale=scale, profile=dense,
    )
    indices = list(point_indices) if point_indices is not None else list(range(len(distances_nm)))
    path.points = [
        FlightPathPoint(
            point_index=idx, distance_nm=float(d),
            altitude_ft=round(path.altitude_at(d), 1),
            time_offset_s=round(path.time_at_s(d), 1),
            phase=path.phase_at(d),
        )
        for idx, d in zip(indices, distances_nm)
    ]
    return path


def recompute_for_cruise(path: FlightPath, cruise_ft: float) -> FlightPath:
    """The same flight flown at another cruise altitude (altitude table, overrides).

    Keeps the stored inputs (route points, performance, elevations, cruise IAS
    and the planned-duration scale's target) so a recalculation never needs
    the aircraft again.
    """
    if abs(cruise_ft - path.cruise_altitude_ft) < 1e-6:
        return path
    planned_h = path.duration_h * path.time_scale if path.time_scale != 1.0 else None
    return compute_flight_path(
        [p.distance_nm for p in path.points], cruise_ft, path.performance,
        path.departure_elevation_ft, path.arrival_elevation_ft,
        cruise_ias_kt=path.cruise_ias_kt,
        point_indices=[p.point_index for p in path.points],
        planned_duration_h=planned_h,
    )


def estimate_duration_h(
    total_distance_nm: float, cruise_ft: float, performance: AircraftPerformance,
    *, cruise_ias_kt: float | None, dep_elev_ft: float = 0.0, arr_elev_ft: float = 0.0,
) -> float:
    """Still-air flight duration (h) from the path: what the new-flight form shows."""
    if total_distance_nm <= 0:
        return 0.0
    path = compute_flight_path(
        [0.0, float(total_distance_nm)], cruise_ft, performance, dep_elev_ft, arr_elev_ft,
        cruise_ias_kt=cruise_ias_kt,
    )
    return path.duration_h


# --- rollout flag and the pipeline builder ------------------------------------

#: ``off`` (no path), ``shadow`` (default: computed and stored, phase-1 cards
#: record what they would grade on ``per_model[].shadow``, grades and point
#: times unchanged) or ``live`` (point times and phase-1 grades read the path).
FLIGHT_PATH_ENV = "WB_FLIGHT_PATH"

_warned_modes: set[str] = set()


def flight_path_mode() -> str:
    """``live``, ``off`` or ``shadow`` (the default, and any other value)."""
    import logging
    import os

    mode = os.environ.get(FLIGHT_PATH_ENV, "").strip().lower()
    if mode in ("live", "off", "shadow"):
        return mode
    if mode and mode not in _warned_modes:
        _warned_modes.add(mode)
        logging.getLogger(__name__).warning(
            "%s=%r is not live, shadow or off: running in shadow", FLIGHT_PATH_ENV, mode,
        )
    return "shadow"


def build_flight_path(
    distances_nm: Sequence[float],
    point_indices: Sequence[int],
    cruise_ft: float,
    *,
    elevation=None,
    performance: AircraftPerformance | None = None,
    cruise_ias_kt: float | None = None,
    planned_duration_h: float | None = None,
) -> FlightPath | None:
    """The briefing's path over its route points, or None when it cannot be built.

    ``elevation`` (an ``ElevationProfile``) gives the departure and arrival
    field elevations (sea level when unknown). ``performance`` None is the
    unknown-aircraft default. Never raises: a failure is logged and the
    briefing reads as flat cruise, as before #1005.
    """
    import logging

    if len(distances_nm) < 2 or distances_nm[-1] <= 0:
        return None
    try:
        from weatherbrief.analysis.advisories._helpers import terrain_at_distance

        dep = terrain_at_distance(elevation, 0.0) or 0.0
        arr = terrain_at_distance(elevation, float(distances_nm[-1])) or 0.0
        perf = performance or resolve_performance(
            category=None, cruise_ias_kt=cruise_ias_kt, known=False,
        )
        return compute_flight_path(
            distances_nm, cruise_ft, perf, dep, arr,
            cruise_ias_kt=cruise_ias_kt, point_indices=point_indices,
            planned_duration_h=planned_duration_h,
        )
    except Exception:
        logging.getLogger(__name__).warning("flight path: build failed", exc_info=True)
        return None


def route_flight_path(
    route_points,
    cruise_ft: float,
    *,
    elevation=None,
    equipage=None,
    cruise_speed_ias_kt: float | None = None,
    planned_duration_h: float | None = None,
) -> FlightPath | None:
    """The path over a route's points for an aircraft equipage, unless ``WB_FLIGHT_PATH=off``.

    ``route_points`` are ``RoutePoint``s (or anything with
    ``distance_from_origin_nm``), indexed by position; ``equipage`` an
    ``AircraftEquipage`` whose ``performance`` is used (None: unknown aircraft).
    """
    if flight_path_mode() == "off" or not route_points:
        return None
    return build_flight_path(
        [float(rp.distance_from_origin_nm or 0.0) for rp in route_points],
        list(range(len(route_points))),
        cruise_ft,
        elevation=elevation,
        performance=getattr(equipage, "performance", None),
        cruise_ias_kt=cruise_speed_ias_kt,
        planned_duration_h=planned_duration_h,
    )


def analyses_flight_path(
    analyses,
    cruise_ft: float,
    *,
    stored: FlightPath | None = None,
    elevation=None,
    equipage=None,
    cruise_speed_ias_kt: float | None = None,
    planned_duration_h: float | None = None,
) -> FlightPath | None:
    """The path for a recalculation: the stored one at this cruise, else built anew.

    For packs built before #1005 (no stored path) the path is built from the
    analyses' own distances, so a ``WB_FLIGHT_PATH=live`` replay of an old
    pack still grades on a path (with the pack's linear point times).
    """
    if flight_path_mode() == "off":
        return None
    if stored is not None:
        return recompute_for_cruise(stored, cruise_ft)
    if not analyses:
        return None
    return build_flight_path(
        [float(a.distance_from_origin_nm or 0.0) for a in analyses],
        [a.point_index for a in analyses],
        cruise_ft,
        elevation=elevation,
        performance=getattr(equipage, "performance", None),
        cruise_ias_kt=cruise_speed_ias_kt,
        planned_duration_h=planned_duration_h,
    )
