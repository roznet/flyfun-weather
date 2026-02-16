"""API endpoints for user preferences and autorouter credentials."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from weatherbrief.api.encryption import decrypt, encrypt
from weatherbrief.db.deps import current_user_id, get_db
from weatherbrief.db.models import UserPreferencesRow

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/user/preferences", tags=["preferences"])


class AdvisoryPreferences(BaseModel):
    """User's advisory configuration."""

    enabled: dict[str, bool] | None = None  # advisory_id -> enabled
    params: dict[str, dict[str, float]] | None = None  # advisory_id -> {param: value}


class FlightDefaults(BaseModel):
    """User's default flight parameters."""

    cruise_altitude_ft: int | None = None
    flight_ceiling_ft: int | None = None
    models: list[str] | None = None  # e.g. ["gfs", "ecmwf", "icon"]
    advisory_models: list[str] | None = None  # subset of models for advisories


class DigestConfig(BaseModel):
    """User's digest configuration."""

    config_name: str | None = None


class PreferencesResponse(BaseModel):
    """Preferences returned to the client (never includes raw credentials)."""

    defaults: FlightDefaults
    digest_config: DigestConfig
    advisories: AdvisoryPreferences
    has_autorouter_creds: bool
    gramet_enabled: bool
    llm_digest_enabled: bool


class PreferencesUpdate(BaseModel):
    """Payload for updating preferences."""

    defaults: FlightDefaults | None = None
    digest_config: DigestConfig | None = None
    advisories: AdvisoryPreferences | None = None
    autorouter_username: str | None = None
    autorouter_password: str | None = None
    gramet_enabled: bool | None = None
    llm_digest_enabled: bool | None = None


def _load_prefs(db: Session, user_id: str) -> UserPreferencesRow:
    """Load or create preferences row for a user."""
    row = db.get(UserPreferencesRow, user_id)
    if row is None:
        row = UserPreferencesRow(user_id=user_id)
        db.add(row)
        db.flush()
    return row


def _parse_defaults(raw: str) -> FlightDefaults:
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        data = {}
    return FlightDefaults(**data)


def _parse_service_toggles(raw: str) -> dict[str, bool]:
    """Extract gramet_enabled and llm_digest_enabled from the defaults blob."""
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        data = {}
    return {
        "gramet_enabled": data.get("gramet_enabled", True),
        "llm_digest_enabled": data.get("llm_digest_enabled", True),
    }


def _parse_advisory_prefs(raw: str) -> AdvisoryPreferences:
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        data = {}
    adv_data = data.get("advisories", {})
    return AdvisoryPreferences(**adv_data)


def _parse_digest_config(raw: str) -> DigestConfig:
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        data = {}
    return DigestConfig(**data)


def _build_response(row: UserPreferencesRow) -> PreferencesResponse:
    """Build a PreferencesResponse from a DB row."""
    toggles = _parse_service_toggles(row.defaults_json)
    return PreferencesResponse(
        defaults=_parse_defaults(row.defaults_json),
        digest_config=_parse_digest_config(row.digest_config_json),
        advisories=_parse_advisory_prefs(row.defaults_json),
        has_autorouter_creds=bool(row.encrypted_autorouter_creds),
        **toggles,
    )


@router.get("", response_model=PreferencesResponse)
def get_preferences(
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Get the current user's preferences."""
    row = _load_prefs(db, user_id)
    return _build_response(row)


@router.put("", response_model=PreferencesResponse)
def update_preferences(
    body: PreferencesUpdate,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Update the current user's preferences."""
    row = _load_prefs(db, user_id)

    # Merge into existing defaults_json to preserve sibling keys
    try:
        data = json.loads(row.defaults_json) if row.defaults_json else {}
    except json.JSONDecodeError:
        data = {}

    if body.defaults is not None:
        defaults_dict = body.defaults.model_dump(exclude_none=True)
        # Enforce advisory_models as subset of models
        if "advisory_models" in defaults_dict and "models" in defaults_dict:
            allowed = set(defaults_dict["models"])
            defaults_dict["advisory_models"] = [
                m for m in defaults_dict["advisory_models"] if m in allowed
            ]
        elif "advisory_models" in defaults_dict:
            # models not being updated — check against existing stored models
            existing_models = set(data.get("models", []))
            if existing_models:
                defaults_dict["advisory_models"] = [
                    m for m in defaults_dict["advisory_models"] if m in existing_models
                ]
        data.update(defaults_dict)

    if body.advisories is not None:
        data["advisories"] = body.advisories.model_dump(exclude_none=True)

    # Service toggles
    if body.gramet_enabled is not None:
        data["gramet_enabled"] = body.gramet_enabled
    if body.llm_digest_enabled is not None:
        data["llm_digest_enabled"] = body.llm_digest_enabled

    row.defaults_json = json.dumps(data)

    if body.digest_config is not None:
        row.digest_config_json = body.digest_config.model_dump_json(exclude_none=True)

    if body.autorouter_username and body.autorouter_password:
        payload = json.dumps({
            "username": body.autorouter_username,
            "password": body.autorouter_password,
        })
        row.encrypted_autorouter_creds = encrypt(payload)

    return _build_response(row)


@router.delete("/autorouter", status_code=204)
def clear_autorouter_credentials(
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Clear the user's stored autorouter credentials."""
    row = _load_prefs(db, user_id)
    row.encrypted_autorouter_creds = ""


def load_autorouter_credentials(db: Session, user_id: str) -> tuple[str, str] | None:
    """Load and decrypt autorouter credentials for a user.

    Returns (username, password) tuple or None if not configured.
    Used by packs.py when preparing a refresh.
    """
    row = db.get(UserPreferencesRow, user_id)
    if not row or not row.encrypted_autorouter_creds:
        return None
    try:
        data = json.loads(decrypt(row.encrypted_autorouter_creds))
        return data["username"], data["password"]
    except Exception:
        logger.warning("Failed to decrypt autorouter credentials for user %s", user_id)
        return None


def load_advisory_prefs(db: Session, user_id: str) -> AdvisoryPreferences:
    """Load advisory preferences for a user.

    Returns AdvisoryPreferences with None for unset fields.
    Used by the pipeline when evaluating route advisories.
    """
    row = db.get(UserPreferencesRow, user_id)
    if not row:
        return AdvisoryPreferences()
    return _parse_advisory_prefs(row.defaults_json)


def load_user_defaults(db: Session, user_id: str) -> FlightDefaults:
    """Load the user's flight defaults.

    Returns a FlightDefaults with None for any unset fields.
    Used by flights.py when creating a flight.
    """
    row = db.get(UserPreferencesRow, user_id)
    if not row:
        return FlightDefaults()
    return _parse_defaults(row.defaults_json)


def load_service_toggles(db: Session, user_id: str) -> dict[str, bool]:
    """Load GRAMET and LLM digest toggle preferences for a user.

    Returns dict with ``gramet_enabled`` and ``llm_digest_enabled`` (default True).
    """
    row = db.get(UserPreferencesRow, user_id)
    if not row:
        return {"gramet_enabled": True, "llm_digest_enabled": True}
    return _parse_service_toggles(row.defaults_json)


@router.get("/advisories/catalog")
def get_advisory_catalog():
    """Return the full advisory catalog for settings UI."""
    from weatherbrief.analysis.advisories import get_catalog

    return [entry.model_dump() for entry in get_catalog()]
