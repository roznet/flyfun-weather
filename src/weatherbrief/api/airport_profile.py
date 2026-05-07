"""Airport profile SSE endpoint for the forecast map's right-click panel.

Provides a phased SSE stream so the frontend can paint axes immediately,
fill in surface scalars from cache, then layer in pressure-level data
(Open-Meteo) and the analyzed sounding once they're ready.

Phases:
  surface  — airport_forecast_snapshots cache (~0ms)
  levels   — Open-Meteo per-airport pressure-level fetch (1–2s)
  derived  — analyze_sounding on combined data (~50ms)
  complete — done

Each phase event carries the partial data the client needs to render
that layer; the client doesn't need to wait for `complete` to start
showing anything.

Note: GRIB enrichment (the `enriched` phase in the design doc) is not
yet implemented. The frontend tolerates its absence — derived runs on
the levels-only profile when no enriched payload arrives.
"""

from __future__ import annotations

import asyncio
import json as json_mod
import logging
from collections.abc import AsyncGenerator
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from flyfun_common.db import current_user_id, get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/maps", tags=["maps"])

_VALID_MODELS = ("gfs", "icon", "ecmwf")
_DEFAULT_WINDOW_H = 3  # selected hour + 3 forward = 4 forecast hours


def _airports_db(request: Request) -> str:
    return request.app.state.db_path


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
            "time": h.isoformat(),
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
            "Airport profile: pressure-level fetch failed for %s/%s",
            model, lat, exc_info=True,
        )
        return None
    return forecasts[0] if forecasts else None


def _hourly_to_dict(h) -> dict[str, Any]:
    """Project a HourlyForecast to a serializable dict (levels included)."""
    return {
        "time": h.time.isoformat() if hasattr(h.time, "isoformat") else str(h.time),
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
        ht = h.time.replace(tzinfo=None) if hasattr(h.time, "tzinfo") and h.time.tzinfo else h.time
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
            "time": target_hour.isoformat(),
            "sounding": sa.model_dump(mode="json"),
        })
    return results


@router.get("/airport-profile")
async def get_airport_profile(
    icao: str = Query(..., description="Airport ICAO code"),
    model: str = Query(default="ecmwf", description="Weather model: gfs / icon / ecmwf"),
    start_hour: str = Query(..., description="ISO 8601 UTC start hour, e.g. 2026-05-07T12:00:00Z"),
    window_h: int = Query(default=_DEFAULT_WINDOW_H, ge=0, le=12),
    request: Request = None,  # type: ignore[assignment]
    _user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
    airports_db: str = Depends(_airports_db),
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

        # Phase 0 (meta): instant — lets the client size the canvas.
        meta = {
            "type": "meta",
            "icao": icao,
            "lat": lat,
            "lon": lon,
            "elevation_ft": elevation_ft,
            "model": model,
            "start_hour": start_dt.isoformat(),
            "window_h": window_h,
            "hours": [h.isoformat() for h in hours],
        }
        yield _event("meta", meta)

        # Phase 1 (surface): cached scalars.
        yield _event("surface", {"type": "surface", "hours": surface})

        # Phase 2 (levels) — runs in a thread since it does network I/O.
        loop = asyncio.get_event_loop()
        try:
            wf = await loop.run_in_executor(
                None, _fetch_pressure_levels, lat, lon, model, hours,
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
                ht = h.time.replace(tzinfo=None) if hasattr(h.time, "tzinfo") and h.time.tzinfo else h.time
                if ht in target_set:
                    kept.append(_hourly_to_dict(h))
            yield _event("levels", {"type": "levels", "hours": kept})
        else:
            yield _event("levels", {"type": "levels", "hours": [], "error": "fetch_failed"})

        # Phase 3 (derived) — analyze_sounding on the levels.
        derived = await loop.run_in_executor(
            None, _build_derived_payload, wf, hours,
        )
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
