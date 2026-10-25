"""Flight-scoped GRIB pre-warm for every source a briefing uses (#929).

Generalises the ICON-D2 flight warm (#469 phase 3). After a model run
publishes, each upcoming flight goes through the briefing's own run choice
and gates for that source, and the files the briefing would download are
downloaded now, with no decode. The warmed cache is therefore what the
briefing reads, and its first refresh on the new run downloads ~0 MB:

* ``icon-d2`` / ``icon-eu``: ``_prepare_icon_eu`` + ``_prefetch_icon_eu_data``,
  exactly as the briefing's phase 1. The variant the briefing would pick
  decides which pass warms a flight; ICON-EU model levels are converted to
  zarr as they land (queued at BACKGROUND, as the briefing's prefetch does).
* ``gfs``: for a GRIB-primary GFS slot (``grib_primary_skip_reason``), the
  sounding + surface blobs of every window hour plus the surface of the
  preceding step, and the cloud-diagnostics blob of every window hour.
* ``hrrr``: for a route the HRRR upgrade takes (``_hrrr_takes_route``), the
  sounding and cloud-diagnostics artifacts of every window hour.

* alternates: on the ``icon-eu`` and ``icon-d2`` passes, for a flight the
  briefing computes alternates for (D-2 inward), the ETA-hour files its
  alternates search reads: the ICON snapshot files (isobaric + surface,
  ``ensure_icon_snapshot_files``) and the cloud diagnostics
  (``ensure_icon_cloud_diag_files``), split between ICON-D2 and ICON-EU by
  the search's own planner (``icon_snapshot_fetch_plan``) over its own
  candidate list (``alternates_fetch_airports``).

ECMWF needs nothing (local ECPDS disk). GFS alternates read the briefing's
``CLOUD_DIAG`` blob, which the ``gfs`` pass warms.

**Limits** (owner, 2026-10-25), so a pass cannot run away. Flights are
taken soonest departure first, so any limit cuts only the flights furthest
out; those brief cold or are warmed by the next run's pass. A cut is logged
with the flight ids and the limit that cut them.

* New bytes downloaded per pass (:class:`~weatherbrief.fetch.grib.cache.CacheWriteMeter`):
  cache hits are free, so this counts distinct hours across flights, which is
  what costs (files are whole-grid per hour).
* Wall time per pass.
* A free-disk floor on the data volume: below it the pass does not start.
* A sanity cap on flights per pass.
* The #490 yield-to-interactive gate, between download units. A deferred pass
  resumes next tick (cache hits fast-forward it); a pass cut by a limit is
  finished, and the caller records it as done.

All limits are environment-tunable, see :func:`warm_limits`.
"""

from __future__ import annotations

import logging
import os
import shutil
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

#: Warm sources, in the cache-slug spelling (also the warming-window key).
WARM_SOURCES: tuple[str, ...] = ("icon-d2", "icon-eu", "gfs", "hrrr")

# Defaults per source. D2's horizon is its model-level horizon (no run can
# serve a flight past 48 h). EU and GFS reach further, 72 h covers the
# flights people brief in the days before. A horizon of 0 turns the source
# off. HRRR is off by default (owner, 2026-10-25): the warm can only fire on
# the 6-hourly extended cycles the freshness registry tracks, while a briefing
# takes the freshest hourly run, so the warmed run is mostly one the briefing
# never reads. ``WB_FLIGHT_WARM_HORIZON_HOURS_HRRR=48`` (the extended cycles'
# horizon) turns it back on.
_DEFAULT_HORIZON_HOURS: dict[str, float] = {
    "icon-d2": 48.0, "icon-eu": 72.0, "gfs": 72.0, "hrrr": 0.0,
}
# GB (1e9) of new downloads per pass. GFS 6 GB is ~34 window hours at
# ~177 MB, ICON-EU ~30 hours at ~200 MB (189 MB model levels + ~10 MB
# cloud diag, measured 2026-07-21). D2 has no
# default cap: it ran uncapped before #929 and its traffic is unchanged.
_DEFAULT_MAX_GB: dict[str, float] = {
    "icon-d2": 0.0, "icon-eu": 6.0, "gfs": 6.0, "hrrr": 2.0,
}
_DEFAULT_MAX_MINUTES = 20.0
_DEFAULT_MIN_FREE_GB = 20.0
_DEFAULT_MAX_FLIGHTS = 40

_GB = 1_000_000_000

# source -> run label of the last low-disk WARNING. A low-disk pass is retried
# every precache tick, so later skips of the same run log at DEBUG.
_low_disk_warned: dict[str, str] = {}


def _env_key(source: str) -> str:
    return source.upper().replace("-", "_")


def _float_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        return float(raw)
    except ValueError:
        logger.warning("Invalid %s=%r, using %s", name, raw, default)
        return default


@dataclass(frozen=True)
class WarmLimits:
    """The limits of one source's warm pass. ``max_bytes`` None = uncapped."""

    horizon_hours: float
    max_bytes: int | None
    max_seconds: float
    min_free_bytes: int
    max_flights: int


def warm_limits(source: str) -> WarmLimits:
    """Limits for ``source``, read from the environment on every call.

    ``WB_FLIGHT_WARM_HORIZON_HOURS_<SRC>`` and ``WB_FLIGHT_WARM_MAX_GB_<SRC>``
    per source (``<SRC>`` = ``ICON_D2``, ``ICON_EU``, ``GFS``, ``HRRR``; a
    max of 0 or less means uncapped), and for every source
    ``WB_FLIGHT_WARM_MAX_MINUTES``, ``WB_FLIGHT_WARM_MIN_FREE_GB`` and
    ``WB_FLIGHT_WARM_MAX_FLIGHTS``.
    """
    key = _env_key(source)
    max_gb = _float_env(f"WB_FLIGHT_WARM_MAX_GB_{key}", _DEFAULT_MAX_GB.get(source, 0.0))
    return WarmLimits(
        horizon_hours=_float_env(
            f"WB_FLIGHT_WARM_HORIZON_HOURS_{key}",
            _DEFAULT_HORIZON_HOURS.get(source, 48.0),
        ),
        max_bytes=int(max_gb * _GB) if max_gb > 0 else None,
        max_seconds=_float_env("WB_FLIGHT_WARM_MAX_MINUTES", _DEFAULT_MAX_MINUTES) * 60,
        min_free_bytes=int(
            _float_env("WB_FLIGHT_WARM_MIN_FREE_GB", _DEFAULT_MIN_FREE_GB) * _GB,
        ),
        max_flights=max(1, int(_float_env("WB_FLIGHT_WARM_MAX_FLIGHTS", _DEFAULT_MAX_FLIGHTS))),
    )


def free_disk_bytes(path: Path) -> int | None:
    """Free bytes on the volume holding ``path`` (or its nearest parent)."""
    probe = path
    while not probe.exists() and probe != probe.parent:
        probe = probe.parent
    try:
        return shutil.disk_usage(probe).free
    except OSError:
        logger.warning("Flight warm: cannot read free disk at %s", probe, exc_info=True)
        return None


@dataclass
class _Flight:
    id: str
    departure_time: datetime
    duration_hours: float
    route_points: list
    route: object | None = None  # RouteConfig, for the alternates candidates


@dataclass
class _Outcome:
    """One flight's warm: ``warmed``, ``skipped`` (not this source), ``stopped``
    (a limit or the yield gate) or ``failed`` (downloads left hours cold)."""

    status: str
    hours: set[tuple[str, int]] = field(default_factory=set)
    reason: str | None = None


# Stop reasons. Only "deferred" leaves the run un-recorded.
DEFERRED = "deferred"
CUT_BYTES = "bytes"
CUT_TIME = "time"
CUT_FLIGHTS = "flights"


def _aware(dt: datetime) -> datetime:
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


# --- Per-source warm units ---------------------------------------------------


def _warm_icon(
    source: str, flight: _Flight, data_dir: Path, stop: Callable[[], bool],
) -> _Outcome:
    """ICON-D2 or ICON-EU: the briefing's prepare + prefetch, download only."""
    from weatherbrief.fetch.grib import _prefetch_icon_eu_data, _prepare_icon_eu
    from weatherbrief.models import ModelSource, RouteCrossSection

    icon_section = RouteCrossSection(
        model=ModelSource.ICON,
        route_points=[],
        fetched_at=datetime.now(timezone.utc),
        point_forecasts=[],
    )
    ctx, skip = _prepare_icon_eu(
        [icon_section], flight.route_points, flight.departure_time,
        data_dir=data_dir,
        flight_duration_hours=flight.duration_hours,
    )
    if ctx is None:
        return _Outcome("skipped", reason=skip or "no_run")
    # The other variant's pass warms it: a D2 flight on the EU pass (and the
    # reverse) is that pass's job, at that model's own run cadence.
    if ctx.variant.slug != source:
        return _Outcome("skipped", reason=f"served_by_{ctx.variant.slug}")

    # One outer unit and half the connection pool (enforced by the callee):
    # warming is discretionary and must never crowd out a concurrent
    # briefing's memory or connections (05:09Z OOM 2026-07-23; one warming
    # flight with two units took the container to 5999/6144 MB on
    # 2026-07-26). ``stop`` rides along as abort_if, consulted between every
    # download unit (#501), and never fires for a flight with nothing to
    # fetch, so a resumption pass fast-forwards through warmed flights.
    completed = _prefetch_icon_eu_data(ctx, outer_workers=1, abort_if=stop)
    run = f"{ctx.init_date}_{ctx.init_hour:02d}z"
    hours = {(run, f) for f in ctx.forecast_hours}
    return _Outcome("warmed" if completed else "stopped", hours=hours)


def _warm_gfs(
    flight: _Flight, data_dir: Path, stop: Callable[[], bool],
) -> _Outcome:
    """GFS GRIB-primary sounding, surface and cloud diagnostics (#913)."""
    from weatherbrief.fetch.grib import (
        GFS_SURFACE_CACHE_KEY,
        _ensure_gfs_cloud_diag,
        _ensure_gfs_sounding,
        _ensure_gfs_surface,
        _gfs_previous_step,
        _gfs_sounding_cache_key,
        _grib_session,
        gfs_sounding_levels,
    )
    from weatherbrief.fetch.grib.cache import (
        cache_dir_for_run,
        cache_key,
        is_cached,
        pin_run_dir,
    )
    from weatherbrief.fetch.grib.grib_fetch import (
        compute_flight_window_hours,
        fetch_idx,
        find_latest_run,
    )
    from weatherbrief.fetch.grib.primary import grib_primary_skip_reason
    from weatherbrief.models import ModelSource

    reason = grib_primary_skip_reason(
        ModelSource.GFS, flight.route_points, flight.departure_time,
        flight.duration_hours,
    )
    if reason is not None:
        # An HRRR route is the hrrr pass's; any other reason leaves the slot
        # on Open-Meteo, whose GRIB patch the airport-profile sweep warms.
        return _Outcome("skipped", reason=f"gfs_{reason}")

    session = _grib_session()
    run_info = find_latest_run(flight.departure_time, session=session)
    if run_info is None:
        return _Outcome("skipped", reason="no_run")
    init_date, init_hour = run_info
    fhours = sorted(compute_flight_window_hours(
        init_date, init_hour, flight.departure_time, flight.duration_hours,
    ))
    if not fhours:
        return _Outcome("skipped", reason="no_hours")
    levels = gfs_sounding_levels()
    lead = _gfs_previous_step(fhours[0])
    steps = ([lead] if lead is not None and lead not in fhours else []) + fhours
    run = f"{init_date}_{init_hour:02d}z"
    run_dir = cache_dir_for_run(data_dir, init_date, init_hour, model="gfs")

    def _wanted(fhour: int) -> list[str]:
        wanted = [cache_key(fhour, GFS_SURFACE_CACHE_KEY)]
        if fhour in fhours:
            wanted += [
                _gfs_sounding_cache_key(fhour, levels),
                cache_key(fhour, "CLOUD_DIAG"),
            ]
        return wanted

    def _hour_warm(fhour: int) -> bool:
        return all(is_cached(run_dir, ck) for ck in _wanted(fhour))

    with pin_run_dir(run_dir):
        for fhour in steps:
            window = fhour in fhours
            if _hour_warm(fhour):
                continue
            # Only ahead of a real download, as everywhere in the warm: a
            # resumption pass must fast-forward through the warmed prefix.
            if stop():
                # Only the hours that landed: the stats line's distinct hours
                # is what explains the byte spend.
                return _Outcome(
                    "stopped", hours={(run, f) for f in fhours if _hour_warm(f)},
                )
            idx_by_fhour: dict[int, str] = {}
            try:
                idx_by_fhour[fhour] = fetch_idx(init_date, init_hour, fhour, session=session)
            except Exception:
                logger.warning(
                    "Flight warm gfs: .idx f%03d unavailable", fhour, exc_info=True,
                )
                continue
            # Each helper logs its own failure and leaves the hour cold.
            _ensure_gfs_surface(init_date, init_hour, fhour, run_dir, idx_by_fhour, session)
            if window:
                _ensure_gfs_sounding(
                    init_date, init_hour, fhour, run_dir, idx_by_fhour, session, levels,
                )
                _ensure_gfs_cloud_diag(
                    init_date, init_hour, fhour, run_dir, idx_by_fhour, session,
                )
        # The helpers log and carry on, so check what actually landed: a
        # NOAA outage must read as failed in the stats line, not as warmed.
        cold = [f for f in steps if not _hour_warm(f)]
    hours = {(run, f) for f in fhours}
    if cold:
        return _Outcome("failed", hours=hours, reason=f"{len(cold)}/{len(steps)} steps cold")
    return _Outcome("warmed", hours=hours)


def _warm_hrrr(
    flight: _Flight, data_dir: Path, stop: Callable[[], bool],
) -> _Outcome:
    """HRRR sounding + cloud diagnostics for a route the upgrade takes (#457)."""
    from weatherbrief.fetch.grib import (
        _download_hrrr_cloud_diag,
        _download_hrrr_sounding,
        _grib_session,
    )
    from weatherbrief.fetch.grib.cache import (
        cache_dir_for_run,
        cache_key,
        is_cached,
        pin_run_dir,
    )
    from weatherbrief.fetch.grib.hrrr_fetch import (
        compute_hrrr_flight_window_hours,
        fetch_hrrr_idx,
        find_latest_hrrr_run,
    )
    from weatherbrief.fetch.grib.primary import _hrrr_takes_route

    if not _hrrr_takes_route(
        flight.route_points, flight.departure_time, flight.duration_hours, None,
    ):
        return _Outcome("skipped", reason="not_hrrr_route")

    session = _grib_session()
    run_info = find_latest_hrrr_run(
        flight.departure_time, session=session,
        cover_until=flight.departure_time + timedelta(hours=flight.duration_hours),
    )
    if run_info is None:
        return _Outcome("skipped", reason="no_run")
    init_date, init_hour = run_info
    fhours = compute_hrrr_flight_window_hours(
        init_date, init_hour, flight.departure_time, flight.duration_hours,
    )
    run = f"{init_date}_{init_hour:02d}z"
    run_dir = cache_dir_for_run(data_dir, init_date, init_hour, model="hrrr")

    with pin_run_dir(run_dir):
        for fhour in fhours:
            snd_ck = cache_key(fhour, "HRRR_SOUNDING")
            diag_ck = cache_key(fhour, "HRRR_CLOUD_DIAG")
            if is_cached(run_dir, snd_ck) and is_cached(run_dir, diag_ck):
                continue
            if stop():
                return _Outcome("stopped", hours={
                    (run, f) for f in fhours
                    if is_cached(run_dir, cache_key(f, "HRRR_SOUNDING"))
                })
            try:
                idx = fetch_hrrr_idx(init_date, init_hour, fhour, session=session)
                if not is_cached(run_dir, snd_ck):
                    _download_hrrr_sounding(
                        init_date, init_hour, fhour, idx, run_dir=run_dir, session=session,
                    )
                if not is_cached(run_dir, diag_ck):
                    _download_hrrr_cloud_diag(
                        init_date, init_hour, fhour, idx, run_dir=run_dir, session=session,
                    )
            except Exception:
                # The briefing would fail this slot over to GFS: nothing more
                # to warm for this hour.
                logger.warning(
                    "Flight warm hrrr: f%02d failed", fhour, exc_info=True,
                )
        # The sounding is what the briefing needs; diagnostics are optional.
        cold = [f for f in fhours if not is_cached(run_dir, cache_key(f, "HRRR_SOUNDING"))]
    hours = {(run, f) for f in fhours}
    if cold:
        return _Outcome("failed", hours=hours, reason=f"{len(cold)}/{len(fhours)} hours cold")
    return _Outcome("warmed", hours=hours)


# The briefing computes alternates for flights up to this many calendar days
# out (pipeline: ``days_out <= 2``).
ALTERNATES_MAX_DAYS_OUT = 2


def _warm_icon_alternates(
    source: str,
    init: datetime,
    flight: _Flight,
    data_dir: Path,
    stop: Callable[[], bool],
    *,
    db_path: str,
    now: datetime,
) -> _Outcome:
    """The alternates search's ETA-hour ICON files, download only (#929).

    The search (``alternates._fetch_eta_snapshots``) reads ICON at the
    destination ETA hour for the destination and its candidates: the cloud
    diagnostics (ICON-EU always, ICON-D2 for the airports D2 serves) and,
    GRIB-primary (#914), the snapshot files of each variant, as
    ``icon_snapshot_fetch_plan`` splits them. The files are whole-grid per
    step, so a few flights arriving in the same hour share them. The ICON-EU
    pass warms the EU files, the ICON-D2 pass the D2 files, each on its own
    fresh run; the search reads that run once Open-Meteo's ICON init
    reaches it.
    """
    from weatherbrief.fetch.grib.cache import cache_dir_for_run, pin_run_dir
    from weatherbrief.fetch.grib.icon_eu_fetch import ICON_D2, ICON_EU
    from weatherbrief.fetch.grib.primary import grib_snapshot_models
    from weatherbrief.tasks.alternates import _eta_hour, alternates_fetch_airports
    from weatherbrief.tasks.standalone_grib import (
        datetime_to_init_parts,
        ensure_icon_cloud_diag_files,
        ensure_icon_snapshot_files,
        icon_cloud_diag_cold_steps,
        icon_snapshot_cold_steps,
    )
    from weatherbrief.tasks.standalone_verification import (
        grib_diag_steps,
        icon_snapshot_fetch_plan,
    )

    if flight.route is None or not db_path:
        return _Outcome("skipped", reason="alternates_no_route")
    days_out = (
        flight.departure_time.astimezone(timezone.utc).date()
        - now.astimezone(timezone.utc).date()
    ).days
    if days_out > ALTERNATES_MAX_DAYS_OUT:
        return _Outcome("skipped", reason="alternates_out_of_range")
    init_time = _aware(init)
    eta = _eta_hour(flight.departure_time, flight.duration_hours)
    if eta < init_time:
        return _Outcome("skipped", reason="alternates_eta_before_run")

    grib_snapshots = "icon" in grib_snapshot_models()
    # Planned on this pass's run. The search plans on Open-Meteo's ICON init,
    # one init for both variants; once that init reaches this run the D2
    # mask, the D2 horizon and so the split are the same.
    plan = (
        icon_snapshot_fetch_plan(
            init_time, alternates_fetch_airports(flight.route, db_path), [eta],
        )
        if grib_snapshots else None
    )
    # (variant, "snapshot" | "diag", steps)
    units: list[tuple[object, str, list[int]]] = []
    if source == "icon-eu":
        diag_steps, _ = grib_diag_steps("icon", init_time, [eta])
        units.append((ICON_EU, "diag", diag_steps))
        if plan is not None:
            units.append((ICON_EU, "snapshot", plan.eu_first))
    elif plan is not None:
        d2_steps = sorted(set(plan.d2_steps.values()))
        units += [(ICON_D2, "snapshot", d2_steps), (ICON_D2, "diag", d2_steps)]
    units = [u for u in units if u[2]]
    if not units:
        return _Outcome("skipped", reason="alternates_nothing")

    init_date, init_hour = datetime_to_init_parts(init_time)
    run = f"{init_date}_{init_hour:02d}z"
    hours: set[tuple[str, int]] = set()
    failed: list[str] = []
    for variant, kind, steps in units:
        run_dir = cache_dir_for_run(data_dir, init_date, init_hour, model=variant.slug)

        def _cold(run_dir=run_dir, kind=kind, steps=steps, variant=variant) -> list[int]:
            if kind == "diag":
                return icon_cloud_diag_cold_steps(run_dir, steps, variant)
            return icon_snapshot_cold_steps(run_dir, init_date, init_hour, steps, variant)

        if _cold():
            # Only ahead of a real download, as everywhere in the warm.
            if stop():
                return _Outcome("stopped", hours=hours)
            ensure = (
                ensure_icon_cloud_diag_files if kind == "diag" else ensure_icon_snapshot_files
            )
            with pin_run_dir(run_dir):
                ensure(init_date, init_hour, steps, variant=variant, data_dir=data_dir)
            # The helpers log and carry on: check what landed, so a DWD outage
            # reads as failed in the stats line (as for GFS and HRRR).
            cold = _cold()
            if cold:
                failed.append(f"{variant.slug} {kind} {cold}")
            landed = [f for f in steps if f not in cold]
        else:
            landed = steps
        hours |= {(f"{run} alternates {variant.slug} {kind}", f) for f in landed}
    if failed:
        return _Outcome("failed", hours=hours, reason="alternates cold: " + "; ".join(failed))
    return _Outcome("warmed", hours=hours)


def _merge(briefing: _Outcome, alternates: _Outcome) -> _Outcome:
    """One flight's outcome from its briefing warm and its alternates warm."""
    hours = briefing.hours | alternates.hours
    for part in (alternates, briefing):
        if part.status in ("stopped", "failed"):
            return _Outcome(part.status, hours=hours, reason=part.reason)
    if briefing.status == "skipped" and alternates.status == "skipped":
        return briefing
    return _Outcome("warmed", hours=hours)


def _warm_unit(
    source: str,
    init: datetime | None = None,
    *,
    db_path: str = "",
    now: datetime | None = None,
):
    if source in ("icon-d2", "icon-eu"):
        def _icon(flight, data_dir, stop):
            out = _warm_icon(source, flight, data_dir, stop)
            if out.status == "stopped" or init is None:
                return out
            try:
                alternates = _warm_icon_alternates(
                    source, init, flight, data_dir, stop,
                    db_path=db_path, now=now or datetime.now(timezone.utc),
                )
            except Exception:
                # The briefing's files are cached: an alternates failure must
                # not count the flight as failed.
                logger.warning(
                    "Flight warm %s: alternates failed for flight %s",
                    source, flight.id, exc_info=True,
                )
                return out
            return _merge(out, alternates)
        return _icon
    if source == "gfs":
        return _warm_gfs
    if source == "hrrr":
        return _warm_hrrr
    raise ValueError(f"unknown warm source {source!r}")


# --- The pass ------------------------------------------------------------------


def _upcoming_rows(reference: datetime, horizon_end: datetime) -> list:
    from flyfun_common.db import SessionLocal
    from sqlalchemy import select

    from weatherbrief.db.models import FlightRow

    db = SessionLocal()
    try:
        # Soonest departure first. A pass is not instantaneous and the yield
        # gate can defer it repeatedly under briefing traffic (the 09z D2 run
        # on 2026-08-20 took six attempts, 11:03Z-11:29Z), so for tens of
        # minutes only a prefix of the flights is warm; and every limit cuts
        # from the tail. Departure order puts today's flights at the front.
        return db.execute(
            select(FlightRow)
            .where(FlightRow.departure_time >= reference)
            .where(FlightRow.departure_time <= horizon_end)
            .order_by(FlightRow.departure_time)
        ).scalars().all()
    finally:
        db.close()


def _to_flight(row, db_path: str) -> _Flight | None:
    from weatherbrief.api.packs import _build_route_config
    from weatherbrief.fetch.route_points import interpolate_route
    from weatherbrief.storage.flights import _row_to_flight

    flight = _row_to_flight(row)
    route = _build_route_config(flight, db_path)
    route_points = interpolate_route(route, spacing_nm=10.0)
    if not route_points:
        return None
    return _Flight(
        id=str(getattr(flight, "id", getattr(row, "id", "?"))),
        departure_time=_aware(flight.departure_time),
        duration_hours=route.flight_duration_hours,
        route_points=route_points,
        route=route,
    )


def warm_flights(
    source: str,
    init: datetime,
    db_path: str,
    *,
    now: datetime | None = None,
    should_defer: Callable[[], bool] | None = None,
    carried_bytes: int = 0,
    carried_seconds: float = 0.0,
) -> dict:
    """Warm ``source``'s GRIB cache for the flights departing within its horizon.

    ``init`` is the freshly-published run that triggered the pass (logging
    only: each flight's run is chosen by the briefing's own run finder, so the
    warm matches the briefing). ``db_path`` is the airports DB for waypoint
    resolution; empty means no-op.

    ``carried_bytes`` / ``carried_seconds`` are what earlier, deferred
    attempts at the same run already spent, so a pass resumed after a #490
    deferral stays inside one budget.

    Returns stats: ``flights_considered`` (in the horizon), ``flights_warmed``,
    ``flights_skipped`` (another source serves them, or nothing to fetch),
    ``flights_failed`` (raised, or downloads left hours cold), ``flights_cut`` with ``cut_by_flights`` /
    ``cut_by_bytes`` / ``cut_by_time`` and ``cut_ids``, ``distinct_hours``,
    ``bytes_downloaded`` and ``wall_seconds`` (both including the carry),
    ``deferred`` (1: yielded to a refresh, resume next tick),
    ``low_disk`` (1: did not start, free disk under the floor) and
    ``disabled`` (1: horizon is 0, the source is off; every other stat is 0).
    """
    from weatherbrief.fetch.grib.cache import cache_write_meter
    from weatherbrief.fetch.grib.precache import _data_dir, _yield_gate

    stats: dict = {
        "flights_considered": 0, "flights_warmed": 0, "flights_skipped": 0,
        "flights_failed": 0, "flights_cut": 0,
        "cut_by_flights": 0, "cut_by_bytes": 0, "cut_by_time": 0, "cut_ids": [],
        "distinct_hours": 0, "bytes_downloaded": carried_bytes,
        "wall_seconds": round(carried_seconds, 1),
        "deferred": 0, "low_disk": 0, "disabled": 0,
    }
    label = f"Flight warm {source} (run {init.strftime('%Y%m%d_%Hz')})"
    if not db_path:
        logger.warning("%s: AIRPORTS_DB not configured, skipping", label)
        return stats

    limits = warm_limits(source)
    if limits.horizon_hours <= 0:
        stats["disabled"] = 1
        logger.debug("%s: off (horizon 0)", label)
        return stats
    data_dir = _data_dir()
    free = free_disk_bytes(data_dir)
    if free is not None and free < limits.min_free_bytes:
        stats["low_disk"] = 1
        repeat = _low_disk_warned.get(source) == label
        _low_disk_warned[source] = label
        (logger.debug if repeat else logger.warning)(
            "%s: skipped, %.1f GB free on the data volume (floor %.0f GB)",
            label, free / _GB, limits.min_free_bytes / _GB,
        )
        return stats
    _low_disk_warned.pop(source, None)

    gate = _yield_gate(should_defer)
    reference = now or datetime.now(timezone.utc)
    unit = _warm_unit(source, init, db_path=db_path, now=reference)
    rows = _upcoming_rows(reference, reference + timedelta(hours=limits.horizon_hours))
    stats["flights_considered"] = len(rows)

    started = time.monotonic()
    hours: set[tuple[str, int]] = set()
    stop_reason: list[str] = []

    with cache_write_meter() as meter:
        def _spent_bytes() -> int:
            return carried_bytes + meter.bytes_written

        def _spent_seconds() -> float:
            return carried_seconds + time.monotonic() - started

        def _limit_hit() -> str | None:
            if limits.max_bytes is not None and _spent_bytes() >= limits.max_bytes:
                return CUT_BYTES
            if _spent_seconds() >= limits.max_seconds:
                return CUT_TIME
            return None

        def _stop() -> bool:
            # Limits first: a pass that is out of budget is finished, not
            # deferred, even while a refresh is running.
            why = _limit_hit() or (DEFERRED if gate() else None)
            if why is not None:
                stop_reason[:] = [why]
                return True
            return False

        def _cut(row, why: str) -> None:
            stats["flights_cut"] += 1
            stats[f"cut_by_{why}"] += 1
            stats["cut_ids"].append(str(getattr(row, "id", "?")))

        # The flight cap counts only flights this source actually works on:
        # in a 72 h window most rows belong to another source (a D2 flight on
        # the EU pass, a non-US route on the HRRR pass), and spending the cap
        # on them would cut this source's own flights (PR #931 review).
        attempted = 0
        for i, row in enumerate(rows):
            if attempted >= limits.max_flights:
                _cut(row, CUT_FLIGHTS)
                continue
            if stop_reason:
                _cut(row, stop_reason[0])
                continue
            why = _limit_hit()
            if why is not None:
                stop_reason[:] = [why]
                _cut(row, why)
                continue
            try:
                flight = _to_flight(row, db_path)
                if flight is None:
                    stats["flights_skipped"] += 1
                    continue
                outcome = unit(flight, data_dir, _stop)
            except Exception:
                stats["flights_failed"] += 1
                logger.warning(
                    "%s failed for flight %s", label, getattr(row, "id", "?"),
                    exc_info=True,
                )
                continue
            if outcome.status == "skipped":
                stats["flights_skipped"] += 1
                logger.debug(
                    "%s: flight %s skipped (%s)", label, flight.id, outcome.reason,
                )
                continue
            attempted += 1
            hours |= outcome.hours
            if outcome.status == "warmed":
                stats["flights_warmed"] += 1
                continue
            if outcome.status == "failed":
                stats["flights_failed"] += 1
                logger.warning(
                    "%s: flight %s not fully warmed (%s)", label, flight.id, outcome.reason,
                )
                continue
            # Stopped mid-flight. Its finished units are cached; it counts as
            # neither warmed nor (when deferred) cut. A stop with no recorded
            # reason is treated as a deferral: resuming next tick is the safe
            # side.
            if not stop_reason or stop_reason[0] == DEFERRED:
                stats["deferred"] = 1
                logger.info(
                    "%s deferring to an active briefing refresh after %d/%d "
                    "flights fully warmed (the current one may be partly "
                    "warmed, %d not started) — will resume next tick",
                    label, stats["flights_warmed"], len(rows), len(rows) - i - 1,
                )
                break
            _cut(row, stop_reason[0])

        stats["bytes_downloaded"] = _spent_bytes()
        stats["wall_seconds"] = round(_spent_seconds(), 1)
    stats["distinct_hours"] = len(hours)

    cut_note = ""
    if stats["flights_cut"]:
        cut_note = (
            f"; cut {stats['flights_cut']} (flights={stats['cut_by_flights']} "
            f"bytes={stats['cut_by_bytes']} time={stats['cut_by_time']}): "
            f"{', '.join(stats['cut_ids'])}"
        )
    budget = (
        f"{limits.max_bytes / _GB:.0f} GB" if limits.max_bytes is not None else "uncapped"
    )
    # A cut means flights brief cold: WARNING with a fixed token so repeated
    # cuts (a budget too tight for the traffic) can be alerted on.
    log = logger.warning if stats["flights_cut"] else logger.info
    log(
        "%s%s: considered %d, warmed %d, skipped %d, failed %d, cut %d; "
        "%d distinct hours, %.2f GB new (budget %s), %.0f s%s%s",
        "FLIGHT_WARM_CUT " if stats["flights_cut"] else "",
        label, stats["flights_considered"], stats["flights_warmed"],
        stats["flights_skipped"], stats["flights_failed"], stats["flights_cut"],
        stats["distinct_hours"], stats["bytes_downloaded"] / _GB, budget,
        stats["wall_seconds"], " (deferred)" if stats["deferred"] else "", cut_note,
    )
    return stats
