"""Weather overview map endpoints.

All endpoints require authentication (current_user_id).

  GET /maps/forecast              — forecast overview for all watchlist airports
  GET /maps/forecast/hours        — available sample hours for a given day
  GET /maps/airport-weather       — forecast + observations for specific airports

The legacy ``GET /maps/verification`` (per-airport accuracy map) was
removed in #154. Use the optimistic-bias leaderboard endpoint instead
(driven by ``verification_stats.get_optimistic_bias_leaderboard``).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from flyfun_common.db import current_user_id, get_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/maps", tags=["maps"])

from weatherbrief.tasks.standalone_verification import SAMPLE_HOURS_UTC as _SAMPLE_HOURS


from weatherbrief.api.deps import airports_db as _airports_db


def _forecast_hour(day: int, hour: int) -> datetime:
    """Absolute UTC datetime for a relative ``(day, hour)`` selection.

    ``day`` is days from today (0 = today). This mirrors the client's date
    labelling (``now + day``) so cache and live queries resolve to the same
    calendar date the UI displays — the relative-day cache key alone is
    ambiguous across a UTC-midnight rollover.
    """
    target_date = (datetime.now(timezone.utc) + timedelta(days=day)).date()
    return datetime(
        target_date.year, target_date.month, target_date.day,
        hour, 0, 0, tzinfo=timezone.utc,
    )


@router.get("/forecast")
def get_forecast_map(
    day: int = Query(default=0, ge=0, le=3),
    hour: int = Query(default=12),
    _user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
    airports_db: str = Depends(_airports_db),
):
    """Return forecast overview data for all watchlist airports.

    Returns per-model forecasts with worst-consensus pre-computed.
    Consensus mode switching (worst/majority) is handled client-side.

    Parameters:
        day: Days from today (0 = today, 1 = tomorrow, ...)
        hour: UTC hour (6, 9, 12, 15, 18)
    """
    from weatherbrief.tasks.cache_builder import get_cached, is_stale
    from weatherbrief.tasks.map_queries import get_forecast_map_data

    if hour not in _SAMPLE_HOURS:
        hour = min(_SAMPLE_HOURS, key=lambda h: abs(h - hour))

    cache_key = f"forecast_map:{day}:{hour}"
    if not is_stale(db, cache_key, "snapshot"):
        cached = get_cached(db, cache_key)
        if cached is not None:
            return cached

    forecast_hour = _forecast_hour(day, hour)
    return get_forecast_map_data(db, forecast_hour, airports_db)


@router.get("/forecast/hours")
def get_available_hours(
    day: int = Query(default=0, ge=0, le=3),
    _user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Return which sample hours have forecast data for a given day.

    Helps the frontend know which hour buttons to enable.
    """
    from sqlalchemy import func, select

    from weatherbrief.db.models import AirportForecastSnapshotRow

    now = datetime.now(timezone.utc)
    target_date = (now + timedelta(days=day)).date()

    # Single query: distinct forecast hours that exist for this date
    hour_rows = db.execute(
        select(func.extract("hour", AirportForecastSnapshotRow.forecast_hour))
        .where(AirportForecastSnapshotRow.forecast_hour >= datetime(
            target_date.year, target_date.month, target_date.day,
            0, 0, 0, tzinfo=timezone.utc,
        ))
        .where(AirportForecastSnapshotRow.forecast_hour < datetime(
            target_date.year, target_date.month, target_date.day,
            23, 59, 59, tzinfo=timezone.utc,
        ))
        .distinct()
    ).scalars().all()
    available = sorted(int(h) for h in hour_rows if int(h) in _SAMPLE_HOURS)

    return {"day": day, "date": target_date.isoformat(), "hours": available}


def compute_day_availability(db: Session) -> list[dict[str, Any]]:
    """Per-relative-day (D-0..D-3) flag for whether any forecast data exists.

    Pure logic (no ``Depends`` defaults) so it can be unit-tested directly and
    reused. The far day (today+3) has no snapshots until the next twice-daily
    model run extends the horizon.
    """
    from sqlalchemy import select

    from weatherbrief.db.models import AirportForecastSnapshotRow

    # Capture `now` once so all four days resolve against the same instant —
    # otherwise a UTC-midnight tick mid-loop could map two days to one date.
    now = datetime.now(timezone.utc)
    days = []
    for day in (0, 1, 2, 3):
        forecast_date = (now + timedelta(days=day)).date()
        start = datetime(
            forecast_date.year, forecast_date.month, forecast_date.day,
            0, 0, 0, tzinfo=timezone.utc,
        )
        end = start + timedelta(days=1)
        has_data = db.execute(
            select(AirportForecastSnapshotRow.id)
            .where(AirportForecastSnapshotRow.forecast_hour >= start)
            .where(AirportForecastSnapshotRow.forecast_hour < end)
            .limit(1)
        ).scalar()
        days.append({
            "day": day,
            "date": forecast_date.isoformat(),
            "available": has_data is not None,
        })
    return days


@router.get("/forecast/days")
def get_available_days(
    _user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Return which relative days (D-0..D-3) have any forecast data.

    The frontend disables days beyond the current model horizon rather than
    showing an empty or mislabelled map.
    """
    return {"days": compute_day_availability(db)}


# ---------------------------------------------------------------------------
# Airport weather — filtered forecast map for specific airports
# ---------------------------------------------------------------------------

from weatherbrief.tasks.airport_watchlist import DEFAULT_PREFIXES


def _resolve_airports(
    icao_codes: list[str],
    airports_db_path: str,
) -> tuple[list[dict[str, Any]], list[str]]:
    """Resolve requested ICAOs to watchlist airports.

    For each requested ICAO:
    - If it's in the watchlist, use it directly.
    - If its prefix is in a supported region but not in the watchlist,
      find the nearest watchlist airport.
    - If it's outside Europe, add to the unsupported list.

    Returns (resolved_airports, unsupported_icaos).
    """
    from euro_aip.models.navpoint import NavPoint

    from weatherbrief.tasks.airport_watchlist import (
        get_configs_dir,
        load_watchlist_with_coords,
    )

    watchlist = load_watchlist_with_coords(get_configs_dir(), airports_db_path)
    watchlist_by_icao = {a.icao: a for a in watchlist}

    # Look up coordinates for non-watchlist airports from euro_aip
    from weatherbrief.airports import _load_airport_model
    model = _load_airport_model(airports_db_path)

    supported_prefixes = set(DEFAULT_PREFIXES)
    resolved = []
    unsupported = []

    for icao in icao_codes:
        icao = icao.upper()
        prefix = icao[:2]

        # Direct watchlist hit
        if icao in watchlist_by_icao:
            resolved.append({"icao": icao})
            continue

        # Check if supported region
        if prefix not in supported_prefixes:
            unsupported.append(icao)
            continue

        # Find coordinates from euro_aip DB
        apt = model.airports.get(icao)
        if apt is None:
            unsupported.append(icao)
            continue

        target = NavPoint(latitude=apt.latitude_deg, longitude=apt.longitude_deg)

        # Find nearest watchlist airport
        best_icao = None
        best_dist = 200.0  # max distance in nm
        for wl_apt in watchlist:
            if wl_apt.icao[:2] != prefix:
                continue  # only search same country first
            wl_point = NavPoint(latitude=wl_apt.lat, longitude=wl_apt.lon)
            _, dist_nm = target.haversine_distance(wl_point)
            if dist_nm < best_dist:
                best_dist = dist_nm
                best_icao = wl_apt.icao

        # Fallback: search all watchlist airports if same-country didn't match
        if best_icao is None:
            for wl_apt in watchlist:
                wl_point = NavPoint(latitude=wl_apt.lat, longitude=wl_apt.lon)
                _, dist_nm = target.haversine_distance(wl_point)
                if dist_nm < best_dist:
                    best_dist = dist_nm
                    best_icao = wl_apt.icao

        if best_icao:
            resolved.append({
                "icao": best_icao,
                "requested_icao": icao,
                "resolution_distance_nm": round(best_dist, 1),
            })
        else:
            unsupported.append(icao)

    return resolved, unsupported


@router.get("/airport-weather")
def get_airport_weather(
    icao: list[str] = Query(description="ICAO codes (1-20)"),
    day: int = Query(default=0, ge=0, le=3),
    hour: int = Query(default=12),
    _user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
    airports_db: str = Depends(_airports_db),
):
    """Return forecast + observation data for specific airports.

    Resolves non-watchlist airports to the nearest monitored airport.
    Returns model predictions with consensus for D-0 to D-3, and
    METAR/TAF observations for D-0 (from the pre-built cache).
    """
    if not icao:
        raise HTTPException(status_code=400, detail="At least one ICAO code required")
    if len(icao) > 20:
        raise HTTPException(status_code=400, detail="Maximum 20 ICAO codes per request")

    resolved, unsupported = _resolve_airports(icao, airports_db)
    if not resolved:
        return {
            "airports": [],
            "unsupported": unsupported,
            "note": "No supported European airports found. Coverage: Western, Central, and Eastern Europe.",
        }

    # Prefer the pre-built cache (rebuilt frequently by the background
    # scheduler) to avoid expensive full-map queries when only a few airports
    # are needed. ``is_stale`` also rejects an entry whose relative-day index
    # no longer maps to today's date (post-midnight, before the next model
    # run); in that case compute live for the correct absolute date so the
    # response date stays consistent with the requested day/hour.
    from weatherbrief.tasks.cache_builder import get_cached, is_stale
    from weatherbrief.tasks.map_queries import (
        enrich_with_observations,
        get_forecast_map_data,
    )

    if hour not in _SAMPLE_HOURS:
        hour = min(_SAMPLE_HOURS, key=lambda h: abs(h - hour))

    cache_key = f"forecast_map:{day}:{hour}"
    data = None
    if not is_stale(db, cache_key, "snapshot"):
        data = get_cached(db, cache_key)

    if data is None:
        forecast_hour = _forecast_hour(day, hour)
        data = get_forecast_map_data(db, forecast_hour, airports_db)
        if day == 0:
            data = enrich_with_observations(db, forecast_hour, data)
        if not data.get("airports"):
            raise HTTPException(
                status_code=503,
                detail=f"Forecast data for day={day} hour={hour} is not yet "
                "available — the day may be beyond the current model horizon. "
                "The next model run extends it; try again shortly.",
            )

    # Filter to requested airports
    resolution_map = {r["icao"]: r for r in resolved if "requested_icao" in r}

    airports_out = []
    all_airports = {a["icao"]: a for a in data.get("airports", [])}

    for res in resolved:
        airport_data = all_airports.get(res["icao"])
        if airport_data is None:
            continue
        entry = dict(airport_data)
        if res["icao"] in resolution_map:
            info = resolution_map[res["icao"]]
            entry["requested_icao"] = info["requested_icao"]
            entry["resolution_distance_nm"] = info["resolution_distance_nm"]
        airports_out.append(entry)

    return {
        "forecast_time": data.get("forecast_time"),
        "model_init_times": data.get("model_init_times", {}),
        "day": day,
        "hour_utc": hour,
        "airports": airports_out,
        "unsupported": unsupported,
    }
