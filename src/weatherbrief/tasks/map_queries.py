"""Database queries for weather overview maps.

Two query types:
1. Forecast map — latest model forecasts at ~830 airports for a selected hour
2. Verification bias map — per-airport accuracy aggregations from verification scores
"""

from __future__ import annotations

import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from weatherbrief.analysis.airport_conditions import classify_flight_category
from weatherbrief.analysis.comparison import (
    circular_spread,
    compare_models,
)
from weatherbrief.db.models import AirportForecastSnapshotRow, VerificationScoreRow
from weatherbrief.models.airport_conditions import FlightCategory
from weatherbrief.tasks.airport_watchlist import (
    WatchlistAirport,
    get_configs_dir,
    load_watchlist_with_coords,
)

logger = logging.getLogger(__name__)

_M_PER_SM = 1609.34

# Models used in standalone verification
_MODELS = ["gfs", "icon", "ecmwf"]

# ---------------------------------------------------------------------------
# Watchlist coordinate cache (loaded once per process)
# ---------------------------------------------------------------------------

_coords_cache: dict[str, tuple[float, float]] | None = None


def _get_coords(airports_db_path: str) -> dict[str, tuple[float, float]]:
    """Return icao → (lat, lon) mapping, cached in-process."""
    global _coords_cache
    if _coords_cache is None:
        airports = load_watchlist_with_coords(get_configs_dir(), airports_db_path)
        _coords_cache = {a.icao: (a.lat, a.lon) for a in airports}
    return _coords_cache


# ---------------------------------------------------------------------------
# 1. Forecast map data
# ---------------------------------------------------------------------------


def _best_ceiling(snap: AirportForecastSnapshotRow) -> float | None:
    """Pick the best ceiling estimate from a snapshot."""
    for field in ("sounding_ceiling_ft", "nwp_ceiling_ft", "cloud_base_ft", "lcl_ft"):
        val = getattr(snap, field, None)
        if val is not None:
            return val
    return None


def _flight_category(snap: AirportForecastSnapshotRow) -> str:
    """Derive flight category from snapshot fields."""
    ceiling = _best_ceiling(snap)
    vis_sm = snap.visibility_m / _M_PER_SM if snap.visibility_m is not None else None
    return classify_flight_category(ceiling, vis_sm).value


def _snap_to_dict(snap: AirportForecastSnapshotRow) -> dict[str, Any]:
    """Convert a forecast snapshot to a lightweight dict for the API response."""
    return {
        "ceiling_ft": _best_ceiling(snap),
        "visibility_m": snap.visibility_m,
        "wind_speed_kt": snap.wind_speed_10m_kt,
        "wind_dir_deg": snap.wind_direction_10m_deg,
        "wind_gust_kt": snap.wind_gusts_10m_kt,
        "cloud_cover_pct": snap.cloud_cover_pct,
        "cape_jkg": snap.cape_jkg,
        "convective_risk": snap.sounding_convective_risk or "none",
        "temperature_c": snap.temperature_2m_c,
        "flight_category": _flight_category(snap),
    }


def _consensus(per_model: dict[str, dict], mode: str = "worst") -> dict[str, Any]:
    """Compute consensus across models for key variables.

    mode: "worst" = most restrictive category, "majority" = most common (worst as tiebreaker)
    """
    models_with_data = list(per_model.keys())
    if not models_with_data:
        return {"flight_category": "VFR", "agreement": "good"}

    cats = [FlightCategory(m["flight_category"]) for m in per_model.values()]

    if mode == "majority":
        counts = Counter(cats)
        max_count = max(counts.values())
        tied = [c for c, n in counts.items() if n == max_count]
        # Most votes wins; on tie, pick worst (most restrictive)
        consensus_cat = FlightCategory.worst(tied).value
    else:
        consensus_cat = FlightCategory.worst(cats).value

    # Overall agreement — check key variables
    agreement = "good"
    for var in ("wind_speed_kt", "ceiling_ft", "cape_jkg"):
        vals = {m: per_model[m].get(var) for m in models_with_data}
        vals = {m: v for m, v in vals.items() if v is not None}
        if len(vals) >= 2:
            div = compare_models(var, vals)
            if div.agreement.value == "poor":
                agreement = "poor"
                break
            elif div.agreement.value == "moderate" and agreement != "poor":
                agreement = "moderate"

    # Means for numeric fields
    result: dict[str, Any] = {
        "flight_category": consensus_cat,
        "agreement": agreement,
    }
    for field in ("wind_speed_kt", "wind_dir_deg", "ceiling_ft", "cape_jkg"):
        vals = [per_model[m].get(field) for m in models_with_data]
        vals = [v for v in vals if v is not None]
        if vals:
            if field == "wind_dir_deg":
                mean, _ = circular_spread(vals)
                result[field] = round(mean, 1)
            else:
                result[field] = round(sum(vals) / len(vals), 1)
    return result


def get_forecast_map_data(
    db: Session,
    forecast_hour: datetime,
    airports_db_path: str,
    consensus_mode: str = "worst",
) -> dict[str, Any]:
    """Return forecast data for all watchlist airports at a given hour.

    Finds the latest model_init_time per model that has data for forecast_hour,
    then returns per-airport, per-model forecasts with consensus.
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
            "forecast_time": forecast_hour.isoformat(),
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

    # Build response with coords and consensus
    airports = []
    for icao, models_data in sorted(by_airport.items()):
        if icao not in coords:
            continue
        lat, lon = coords[icao]
        airports.append({
            "icao": icao,
            "lat": lat,
            "lon": lon,
            "models": models_data,
            "consensus": _consensus(models_data, consensus_mode),
        })

    return {
        "forecast_time": forecast_hour.isoformat(),
        "model_init_times": {m: t.isoformat() for m, t in init_times.items()},
        "airports": airports,
    }


# ---------------------------------------------------------------------------
# 2. Verification bias map data
# ---------------------------------------------------------------------------


def get_verification_map_data(
    db: Session,
    since: datetime,
    until: datetime,
    model: str | None,
    days_out: int,
    airports_db_path: str,
) -> dict[str, Any]:
    """Return per-airport verification accuracy stats for the map.

    Aggregates verification_scores (source='standalone') by ICAO, computing
    accuracy metrics suitable for geographic visualization.
    """
    coords = _get_coords(airports_db_path)

    q = (
        select(
            VerificationScoreRow.icao,
            func.count().label("sample_count"),
            func.avg(VerificationScoreRow.category_match + 0).label("category_match_rate"),
            func.avg(func.abs(VerificationScoreRow.ceiling_delta_ft)).label("ceiling_mae"),
            func.avg(func.abs(VerificationScoreRow.wind_speed_delta_kt)).label("wind_mae"),
            func.avg(func.abs(VerificationScoreRow.temperature_delta_c)).label("temp_mae"),
            func.avg(func.abs(VerificationScoreRow.visibility_delta_m)).label("vis_mae"),
            # Bias direction: positive = model too high (optimistic)
            func.avg(VerificationScoreRow.ceiling_delta_ft).label("ceiling_bias"),
        )
        .where(VerificationScoreRow.source == "standalone")
        .where(VerificationScoreRow.days_out == days_out)
        .where(VerificationScoreRow.observation_time >= since)
        .where(VerificationScoreRow.observation_time <= until)
        .group_by(VerificationScoreRow.icao)
    )

    if model and model != "all":
        q = q.where(VerificationScoreRow.model == model)

    rows = db.execute(q).all()

    airports = []
    for row in rows:
        icao = row.icao
        if icao not in coords:
            continue
        lat, lon = coords[icao]
        airports.append({
            "icao": icao,
            "lat": lat,
            "lon": lon,
            "sample_count": row.sample_count,
            "category_match_pct": round(float(row.category_match_rate or 0) * 100, 1),
            "ceiling_mae_ft": round(float(row.ceiling_mae or 0), 0),
            "wind_mae_kt": round(float(row.wind_mae or 0), 1),
            "temp_mae_c": round(float(row.temp_mae or 0), 1),
            "vis_mae_m": round(float(row.vis_mae or 0), 0),
            "ceiling_bias_ft": round(float(row.ceiling_bias or 0), 0),
        })

    return {
        "period_since": since.isoformat(),
        "period_until": until.isoformat(),
        "model": model or "all",
        "days_out": days_out,
        "airports": airports,
    }
