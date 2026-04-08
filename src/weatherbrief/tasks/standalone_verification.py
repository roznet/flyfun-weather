"""Standalone airport verification pipeline.

Flight-independent NWP accuracy dataset: predict weather at METAR-reporting
airports across Western/Central Europe, then score against actual METARs
at multiple lead times (D-0 through D-7).
"""

from __future__ import annotations

import json
import logging
import os
import time
from datetime import datetime, timedelta, timezone
from itertools import batched
from pathlib import Path

import requests
from sqlalchemy import select, tuple_

from weatherbrief.db.models import (
    AirportForecastSnapshotRow,
    TafVerificationScoreRow,
    VerificationCycleRow,
    VerificationObservationRow,
    VerificationScoreRow,
)
from weatherbrief.tasks.airport_watchlist import WatchlistAirport

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration defaults (hardcoded until config table in Step 10)
# ---------------------------------------------------------------------------

STANDALONE_MODELS = ["gfs", "icon", "ecmwf"]
SAMPLE_HOURS_UTC = [6, 9, 12, 15, 18]  # light cycles at 09/12/15, full at 06/18
FULL_CYCLE_HOURS_UTC = {6, 18}  # full forecast fetch + scoring at synoptic boundary hours

# Model forecast horizon — 4 days is enough for actionable verification stats
MODEL_FORECAST_DAYS = {
    "gfs": 4,
    "icon": 4,
    "ecmwf": 4,
}

_OPEN_METEO_BATCH_SIZE = 100  # airports per Open-Meteo API call (also retry boundary)
_LCL_CONSTANT_FT = 400  # 400 * (T - Td) approximation for LCL in feet


# ---------------------------------------------------------------------------
# Sounding proxy — lightweight stand-in for SoundingAnalysis at scoring time
# ---------------------------------------------------------------------------

from dataclasses import dataclass, field as dc_field
from typing import Optional


@dataclass
class _IndicesProxy:
    sounding_ceiling_ft: Optional[float] = None


@dataclass
class _CloudLayerProxy:
    base_ft: float = 0.0
    coverage: object = None


@dataclass
class _ConvectiveProxy:
    risk_level: object = None


@dataclass
class _SoundingProxy:
    """Minimal sounding stand-in for reconcile_ceiling and convective scoring."""
    indices: Optional[_IndicesProxy] = None
    dd_cloud_layers: list = dc_field(default_factory=list)
    convective: Optional[_ConvectiveProxy] = None


def _build_sounding_proxy(snap: AirportForecastSnapshotRow):
    """Reconstruct a minimal SoundingAnalysis proxy from stored snapshot fields.

    Returns None when no sounding data was stored (backward-compatible fallback).
    """
    if (snap.sounding_ceiling_ft is None
            and snap.sounding_cloud_base_ft is None
            and snap.sounding_convective_risk is None):
        return None

    from weatherbrief.models.analysis import CloudCoverage, ConvectiveRisk

    indices = _IndicesProxy(sounding_ceiling_ft=snap.sounding_ceiling_ft)

    dd_cloud_layers = []
    if snap.sounding_cloud_base_ft is not None:
        dd_cloud_layers = [_CloudLayerProxy(
            base_ft=snap.sounding_cloud_base_ft,
            coverage=CloudCoverage.BKN,
        )]

    convective = None
    if snap.sounding_convective_risk is not None:
        try:
            risk = ConvectiveRisk(snap.sounding_convective_risk)
        except ValueError:
            risk = ConvectiveRisk.NONE
        convective = _ConvectiveProxy(risk_level=risk)

    return _SoundingProxy(
        indices=indices,
        dd_cloud_layers=dd_cloud_layers,
        convective=convective,
    )


# ---------------------------------------------------------------------------
# Phase A: Fetch forecasts from Open-Meteo + GRIB ceiling
# ---------------------------------------------------------------------------

def _fetch_forecasts_for_model(
    model: str,
    init_time: datetime,
    airports: list[WatchlistAirport],
    session: requests.Session,
) -> list[dict]:
    """Fetch Open-Meteo surface forecasts for all airports for one model.

    Returns list of dicts, each with airport ICAO and per-sample-hour values.
    Filters to SAMPLE_HOURS_UTC only.
    """
    from weatherbrief.models.analysis import ModelSource, RoutePoint
    from weatherbrief.fetch.open_meteo import OpenMeteoClient

    model_source = ModelSource(model)
    forecast_days = MODEL_FORECAST_DAYS.get(model, 7)

    start_date = init_time.strftime("%Y-%m-%d")
    end_dt = init_time + timedelta(days=forecast_days)
    end_date = end_dt.strftime("%Y-%m-%d")

    client = OpenMeteoClient(timeout=60)
    all_results: list[dict] = []

    # Process airports in chunks
    chunk_num = 0
    total_chunks = (len(airports) + _OPEN_METEO_BATCH_SIZE - 1) // _OPEN_METEO_BATCH_SIZE
    for chunk_airports in batched(airports, _OPEN_METEO_BATCH_SIZE):
        chunk_num += 1
        chunk_list = list(chunk_airports)
        points = [
            RoutePoint(
                lat=a.lat, lon=a.lon,
                distance_from_origin_nm=0.0,
                waypoint_icao=a.icao,
            )
            for a in chunk_list
        ]

        forecasts = None
        for attempt in range(3):
            try:
                forecasts = client.fetch_multi_point(
                    points, model_source,
                    start_date=start_date, end_date=end_date,
                )
                break
            except Exception:
                if attempt < 2:
                    wait = 5 * (attempt + 1)
                    logger.warning(
                        "Open-Meteo %s chunk %d airports attempt %d/3 failed, "
                        "retrying in %ds",
                        model, len(chunk_list), attempt + 1, wait,
                    )
                    time.sleep(wait)
                else:
                    logger.warning(
                        "Open-Meteo %s fetch failed for chunk of %d airports "
                        "after 3 attempts",
                        model, len(chunk_list), exc_info=True,
                    )
        if forecasts is None:
            continue

        logger.info("Model %s chunk %d/%d: processing %d airports",
                    model, chunk_num, total_chunks, len(chunk_list))
        for airport, wpf in zip(chunk_list, forecasts):
            # Filter to sample hours only
            for hourly in wpf.hourly:
                utc_hour = hourly.time.hour if hasattr(hourly.time, 'hour') else None
                if utc_hour not in SAMPLE_HOURS_UTC:
                    continue

                # Compute LCL from T-Td
                lcl_ft = None
                if hourly.temperature_2m_c is not None and hourly.dewpoint_2m_c is not None:
                    spread = hourly.temperature_2m_c - hourly.dewpoint_2m_c
                    if spread >= 0:
                        lcl_ft = _LCL_CONSTANT_FT * spread

                snap = {
                    "icao": airport.icao,
                    "model": model,
                    "model_init_time": init_time,
                    "forecast_hour": hourly.time if hourly.time.tzinfo else hourly.time.replace(tzinfo=timezone.utc),
                    "temperature_2m_c": hourly.temperature_2m_c,
                    "dewpoint_2m_c": hourly.dewpoint_2m_c,
                    "visibility_m": hourly.visibility_m,
                    "wind_speed_10m_kt": hourly.wind_speed_10m_kt,
                    "wind_direction_10m_deg": hourly.wind_direction_10m_deg,
                    "wind_gusts_10m_kt": hourly.wind_gusts_10m_kt,
                    "precipitation_mm": hourly.precipitation_mm,
                    "snowfall_cm": hourly.snowfall_cm,
                    "cape_jkg": hourly.cape_jkg,
                    "weather_code": hourly.weather_code,
                    "cloud_cover_pct": hourly.cloud_cover_pct,
                    "cloud_cover_low_pct": hourly.cloud_cover_low_pct,
                    "lcl_ft": lcl_ft,
                }

                # Run sounding analysis on pressure levels (already fetched)
                _enrich_with_sounding(snap, hourly, model)

                all_results.append(snap)

    return all_results


def _enrich_with_sounding(snap: dict, hourly, model: str) -> None:
    """Run sounding analysis on pressure-level data and store results in snap dict.

    Fails silently — surface data is preserved even if sounding analysis fails.
    """
    if not getattr(hourly, "pressure_levels", None):
        return

    try:
        from weatherbrief.analysis.sounding import analyze_sounding_lite
        from weatherbrief.models.analysis import CloudCoverage

        sounding = analyze_sounding_lite(
            hourly.pressure_levels, hourly, model_key=model,
        )
        if sounding is None:
            return

        # Thermodynamic indices
        if sounding.indices:
            snap["sounding_ceiling_ft"] = sounding.indices.sounding_ceiling_ft
            snap["freezing_level_ft"] = sounding.indices.freezing_level_ft
            snap["sounding_cape_jkg"] = sounding.indices.cape_surface_jkg
            snap["sounding_cin_jkg"] = sounding.indices.cin_surface_jkg
            snap["sounding_lifted_index"] = sounding.indices.lifted_index

        # Lowest BKN/OVC cloud layer base → sounding_cloud_base_ft
        bkn_ovc = [
            cl for cl in sounding.dd_cloud_layers
            if cl.coverage in (CloudCoverage.BKN, CloudCoverage.OVC)
        ]
        if bkn_ovc:
            lowest = min(bkn_ovc, key=lambda cl: cl.base_ft)
            snap["sounding_cloud_base_ft"] = lowest.base_ft

        # Convective risk
        if sounding.convective and sounding.convective.risk_level is not None:
            snap["sounding_convective_risk"] = sounding.convective.risk_level.value

    except Exception:
        logger.warning(
            "Sounding analysis failed for %s %s, surface data preserved",
            snap.get("icao"), model, exc_info=True,
        )


def _enrich_with_grib(
    snapshots: list[dict],
    model: str,
    init_time: datetime,
    airports: list[WatchlistAirport],
    session: requests.Session,
) -> None:
    """Enrich snapshot dicts with GRIB ceiling/cloud_base data in-place.

    Fetches GRIB cloud diagnostics for the model's forecast hours
    and maps nwp_ceiling_ft and cloud_base_ft onto the matching snapshot dicts.
    """
    from weatherbrief.tasks.standalone_grib import (
        AirportCeilingData,
        datetime_to_init_parts,
        fetch_gfs_cloud_diag,
        fetch_icon_cloud_diag,
    )

    if model == "ecmwf":
        # ECMWF uses SFTP GRIB delivery — not yet implemented
        return

    init_date, init_hour = datetime_to_init_parts(init_time)

    # Determine which forecast hours we need (as offsets from init)
    fhour_set: set[int] = set()
    for snap in snapshots:
        if snap["model"] != model:
            continue
        delta = snap["forecast_hour"] - init_time
        offset = int(delta.total_seconds() / 3600)
        if offset >= 0:
            fhour_set.add(offset)

    if not fhour_set:
        return

    forecast_hours = sorted(fhour_set)
    lats = [a.lat for a in airports]
    lons = [a.lon for a in airports]

    # Build ICAO → index mapping
    icao_to_idx = {a.icao: i for i, a in enumerate(airports)}

    fetch_fn = fetch_gfs_cloud_diag if model == "gfs" else fetch_icon_cloud_diag

    try:
        grib_data = fetch_fn(
            init_date, init_hour, forecast_hours, lats, lons, session=session,
        )
    except Exception:
        logger.warning("GRIB enrichment failed for %s", model, exc_info=True)
        return

    # Map GRIB data back to snapshots
    for snap in snapshots:
        if snap["model"] != model:
            continue
        delta = snap["forecast_hour"] - init_time
        fhour = int(delta.total_seconds() / 3600)
        airport_idx = icao_to_idx.get(snap["icao"])

        if fhour in grib_data and airport_idx is not None:
            ceiling_data = grib_data[fhour]
            if airport_idx < len(ceiling_data):
                cd = ceiling_data[airport_idx]
                snap["nwp_ceiling_ft"] = cd.nwp_ceiling_ft
                snap["cloud_base_ft"] = cd.cloud_base_ft


# ---------------------------------------------------------------------------
# Phase B: Store forecast snapshots in DB
# ---------------------------------------------------------------------------

def _normalize_key(icao: str, model: str, init_time: datetime, fhour: datetime) -> tuple:
    """Normalize a snapshot key by stripping tzinfo for consistent comparison.

    SQLite returns naive datetimes, so we strip tzinfo from all keys to match.
    """
    init_naive = init_time.replace(tzinfo=None) if init_time.tzinfo else init_time
    fhour_naive = fhour.replace(tzinfo=None) if fhour.tzinfo else fhour
    return (icao, model, init_naive, fhour_naive)


def _store_snapshots(snapshots: list[dict], db) -> int:
    """Insert new forecast snapshot rows, skipping duplicates. Returns count of new rows."""
    if not snapshots:
        return 0

    # Bulk-fetch existing keys in one query
    snap_keys = [
        (s["icao"], s["model"], s["model_init_time"], s["forecast_hour"])
        for s in snapshots
    ]
    # Deduplicate input keys for the query
    unique_keys = list({k for k in snap_keys})

    existing_keys: set[tuple] = set()
    # Query in chunks to stay within SQL parameter limits
    for i in range(0, len(unique_keys), 500):
        chunk = unique_keys[i : i + 500]
        rows = db.execute(
            select(
                AirportForecastSnapshotRow.icao,
                AirportForecastSnapshotRow.model,
                AirportForecastSnapshotRow.model_init_time,
                AirportForecastSnapshotRow.forecast_hour,
            ).where(
                tuple_(
                    AirportForecastSnapshotRow.icao,
                    AirportForecastSnapshotRow.model,
                    AirportForecastSnapshotRow.model_init_time,
                    AirportForecastSnapshotRow.forecast_hour,
                ).in_(chunk)
            )
        ).all()
        for r in rows:
            existing_keys.add(_normalize_key(*r))

    stored = 0
    now = datetime.now(timezone.utc)
    for snap in snapshots:
        key = _normalize_key(
            snap["icao"], snap["model"],
            snap["model_init_time"], snap["forecast_hour"],
        )
        if key in existing_keys:
            continue

        row = AirportForecastSnapshotRow(
            icao=snap["icao"],
            model=snap["model"],
            model_init_time=snap["model_init_time"],
            forecast_hour=snap["forecast_hour"],
            fetched_at=now,
            temperature_2m_c=snap.get("temperature_2m_c"),
            dewpoint_2m_c=snap.get("dewpoint_2m_c"),
            visibility_m=snap.get("visibility_m"),
            wind_speed_10m_kt=snap.get("wind_speed_10m_kt"),
            wind_direction_10m_deg=snap.get("wind_direction_10m_deg"),
            wind_gusts_10m_kt=snap.get("wind_gusts_10m_kt"),
            precipitation_mm=snap.get("precipitation_mm"),
            snowfall_cm=snap.get("snowfall_cm"),
            cape_jkg=snap.get("cape_jkg"),
            weather_code=snap.get("weather_code"),
            cloud_cover_pct=snap.get("cloud_cover_pct"),
            cloud_cover_low_pct=snap.get("cloud_cover_low_pct"),
            nwp_ceiling_ft=snap.get("nwp_ceiling_ft"),
            cloud_base_ft=snap.get("cloud_base_ft"),
            lcl_ft=snap.get("lcl_ft"),
            sounding_ceiling_ft=snap.get("sounding_ceiling_ft"),
            sounding_cloud_base_ft=snap.get("sounding_cloud_base_ft"),
            freezing_level_ft=snap.get("freezing_level_ft"),
            sounding_cape_jkg=snap.get("sounding_cape_jkg"),
            sounding_cin_jkg=snap.get("sounding_cin_jkg"),
            sounding_lifted_index=snap.get("sounding_lifted_index"),
            sounding_convective_risk=snap.get("sounding_convective_risk"),
        )
        db.add(row)
        existing_keys.add(key)  # prevent duplicates within same batch
        stored += 1

    db.flush()
    return stored


# ---------------------------------------------------------------------------
# Phase C: Fetch METAR + TAF
# ---------------------------------------------------------------------------

def _fetch_and_store_observations(
    airports: list[WatchlistAirport],
    airports_db_path: str,
    db,
) -> int:
    """Fetch current METARs for all watchlist airports and store them.

    Reuses the existing flight verification infrastructure.
    """
    from weatherbrief.tasks.verification import fetch_observations_batch, store_observations

    icaos = [a.icao for a in airports]
    observations = fetch_observations_batch(icaos, airports_db_path)
    return store_observations(observations, {}, db)


# ---------------------------------------------------------------------------
# Phase D: Score against stored forecasts
# ---------------------------------------------------------------------------

def _snapshot_to_hourly(snap: AirportForecastSnapshotRow):
    """Convert a DB snapshot row to a minimal HourlyForecast for scoring."""
    from weatherbrief.models.analysis import HourlyForecast, NWPCloudDiagnostics

    nwp_diag = None
    if snap.nwp_ceiling_ft is not None:
        nwp_diag = NWPCloudDiagnostics(ceiling_ft=snap.nwp_ceiling_ft)

    return HourlyForecast(
        time=snap.forecast_hour,
        temperature_2m_c=snap.temperature_2m_c,
        dewpoint_2m_c=snap.dewpoint_2m_c,
        visibility_m=snap.visibility_m,
        wind_speed_10m_kt=snap.wind_speed_10m_kt,
        wind_direction_10m_deg=snap.wind_direction_10m_deg,
        wind_gusts_10m_kt=snap.wind_gusts_10m_kt,
        precipitation_mm=snap.precipitation_mm,
        snowfall_cm=snap.snowfall_cm,
        cape_jkg=snap.cape_jkg,
        weather_code=snap.weather_code,
        cloud_cover_pct=snap.cloud_cover_pct,
        cloud_cover_low_pct=snap.cloud_cover_low_pct,
        nwp_cloud_diagnostics=nwp_diag,
    )



def _score_cycle(
    cycle_time: datetime,
    airports: list[WatchlistAirport],
    airports_db_path: str,
    db,
) -> int:
    """Score observations at cycle_time against all matching forecast snapshots.

    Returns number of scores created.
    """
    from weatherbrief.airports import get_runway_ends
    from weatherbrief.tasks.scoring import _score_model_vs_metar, _score_taf_vs_metar

    # Fetch all snapshots that predict within ±90 min of cycle_time
    window_start = cycle_time - timedelta(minutes=90)
    window_end = cycle_time + timedelta(minutes=90)

    matching_snapshots = db.execute(
        select(AirportForecastSnapshotRow)
        .where(AirportForecastSnapshotRow.forecast_hour.between(window_start, window_end))
        .order_by(
            AirportForecastSnapshotRow.icao,
            AirportForecastSnapshotRow.model,
            AirportForecastSnapshotRow.model_init_time,
        )
    ).scalars().all()

    if not matching_snapshots:
        logger.info("No forecast snapshots matching cycle time %s", cycle_time)
        return 0

    # Fetch observations around cycle_time
    obs_rows = db.execute(
        select(VerificationObservationRow)
        .where(VerificationObservationRow.observation_time.between(window_start, window_end))
    ).scalars().all()

    if not obs_rows:
        logger.info("No observations near cycle time %s", cycle_time)
        return 0

    # Build lookup: icao → observation (closest to cycle_time)
    def _ensure_utc(dt: datetime) -> datetime:
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt

    obs_by_icao: dict[str, VerificationObservationRow] = {}
    for obs in obs_rows:
        icao = obs.icao
        if icao not in obs_by_icao:
            obs_by_icao[icao] = obs
        else:
            # Keep the one closest to cycle_time
            existing_delta = abs((_ensure_utc(obs_by_icao[icao].observation_time) - cycle_time).total_seconds())
            new_delta = abs((_ensure_utc(obs.observation_time) - cycle_time).total_seconds())
            if new_delta < existing_delta:
                obs_by_icao[icao] = obs

    # Load runway data for wind advisory scoring
    unique_icaos = list(set(obs_by_icao.keys()))
    runway_map = get_runway_ends(unique_icaos, airports_db_path)

    # Bulk-fetch existing score keys to avoid per-row duplicate checks
    def _strip_tz(dt: datetime) -> datetime:
        return dt.replace(tzinfo=None) if dt and dt.tzinfo else dt

    existing_score_keys: set[tuple] = set()
    score_key_rows = db.execute(
        select(
            VerificationScoreRow.icao,
            VerificationScoreRow.observation_time,
            VerificationScoreRow.model,
            VerificationScoreRow.model_init_time,
        ).where(
            VerificationScoreRow.source == "standalone",
            VerificationScoreRow.observation_time.between(window_start, window_end),
        )
    ).all()
    for r in score_key_rows:
        existing_score_keys.add((r[0], _strip_tz(r[1]), r[2], _strip_tz(r[3])))

    existing_taf_keys: set[tuple] = set()
    taf_key_rows = db.execute(
        select(
            TafVerificationScoreRow.icao,
            TafVerificationScoreRow.observation_time,
            TafVerificationScoreRow.taf_issue_time,
        ).where(
            TafVerificationScoreRow.source == "standalone",
            TafVerificationScoreRow.observation_time.between(window_start, window_end),
        )
    ).all()
    for r in taf_key_rows:
        existing_taf_keys.add((r[0], _strip_tz(r[1]), _strip_tz(r[2])))

    scores_created = 0

    for snap in matching_snapshots:
        obs = obs_by_icao.get(snap.icao)
        if obs is None:
            continue

        # Compute days_out
        snap_init = snap.model_init_time
        if snap_init.tzinfo is None:
            snap_init = snap_init.replace(tzinfo=timezone.utc)
        fh = snap.forecast_hour
        if fh.tzinfo is None:
            fh = fh.replace(tzinfo=timezone.utc)
        days_out = (fh.date() - snap_init.date()).days

        # Check duplicate via in-memory set (strip tz for SQLite compat)
        score_key = (snap.icao, _strip_tz(obs.observation_time), snap.model, _strip_tz(snap.model_init_time))
        if score_key in existing_score_keys:
            continue

        weather = json.loads(obs.weather) if obs.weather else []
        runway_ends = runway_map.get(snap.icao, [])

        hourly = _snapshot_to_hourly(snap)
        # Reconstruct sounding proxy from stored snapshot fields (no API call)
        sounding_proxy = _build_sounding_proxy(snap)
        score_row = _score_model_vs_metar(
            obs_row=obs,
            obs_weather=weather,
            sounding=sounding_proxy,
            hourly=hourly,
            runway_ends=runway_ends,
            model=snap.model,
            model_init_time=snap.model_init_time,
            days_out=days_out,
            source="standalone",
        )

        if score_row is not None:
            # Add cloud_base and LCL deltas
            if obs.ceiling_ft is not None:
                if snap.cloud_base_ft is not None:
                    score_row.cloud_base_delta_ft = snap.cloud_base_ft - float(obs.ceiling_ft)
                if snap.lcl_ft is not None:
                    score_row.lcl_delta_ft = snap.lcl_ft - float(obs.ceiling_ft)
            db.add(score_row)
            existing_score_keys.add(score_key)
            scores_created += 1

    # TAF scoring
    for obs in obs_by_icao.values():
        if obs.taf_issue_time is None:
            continue

        taf_key = (obs.icao, _strip_tz(obs.observation_time), _strip_tz(obs.taf_issue_time))
        if taf_key in existing_taf_keys:
            continue

        weather = json.loads(obs.weather) if obs.weather else []
        runway_ends = runway_map.get(obs.icao, [])

        taf_row = _score_taf_vs_metar(obs, weather, runway_ends, source="standalone")
        if taf_row is not None:
            db.add(taf_row)
            existing_taf_keys.add(taf_key)
            scores_created += 1

    db.flush()
    return scores_created


# ---------------------------------------------------------------------------
# Phase E: Prune old forecast snapshots
# ---------------------------------------------------------------------------

def _prune_old_snapshots(db, retention_days: int = 10) -> int:
    """Delete forecast snapshots older than retention_days."""
    cutoff = datetime.now(timezone.utc) - timedelta(days=retention_days)
    result = db.execute(
        AirportForecastSnapshotRow.__table__.delete().where(
            AirportForecastSnapshotRow.fetched_at < cutoff
        )
    )
    return result.rowcount


# ---------------------------------------------------------------------------
# Main cycle orchestrator
# ---------------------------------------------------------------------------

def run_standalone_cycle(
    airports: list[WatchlistAirport],
    airports_db_path: str,
    *,
    fetch_forecasts: bool = True,
) -> dict:
    """Run one standalone verification cycle.

    When *fetch_forecasts* is True (full cycle), runs Phases A-E including
    Open-Meteo forecast fetching.  When False (light cycle), skips straight
    to observations + scoring — no external API calls except aviationweather.gov.

    Returns a summary dict with counts and timing.
    """
    from flyfun_common.db import SessionLocal
    from weatherbrief.fetch.model_status import fetch_model_metadata

    cycle_type = "full" if fetch_forecasts else "light"
    t_start = time.monotonic()
    now = datetime.now(timezone.utc)

    db = SessionLocal()
    session = requests.Session()

    try:
        models_fetched = 0
        snapshots_stored = 0

        if not fetch_forecasts:
            logger.info("Light cycle — skipping forecast fetch, observations + scoring only")
        else:
            # Phase A+B: Fetch and store forecasts per model
            # Process one model at a time to limit memory usage — each model's
            # snapshots are stored and freed before fetching the next.
            metadata = fetch_model_metadata(STANDALONE_MODELS)

        for model in (STANDALONE_MODELS if fetch_forecasts else []):
            meta = metadata.get(model)
            if meta is None:
                logger.warning("No metadata for model %s, skipping", model)
                continue

            init_time = datetime.fromtimestamp(meta.last_init_time, tz=timezone.utc)

            # Check if we already have snapshots for this init time
            existing = db.execute(
                select(AirportForecastSnapshotRow.id)
                .where(AirportForecastSnapshotRow.model == model)
                .where(AirportForecastSnapshotRow.model_init_time == init_time)
                .limit(1)
            ).scalar_one_or_none()

            if existing is not None:
                logger.info("Model %s init %s already stored, skipping fetch", model, init_time)
                continue

            logger.info("Fetching %s forecasts (init %s) for %d airports",
                        model, init_time, len(airports))

            snapshots = _fetch_forecasts_for_model(model, init_time, airports, session)
            logger.info("Model %s: %d snapshot values from Open-Meteo", model, len(snapshots))

            # GRIB enrichment for ceiling/cloud_base
            _enrich_with_grib(snapshots, model, init_time, airports, session)

            # Store immediately, commit, and free memory
            stored = _store_snapshots(snapshots, db)
            db.commit()
            snapshots_stored += stored
            logger.info("Model %s: stored %d snapshots", model, stored)
            del snapshots
            models_fetched += 1

        t_fetch_done = time.monotonic()

        # Phase C: Fetch METAR/TAF
        obs_stored = _fetch_and_store_observations(airports, airports_db_path, db)
        t_obs_done = time.monotonic()
        logger.info("Stored %d new observations", obs_stored)

        # Phase D: Score
        scores_created = _score_cycle(now, airports, airports_db_path, db)
        t_score_done = time.monotonic()
        logger.info("Created %d scores", scores_created)

        # Phase E: Prune
        pruned = _prune_old_snapshots(db)
        if pruned:
            logger.info("Pruned %d old forecast snapshots", pruned)

        # Record cycle metrics
        t_end = time.monotonic()
        duration_ms = int((t_end - t_start) * 1000)

        cycle_row = VerificationCycleRow(
            started_at=now,
            duration_ms=duration_ms,
            source=f"standalone_{cycle_type}",
            # fetch+store is interleaved per model, so combined into phase_fetch
            phase_fetch_ms=int((t_fetch_done - t_start) * 1000),
            phase_gather_ms=int((t_obs_done - t_fetch_done) * 1000),
            phase_score_ms=int((t_score_done - t_obs_done) * 1000),
            airports=len(airports),
            observations_stored=obs_stored,
            scored=scores_created,
        )
        db.add(cycle_row)
        db.commit()

        return {
            "cycle_type": cycle_type,
            "models_fetched": models_fetched,
            "snapshots_stored": snapshots_stored,
            "observations_stored": obs_stored,
            "scores_created": scores_created,
            "pruned": pruned,
            "duration_ms": duration_ms,
        }

    except Exception:
        db.rollback()
        # Record the failure in a separate session so the error row survives
        _record_failed_cycle(now, t_start, cycle_type, len(airports))
        raise
    finally:
        session.close()
        db.close()


def _record_failed_cycle(
    started_at: datetime,
    t_start: float,
    cycle_type: str,
    airport_count: int,
) -> None:
    """Commit a VerificationCycleRow with error info using a fresh session."""
    import traceback

    from flyfun_common.db import SessionLocal

    duration_ms = int((time.monotonic() - t_start) * 1000)
    error_msg = traceback.format_exc()[-500:]  # last 500 chars

    try:
        err_db = SessionLocal()
        cycle_row = VerificationCycleRow(
            started_at=started_at,
            duration_ms=duration_ms,
            source=f"standalone_{cycle_type}",
            phase_fetch_ms=0,
            phase_find_ms=0,
            phase_gather_ms=0,
            phase_score_ms=0,
            phase_finalize_ms=0,
            airports=airport_count,
            observations_stored=0,
            scored=0,
            error=error_msg,
        )
        err_db.add(cycle_row)
        err_db.commit()
        err_db.close()
        logger.info("Recorded failed %s cycle in DB (%dms)", cycle_type, duration_ms)
    except Exception:
        logger.warning("Failed to record error cycle row", exc_info=True)
