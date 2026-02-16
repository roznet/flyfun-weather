"""Model catalog endpoint — static system config, no auth required."""

from __future__ import annotations

from fastapi import APIRouter

from weatherbrief.fetch.variables import MODEL_ENDPOINTS

router = APIRouter(prefix="/models", tags=["models"])


@router.get("")
def list_models():
    return [
        {"key": k, "name": v.name, "default": v.default}
        for k, v in MODEL_ENDPOINTS.items()
    ]
