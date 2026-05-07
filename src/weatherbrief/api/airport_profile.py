"""Airport profile SSE endpoint for the forecast map's right-click panel.

Provides a phased SSE stream so the frontend can paint axes immediately,
fill in surface scalars from cache, then layer in pressure-level data
(Open-Meteo), augment with local-GRIB sources where available, and run
the sounding analysis on the assembled profile.

Phases:
  meta     — icao + lat/lon/elevation + hour list (~0ms)
  surface  — airport_forecast_snapshots cache (~0ms)
  levels   — Open-Meteo per-airport pressure-level fetch (1–2s)
  enriched — local-GRIB augmentation via fetch.grib.enrich_forecasts (up to ~10s)
  derived  — analyze_sounding on the (possibly-enriched) profile (~50ms)
  complete — done

Each phase event carries the partial data the client needs to render
that layer; the client doesn't need to wait for `complete` to start
showing anything. The `enriched` phase is skipped fast-path when no
local GRIB is configured (see `_grib_enrich_levels`).
"""

from __future__ import annotations

import asyncio
import json as json_mod
import logging
from collections.abc import AsyncGenerator
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from flyfun_common.db import current_user_id, get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/maps", tags=["maps"])

_VALID_MODELS = ("gfs", "icon", "ecmwf")
_DEFAULT_WINDOW_H = 3  # selected hour + 3 forward = 4 forecast hours


def _iso_utc(dt: datetime) -> str:
    """Serialize a datetime as a UTC-aware ISO 8601 string.

    All time keys emitted by this endpoint go through here so that strict
    equality (`===`) on the client matches between phases. Open-Meteo
    sometimes returns naive datetimes; we treat those as UTC.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.isoformat()


from weatherbrief.api.deps import airports_db as _airports_db, data_dir as _data_dir


def _resolve_airport_coords(icao: str, airports_db_path: str) -> tuple[float, float, float | None] | None:
    """Look up an ICAO's lat/lon/elevation from the euro_aip database.

    Returns (lat, lon, elevation_ft) or None if not found.
    """
    from weatherbrief.airports import _load_airport_model

    model = _load_airport_model(airports_db_path)
    apt = model.airports.get(icao)
    if apt is None:
        return None
    # euro_aip's Airport model field name varies — try common spellings.
    elev_ft = None
    for attr in ("elevation_ft", "elevation_feet"):
        v = getattr(apt, attr, None)
        if v is not None:
            elev_ft = float(v)
            break
    if elev_ft is None:
        # Fall back to meters → feet if available.
        elev_m = getattr(apt, "elevation_m", None) or getattr(apt, "elevation_meters", None)
        if elev_m is not None:
            elev_ft = float(elev_m) * 3.28084
    return float(apt.latitude_deg), float(apt.longitude_deg), elev_ft


def _model_to_source(model: str):
    """Map model id to OpenMeteo ModelSource."""
    from weatherbrief.models import ModelSource

    return {
        "gfs": ModelSource.GFS,
        "icon": ModelSource.ICON,
        "ecmwf": ModelSource.ECMWF,
    }[model]


def _build_hours(start_hour: datetime, window_h: int) -> list[datetime]:
    """Return the [start, start+1, ..., start+window_h] hour list."""
    return [start_hour + timedelta(hours=i) for i in range(window_h + 1)]


def _surface_from_cache(
    db: Session,
    icao: str,
    model: str,
    hours: list[datetime],
) -> list[dict[str, Any]]:
    """Read surface fields from airport_forecast_snapshots for each hour.

    Picks the most recent snapshot per (icao, model, forecast_hour). Hours
    with no data simply don't appear in the result; the frontend treats
    those as gaps.
    """
    from weatherbrief.db.models import AirportForecastSnapshotRow

    if not hours:
        return []

    rows = db.execute(
        select(AirportForecastSnapshotRow)
        .where(AirportForecastSnapshotRow.icao == icao)
        .where(AirportForecastSnapshotRow.model == model)
        .where(AirportForecastSnapshotRow.forecast_hour.in_(hours))
        .order_by(
            AirportForecastSnapshotRow.forecast_hour.asc(),
            AirportForecastSnapshotRow.model_init_time.desc(),
        )
    ).scalars().all()

    # Pick the freshest snapshot per forecast_hour (latest model_init_time).
    by_hour: dict[datetime, AirportForecastSnapshotRow] = {}
    for r in rows:
        fh = r.forecast_hour
        if fh.tzinfo is None:
            fh = fh.replace(tzinfo=timezone.utc)
        if fh not in by_hour:
            by_hour[fh] = r

    result: list[dict[str, Any]] = []
    for h in hours:
        # Match either tz-aware or naive (DBs vary).
        row = by_hour.get(h) or by_hour.get(h.replace(tzinfo=None))
        if row is None:
            continue
        result.append({
            "time": _iso_utc(h),
            "temperature_2m_c": row.temperature_2m_c,
            "dewpoint_2m_c": row.dewpoint_2m_c,
            "visibility_m": row.visibility_m,
            "wind_speed_kt": row.wind_speed_10m_kt,
            "wind_direction_deg": row.wind_direction_10m_deg,
            "wind_gusts_kt": row.wind_gusts_10m_kt,
            "precipitation_mm": row.precipitation_mm,
            "snowfall_cm": row.snowfall_cm,
            "cape_jkg": row.cape_jkg,
            "cloud_cover_pct": row.cloud_cover_pct,
            "cloud_cover_low_pct": row.cloud_cover_low_pct,
            "ceiling_ft": row.nwp_ceiling_ft or row.sounding_ceiling_ft,
            "freezing_level_ft": row.freezing_level_ft,
        })
    return result


def _fetch_pressure_levels(
    icao: str,
    lat: float,
    lon: float,
    model: str,
    hours: list[datetime],
):
    """Fetch hourly pressure-level data from Open-Meteo for one airport.

    Returns a WaypointForecast (or None on failure).
    """
    from weatherbrief.fetch.open_meteo import OpenMeteoClient
    from weatherbrief.models import RoutePoint

    if not hours:
        return None

    client = OpenMeteoClient(timeout=30)
    point = RoutePoint(
        lat=lat, lon=lon, distance_from_origin_nm=0.0,
    )
    start_date = hours[0].date().isoformat()
    end_date = hours[-1].date().isoformat()

    try:
        forecasts = client.fetch_multi_point(
            [point], _model_to_source(model),
            start_date=start_date, end_date=end_date,
        )
    except Exception:
        logger.warning(
            "Airport profile: pressure-level fetch failed for %s %s (%.4f, %.4f)",
            icao, model, lat, lon, exc_info=True,
        )
        return None
    return forecasts[0] if forecasts else None


def _hourly_to_dict(h) -> dict[str, Any]:
    """Project a HourlyForecast to a serializable dict (levels included).

    The `time` key uses `_iso_utc()` so it round-trips exactly with the
    `meta.hours[i]` strings on the client (Open-Meteo returns naive
    datetimes which would otherwise miss the `+00:00` suffix).
    """
    return {
        "time": _iso_utc(h.time),
        "temperature_2m_c": h.temperature_2m_c,
        "dewpoint_2m_c": h.dewpoint_2m_c,
        "wind_speed_10m_kt": h.wind_speed_10m_kt,
        "wind_direction_10m_deg": h.wind_direction_10m_deg,
        "wind_gusts_10m_kt": h.wind_gusts_10m_kt,
        "cape_jkg": h.cape_jkg,
        "cloud_cover_pct": h.cloud_cover_pct,
        "cloud_cover_low_pct": h.cloud_cover_low_pct,
        "freezing_level_m": h.freezing_level_m,
        "visibility_m": h.visibility_m,
        "pressure_levels": [
            {
                "pressure_hpa": pl.pressure_hpa,
                "altitude_ft": (pl.geopotential_height_m * 3.28084) if pl.geopotential_height_m is not None else None,
                "temperature_c": pl.temperature_c,
                "dewpoint_c": pl.dewpoint_c,
                "wind_speed_kt": pl.wind_speed_kt,
                "wind_direction_deg": pl.wind_direction_deg,
                "relative_humidity_pct": pl.relative_humidity_pct,
                "cloud_area_fraction_pct": pl.cloud_area_fraction_pct,
            }
            for pl in (h.pressure_levels or [])
        ],
    }


def _grib_enrich_levels(
    waypoint_forecast,
    lat: float,
    lon: float,
    model: str,
    hours: list[datetime],
    data_dir,
) -> dict[str, Any]:
    """Run the local-GRIB enrichment pipeline against a synthetic single-point
    "route" so the existing per-flight machinery can run unchanged.

    The pipeline (`fetch.grib.enrich_forecasts`) is route-shaped: it expects
    a list of `RouteCrossSection`, a list of `RoutePoint`, and a flight
    departure time / duration. Wrap our (icao, model, hours[]) into that
    shape and let the function modify the WaypointForecast in-place.

    The returned dict is suitable for the SSE payload — it summarises which
    sources contributed (so the client can show "GRIB enriched: ECMWF run
    12Z" or "no local GRIB available").

    NOTE: This path requires:
      - ECMWF: a populated ECMWF_GRIB_DIR with a recent run covering hours[-1]
      - ICON-EU: airport inside the ICON-EU domain (~Europe + adjoining seas)
      - GFS: NOAA S3 access (GFS GRIB is downloaded on-demand into data_dir)
    Any of those failing is non-fatal: the pre-enrichment Open-Meteo levels
    stay intact and `analyze_sounding` still runs in the derived phase.
    """
    if waypoint_forecast is None or not hours:
        return {"sources": {}, "skipped": {"all": "no_levels"}}

    # Fast preflight: if nothing is configured locally, skip the heavy
    # call entirely so dev environments don't pay a futile S3 round-trip
    # on every right-click. The check is intentionally cheap and
    # conservative — production deployments set ECMWF_GRIB_DIR to a real
    # populated directory; dev typically doesn't.
    from weatherbrief.fetch.grib.ecmwf_fetch import ecmwf_grib_dir
    ecmwf_dir = ecmwf_grib_dir()
    # is_dir() returns False for nonexistent paths — no exception risk.
    if not (ecmwf_dir.is_dir() and any(ecmwf_dir.iterdir())):
        return {"sources": {}, "skipped": {"all": "no_local_grib_configured"}}

    from datetime import timezone as _tz

    from weatherbrief.fetch.grib import enrich_forecasts
    from weatherbrief.models import RouteCrossSection, RoutePoint

    src = _model_to_source(model)
    point = RoutePoint(lat=lat, lon=lon, distance_from_origin_nm=0.0)
    cs = RouteCrossSection(
        model=src,
        route_points=[point],
        fetched_at=datetime.now(_tz.utc),
        point_forecasts=[waypoint_forecast],
    )

    departure = hours[0]
    duration_h = (hours[-1] - hours[0]).total_seconds() / 3600.0

    try:
        grib_init_times, grib_skip_reasons = enrich_forecasts(
            cross_sections=[cs],
            all_forecasts=[waypoint_forecast],
            route_points=[point],
            departure_time=departure,
            data_dir=data_dir,
            flight_duration_hours=duration_h,
        )
    except Exception:
        logger.warning("Airport profile: GRIB enrichment failed", exc_info=True)
        return {"sources": {}, "skipped": {"all": "exception"}}

    sources: dict[str, dict[str, Any]] = {}
    for m, ts in grib_init_times.items():
        sources[m] = {"init_time_unix": ts}
    skipped = {m: reason for m, reason in grib_skip_reasons.items()}
    return {"sources": sources, "skipped": skipped}


def _build_derived_payload(
    waypoint_forecast,
    hours: list[datetime],
) -> list[dict[str, Any]]:
    """Run analyze_sounding on each hour and build per-point analysis dicts."""
    from weatherbrief.analysis.sounding import analyze_sounding

    if waypoint_forecast is None:
        return []

    # Index hourly entries by their UTC time (naive form for matching).
    by_time: dict[Any, Any] = {}
    for h in waypoint_forecast.hourly:
        ht = h.time.replace(tzinfo=None) if h.time.tzinfo else h.time
        by_time[ht] = h

    results: list[dict[str, Any]] = []
    for idx, target_hour in enumerate(hours):
        hourly = by_time.get(target_hour.replace(tzinfo=None))
        if hourly is None or not hourly.pressure_levels:
            continue
        try:
            sa = analyze_sounding(hourly.pressure_levels, hourly)
        except Exception:
            logger.debug(
                "analyze_sounding failed for airport profile hour %s",
                target_hour, exc_info=True,
            )
            sa = None
        if sa is None:
            continue
        results.append({
            "point_index": idx,
            "time": _iso_utc(target_hour),
            "sounding": sa.model_dump(mode="json"),
        })
    return results


@router.get("/airport-profile")
async def get_airport_profile(
    icao: str = Query(..., description="Airport ICAO code"),
    model: str = Query(default="ecmwf", description="Weather model: gfs / icon / ecmwf"),
    start_hour: str = Query(..., description="ISO 8601 UTC start hour, e.g. 2026-05-07T12:00:00Z"),
    window_h: int = Query(default=_DEFAULT_WINDOW_H, ge=0, le=12),
    enrich: bool = Query(default=True, description="Run local-GRIB enrichment phase"),
    _user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
    airports_db: str = Depends(_airports_db),
    data_dir: Path = Depends(_data_dir),
):
    """Stream airport profile data via Server-Sent Events.

    Phases (each emitted as a separate SSE event):
      - meta:    icao, lat/lon, elevation, hours covered
      - surface: cached surface fields per hour
      - levels:  raw pressure-level data per hour (Open-Meteo)
      - derived: analyzed sounding (clouds, icing, CAT, indices) per hour
      - complete or error
    """
    icao = icao.upper()
    if model not in _VALID_MODELS:
        raise HTTPException(status_code=400, detail=f"Invalid model: {model}")

    try:
        start_dt = datetime.fromisoformat(start_hour.replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid start_hour (expected ISO 8601)")
    if start_dt.tzinfo is None:
        start_dt = start_dt.replace(tzinfo=timezone.utc)
    else:
        start_dt = start_dt.astimezone(timezone.utc)
    # Snap to top-of-hour
    start_dt = start_dt.replace(minute=0, second=0, microsecond=0)

    coords = _resolve_airport_coords(icao, airports_db)
    if coords is None:
        raise HTTPException(status_code=404, detail=f"Airport {icao} not found")
    lat, lon, elevation_ft = coords

    hours = _build_hours(start_dt, window_h)

    # Snapshot surface from cache before opening the long-lived stream so
    # any DB error surfaces as a normal HTTP error (cleaner UX than an
    # SSE error mid-stream).
    surface = _surface_from_cache(db, icao, model, hours)

    async def event_generator() -> AsyncGenerator[str, None]:
        def _event(event_type: str, payload: dict) -> str:
            return f"event: {event_type}\ndata: {json_mod.dumps(payload, default=str)}\n\n"

        def _error_event(phase: str, message: str) -> str:
            """Match /refresh/stream's pattern: emit a structured error
            event so the client sees a clean handoff instead of the
            EventSource onerror connection-level signal. After yielding
            this, the generator should `break` out of `event_generator`
            so the SSE stream closes normally."""
            return _event("error", {"type": "error", "phase": phase, "message": message})

        # Phase 0 (meta): instant — lets the client size the canvas.
        meta = {
            "type": "meta",
            "icao": icao,
            "lat": lat,
            "lon": lon,
            "elevation_ft": elevation_ft,
            "model": model,
            "start_hour": _iso_utc(start_dt),
            "window_h": window_h,
            "hours": [_iso_utc(h) for h in hours],
        }
        yield _event("meta", meta)

        # Phase 1 (surface): cached scalars. Already snapshotted above so
        # nothing to do in the executor.
        yield _event("surface", {"type": "surface", "hours": surface})

        # Phase 2 (levels) — runs in a thread since it does network I/O.
        loop = asyncio.get_running_loop()
        try:
            wf = await loop.run_in_executor(
                None, _fetch_pressure_levels, icao, lat, lon, model, hours,
            )
        except Exception as exc:
            logger.warning("Airport profile: levels fetch raised: %s", exc, exc_info=True)
            wf = None

        if wf is not None:
            # Filter to the requested hours. Open-Meteo can return naive
            # datetimes; normalize before matching.
            target_set = {h.replace(tzinfo=None) for h in hours}
            kept = []
            for h in wf.hourly:
                ht = h.time.replace(tzinfo=None) if h.time.tzinfo else h.time
                if ht in target_set:
                    kept.append(_hourly_to_dict(h))
            yield _event("levels", {"type": "levels", "hours": kept})
        else:
            yield _event("levels", {"type": "levels", "hours": [], "error": "fetch_failed"})

        # Phase 2.5 (enriched) — local-GRIB augmentation. Modifies wf in-place.
        # Skipped when the levels phase failed (nothing to augment) or when
        # the caller passed enrich=false. The whole phase is guarded so a
        # PermissionError on iterdir() (preflight) or any other unexpected
        # raise doesn't kill the stream — the client still gets the derived
        # phase on the unenriched profile.
        if enrich and wf is not None:
            try:
                enrichment = await loop.run_in_executor(
                    None, _grib_enrich_levels, wf, lat, lon, model, hours, data_dir,
                )
            except Exception as exc:
                logger.warning(
                    "Airport profile: enrichment executor raised: %s", exc, exc_info=True,
                )
                enrichment = {"sources": {}, "skipped": {"all": "exception"}}
            yield _event("enriched", {"type": "enriched", **enrichment})

        # Phase 3 (derived) — analyze_sounding on the (possibly-enriched) levels.
        # If this fails the client gets nothing useful past the levels phase;
        # emit a structured error and stop instead of letting the executor
        # exception bubble up and kill the stream silently.
        try:
            derived = await loop.run_in_executor(
                None, _build_derived_payload, wf, hours,
            )
        except Exception as exc:
            logger.warning(
                "Airport profile: derived executor raised: %s", exc, exc_info=True,
            )
            yield _error_event("derived", str(exc))
            return
        yield _event("derived", {"type": "derived", "points": derived})

        yield _event("complete", {"type": "complete"})

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
