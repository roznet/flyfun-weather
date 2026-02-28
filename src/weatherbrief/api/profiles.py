"""API endpoints for flight profile management."""

from __future__ import annotations

import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from weatherbrief.db.deps import current_user_id, get_db
from weatherbrief.db.models import FlightProfileRow
from weatherbrief.storage.flights import (
    delete_profile,
    ensure_default_profile,
    list_profiles,
    load_profile,
    save_profile,
    update_profile,
)
from weatherbrief.models import FlightProfile

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/user/profiles", tags=["profiles"])


class ProfileSettings(BaseModel):
    """Settings stored inside a profile."""

    cruise_altitude_ft: int | None = None
    flight_ceiling_ft: int | None = None
    speed_kt: int | None = None
    models: list[str] | None = None
    advisory_models: list[str] | None = None
    gramet_enabled: bool | None = None
    llm_digest_enabled: bool | None = None
    icing_severity_enhance: bool | None = None
    icing_method: str | None = None  # "ogimet_dd", "ogimet_nwp", or "sfip_nwp"
    flight_rules: str | None = None  # "vfr_only" or "vfr_ifr"
    advisories: dict | None = None  # {enabled: {}, params: {}}


class ProfileResponse(BaseModel):
    """Profile data returned to the client."""

    id: int
    name: str
    is_default: bool
    settings: ProfileSettings
    created_at: str
    updated_at: str


class CreateProfileRequest(BaseModel):
    """Request to create a new profile."""

    name: str
    settings: ProfileSettings | None = None


class UpdateProfileRequest(BaseModel):
    """Request to update an existing profile."""

    name: str | None = None
    settings: ProfileSettings | None = None
    is_default: bool | None = None


class DuplicateProfileRequest(BaseModel):
    """Request to duplicate a profile with a new name."""

    name: str


def _profile_to_response(profile: FlightProfile) -> ProfileResponse:
    return ProfileResponse(
        id=profile.id,
        name=profile.name,
        is_default=profile.is_default,
        settings=ProfileSettings(**profile.settings),
        created_at=profile.created_at.isoformat(),
        updated_at=profile.updated_at.isoformat(),
    )


@router.get("", response_model=list[ProfileResponse])
def list_user_profiles(
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """List all profiles for the current user. Creates a default if none exist."""
    ensure_default_profile(db, user_id)
    profiles = list_profiles(db, user_id)
    return [_profile_to_response(p) for p in profiles]


@router.post("", response_model=ProfileResponse, status_code=201)
def create_profile(
    req: CreateProfileRequest,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Create a new profile."""
    if not req.name or not req.name.strip():
        raise HTTPException(status_code=422, detail="Profile name is required")

    # Check for duplicate names
    existing = list_profiles(db, user_id)
    if any(p.name.lower() == req.name.strip().lower() for p in existing):
        raise HTTPException(status_code=409, detail=f"Profile '{req.name.strip()}' already exists")

    settings = req.settings.model_dump(exclude_none=True) if req.settings else {}
    from datetime import datetime, timezone

    profile = FlightProfile(
        id=0,  # will be assigned by DB
        user_id=user_id,
        name=req.name.strip(),
        is_default=False,
        settings=settings,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    saved = save_profile(db, profile, user_id)
    return _profile_to_response(saved)


@router.get("/{profile_id}", response_model=ProfileResponse)
def get_profile(
    profile_id: int,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Get a specific profile."""
    profile = _load_owned_profile(db, profile_id, user_id)
    return _profile_to_response(profile)


@router.put("/{profile_id}", response_model=ProfileResponse)
def update_user_profile(
    profile_id: int,
    req: UpdateProfileRequest,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Update an existing profile."""
    profile = _load_owned_profile(db, profile_id, user_id)

    kwargs: dict = {}
    if req.name is not None:
        name = req.name.strip()
        if not name:
            raise HTTPException(status_code=422, detail="Profile name cannot be empty")
        # Check for duplicate names (excluding this profile)
        existing = list_profiles(db, user_id)
        if any(p.name.lower() == name.lower() and p.id != profile_id for p in existing):
            raise HTTPException(status_code=409, detail=f"Profile '{name}' already exists")
        kwargs["name"] = name

    if req.settings is not None:
        # Merge new settings with existing
        current_settings = profile.settings.copy()
        new_settings = req.settings.model_dump(exclude_none=True)
        current_settings.update(new_settings)
        kwargs["settings"] = current_settings

    if req.is_default is True:
        # Clear default flag from all other profiles
        from sqlalchemy import select, update as sql_update

        db.execute(
            sql_update(FlightProfileRow)
            .where(FlightProfileRow.user_id == user_id)
            .values(is_default=False)
        )
        kwargs["is_default"] = True

    if kwargs:
        updated = update_profile(db, profile_id, **kwargs)
        return _profile_to_response(updated)
    return _profile_to_response(profile)


@router.delete("/{profile_id}", status_code=204)
def delete_user_profile(
    profile_id: int,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Delete a profile. Cannot delete the default profile."""
    profile = _load_owned_profile(db, profile_id, user_id)
    if profile.is_default:
        raise HTTPException(status_code=400, detail="Cannot delete the default profile")
    try:
        delete_profile(db, profile_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Profile not found")


@router.post("/{profile_id}/duplicate", response_model=ProfileResponse, status_code=201)
def duplicate_profile(
    profile_id: int,
    req: DuplicateProfileRequest,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Duplicate an existing profile with a new name."""
    source = _load_owned_profile(db, profile_id, user_id)

    name = req.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Profile name is required")

    # Check for duplicate names
    existing = list_profiles(db, user_id)
    if any(p.name.lower() == name.lower() for p in existing):
        raise HTTPException(status_code=409, detail=f"Profile '{name}' already exists")

    from datetime import datetime, timezone

    new_profile = FlightProfile(
        id=0,
        user_id=user_id,
        name=name,
        is_default=False,
        settings=source.settings.copy(),
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    saved = save_profile(db, new_profile, user_id)
    return _profile_to_response(saved)


def _load_owned_profile(db: Session, profile_id: int, user_id: str) -> FlightProfile:
    """Load a profile, verifying ownership."""
    try:
        profile = load_profile(db, profile_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Profile not found")
    if profile.user_id != user_id:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile


def load_profile_settings(db: Session, profile_id: int | None, user_id: str) -> dict:
    """Load profile settings for use by the pipeline.

    Falls back to empty dict if profile_id is None or not found.
    """
    if profile_id is None:
        # Try the user's default profile
        profile = _get_default_profile(db, user_id)
        if profile:
            return profile.settings
        return {}
    try:
        profile = load_profile(db, profile_id)
        if profile.user_id == user_id:
            return profile.settings
    except KeyError:
        pass
    return {}


def _get_default_profile(db: Session, user_id: str) -> FlightProfile | None:
    """Get the user's default profile, or None."""
    from sqlalchemy import select

    stmt = (
        select(FlightProfileRow)
        .where(FlightProfileRow.user_id == user_id, FlightProfileRow.is_default.is_(True))
    )
    row = db.execute(stmt).scalar_one_or_none()
    if row:
        return FlightProfile(
            id=row.id,
            user_id=row.user_id,
            name=row.name,
            is_default=row.is_default,
            settings=json.loads(row.settings_json) if row.settings_json else {},
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
    return None
