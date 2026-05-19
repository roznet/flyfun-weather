"""Climatology / patterns endpoints (issue #155).

Read-only endpoints over ``airport_monthly_summary`` and
``airport_daily_summary``. All ~830 watchlist airports plot per query;
indexed table reads are cheap (<200ms target), so no cache layer.

Gating note: every route currently authenticates via ``current_user_id``.
To gate the whole feature behind a user preference later, swap the
dependency for one that checks ``climatology_views_enabled`` — single-line
change per route, no other refactor needed.

  GET /climatology/category        — view #1 (VFR/MVFR/IFR/LIFR %)
  GET /climatology/phenomena       — view #2 (TS or fog frequency)
  GET /climatology/wind            — view #3 (wind metric)
  GET /climatology/volatility      — view #4 (category transitions per obs)
  GET /climatology/volatility/top  — view #4 leaderboard (top N airports)
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from flyfun_common.db import current_user_id, get_db

from weatherbrief.api.deps import airports_db as _airports_db

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/climatology", tags=["climatology"])


@router.get("/category")
def get_category_map(
    month: str = Query(..., description="Month as 'YYYY-MM' (e.g. '2026-05')"),
    _user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
    airports_db: str = Depends(_airports_db),
):
    """Per-airport flight-category counts and percentages for ``month``.

    For a completed month: reads ``airport_monthly_summary`` directly.
    For the current UTC month: SUMs ``airport_daily_summary`` over the
    completed UTC days in the month so far (``is_mtd=True`` in the
    response, plus ``as_of_date``).
    """
    from weatherbrief.tasks.climatology_queries import (
        get_category_climatology,
        resolve_month_window,
    )

    try:
        window = resolve_month_window(month)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return get_category_climatology(db, window, airports_db)


@router.get("/phenomena")
def get_phenomena_map(
    month: str = Query(..., description="Month as 'YYYY-MM'"),
    kind: str = Query(..., description="'ts' or 'fog'"),
    _user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
    airports_db: str = Depends(_airports_db),
):
    """TS or fog frequency (% of observations) per airport."""
    from weatherbrief.tasks.climatology_queries import (
        get_phenomena_climatology,
        resolve_month_window,
    )

    try:
        window = resolve_month_window(month)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return get_phenomena_climatology(db, window, airports_db, kind)


@router.get("/wind")
def get_wind_map(
    month: str = Query(..., description="Month as 'YYYY-MM'"),
    metric: str = Query(..., description="'over25', 'p95', or 'gust'"),
    _user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
    airports_db: str = Depends(_airports_db),
):
    """Wind metric per airport. MTD restricts to 'gust' only."""
    from weatherbrief.tasks.climatology_queries import (
        get_wind_climatology,
        resolve_month_window,
    )

    try:
        window = resolve_month_window(month)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return get_wind_climatology(db, window, airports_db, metric)


@router.get("/volatility")
def get_volatility_map(
    month: str = Query(..., description="Month as 'YYYY-MM'"),
    _user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
    airports_db: str = Depends(_airports_db),
):
    """Category transitions per observation, per airport."""
    from weatherbrief.tasks.climatology_queries import (
        get_volatility_climatology,
        resolve_month_window,
    )

    try:
        window = resolve_month_window(month)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return get_volatility_climatology(db, window, airports_db)


@router.get("/volatility/top")
def get_volatility_leaderboard_endpoint(
    month: str = Query(..., description="Month as 'YYYY-MM'"),
    limit: int = Query(default=20, ge=1, le=100),
    _user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
    airports_db: str = Depends(_airports_db),
):
    """Top airports ranked by category_changes / n_obs."""
    from weatherbrief.tasks.climatology_queries import (
        get_volatility_leaderboard,
        resolve_month_window,
    )

    try:
        window = resolve_month_window(month)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    return get_volatility_leaderboard(db, window, airports_db, limit=limit)
