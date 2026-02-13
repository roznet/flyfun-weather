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


class FlightDefaults(BaseModel):
    """User's default flight parameters."""

    cruise_altitude_ft: int | None = None
    flight_ceiling_ft: int | None = None
    models: list[str] | None = None  # e.g. ["gfs", "ecmwf", "icon"]


class DigestConfig(BaseModel):
    """User's digest configuration."""

    config_name: str | None = None


class PreferencesResponse(BaseModel):
    """Preferences returned to the client (never includes raw credentials)."""

    defaults: FlightDefaults
    digest_config: DigestConfig
    has_autorouter_creds: bool


class PreferencesUpdate(BaseModel):
    """Payload for updating preferences."""

    defaults: FlightDefaults | None = None
    digest_config: DigestConfig | None = None
    autorouter_username: str | None = None
    autorouter_password: str | None = None


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


def _parse_digest_config(raw: str) -> DigestConfig:
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        data = {}
    return DigestConfig(**data)


@router.get("", response_model=PreferencesResponse)
def get_preferences(
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Get the current user's preferences."""
    row = _load_prefs(db, user_id)
    return PreferencesResponse(
        defaults=_parse_defaults(row.defaults_json),
        digest_config=_parse_digest_config(row.digest_config_json),
        has_autorouter_creds=bool(row.encrypted_autorouter_creds),
    )


@router.put("", response_model=PreferencesResponse)
def update_preferences(
    body: PreferencesUpdate,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Update the current user's preferences."""
    row = _load_prefs(db, user_id)

    if body.defaults is not None:
        row.defaults_json = body.defaults.model_dump_json(exclude_none=True)

    if body.digest_config is not None:
        row.digest_config_json = body.digest_config.model_dump_json(exclude_none=True)

    if body.autorouter_username and body.autorouter_password:
        payload = json.dumps({
            "username": body.autorouter_username,
            "password": body.autorouter_password,
        })
        row.encrypted_autorouter_creds = encrypt(payload)

    return PreferencesResponse(
        defaults=_parse_defaults(row.defaults_json),
        digest_config=_parse_digest_config(row.digest_config_json),
        has_autorouter_creds=bool(row.encrypted_autorouter_creds),
    )


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


def load_user_defaults(db: Session, user_id: str) -> FlightDefaults:
    """Load the user's flight defaults.

    Returns a FlightDefaults with None for any unset fields.
    Used by flights.py when creating a flight.
    """
    row = db.get(UserPreferencesRow, user_id)
    if not row:
        return FlightDefaults()
    return _parse_defaults(row.defaults_json)
