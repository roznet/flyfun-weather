"""GRIB-primary briefing slots: ECMWF, ICON (#912) and GFS (#913) without Open-Meteo.

The briefing fetch used to pull every model from Open-Meteo with the full
variable set and then let GRIB enrichment overwrite the ECMWF and ICON
soundings (and most of the ECMWF surface). For those two slots the
Open-Meteo request was mostly paid-for data thrown away.

A slot is now **GRIB-primary** when a cheap, network-free gate says GRIB
should cover it:

* ECMWF: a ready run on the local ECPDS disk whose observed horizon reaches
  the flight-window end (the same ``find_best_ecmwf_run`` the enrichment
  uses), with both surface (a1) and pressure-level (a2) files.
* ICON: the whole route inside the ICON-EU domain, the window inside a
  publishable run's horizon (``icon_eu_window_out_of_range``), and not a
  historical briefing (DWD keeps current runs only).
* GFS (#913): the window inside the newest publishable run's 384 h horizon
  (``gfs_window_out_of_range``), not a historical briefing, and not a route
  the HRRR upgrade takes (whole route in the CONUS domain, window inside an
  HRRR horizon): that slot keeps today's path, HRRR sounding over Open-Meteo.

A GRIB-primary slot starts as a **skeleton**: one ``WaypointForecast`` per
route point carrying the same day span of hours Open-Meteo would have
returned, every value None. Enrichment fills it from GRIB. Afterwards
:func:`fill_grib_primary_gaps` checks every flight-window hour of every
point, and fetches Open-Meteo, with today's full variable set, for the
points GRIB left short, then splices those values into the gaps only. So a
GRIB outage degrades to exactly today's Open-Meteo slot, one point at a
time, and a covered slot costs zero Open-Meteo calls.

``WB_GRIB_PRIMARY`` is the kill switch: ``0`` turns it off (every slot back
to Open-Meteo first), a comma list (``ecmwf,icon``) limits it to those models.
"""

from __future__ import annotations

import logging
import math
import os
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

from weatherbrief.models import (
    HourlyForecast,
    ModelSource,
    RoutePoint,
    Waypoint,
    WaypointForecast,
)

if TYPE_CHECKING:
    from weatherbrief.fetch.open_meteo import OpenMeteoClient
    from weatherbrief.models import RouteCrossSection

logger = logging.getLogger(__name__)

GRIB_PRIMARY_MODELS: tuple[ModelSource, ...] = (
    ModelSource.ECMWF, ModelSource.ICON, ModelSource.GFS,
)

# Fields every flight-window hour must carry for a point to count as covered.
# The sounding plus the surface fields the advisories lean on hardest; a hole
# in any of them sends the point to the Open-Meteo fallback.
_REQUIRED_HOURLY_FIELDS: tuple[str, ...] = (
    "temperature_2m_c",
    "wind_speed_10m_kt",
    "precipitation_mm",
    "cloud_cover_pct",
)

# HourlyForecast fields a fallback splice never touches: the time key, the
# sounding (spliced as a whole, below) and the GRIB-only diagnostics.
_SPLICE_SKIP_FIELDS: frozenset[str] = frozenset({
    "time",
    "pressure_levels",
    "nwp_cloud_diagnostics",
    "explicit_convective_diagnostics",
})


def grib_primary_models() -> tuple[ModelSource, ...]:
    """Models the ``WB_GRIB_PRIMARY`` switch allows (default: ECMWF, ICON, GFS)."""
    raw = os.environ.get("WB_GRIB_PRIMARY", "1").strip().lower()
    if raw in ("", "1", "true", "yes", "on"):
        return GRIB_PRIMARY_MODELS
    if raw in ("0", "false", "no", "off"):
        return ()
    wanted = {part.strip() for part in raw.split(",") if part.strip()}
    return tuple(m for m in GRIB_PRIMARY_MODELS if m.value in wanted)


# Standalone snapshots (forecast map + alternates, #914) have their own
# switch, so the map can be rolled back without touching briefings. ECMWF is
# not listed: its snapshots were GRIB-first before this switch existed.
# ``WB_GRIB_SNAPSHOTS=icon`` rolls GFS back alone, ``=gfs`` ICON.
GRIB_SNAPSHOT_MODELS: tuple[str, ...] = ("icon", "gfs")


def grib_snapshot_models() -> tuple[str, ...]:
    """Snapshot models ``WB_GRIB_SNAPSHOTS`` allows (default: all of them).

    Same grammar as ``WB_GRIB_PRIMARY``: ``0`` turns GRIB-primary snapshots
    off (Open-Meteo first, as before #914), a comma list limits it.
    """
    raw = os.environ.get("WB_GRIB_SNAPSHOTS", "1").strip().lower()
    if raw in ("", "1", "true", "yes", "on"):
        return GRIB_SNAPSHOT_MODELS
    if raw in ("0", "false", "no", "off"):
        return ()
    wanted = {part.strip() for part in raw.split(",") if part.strip()}
    return tuple(m for m in GRIB_SNAPSHOT_MODELS if m in wanted)


def grib_snapshot_d2_enabled() -> bool:
    """ICON-D2 for the short-range ICON snapshots (#928); default on.

    ``WB_GRIB_SNAPSHOTS_D2=0`` builds every ICON row from ICON-EU, as #926
    did, for comparison or rollback of the D2 part alone.
    """
    raw = os.environ.get("WB_GRIB_SNAPSHOTS_D2", "1").strip().lower()
    return raw not in ("0", "false", "no", "off")


def grib_snapshot_drill(model: str) -> str | None:
    """Failure drill for GRIB-primary snapshots: ``"all"``, ``"half"`` or None.

    ``WB_GRIB_SNAPSHOTS_DRILL=icon`` discards every GRIB snapshot of that
    model after the build, ``icon:half`` every other airport's, so the
    Open-Meteo fallback can be exercised on a live cycle. Mirrors
    ``WB_GRIB_PRIMARY_DRILL``.
    """
    raw = os.environ.get("WB_GRIB_SNAPSHOTS_DRILL", "").strip().lower()
    for part in raw.split(","):
        name, _, mode = part.strip().partition(":")
        if name == model:
            return "half" if mode == "half" else "all"
    return None


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def grib_primary_skip_reason(
    model: ModelSource,
    route_points: list[RoutePoint],
    departure_time: datetime,
    flight_duration_hours: float,
    *,
    as_of_time: datetime | None = None,
    historical_mode: bool = False,
) -> str | None:
    """Why *model* can't be GRIB-primary for this flight, or None if it can.

    Network-free and cheap: a directory scan for ECMWF, arithmetic for ICON
    and GFS.
    A pass is a prediction, not a promise; what GRIB then fails to deliver is
    caught per point by :func:`fill_grib_primary_gaps`.
    """
    if model not in grib_primary_models():
        return "disabled"
    departure_time = _aware(departure_time)
    if model == ModelSource.ECMWF:
        return _ecmwf_skip_reason(departure_time, flight_duration_hours, as_of_time)
    if model == ModelSource.ICON:
        return _icon_skip_reason(
            route_points, departure_time, flight_duration_hours,
            as_of_time, historical_mode,
        )
    if model == ModelSource.GFS:
        return _gfs_skip_reason(
            route_points, departure_time, flight_duration_hours,
            as_of_time, historical_mode,
        )
    return "not_grib_primary"


def _ecmwf_skip_reason(
    departure_time: datetime,
    flight_duration_hours: float,
    as_of_time: datetime | None,
) -> str | None:
    from weatherbrief.fetch.grib.ecmwf_fetch import (
        ecmwf_grib_dir,
        find_best_ecmwf_run,
        scan_ecmwf_files,
    )

    grib_dir = ecmwf_grib_dir()
    try:
        files = scan_ecmwf_files(grib_dir)
    except Exception:
        logger.warning("GRIB-primary ecmwf: scan of %s failed", grib_dir, exc_info=True)
        return "scan_failed"
    if as_of_time is not None:
        files = [f for f in files if f.base_time <= as_of_time]
    if not files:
        return "no_run"
    # Same cover_until as _enrich_ecmwf_inner, so the gate and the enrichment
    # pick the same run.
    flight_end = departure_time + timedelta(hours=max(flight_duration_hours, 1))
    run_files = find_best_ecmwf_run(files, cover_until=flight_end, data_dir=grib_dir)
    if not run_files:
        return "no_run"
    base_time = run_files[0].base_time
    max_step = max(f.step_hours for f in run_files)
    if base_time + timedelta(hours=max_step) < flight_end:
        return "out_of_range"
    if not any(f.is_surface for f in run_files) or not any(
        f.is_pressure_level for f in run_files
    ):
        return "incomplete_run"
    return None


def _icon_skip_reason(
    route_points: list[RoutePoint],
    departure_time: datetime,
    flight_duration_hours: float,
    as_of_time: datetime | None,
    historical_mode: bool,
) -> str | None:
    from weatherbrief.fetch.grib.icon_eu_fetch import (
        ICON_EU,
        icon_eu_window_out_of_range,
        route_in_icon_eu_domain,
    )

    if historical_mode:
        # DWD opendata keeps the current runs only.
        return "historical"
    # ICON-EU's domain contains ICON-D2's, so EU is the gate; the D2/EU choice
    # stays with _prepare_icon_eu.
    if not route_in_icon_eu_domain(route_points, ICON_EU):
        return "out_of_domain"
    if icon_eu_window_out_of_range(
        departure_time, flight_duration_hours, as_of_time, ICON_EU,
    ):
        return "out_of_range"
    return None


def _gfs_skip_reason(
    route_points: list[RoutePoint],
    departure_time: datetime,
    flight_duration_hours: float,
    as_of_time: datetime | None,
    historical_mode: bool,
) -> str | None:
    from weatherbrief.fetch.grib.grib_fetch import gfs_window_out_of_range

    if historical_mode:
        # NOAA keeps old GFS runs, but replays and backdated briefings stay on
        # Open-Meteo until that rule is decided for every GRIB-primary model.
        return "historical"
    if _hrrr_takes_route(route_points, departure_time, flight_duration_hours, as_of_time):
        # The HRRR upgrade (#457) replaces the sounding of an Open-Meteo slot
        # and keeps Open-Meteo's surface. Left exactly as it is.
        return "hrrr_route"
    if gfs_window_out_of_range(departure_time, flight_duration_hours, as_of_time):
        return "out_of_range"
    return None


def _hrrr_takes_route(
    route_points: list[RoutePoint],
    departure_time: datetime,
    flight_duration_hours: float,
    as_of_time: datetime | None,
) -> bool:
    """Would ``_try_enrich_gfs_from_hrrr`` pass its network-free gates?

    Same three checks in the same order, on the same helpers: the
    ``WB_HRRR_ENABLED`` switch (``hrrr_enabled``), the whole route in the CONUS
    domain, a publishable run reaching the window end. A probe failure later
    still falls back to plain GFS there.
    """
    from weatherbrief.fetch.grib.hrrr_fetch import (
        hrrr_enabled,
        hrrr_window_out_of_range,
        route_in_hrrr_domain,
    )

    if not hrrr_enabled():
        return False
    try:
        if not route_in_hrrr_domain(route_points):
            return False
    except Exception:
        logger.warning("GRIB-primary gfs: HRRR domain check failed", exc_info=True)
        return False
    return not hrrr_window_out_of_range(
        departure_time, flight_duration_hours, as_of_time,
    )


def _waypoint_for(point: RoutePoint) -> Waypoint:
    """The Waypoint Open-Meteo's ``fetch_multi_point`` builds for *point*."""
    if point.waypoint_icao:
        return Waypoint(
            icao=point.waypoint_icao,
            name=point.waypoint_name or point.waypoint_icao,
            lat=point.lat,
            lon=point.lon,
        )
    label = f"RP{int(point.distance_from_origin_nm):03d}"
    return Waypoint(icao=label, name=label, lat=point.lat, lon=point.lon)


def build_skeleton(
    route_points: list[RoutePoint],
    model: ModelSource,
    *,
    start_date: str,
    end_date: str,
) -> list[WaypointForecast]:
    """Empty point forecasts spanning the hours Open-Meteo would have returned.

    One ``WaypointForecast`` per route point, with an ``HourlyForecast`` for
    every hour from ``start_date`` 00:00 to ``end_date`` 23:00 UTC and every
    value None. Keeping Open-Meteo's day span keeps the hour-indexed code
    paths (time-scan baselines, the time fill's anchor detection) unchanged:
    an hour GRIB did not reach simply carries no data.
    """
    start = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    end = datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    n_hours = int((end - start).total_seconds() // 3600) + 24
    fetched_at = datetime.now(timezone.utc)
    return [
        WaypointForecast(
            waypoint=_waypoint_for(point),
            model=model,
            fetched_at=fetched_at,
            hourly=[
                HourlyForecast(time=start + timedelta(hours=h))
                for h in range(n_hours)
            ],
        )
        for point in route_points
    ]


def required_hours(
    departure_time: datetime, flight_duration_hours: float,
) -> list[datetime]:
    """The flight-window hours a GRIB-primary point must cover.

    ``floor(departure) .. ceil(departure + ceil(duration))``, at least one
    hour: the same contiguous window the GFS/ICON fetch uses
    (``contiguous_window_forecast_hours``) and the window the advisories
    sample at.
    """
    dep = _aware(departure_time)
    lo = dep.replace(minute=0, second=0, microsecond=0)
    end = dep + timedelta(hours=max(1, math.ceil(max(flight_duration_hours, 0.0))))
    hi = end.replace(minute=0, second=0, microsecond=0)
    if hi < end:
        hi += timedelta(hours=1)
    n = int((hi - lo).total_seconds() // 3600)
    return [lo + timedelta(hours=h) for h in range(n + 1)]


def _hour_covered(h: HourlyForecast | None) -> bool:
    if h is None or not h.pressure_levels:
        return False
    return all(getattr(h, f) is not None for f in _REQUIRED_HOURLY_FIELDS)


def _hours_by_time(wf: WaypointForecast) -> dict[datetime, HourlyForecast]:
    return {_aware(h.time): h for h in wf.hourly}


def uncovered_points(
    point_forecasts: list[WaypointForecast], hours: list[datetime],
) -> list[int]:
    """Indices of the points missing data at any of *hours*."""
    out: list[int] = []
    for i, wf in enumerate(point_forecasts):
        by_time = _hours_by_time(wf)
        if not all(_hour_covered(by_time.get(t)) for t in hours):
            out.append(i)
    return out


def splice_open_meteo(target: WaypointForecast, source: WaypointForecast) -> int:
    """Fill *target*'s gaps from an Open-Meteo forecast for the same point.

    Matched by valid time. An hour without a sounding takes Open-Meteo's
    levels; any scalar field still None takes Open-Meteo's value. GRIB values
    and the GRIB diagnostics are never overwritten. Hours Open-Meteo has and
    the target lacks are added. Returns the number of hours touched.
    """
    by_time = _hours_by_time(target)
    touched = 0
    added = False
    for src in source.hourly:
        t = _aware(src.time)
        dst = by_time.get(t)
        if dst is None:
            target.hourly.append(src.model_copy(deep=True))
            added = True
            touched += 1
            continue
        changed = False
        if not dst.pressure_levels and src.pressure_levels:
            dst.pressure_levels = src.pressure_levels
            changed = True
        for name in HourlyForecast.model_fields:
            if name in _SPLICE_SKIP_FIELDS:
                continue
            if getattr(dst, name) is None:
                value = getattr(src, name)
                if value is not None:
                    setattr(dst, name, value)
                    changed = True
        touched += changed
    if added:
        target.hourly.sort(key=lambda h: _aware(h.time))
    return touched


def apply_failure_drill(
    cross_sections: list["RouteCrossSection"], primary_models: list[ModelSource],
) -> None:
    """Dev-only drill: pretend GRIB delivered nothing for some slots.

    ``WB_GRIB_PRIMARY_DRILL=icon`` empties every point of the icon skeleton
    after enrichment, ``icon:half`` every other point (ECMWF and GFS likewise), so
    the Open-Meteo fallback and its reporting can be watched end to end on a
    machine where GRIB is healthy. Unset in production.
    """
    raw = os.environ.get("WB_GRIB_PRIMARY_DRILL", "").strip().lower()
    if not raw:
        return
    for item in raw.split(","):
        name, _, mode = item.strip().partition(":")
        model = next((m for m in primary_models if m.value == name), None)
        section = next((cs for cs in cross_sections if cs.model == model), None)
        if section is None:
            continue
        step = 2 if mode == "half" else 1
        for wf in section.point_forecasts[::step]:
            wf.hourly[:] = [HourlyForecast(time=h.time) for h in wf.hourly]
        logger.warning(
            "GRIB-primary drill: emptied %d/%d %s points (WB_GRIB_PRIMARY_DRILL)",
            len(section.point_forecasts[::step]), len(section.point_forecasts), name,
        )


@dataclass
class GribPrimaryOutcome:
    """What happened to one GRIB-primary slot after enrichment."""

    model: str
    points_total: int
    points_fallback: int = 0
    # Open-Meteo calls the fallback made for this slot (billing, #910).
    open_meteo_calls: int = 0
    # The fallback fetch raised (``error``) or returned no values (``empty``).
    # The slot is then incomplete, and the caller drops it exactly as a failed
    # or empty Open-Meteo fetch is handled today.
    error: BaseException | None = None
    empty: bool = False

    @property
    def failed(self) -> bool:
        return self.error is not None or self.empty


def fill_grib_primary_gaps(
    cross_sections: list["RouteCrossSection"],
    route_points: list[RoutePoint],
    primary_models: list[ModelSource],
    departure_time: datetime,
    flight_duration_hours: float,
    *,
    client: "OpenMeteoClient",
    start_date: str,
    end_date: str,
    reason: str = "",
) -> list[GribPrimaryOutcome]:
    """Open-Meteo fallback for the points GRIB left short (#912).

    For each GRIB-primary section, finds the points missing a sounding or a
    required surface field at any flight-window hour and fetches Open-Meteo,
    full variable set, for those points only, then splices the values into
    the gaps (:func:`splice_open_meteo`). Mutates the sections in place, which
    also updates the shared waypoint forecasts in ``all_forecasts``.

    *reason* is logged with each fallback ("grib enrichment failed", ...).
    """
    from weatherbrief.fetch.open_meteo import EmptyForecastError

    # Only the hours the slot can hold: the window ends at ceil(arrival), which
    # for an arrival late in the day is 00Z of a day the fetch span
    # (start_date..end_date) does not reach. Neither GRIB nor the Open-Meteo
    # fallback has that hour, and an Open-Meteo slot never carried it either.
    span_lo = datetime.strptime(start_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
    span_hi = (datetime.strptime(end_date, "%Y-%m-%d").replace(tzinfo=timezone.utc)
               + timedelta(hours=23))
    hours = [t for t in required_hours(departure_time, flight_duration_hours)
             if span_lo <= t <= span_hi]
    outcomes: list[GribPrimaryOutcome] = []
    for model in primary_models:
        section = next((cs for cs in cross_sections if cs.model == model), None)
        if section is None:
            continue
        pfs = section.point_forecasts
        outcome = GribPrimaryOutcome(model=model.value, points_total=len(pfs))
        outcomes.append(outcome)
        missing = uncovered_points(pfs, hours)
        if not missing:
            logger.info(
                "GRIB-primary %s: all %d points covered from GRIB, no Open-Meteo call",
                model.value, len(pfs),
            )
            continue
        outcome.points_fallback = len(missing)
        logger.info(
            "GRIB-primary %s: Open-Meteo fallback for %d/%d points (%s)",
            model.value, len(missing), len(pfs),
            reason or "GRIB left flight-window hours uncovered",
        )
        calls_before = client.call_count
        try:
            fetched = client.fetch_multi_point(
                [route_points[i] for i in missing], model,
                start_date=start_date, end_date=end_date,
            )
        except EmptyForecastError as exc:
            logger.warning(
                "GRIB-primary %s: Open-Meteo fallback returned no values: %s",
                model.value, exc,
            )
            outcome.empty = True
            fetched = None
        except Exception as exc:
            logger.warning(
                "GRIB-primary %s: Open-Meteo fallback failed", model.value,
                exc_info=True,
            )
            outcome.error = exc
            fetched = None
        outcome.open_meteo_calls = client.call_count - calls_before
        if fetched is None:
            continue
        if len(fetched) != len(missing):
            # zip() below would silently drop the tail; those points stay
            # short and show up in the "still short" count.
            logger.warning(
                "GRIB-primary %s: Open-Meteo fallback returned %d forecasts for %d points",
                model.value, len(fetched), len(missing),
            )
        for i, om_wf in zip(missing, fetched):
            splice_open_meteo(pfs[i], om_wf)
        still = uncovered_points([pfs[i] for i in missing], hours)
        if still:
            # Open-Meteo answered but has holes of its own (a null tail, a
            # field it lacks): today's slot would carry the same holes.
            logger.info(
                "GRIB-primary %s: %d point(s) still short after fallback, kept "
                "as an Open-Meteo slot would be",
                model.value, len(still),
            )
    return outcomes
