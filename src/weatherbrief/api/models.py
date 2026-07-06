"""Model catalog endpoint — static system config, no auth required."""

from __future__ import annotations

from fastapi import APIRouter

from weatherbrief.fetch.variables import (
    MAX_BOOKING_LEAD_DAYS,
    MODEL_ENDPOINTS,
    dual_model_horizon_days,
)

router = APIRouter(prefix="/models", tags=["models"])


@router.get("")
def list_models():
    # ``max_days`` lets the frontend derive the bookable forecast horizon
    # (min of ECMWF and GFS, minus one) from the same source the backend gate
    # uses, instead of hardcoding it — see dual_model_horizon_days().
    return [
        {"key": k, "name": v.name, "default": v.default, "max_days": v.max_days}
        for k, v in MODEL_ENDPOINTS.items()
    ]


@router.get("/config")
def models_config():
    """Booking limits, served so the frontend shares one source with the backend
    gate instead of hardcoding them (see ``api/flights.py``).

    - ``max_booking_lead_days`` — how far ahead a flight may be saved
      (``_reject_if_beyond_booking_cap``); bounds the date picker.
    - ``forecast_horizon_days`` — last lead day with a full two-model briefing
      (``dual_model_horizon_days``); beyond it a saved flight is pending coverage.
    """
    return {
        "max_booking_lead_days": MAX_BOOKING_LEAD_DAYS,
        "forecast_horizon_days": dual_model_horizon_days(),
    }
