"""Database queries for weather overview maps.

Two query types:
1. Forecast map — latest model forecasts at ~830 airports for a selected hour
2. Verification bias map — per-airport accuracy aggregations from verification scores
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from weatherbrief.analysis.airport_consensus import (
    best_ceiling as _shared_best_ceiling,
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


def _best_ceiling(snap: AirportForecastSnapshotRow) -> float | None:
    """Pick the best ceiling estimate from a snapshot (row→dict adapter)."""
    return _shared_best_ceiling(_row_to_snapshot_dict(snap))


def _flight_category(snap: AirportForecastSnapshotRow) -> str:
    """Derive flight category from snapshot fields (row→dict adapter)."""
    return _shared_flight_category(_row_to_snapshot_dict(snap))


def _snap_to_dict(snap: AirportForecastSnapshotRow) -> dict[str, Any]:
    """Convert a forecast snapshot to a lightweight dict for the API response."""
    return _shared_snap_to_dict(_row_to_snapshot_dict(snap))


def _enrich_wind(d: dict, runway_ends: list[RunwayEnd]) -> None:
    """Add crosswind/headwind for best runway to a snapshot dict."""
    _shared_enrich_wind(d, runway_ends)


def _consensus(per_model: dict[str, dict], mode: str = "worst") -> dict[str, Any]:
    """Compute consensus across models for key variables."""
    return _shared_consensus(per_model, mode)


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
    coords = _get_coords(airports_db_path)

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

    # Group by airport
    by_airport: dict[str, dict[str, dict]] = {}
    for snap in snaps:
        if snap.icao not in by_airport:
            by_airport[snap.icao] = {}
        by_airport[snap.icao][snap.model] = _snap_to_dict(snap)

    # Enrich per-model data with runway crosswind/headwind
    runways = _get_runways(airports_db_path)
    for icao, models_data in by_airport.items():
        rwy_ends = runways.get(icao, [])
        for model_dict in models_data.values():
            _enrich_wind(model_dict, rwy_ends)

    # Build response with coords, consensus, and FAA/EASA alternate-required flags
    approaches = _get_approach_classes(airports_db_path)
    airports = []
    for icao, models_data in sorted(by_airport.items()):
        if icao not in coords:
            continue
        lat, lon = coords[icao]
        atype, has_iap = approaches.get(icao, (None, False))
        # Per-model FAA/EASA alternate-required flags (the airport colour is
        # aggregated worst-of-models client-side; the popup shows the spread).
        for model_dict in models_data.values():
            flag = _alt_required(
                model_dict.get("ceiling_ft"), model_dict.get("visibility_m"),
                atype, has_iap,
            )
            if flag is not None:
                model_dict["alt_required"] = flag
        airports.append({
            "icao": icao,
            "lat": lat,
            "lon": lon,
            "approach_type": atype,
            "models": models_data,
            # Both consensus modes are baked so the clients (web + iOS) carry
            # zero consensus logic — they read whichever block matches the
            # selected Worst/Majority mode. See designs/forecast-page.md and #419.
            "consensus": _consensus(models_data, "worst"),
            "consensus_majority": _consensus(models_data, "majority"),
        })

    return {
        "forecast_time": forecast_hour.replace(tzinfo=timezone.utc).isoformat(),
        "model_init_times": {
            m: t.replace(tzinfo=timezone.utc).isoformat() for m, t in init_times.items()
        },
        "airports": airports,
    }


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
