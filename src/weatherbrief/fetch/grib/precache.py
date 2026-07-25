"""Pre-cache ICON-EU / GFS GRIB byte ranges for airport-profile selectables.

Reuses the existing per-flight fetch primitives — same disk cache layout,
same dedup, same cleanup. The only new logic is the iteration over
(forecast hour × variable) combos that cover the ``/maps.html`` D-0..D-3
control range. The byte-range cache is shared with the flight-driven path,
so a warmed run also speeds up briefings whose flight window overlaps.

See issue #126 for the rationale + expected per-pass cost (~32 GB ICON-EU,
~6 GB GFS over the 64 unique forecast hours).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from pathlib import Path

from weatherbrief.fetch.grib.cache import (
    cache_dir_for_run,
    cache_key,
    is_cached,
    put_cached,
)
from weatherbrief.fetch.grib.icon_eu_fetch import (
    ICON_EU_CLOUD_DIAG_CACHE_KEY,
    ICON_EU_VARIABLES as ICON_EU_PRECACHE_VARS,
)

logger = logging.getLogger(__name__)


# /maps.html Forecast controls offer hour-options 06/09/12/15/18 Z, each
# spawning a 4-hour window. The union deduplicates to hours 06–21 Z per day.
PRECACHE_FORECAST_HOURS_PER_DAY = (
    6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21,
)

# Forecast controls cover D-0 through D-3.
PRECACHE_DAYS_AHEAD = 4

# Both ICON-EU main cycles and GFS main cycles publish to 120 h; cap there.
PRECACHE_MAX_FORECAST_HOUR = 120

# Only main cycles can cover D-3 (short cycles top out at 30 h horizon).
MAIN_CYCLE_HOURS = (0, 6, 12, 18)


# --- Wall-clock warming window (issue #475) --------------------------------
#
# No user refreshes a flight in the middle of the night, so overnight warming
# passes are wasted DWD bandwidth. Gate on the wall-clock time the warming pass
# runs (NOT the run's init hour) with a per-model window ``[start, end)`` in UTC
# hours; a model absent from the map is ungated.
#
# Default 03Z-21Z for the two DWD models. D2 warming fires at 02/05/08/11/14/
# 17/20/23 UTC (publish_delay 2 h after the 3-hourly inits); [3, 21) keeps the
# six daytime passes (05..20). The ICON-EU airport precache publishes at
# 03/09/15/21 UTC; [3, 21) drops the 21Z pass. Any window from 03Z to 20Z
# inclusive yields the same daytime D2 passes — 21Z is the endpoint for margin,
# not because it's tight (the nearest passes outside are 23Z / 02Z).
#
# Only ONE D2 pass per day is actually dropped, not two, so the real saving is
# ~13% of forecast-hours (#475 first estimated 24% by assuming both night
# passes vanish). The 23Z pass is genuinely lost: by the time the window opens
# at 03Z its run (21z) has been superseded. The 02Z pass is *deferred, not
# dropped* — ``should_warm`` leaves ``last_done`` untouched at 02Z, and at 03Z
# the same 00z run is still the freshest (03z doesn't publish until 05Z), so it
# is warmed then. That is the intended behaviour and worth keeping: prod has
# 05Z departures whose pilots check around 03-04Z, and they should find a warm
# cache on the freshest run available.
#
# GFS is left ungated on purpose: it's the cheapest model (~1.5 GB/run) so
# gating saves least, and it is the one thing US expansion would need running
# around the clock. Per-model config means US expansion is a config change, not
# a code change.
MODEL_WARMING_WINDOW_UTC: dict[str, tuple[int, int]] = {
    "icon-d2": (3, 21),
    "icon-eu": (3, 21),
}


def is_within_warming_window(model: str, now: datetime) -> bool:
    """Whether ``model``'s warming may run at wall-clock ``now`` (UTC).

    Models absent from :data:`MODEL_WARMING_WINDOW_UTC` are ungated (GFS).
    """
    window = MODEL_WARMING_WINDOW_UTC.get(model)
    if window is None:
        return True
    start, end = window
    return start <= now.hour < end


def should_warm(
    model: str,
    run_key: str,
    last_key: str | None,
    now: datetime,
) -> bool:
    """Decide whether to warm ``run_key`` for ``model`` at ``now``.

    True only when the run hasn't already been warmed AND ``now`` is inside the
    model's warming window. A pass outside the window is *skipped, not
    deferred*: the caller must NOT record ``run_key`` as done, so nothing is
    backfilled — the next in-window tick simply warms whatever run is freshest
    then (a newer ``run_key``), never replaying the skipped overnight run.
    """
    if last_key == run_key:
        return False
    return is_within_warming_window(model, now)


# --- Yielding to interactive briefings (issue #490) ------------------------
#
# Warming is discretionary and fully idempotent, so it costs nothing to stop
# mid-pass: the next 5-min tick resumes and every already-warmed combo is an
# `is_cached` skip. An interactive refresh, by contrast, cannot be paused —
# the user is watching it. On 2026-07-23 05:09Z a D2 flight warm overlapped a
# user's refresh, the cgroup (pinned near its 6 GiB limit by page cache from
# warm writes) had no reclaim headroom for the decode workers' anon spike, and
# the OOM killer took the container down mid-briefing.
#
# So: between units of work (per flight for D2, per forecast hour / variable
# for EU + GFS) the warm pass asks whether the refresh registry has anything
# queued or running, and bails out if so. The caller must NOT record the run as
# done when a pass bails — see `precache_*` returning ``deferred``.

#: Keep yielding for this long after the last refresh finishes. Memory freed by
#: a refresh isn't reclaimed the instant its registry entry disappears (decode
#: workers wind down, pages get written back), and users often fire a second
#: refresh right after the first — starting a 32 GB sweep into that tail is
#: exactly the collision this issue is about.
WARM_YIELD_COOLDOWN_SECONDS = 60.0


def interactive_refresh_active(
    cooldown_seconds: float = WARM_YIELD_COOLDOWN_SECONDS,
) -> bool:
    """Whether a briefing refresh is running (or just finished) right now.

    True while any refresh is queued/refreshing in
    :data:`weatherbrief.api.packs.refresh_registry`, and for
    ``cooldown_seconds`` after the last one finished. Imported lazily so the
    fetch layer keeps no import-time dependency on the API layer; if the API
    module can't be imported (scripts, tests) the answer is "no refresh".
    """
    try:
        from weatherbrief.api.packs import refresh_registry
    except Exception:  # pragma: no cover - defensive: no API layer available
        return False
    idle = refresh_registry.idle_seconds()
    if idle is None:
        return False  # no refresh has run since process start
    if idle == 0.0:
        return True  # queued or running right now — always yield
    return idle < cooldown_seconds


def _yield_gate(
    should_defer: Callable[[], bool] | None,
) -> Callable[[], bool]:
    """Resolve the caller-supplied yield predicate to the default gate."""
    return interactive_refresh_active if should_defer is None else should_defer


def airport_profile_forecast_hours(init: datetime) -> list[int]:
    """Forecast-hour offsets (from ``init``) covering D-0..D-3 selectables.

    Returns sorted unique offsets within the 120 h main-cycle horizon. Hours
    in the past relative to the run init are skipped (a 12 Z run can't supply
    a 06 Z target on the same day).
    """
    hours: set[int] = set()
    for day_offset in range(PRECACHE_DAYS_AHEAD):
        for utc_hour in PRECACHE_FORECAST_HOURS_PER_DAY:
            target = (init + timedelta(days=day_offset)).replace(
                hour=utc_hour, minute=0, second=0, microsecond=0,
            )
            if target < init:
                continue
            offset = int((target - init).total_seconds() // 3600)
            if offset > PRECACHE_MAX_FORECAST_HOUR:
                continue
            hours.add(offset)
    return sorted(hours)


def _data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR", "data"))


def precache_icon_eu_run(
    init: datetime,
    *,
    should_defer: Callable[[], bool] | None = None,
) -> dict[str, int]:
    """Fetch every (var × forecast_hour) for D-0..D-3 from the given run.

    Idempotent: skips combos already on disk. Bytes land in the shared
    byte-range cache (``{data_dir}/.cache/grib/icon-eu/{run}/...``) so a
    flight briefing landing in the same window will hit the warm cache.

    Yields to interactive briefings: ``should_defer`` (default
    :func:`interactive_refresh_active`) is polled before each variable
    download, and a True answer stops the pass with ``deferred=1`` in the
    stats. Callers must then leave the run un-recorded so the next tick
    resumes it.
    """
    from weatherbrief.fetch.grib import _grib_session
    from weatherbrief.fetch.grib.icon_eu_fetch import (
        ICON_EU_MODEL_LEVEL_MAX,
        ICON_EU_MODEL_LEVEL_MIN,
        fetch_icon_eu_per_variable,
        fetch_icon_eu_single_level,
    )

    init_date = init.strftime("%Y%m%d")
    init_hour = init.hour
    forecast_hours = airport_profile_forecast_hours(init)
    levels = list(range(ICON_EU_MODEL_LEVEL_MIN, ICON_EU_MODEL_LEVEL_MAX + 1))

    data_dir = _data_dir()
    run_dir = cache_dir_for_run(data_dir, init_date, init_hour, model="icon-eu")
    session = _grib_session()

    gate = _yield_gate(should_defer)
    deferred = False

    hours_fetched = 0
    vars_fetched = 0
    bytes_downloaded = 0

    for fhour in forecast_hours:
        if gate():
            deferred = True
            break

        # Legacy combined cache (briefings on older code paths) → counts as covered
        if is_cached(run_dir, cache_key(fhour, "ICON_EU_QC_QI_P")):
            hours_fetched += 1
            continue

        for var in ICON_EU_PRECACHE_VARS:
            ck = cache_key(fhour, f"ICON_EU_{var.upper()}")
            if is_cached(run_dir, ck):
                continue
            # Per-variable, not just per-hour: one hour is 9 downloads, and a
            # refresh that arrives mid-hour shouldn't wait for all of them.
            if gate():
                deferred = True
                break
            try:
                # Gentle worker count: the airport-profile sweep is the least
                # urgent GRIB consumer, so it gets a fraction of the download
                # pool a briefing prefetch would use (05:09Z OOM, 2026-07-23).
                per_var = fetch_icon_eu_per_variable(
                    init_date, init_hour, fhour,
                    levels=levels, variables=[var], session=session,
                    max_workers=5,
                )
                data = per_var.get(var)
                if data:
                    put_cached(run_dir, ck, data)
                    vars_fetched += 1
                    bytes_downloaded += len(data)
            except Exception:
                logger.warning(
                    "Pre-cache ICON-EU f%03d %s failed", fhour, var, exc_info=True,
                )

        if deferred:
            break

        # Unlike the flight-briefing prefetch/enrichment paths, this warm-up
        # does NOT prepend the rain_con de-accumulation predecessor step (#421).
        # The airport-profile hour set is gappy across day boundaries, so a
        # single leading step wouldn't give parity anyway, and warming every
        # block's predecessor is not worth the complexity here: a briefing whose
        # window starts on a boundary hour just fetches that one predecessor
        # live in `_enrich_icon_eu_cloud_diagnostics`. The firing gate stays
        # missing-data-safe throughout — this is a warm-path gap, not a
        # correctness one.
        diag_ck = cache_key(fhour, ICON_EU_CLOUD_DIAG_CACHE_KEY)
        if not is_cached(run_dir, diag_ck):
            try:
                fetched = fetch_icon_eu_single_level(
                    init_date, init_hour, [fhour], session=session,
                    max_workers=5,
                )
                grib_bytes = fetched.get(fhour)
                if grib_bytes:
                    put_cached(run_dir, diag_ck, grib_bytes)
                    vars_fetched += 1
                    bytes_downloaded += len(grib_bytes)
            except Exception:
                logger.warning(
                    "Pre-cache ICON-EU cloud diag f%03d failed",
                    fhour, exc_info=True,
                )

        hours_fetched += 1

    if deferred:
        logger.info(
            "Pre-cache ICON-EU %s deferring to an active briefing refresh "
            "after %d/%d forecast hours — will resume next tick",
            init.strftime("%Y%m%d_%Hz"), hours_fetched, len(forecast_hours),
        )

    return {
        "hours_fetched": hours_fetched,
        "vars_fetched": vars_fetched,
        "bytes_downloaded": bytes_downloaded,
        "forecast_hours_total": len(forecast_hours),
        "deferred": int(deferred),
    }


# ICON-D2 flight warming (#469 phase 3) -----------------------------------
#
# Unlike the airport-profile precache above (a fixed D-0..D-3 grid for the
# maps), this warms ONLY what we already know will be asked for: the routes and
# window hours of flights actually in the DB for the next WARM_HORIZON_HOURS.
# It reuses the flight-briefing prepare+prefetch path verbatim
# (_prepare_icon_eu → _prefetch_icon_eu_data), so the warmed cache is
# byte-for-byte what an on-demand briefing would fetch — same run, same
# per-level files over the same full model column — giving a near-100% hit rate
# instead of the ~20% a broad daylight precache would (the rejected option in
# #469). Broad daylight precaching was 2–4× more DWD bandwidth than on-demand.

# D2's model-level horizon is 48h, so a flight beyond that can't be served by
# any single D2 run — no point warming it.
WARM_HORIZON_HOURS = 48


def precache_icon_d2_flights(
    init: datetime,
    db_path: str,
    *,
    now: datetime | None = None,
    should_defer: Callable[[], bool] | None = None,
) -> dict[str, int]:
    """Warm the ICON-D2 cache for flights departing within the next 48h.

    Called after a D2 run publishes. For each upcoming flight whose route fits
    the D2 domain and whose window a D2 run covers, this runs the SAME
    prepare+prefetch the briefing pipeline runs, so the cache it fills is
    exactly what the briefing will read. Flights served by ICON-EU (outside the
    D2 domain, or window beyond 48h) are skipped here — EU warming is the
    airport-profile precache's job.

    ``init`` is the freshly-published run (used only for logging; run selection
    goes through the normal freshest-run finder so it matches the briefing).
    ``db_path`` is the airports DB for waypoint resolution; empty → no-op.

    Yields to interactive briefings between flights: ``should_defer`` (default
    :func:`interactive_refresh_active`) is polled before each flight, and a
    True answer stops the pass with ``deferred=1`` in the stats so the caller
    leaves the run un-recorded. The next tick re-walks the flight list from the
    start; flights already warmed cost only their cache hits.
    """
    from weatherbrief.fetch.grib import _prefetch_icon_eu_data, _prepare_icon_eu
    from weatherbrief.fetch.grib.icon_eu_fetch import ICON_D2
    from weatherbrief.fetch.route_points import interpolate_route
    from weatherbrief.models import ModelSource, RouteCrossSection

    stats = {
        "flights_considered": 0, "flights_warmed": 0, "flights_skipped": 0,
        "deferred": 0,
    }

    if not db_path:
        logger.warning("ICON-D2 flight warm: AIRPORTS_DB not configured, skipping")
        return stats

    gate = _yield_gate(should_defer)
    if gate():
        stats["deferred"] = 1
        logger.info(
            "ICON-D2 flight warm (run %s) deferring to an active briefing "
            "refresh before starting — will resume next tick",
            init.strftime("%Y%m%d_%Hz"),
        )
        return stats

    from flyfun_common.db import SessionLocal
    from sqlalchemy import select

    from weatherbrief.api.packs import _build_route_config
    from weatherbrief.db.models import FlightRow
    from weatherbrief.storage.flights import _row_to_flight

    reference = now or datetime.now(timezone.utc)
    horizon_end = reference + timedelta(hours=WARM_HORIZON_HOURS)
    data_dir = _data_dir()

    db = SessionLocal()
    try:
        rows = db.execute(
            select(FlightRow)
            .where(FlightRow.departure_time >= reference)
            .where(FlightRow.departure_time <= horizon_end)
        ).scalars().all()
    finally:
        db.close()

    for row in rows:
        # Between flights: one flight's prefetch is the unit of warm work that
        # can't be interrupted, so this is the finest granularity available
        # without reaching into the shared briefing prefetch path.
        if gate():
            stats["deferred"] = 1
            logger.info(
                "ICON-D2 flight warm (run %s) deferring to an active briefing "
                "refresh after %d/%d flights — will resume next tick",
                init.strftime("%Y%m%d_%Hz"),
                stats["flights_warmed"], len(rows),
            )
            break

        stats["flights_considered"] += 1
        try:
            flight = _row_to_flight(row)
            route = _build_route_config(flight, db_path)
            route_points = interpolate_route(route, spacing_nm=10.0)
            if not route_points:
                stats["flights_skipped"] += 1
                continue

            icon_section = RouteCrossSection(
                model=ModelSource.ICON,
                route_points=[],
                fetched_at=reference,
                point_forecasts=[],
            )
            ctx, _skip = _prepare_icon_eu(
                [icon_section], route_points, flight.departure_time,
                data_dir=data_dir,
                flight_duration_hours=route.flight_duration_hours,
            )
            # Only warm D2. An EU context means the route/window isn't D2-eligible
            # (or no covering D2 run) — the airport-profile precache warms EU.
            if ctx is None or ctx.variant is not ICON_D2:
                stats["flights_skipped"] += 1
                continue

            # Half the outer units (and half the connection pool, enforced by
            # the callee) of an interactive prefetch: warming is discretionary
            # and must never crowd out a concurrent briefing's memory or
            # connections (05:09Z OOM, 2026-07-23).
            _prefetch_icon_eu_data(ctx, outer_workers=2)
            stats["flights_warmed"] += 1
        except Exception:
            stats["flights_skipped"] += 1
            logger.warning(
                "ICON-D2 flight warm failed for flight %s",
                getattr(row, "id", "?"), exc_info=True,
            )

    logger.info(
        "ICON-D2 flight warm (run %s): %s",
        init.strftime("%Y%m%d_%Hz"), stats,
    )
    return stats


def precache_gfs_run(
    init: datetime,
    *,
    should_defer: Callable[[], bool] | None = None,
) -> dict[str, int]:
    """Pre-cache GFS CLWMR/ICMR + cloud diagnostics for D-0..D-3.

    Fetches the full pressure-level superset (``target_levels=None``) so any
    briefing's level subset will hit the cache.

    Yields to interactive briefings between forecast hours — see
    :func:`precache_icon_eu_run`.
    """
    from weatherbrief.fetch.grib import _grib_session
    from weatherbrief.fetch.grib.gfs_idx import (
        plan_byte_ranges,
        plan_cloud_diag_byte_ranges,
    )
    from weatherbrief.fetch.grib.grib_fetch import (
        fetch_byte_ranges,
        fetch_cloud_diag_ranges,
        fetch_idx,
    )

    init_date = init.strftime("%Y%m%d")
    init_hour = init.hour
    forecast_hours = airport_profile_forecast_hours(init)

    data_dir = _data_dir()
    run_dir = cache_dir_for_run(data_dir, init_date, init_hour, model="gfs")
    session = _grib_session()

    gate = _yield_gate(should_defer)
    deferred = False

    hours_fetched = 0
    vars_fetched = 0
    bytes_downloaded = 0

    for fhour in forecast_hours:
        if gate():
            deferred = True
            break

        clwmr_ck = cache_key(fhour, "CLWMR_ICMR")
        diag_ck = cache_key(fhour, "CLOUD_DIAG")
        clwmr_hit = is_cached(run_dir, clwmr_ck)
        diag_hit = is_cached(run_dir, diag_ck)
        if clwmr_hit and diag_hit:
            hours_fetched += 1
            continue

        try:
            idx_text = fetch_idx(init_date, init_hour, fhour, session=session)
        except Exception:
            logger.warning(
                "Pre-cache GFS .idx fetch failed f%03d", fhour, exc_info=True,
            )
            continue

        if not clwmr_hit:
            try:
                ranges = plan_byte_ranges(idx_text, target_levels=None)
                if ranges:
                    grib_bytes = fetch_byte_ranges(
                        init_date, init_hour, fhour, ranges, session=session,
                    )
                    if grib_bytes:
                        put_cached(run_dir, clwmr_ck, grib_bytes)
                        vars_fetched += 1
                        bytes_downloaded += len(grib_bytes)
            except Exception:
                logger.warning(
                    "Pre-cache GFS CLWMR/ICMR f%03d failed", fhour, exc_info=True,
                )

        if not diag_hit:
            try:
                ranges = plan_cloud_diag_byte_ranges(idx_text)
                if ranges:
                    grib_bytes = fetch_cloud_diag_ranges(
                        init_date, init_hour, fhour, ranges, session=session,
                    )
                    if grib_bytes:
                        put_cached(run_dir, diag_ck, grib_bytes)
                        vars_fetched += 1
                        bytes_downloaded += len(grib_bytes)
            except Exception:
                logger.warning(
                    "Pre-cache GFS cloud diag f%03d failed", fhour, exc_info=True,
                )

        hours_fetched += 1

    if deferred:
        logger.info(
            "Pre-cache GFS %s deferring to an active briefing refresh after "
            "%d/%d forecast hours — will resume next tick",
            init.strftime("%Y%m%d_%Hz"), hours_fetched, len(forecast_hours),
        )

    return {
        "hours_fetched": hours_fetched,
        "vars_fetched": vars_fetched,
        "bytes_downloaded": bytes_downloaded,
        "forecast_hours_total": len(forecast_hours),
        "deferred": int(deferred),
    }
