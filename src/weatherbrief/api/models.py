"""Model catalog endpoint — static system config, no auth required."""

from __future__ import annotations

from fastapi import APIRouter

from weatherbrief.fetch.variables import MODEL_ENDPOINTS

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
