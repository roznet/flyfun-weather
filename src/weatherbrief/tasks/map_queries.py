"""Database queries for weather overview maps.

Two query types:
1. Forecast map — latest model forecasts at ~830 airports for a selected hour
2. Verification bias map — per-airport accuracy aggregations from verification scores
"""

from __future__ import annotations

import json
import logging
import threading
from collections import OrderedDict
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from weatherbrief.analysis.airport_consensus import (
    consensus as _shared_consensus,
    enrich_wind as _shared_enrich_wind,
    flight_category as _shared_flight_category,
    snap_to_dict as _shared_snap_to_dict,
)
from weatherbrief.analysis.alternate_requirement import (
    compute_easa_trigger,
    compute_faa_trigger,
    nwp_window,
    proxy_for_approach,
)
from weatherbrief.db.models import (
    AirportForecastSnapshotRow,
    VerificationCacheRow,
    VerificationObservationRow,
    VerificationScoreRow,
)
from weatherbrief.models.airport_conditions import RunwayEnd
from weatherbrief.models.alternate_requirement import TriggerVerdict
from weatherbrief.tasks.airport_watchlist import (
    WatchlistAirport,
    get_configs_dir,
    load_watchlist_with_coords,
)

logger = logging.getLogger(__name__)

# Models used in standalone verification
_MODELS = ["gfs", "icon", "ecmwf"]

# ---------------------------------------------------------------------------
# Watchlist coordinate cache (loaded once per process)
# ---------------------------------------------------------------------------

_coords_cache: dict[str, tuple[float, float]] | None = None
_runway_cache: dict[str, list[RunwayEnd]] | None = None


def _get_coords(airports_db_path: str) -> dict[str, tuple[float, float]]:
    """Return icao → (lat, lon) mapping, cached in-process.

    The watchlist config changes infrequently and triggers a process restart
    (Docker rebuild + deploy) when it does, so no TTL is needed.
    """
    global _coords_cache
    if _coords_cache is None:
        airports = load_watchlist_with_coords(get_configs_dir(), airports_db_path)
        _coords_cache = {a.icao: (a.lat, a.lon) for a in airports}
    return _coords_cache


def _get_runways(airports_db_path: str) -> dict[str, list[RunwayEnd]]:
    """Return icao → runway ends mapping, cached in-process."""
    global _runway_cache
    if _runway_cache is None:
        from weatherbrief.airports import get_runway_ends

        coords = _get_coords(airports_db_path)
        _runway_cache = get_runway_ends(list(coords.keys()), airports_db_path)
    return _runway_cache


_elevation_cache: dict[str, float | None] | None = None


def _get_elevations(airports_db_path: str) -> dict[str, float | None]:
    """Return icao → published field elevation (ft AMSL), cached in-process.

    Used to convert MSL model/sounding ceilings to AGL for flight-category and
    FAA/EASA alternate triggers. Mirrors the runway cache. (#441 finding #3)
    """
    global _elevation_cache
    if _elevation_cache is None:
        try:
            from weatherbrief.airports import get_airport_elevations

            coords = _get_coords(airports_db_path)
            _elevation_cache = get_airport_elevations(
                list(coords.keys()), airports_db_path,
            )
        except Exception:
            # Elevation is an enhancement, not required: on failure the ceiling
            # stays datum-naive rather than breaking the forecast map. (#441)
            logger.warning("Elevation lookup failed; ceilings stay datum-naive", exc_info=True)
            _elevation_cache = {}
    return _elevation_cache


_approach_cache: dict[str, tuple[str | None, bool]] | None = None


def _get_approach_classes(airports_db_path: str) -> dict[str, tuple[str | None, bool]]:
    """Return icao → (most-precise approach_type, has_iap), cached in-process.

    Approach data is static (changes only with a nav.db rebuild + redeploy), so
    it is computed once for the whole watchlist — mirrors the runway cache and
    the per-destination lookup in ``tasks/alternate_requirement``.
    """
    global _approach_cache
    if _approach_cache is None:
        out: dict[str, tuple[str | None, bool]] = {}
        try:
            from weatherbrief.airports import _load_airport_model

            coords = _get_coords(airports_db_path)
            model = _load_airport_model(airports_db_path)
            for icao in coords:
                try:
                    ap = model.airports.get(icao)
                    approaches = ap.procedures_query.approaches() if ap is not None else None
                    if approaches is None or not approaches.exists():
                        out[icao] = (None, False)
                        continue
                    best = approaches.most_precise()
                    out[icao] = (best.approach_type if best is not None else None, True)
                except Exception:
                    out[icao] = (None, False)
        except Exception:
            # No nav.db / model — degrade to "no approach data" (EASA uses its
            # no-IAP VMC proxy, FAA is approach-independent). Map still renders.
            logger.warning("map: approach-class load failed; alternate-needed degrades", exc_info=True)
        _approach_cache = out
    return _approach_cache


def _alt_required(
    ceiling_ft: float | None,
    visibility_m: float | None,
    approach_type: str | None,
    has_iap: bool,
) -> dict[str, bool] | None:
    """FAA/EASA "is a destination alternate required?" flags from NWP ceiling/vis.

    Reuses the briefing's destination-trigger logic on the NWP path. EASA
    MARGINAL collapses to required (conservative). Returns ``None`` when the
    model has neither a ceiling nor a visibility (no usable data → marker shows
    "no data"). A *missing visibility alone* is NOT treated as poor visibility —
    ECMWF visibility is GRIB-only and not republished here, so we judge on the
    ceiling rather than let an absent value spuriously force "required"; a real
    low visibility still triggers. (This differs from the briefing's destination
    path, which has a real consensus visibility and keeps missing-vis → fail.)
    """
    if ceiling_ft is None and visibility_m is None:
        return None
    eff_vis = visibility_m if visibility_m is not None else 99999.0
    window = nwp_window(ceiling_ft, eff_vis)
    proxy = proxy_for_approach(approach_type, has_iap)
    faa = compute_faa_trigger(window).status == TriggerVerdict.REQUIRED
    easa = compute_easa_trigger(window, proxy).status != TriggerVerdict.NOT_REQUIRED
    return {"faa": faa, "easa": easa}


# ---------------------------------------------------------------------------
# 1. Forecast map data
# ---------------------------------------------------------------------------


# Snapshot → category/wind/consensus assembly now lives in the shared,
# dict-based ``analysis.airport_consensus`` so the forecast map and the
# route-alternates stage compute identical results (issue #210, "Seam 2").
# These module-level wrappers preserve the original row/attribute-access
# signatures (the forecast map reads ORM rows; tests pass SimpleNamespace).

# Snapshot columns the shared assembly reads, copied off the ORM row.
_SNAPSHOT_DICT_FIELDS = (
    "model",  # needed for the ECMWF-AGL ceiling exception (#441 finding #3)
    "sounding_ceiling_ft",
    "nwp_ceiling_ft",
    "cloud_base_ft",
    "lcl_ft",
    "visibility_m",
    "wind_speed_10m_kt",
    "wind_direction_10m_deg",
    "wind_gusts_10m_kt",
    "cloud_cover_pct",
    "cape_jkg",
    "sounding_convective_risk",
    "temperature_2m_c",
)


def _row_to_snapshot_dict(snap: AirportForecastSnapshotRow) -> dict[str, Any]:
    """Adapt an ORM snapshot row to the plain dict the shared assembly reads.

    ``getattr(..., None)`` keeps the adapter tolerant of partial test doubles
    (e.g. ``SimpleNamespace``) that only set the fields a given test exercises.
    """
    return {field: getattr(snap, field, None) for field in _SNAPSHOT_DICT_FIELDS}




def _flight_category(
    snap: AirportForecastSnapshotRow, field_elevation_ft: float | None = None,
) -> str:
    """Derive flight category from snapshot fields (row→dict adapter)."""
    return _shared_flight_category(
        _row_to_snapshot_dict(snap), field_elevation_ft=field_elevation_ft,
    )


def _snap_to_dict(
    snap: AirportForecastSnapshotRow, field_elevation_ft: float | None = None,
) -> dict[str, Any]:
    """Convert a forecast snapshot to a lightweight dict for the API response."""
    return _shared_snap_to_dict(
        _row_to_snapshot_dict(snap), field_elevation_ft=field_elevation_ft,
    )


def _enrich_wind(d: dict, runway_ends: list[RunwayEnd]) -> None:
    """Add crosswind/headwind for best runway to a snapshot dict."""
    _shared_enrich_wind(d, runway_ends)


def _consensus(per_model: dict[str, dict], mode: str = "worst") -> dict[str, Any]:
    """Compute consensus across models for key variables."""
    return _shared_consensus(per_model, mode)


def assemble_map_airports(
    by_airport: dict[str, dict[str, dict]],
    airports_db_path: str,
    observed: dict[str, dict[str, dict]] | None = None,
) -> list[dict[str, Any]]:
    """Turn per-airport, per-model forecast dicts into map airport entries.

    ``by_airport`` maps ICAO → model → the ``snap_to_dict`` output. Each
    entry gets runway crosswind/headwind, the FAA/EASA alternate-required
    flags, coordinates, and both baked consensus blocks. Shared by the
    forecast map and the historical map so a snapshot renders identically in
    both.

    ``observed`` (historical map only) maps ICAO → source (``metar``/``taf``)
    → a dict with the same value keys. Observed sources get the same wind and
    alternate enrichment but are carried under ``observed`` and **never** feed
    the consensus, which is a statement about the models. When it is given,
    airports with observations but no model data are included too, with
    ``consensus: None`` rather than the empty-input "VFR" default.
    """
    coords = _get_coords(airports_db_path)
    runways = _get_runways(airports_db_path)
    approaches = _get_approach_classes(airports_db_path)

    icaos = set(by_airport)
    if observed is not None:
        icaos |= set(observed)

    airports = []
    for icao in sorted(icaos):
        if icao not in coords:
            continue
        lat, lon = coords[icao]
        atype, has_iap = approaches.get(icao, (None, False))
        rwy_ends = runways.get(icao, [])
        models_data = by_airport.get(icao, {})
        observed_data = observed.get(icao, {}) if observed is not None else {}
        # Runway crosswind/headwind, then per-source FAA/EASA alternate-required
        # flags (the airport colour is aggregated worst-of-models client-side;
        # the popup shows the spread).
        for source_dict in (*models_data.values(), *observed_data.values()):
            _enrich_wind(source_dict, rwy_ends)
            flag = _alt_required(
                source_dict.get("ceiling_ft"), source_dict.get("visibility_m"),
                atype, has_iap,
            )
            if flag is not None:
                source_dict["alt_required"] = flag
        entry: dict[str, Any] = {
            "icao": icao,
            "lat": lat,
            "lon": lon,
            "approach_type": atype,
            "models": models_data,
            # Both consensus modes are baked so the clients (web + iOS) carry
            # zero consensus logic — they read whichever block matches the
            # selected Worst/Majority mode. See designs/forecast-page.md and #419.
            "consensus": _consensus(models_data, "worst") if models_data else None,
            "consensus_majority": (
                _consensus(models_data, "majority") if models_data else None
            ),
        }
        if observed is not None:
            entry["observed"] = observed_data
        airports.append(entry)
    return airports


def get_forecast_map_data(
    db: Session,
    forecast_hour: datetime,
    airports_db_path: str,
) -> dict[str, Any]:
    """Return forecast data for all watchlist airports at a given hour.

    Finds the latest model_init_time per model that has data for forecast_hour,
    then returns per-airport, per-model forecasts with both the worst
    (``consensus``) and majority (``consensus_majority``) consensus baked in, so
    the clients render without recomputing either.
    """
    # Find latest model_init_time per model that has snapshots for this hour
    init_times: dict[str, datetime] = {}
    for model in _MODELS:
        row = db.execute(
            select(func.max(AirportForecastSnapshotRow.model_init_time))
            .where(AirportForecastSnapshotRow.model == model)
            .where(AirportForecastSnapshotRow.forecast_hour == forecast_hour)
        ).scalar()
        if row:
            init_times[model] = row

    if not init_times:
        return {
            "forecast_time": forecast_hour.replace(tzinfo=timezone.utc).isoformat(),
            "model_init_times": {},
            "airports": [],
        }

    # Fetch all snapshots for these (model, init_time, hour) combos
    conditions = []
    for model, init_time in init_times.items():
        conditions.append(
            (AirportForecastSnapshotRow.model == model)
            & (AirportForecastSnapshotRow.model_init_time == init_time)
            & (AirportForecastSnapshotRow.forecast_hour == forecast_hour)
        )

    snaps = db.execute(
        select(AirportForecastSnapshotRow).where(or_(*conditions))
    ).scalars().all()

    # Group by airport (ceiling → AGL via published field elevation, #441 #3)
    elevations = _get_elevations(airports_db_path)
    by_airport: dict[str, dict[str, dict]] = {}
    for snap in snaps:
        if snap.icao not in by_airport:
            by_airport[snap.icao] = {}
        by_airport[snap.icao][snap.model] = _snap_to_dict(
            snap, field_elevation_ft=elevations.get(snap.icao),
        )

    airports = assemble_map_airports(by_airport, airports_db_path)

    return {
        "forecast_time": forecast_hour.replace(tzinfo=timezone.utc).isoformat(),
        "model_init_times": {
            m: t.isoformat() for m, t in init_times.items()
        },
        "airports": airports,
    }


def watchlist_coords(airports_db_path: str) -> dict[str, tuple[float, float]]:
    """ICAO → (lat, lon) for every airport the forecast map monitors."""
    return _get_coords(airports_db_path)


def approach_classes(airports_db_path: str) -> dict[str, tuple[str | None, bool]]:
    """ICAO → (most-precise approach_type, has_iap) for monitored airports."""
    return _get_approach_classes(airports_db_path)


def load_forecast_slot(
    db: Session,
    day: int,
    hour: int,
    airports_db_path: str,
    *,
    with_observations: bool = False,
) -> dict[str, Any]:
    """Forecast-map payload for one ``(day, hour)`` slot, cache first.

    Prefers the pre-built cache (rebuilt by the background scheduler).
    ``is_stale`` also rejects an entry whose relative-day index no longer maps
    to today's date (post-midnight, before the next model run); in that case
    compute live for the correct absolute date. ``hour`` must already be one
    of ``sample_hours_for_day(day)``. ``with_observations`` adds METAR/TAF on
    a live D-0 computation (the cached D-0 entry already carries them).

    The result may be a freshly parsed cache blob or a live computation:
    callers own it, but treat it as read-only so the contract holds if a
    cache layer starts sharing objects. For many slots at once use
    :func:`load_forecast_slots`, which bounds the live work.
    """
    from weatherbrief.tasks.cache_builder import (
        forecast_map_cache_key,
        get_cached,
        is_stale,
    )
    from weatherbrief.tasks.forecast_grid import forecast_hour_for

    cache_key = forecast_map_cache_key(day, hour)
    if not is_stale(db, cache_key, "snapshot"):
        cached = get_cached(db, cache_key)
        if cached is not None:
            return cached

    forecast_hour = forecast_hour_for(day, hour)
    data = get_forecast_map_data(db, forecast_hour, airports_db_path)
    if with_observations and day == 0:
        data = enrich_with_observations(db, forecast_hour, data)
    return data


# Parsed, caller-projected slot payloads keyed by (project_id, cache_key,
# computed_at): a cache generation is immutable once written, so a hit is
# always valid. Sized to the whole map grid so a full-horizon call never
# evicts its own slots; shared across the endpoint threadpool, hence the
# lock. Entries are shared between callers and must be treated read-only.
_SLOT_LRU: OrderedDict[tuple[str, str, str], dict[str, Any]] = OrderedDict()
_SLOT_LRU_LOCK = threading.Lock()


def _slot_lru_max() -> int:
    from weatherbrief.tasks.forecast_grid import day_hour_pairs

    return len(day_hour_pairs())


def _lru_get(key: tuple[str, str, str]) -> dict[str, Any] | None:
    with _SLOT_LRU_LOCK:
        hit = _SLOT_LRU.get(key)
        if hit is not None:
            _SLOT_LRU.move_to_end(key)
        return hit


def _lru_put(key: tuple[str, str, str], value: dict[str, Any]) -> None:
    with _SLOT_LRU_LOCK:
        _SLOT_LRU[key] = value
        _SLOT_LRU.move_to_end(key)
        limit = _slot_lru_max()
        while len(_SLOT_LRU) > limit:
            _SLOT_LRU.popitem(last=False)


def load_forecast_slots(
    db: Session,
    targets: dict[Any, datetime],
    airports_db_path: str,
    *,
    project: Callable[[dict[str, Any]], dict[str, Any]],
    project_id: str,
    max_live: int = 1,
) -> tuple[dict[Any, dict[str, Any]], dict[str, list[Any]]]:
    """Load many forecast-map slots with a fixed, small query budget.

    ``targets`` maps a caller key to the absolute UTC forecast hour wanted.
    Returns ``(data_by_key, status)``; ``status`` lists keys that were
    ``stale`` (served from the previous model cycle's cache), computed
    ``live``, or ``missing``.

    Unlike calling :func:`load_forecast_slot` in a loop, this never fans out
    into one full-map live query per slot:

    * The cache is matched on the **absolute** forecast time each entry
      holds (``computed_at`` date + its relative day, confirmed against the
      payload's ``forecast_time``). After a UTC-midnight rollover yesterday's
      ``day=4`` entry is today's D+3 and is still served; ``is_stale``'s date
      rule would force the live path for every slot until the next cycle.
    * An entry older than the newest snapshots is served anyway and reported
      ``stale``: a previous model run beats a live recomputation.
    * Only slots no cache entry covers (the newest horizon day after
      midnight) are computed live, at most ``max_live`` of them; the rest
      are ``missing``.

    Queries: one indexed MAX(fetched_at), one cache-meta read over the grid's
    keys (unique-key IN list), one blob read for the slots not already in the
    in-process LRU, plus at most ``max_live`` live computations. Parsed blobs
    go through ``project`` (keep only what the caller needs) and are cached
    per ``project_id`` in an LRU the size of the grid, so repeat calls in the
    same cache generation read no blobs at all. Returned payloads may be
    shared with other requests: read-only.
    """
    from weatherbrief.tasks.cache_builder import (
        forecast_map_cache_key,
        get_snapshot_max_time,
    )
    from weatherbrief.tasks.forecast_grid import day_hour_pairs

    live_max = get_snapshot_max_time(db)
    keys = [forecast_map_cache_key(d, h) for d, h in day_hour_pairs()]
    meta = db.execute(
        select(
            VerificationCacheRow.cache_key,
            VerificationCacheRow.computed_at,
            VerificationCacheRow.source_max_time,
        ).where(VerificationCacheRow.cache_key.in_(keys))
    ).all()

    # Absolute forecast hour → (cache_key, computed_at, stale). When two
    # entries claim the same hour (mid-rollover), the newer computation wins.
    by_time: dict[datetime, tuple[str, datetime, bool]] = {}
    for key, computed_at, smax in meta:
        if computed_at is None:
            continue
        _, d, h = key.rsplit(":", 2)
        if computed_at.tzinfo is None:  # TZDateTime returns aware; be safe
            computed_at = computed_at.replace(tzinfo=timezone.utc)
        date = computed_at.astimezone(timezone.utc).date() + timedelta(days=int(d))
        t = datetime(date.year, date.month, date.day, int(h), tzinfo=timezone.utc)
        stale = smax is None or (live_max is not None and live_max > smax)
        prev = by_time.get(t)
        if prev is None or computed_at > prev[1]:
            by_time[t] = (key, computed_at, stale)

    out: dict[Any, dict[str, Any]] = {}
    status: dict[str, list[Any]] = {"stale": [], "live": [], "missing": []}
    uncovered: list[Any] = []
    to_read: dict[str, list[tuple[Any, datetime, tuple[str, str, str], bool]]] = {}
    for k, t in targets.items():
        hit = by_time.get(t)
        if hit is None:
            uncovered.append(k)
            continue
        key, computed_at, stale = hit
        lru_key = (project_id, key, computed_at.isoformat())
        cached = _lru_get(lru_key)
        if cached is not None:
            out[k] = cached
            if stale:
                status["stale"].append(k)
            continue
        to_read.setdefault(key, []).append((k, t, lru_key, stale))

    if to_read:
        blobs = dict(db.execute(
            select(VerificationCacheRow.cache_key, VerificationCacheRow.data_json)
            .where(VerificationCacheRow.cache_key.in_(list(to_read)))
        ).all())
        for key, wanted in to_read.items():
            raw = blobs.get(key)
            data = json.loads(raw) if raw else None
            for k, t, lru_key, stale in wanted:
                # The payload is the authority on which hour it holds.
                if data is None or data.get("forecast_time") != t.isoformat():
                    uncovered.append(k)
                    continue
                slim = project(data)
                _lru_put(lru_key, slim)
                out[k] = slim
                if stale:
                    status["stale"].append(k)

    for k in uncovered:
        if len(status["live"]) < max_live:
            data = get_forecast_map_data(db, targets[k], airports_db_path)
            if data.get("airports"):
                out[k] = project(data)
                status["live"].append(k)
                continue
        status["missing"].append(k)
    return out, status


def enrich_with_observations(
    db: Session,
    forecast_hour: datetime,
    forecast_data: dict[str, Any],
) -> dict[str, Any]:
    """Add latest METAR/TAF observations to forecast map data.

    For each airport in the forecast data, find the most recent observation
    on the same date as forecast_hour.  This is called during cache rebuild
    for D-0 entries only — observations are at most ~3 hours old.
    """
    airport_icaos = [a["icao"] for a in forecast_data.get("airports", [])]
    if not airport_icaos:
        return forecast_data

    obs_date = forecast_hour.date()
    start_of_day = datetime(obs_date.year, obs_date.month, obs_date.day,
                            tzinfo=timezone.utc)
    end_of_day = start_of_day + timedelta(days=1)

    # Fetch all observations for the target date in one query
    rows = db.execute(
        select(VerificationObservationRow)
        .where(VerificationObservationRow.icao.in_(airport_icaos))
        .where(VerificationObservationRow.observation_time >= start_of_day)
        .where(VerificationObservationRow.observation_time < end_of_day)
        .order_by(VerificationObservationRow.observation_time.desc())
    ).scalars().all()

    # Keep only the latest observation per airport
    obs_by_icao: dict[str, VerificationObservationRow] = {}
    for row in rows:
        if row.icao not in obs_by_icao:
            obs_by_icao[row.icao] = row

    for airport in forecast_data["airports"]:
        obs = obs_by_icao.get(airport["icao"])
        if obs is None:
            continue
        airport["observation"] = {
            "metar_raw": obs.metar_raw,
            "observation_time": obs.observation_time.isoformat(),
            "flight_category": obs.flight_category,
            "ceiling_ft": obs.ceiling_ft,
            "visibility_m": obs.visibility_m,
            "wind_speed_kt": obs.wind_speed_kt,
            "wind_dir_deg": obs.wind_dir,
            "wind_gust_kt": obs.wind_gust_kt,
            "temperature_c": obs.temperature_c,
            "dewpoint_c": obs.dewpoint_c,
            "taf_raw": obs.taf_raw,
            "taf_flight_category": obs.taf_flight_category,
            "taf_ceiling_ft": obs.taf_ceiling_ft,
            "taf_wind_speed_kt": obs.taf_wind_speed_kt,
            "taf_wind_dir_deg": obs.taf_wind_dir,
        }

    return forecast_data


# Note: ``get_verification_map_data`` was removed in #154 — the bias map
# view it powered has been replaced by the optimistic-bias leaderboard
# (see ``tasks/verification_stats.get_optimistic_bias_leaderboard``).
