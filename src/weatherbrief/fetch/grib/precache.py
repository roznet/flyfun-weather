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
from datetime import datetime, timedelta
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
# So: between units of work the warm pass asks whether the refresh registry has
# anything queued or running, and bails out if so. The caller must NOT record
# the run as done when a pass bails — see `precache_*` returning ``deferred``.
#
# A "unit" is one download everywhere: per (forecast hour, variable) for the EU
# airport sweep and for D2 flights, per forecast hour for GFS. D2 was the
# exception until #501 — it checked once per FLIGHT, so a pass that had just
# entered a flight stayed resident for that flight's whole ~80 s job list, and
# on 2026-07-26 one such overlap took the container to 5999/6144 MB. The gate
# only helps to the extent it can fire promptly, so its granularity has to
# match the granularity of the work.

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


def icon_eu_profile_forecast_hours(init: datetime) -> list[int]:
    """``airport_profile_forecast_hours`` snapped to ICON-EU's publication grid.

    The raw offsets come from wall-clock target hours (06-21 Z per day), which
    are hourly by construction. ICON-EU only publishes hourly to 78 h and
    3-hourly beyond, so feeding the raw list to the fetcher asks DWD for files
    that do not exist — every off-grid hour 404s on all 40 levels, is never
    cached, and is therefore retried on every subsequent tick.

    Snapping is not just a noise fix: it shrinks the day-3 span from 16 hourly
    offsets to the 6 coarse steps that exist, and those are exactly the hours
    a briefing asks for (the briefing path snaps via
    :func:`~weatherbrief.fetch.grib.icon_eu_fetch.bracket_icon_eu_forecast_hours`
    before fetching). GFS is hourly to 120 h, so its precache keeps the raw
    list — this is why the snap lives here and not in the shared helper.
    """
    from weatherbrief.fetch.grib.icon_eu_fetch import icon_eu_snap_forecast_hours

    return icon_eu_snap_forecast_hours(airport_profile_forecast_hours(init))


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
    :func:`interactive_refresh_active`) is polled before each real download
    (per variable, and before the cloud-diag fetch) — never ahead of a free
    ``is_cached`` hit, so a resumption pass fast-forwards through the warmed
    prefix even during a refresh burst. A True answer stops the pass with
    ``deferred=1`` in the stats. Callers must then leave the run un-recorded
    so the next tick resumes it.
    """
    from weatherbrief.fetch.grib import DecodePriority, _grib_session
    from weatherbrief.fetch.grib.icon_eu_fetch import (
        ICON_EU_MODEL_LEVEL_MAX,
        ICON_EU_MODEL_LEVEL_MIN,
        fetch_icon_eu_per_variable,
        fetch_icon_eu_single_level,
    )
    from weatherbrief.fetch.grib.icon_zarr import (
        cache_model_level_blob,
        cached_model_level_entry,
    )

    init_date = init.strftime("%Y%m%d")
    init_hour = init.hour
    forecast_hours = icon_eu_profile_forecast_hours(init)
    levels = list(range(ICON_EU_MODEL_LEVEL_MIN, ICON_EU_MODEL_LEVEL_MAX + 1))

    data_dir = _data_dir()
    run_dir = cache_dir_for_run(data_dir, init_date, init_hour, model="icon-eu")
    session = _grib_session()

    gate = _yield_gate(should_defer)
    deferred = False

    hours_fetched = 0
    vars_fetched = 0
    bytes_downloaded = 0

    # The yield gate is deliberately checked only where a real download is
    # about to start, never ahead of an `is_cached` hit-check: cache hits are
    # free, and a resumption pass must be able to fast-forward through the
    # already-warmed prefix even while a refresh is active — otherwise a
    # sustained burst (the 05Z scenario) pins the pass at unit 0 every tick
    # and `last_done` starves (PR #498 review).
    for fhour in forecast_hours:
        # Legacy combined cache (briefings on older code paths) → counts as covered
        if is_cached(run_dir, cache_key(fhour, "ICON_EU_QC_QI_P")):
            hours_fetched += 1
            continue

        for var in ICON_EU_PRECACHE_VARS:
            ck = cache_key(fhour, f"ICON_EU_{var.upper()}")
            if cached_model_level_entry(run_dir, ck) is not None:
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
                    # This pass runs ahead of DWD's publication frontier by
                    # design, so an all-404 hour means "not landed yet" and is
                    # retried next tick. Only 404s are quieted; a 500 or a
                    # timeout still warns.
                    expect_missing=True,
                )
                data = per_var.get(var)
                if data:
                    # Converted to zarr on write (#705); the warm loop is
                    # thus where most conversion CPU is spent, so it goes to
                    # the pool at the lowest priority (this loop sets none).
                    cache_model_level_blob(
                        run_dir, ck, data, priority=DecodePriority.BACKGROUND,
                    )
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
            if gate():
                deferred = True
                break
            try:
                fetched = fetch_icon_eu_single_level(
                    init_date, init_hour, [fhour], session=session,
                    max_workers=5,
                    expect_missing=True,
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
# maps), the flight warm fetches ONLY what we already know will be asked for:
# the routes and window hours of flights actually in the DB. It reuses the
# briefing's own prepare + prefetch, so the warmed cache is byte-for-byte what
# an on-demand briefing would fetch, giving a near-100% hit rate instead of the
# ~20% a broad daylight precache would (the rejected option in #469). #929
# generalised it to every GRIB source a briefing uses: see ``flight_warm``.


def precache_icon_d2_flights(
    init: datetime,
    db_path: str,
    *,
    now: datetime | None = None,
    should_defer: Callable[[], bool] | None = None,
) -> dict:
    """Warm the ICON-D2 cache for upcoming flights (``warm_flights("icon-d2")``)."""
    from weatherbrief.fetch.grib.flight_warm import warm_flights

    return warm_flights(
        "icon-d2", init, db_path, now=now, should_defer=should_defer,
    )


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
        clwmr_ck = cache_key(fhour, "CLWMR_ICMR")
        diag_ck = cache_key(fhour, "CLOUD_DIAG")
        clwmr_hit = is_cached(run_dir, clwmr_ck)
        diag_hit = is_cached(run_dir, diag_ck)
        if clwmr_hit and diag_hit:
            hours_fetched += 1
            continue

        # Gate only when a real fetch is pending, after the free hit-check —
        # a resumption pass must fast-forward through the warmed prefix even
        # while a refresh is active (PR #498 review).
        if gate():
            deferred = True
            break

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
