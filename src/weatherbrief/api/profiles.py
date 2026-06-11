"""API endpoints for flight profile management."""

from __future__ import annotations

import copy
import json
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel
from sqlalchemy.orm import Session

from flyfun_common.db import current_user_id, get_db
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
    auto_front_detection: bool | None = None  # experimental front detection (#196)
    compute_alternates: bool | None = None  # weather-based divert candidates, D-2 inward (#210)
    icing_method: str | None = None  # "ogimet_dd", "ogimet_nwp", or "sfip_nwp"
    cloud_method: str | None = None  # "dd" or "nwp"
    convective_method: str | None = None  # "thermo" or "nwp"
    flight_rules: str | None = None  # "vfr_only" or "vfr_ifr"
    digest_guidance: Literal["conservative", "balanced", "tolerant"] | None = None
    advisories: dict | None = None  # {enabled: {}, params: {}}


class ProfileResponse(BaseModel):
    """Profile data returned to the client."""

    id: int
    name: str
    is_default: bool
    settings: ProfileSettings
    system_template_key: str | None = None
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
        system_template_key=profile.system_template_key,
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


# Matches the FlightProfileRow.name column (String(100)).
MAX_PROFILE_NAME_LENGTH = 100


def _validated_profile_name(raw: str | None) -> str:
    """Strip and length-check a profile name; 422 on empty or too long."""
    name = (raw or "").strip()
    if not name:
        raise HTTPException(status_code=422, detail="Profile name is required")
    if len(name) > MAX_PROFILE_NAME_LENGTH:
        raise HTTPException(
            status_code=422,
            detail=f"Profile name must be at most {MAX_PROFILE_NAME_LENGTH} characters",
        )
    return name


@router.post("", response_model=ProfileResponse, status_code=201)
def create_profile(
    req: CreateProfileRequest,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Create a new profile."""
    name = _validated_profile_name(req.name)

    # Check for duplicate names
    existing = list_profiles(db, user_id)
    if any(p.name.lower() == name.lower() for p in existing):
        raise HTTPException(status_code=409, detail=f"Profile '{name}' already exists")

    settings = req.settings.model_dump(exclude_none=True) if req.settings else {}
    from datetime import datetime, timezone

    profile = FlightProfile(
        id=0,  # will be assigned by DB
        user_id=user_id,
        name=name,
        is_default=False,
        settings=settings,
        created_at=datetime.now(timezone.utc),
        updated_at=datetime.now(timezone.utc),
    )
    saved = save_profile(db, profile, user_id)
    return _profile_to_response(saved)


@router.get("/{profile_id:int}", response_model=ProfileResponse)
def get_profile(
    profile_id: int,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Get a specific profile."""
    profile = _load_owned_profile(db, profile_id, user_id)
    return _profile_to_response(profile)


@router.put("/{profile_id:int}", response_model=ProfileResponse)
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
        name = _validated_profile_name(req.name)
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


@router.delete("/{profile_id:int}", status_code=204)
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


@router.post("/{profile_id:int}/duplicate", response_model=ProfileResponse, status_code=201)
def duplicate_profile(
    profile_id: int,
    req: DuplicateProfileRequest,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Duplicate an existing profile with a new name."""
    source = _load_owned_profile(db, profile_id, user_id)

    name = _validated_profile_name(req.name)

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


class SystemTemplateResponse(BaseModel):
    """System profile template info returned to clients."""

    key: str
    name: str
    description: str
    settings: ProfileSettings


@router.get("/system-templates", response_model=list[SystemTemplateResponse])
def list_system_templates(
    locale: str = "en",
    user_id: str = Depends(current_user_id),
):
    """List available system profile templates with localized descriptions."""
    from weatherbrief.storage.system_profiles import load_system_templates

    templates = load_system_templates()
    result = []
    for tpl in templates:
        descriptions = tpl.get("descriptions", {})
        desc = descriptions.get(locale, descriptions.get("en", ""))
        result.append(SystemTemplateResponse(
            key=tpl["key"],
            name=tpl["name"],
            description=desc,
            settings=ProfileSettings(**tpl["settings"]),
        ))
    return result


class DigestGuidancePresetResponse(BaseModel):
    """Digest guidance preset info returned to clients."""

    key: str
    name: str
    description: str


@router.get("/digest-guidance-presets", response_model=list[DigestGuidancePresetResponse])
def list_digest_guidance_presets(
    locale: str = "en",
    user_id: str = Depends(current_user_id),
):
    """List available digest guidance presets with localized descriptions."""
    from weatherbrief.digest.llm_config import load_guidance_index

    return [
        DigestGuidancePresetResponse(**preset)
        for preset in load_guidance_index(locale)
    ]


class DigestGuidanceTextResponse(BaseModel):
    """Full guidance text for a preset."""

    key: str
    text: str


@router.get("/digest-guidance-presets/{key}/text", response_model=DigestGuidanceTextResponse)
def get_digest_guidance_text(
    key: str,
    user_id: str = Depends(current_user_id),
):
    """Return the full guidance prompt text for a preset (read-only)."""
    from weatherbrief.digest.llm_config import load_guidance_text

    try:
        text = load_guidance_text(key)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return DigestGuidanceTextResponse(key=key, text=text)


@router.post("/{profile_id:int}/reset", response_model=ProfileResponse)
def reset_profile_to_template(
    profile_id: int,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Reset a profile's settings to its system template.

    Only works for profiles that were created from a system template
    (have a system_template_key set).
    """
    from weatherbrief.storage.system_profiles import get_system_template

    profile = _load_owned_profile(db, profile_id, user_id)
    if not profile.system_template_key:
        raise HTTPException(
            status_code=400,
            detail="This profile was not created from a system template and cannot be reset",
        )

    tpl = get_system_template(profile.system_template_key)
    if not tpl:
        raise HTTPException(
            status_code=404,
            detail=f"System template '{profile.system_template_key}' no longer exists",
        )

    updated = update_profile(db, profile_id, settings=tpl["settings"], name=tpl["name"])
    return _profile_to_response(updated)


def _load_owned_profile(db: Session, profile_id: int, user_id: str) -> FlightProfile:
    """Load a profile, verifying ownership."""
    try:
        profile = load_profile(db, profile_id)
    except KeyError:
        raise HTTPException(status_code=404, detail="Profile not found")
    if profile.user_id != user_id:
        raise HTTPException(status_code=404, detail="Profile not found")
    return profile


class ProfileContext:
    """Profile settings and name loaded together to avoid duplicate DB queries."""

    __slots__ = ("settings", "name")

    def __init__(self, settings: dict, name: str | None = None):
        self.settings = settings
        self.name = name


def load_profile_context(db: Session, profile_id: int | None, user_id: str) -> ProfileContext:
    """Load profile settings and name for use by the pipeline.

    Falls back to empty settings if profile_id is None or not found.
    """
    if profile_id is None:
        profile = _get_default_profile(db, user_id)
        if profile:
            return ProfileContext(settings=profile.settings, name=profile.name)
        return ProfileContext(settings={})
    try:
        profile = load_profile(db, profile_id)
        if profile.user_id == user_id:
            return ProfileContext(settings=profile.settings, name=profile.name)
    except KeyError:
        pass
    return ProfileContext(settings={})


def load_profile_settings(db: Session, profile_id: int | None, user_id: str) -> dict:
    """Load profile settings dict for use by the pipeline.

    Convenience wrapper around load_profile_context for callers that only
    need the settings dict.
    """
    return load_profile_context(db, profile_id, user_id).settings


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
            system_template_key=row.system_template_key,
            created_at=row.created_at,
            updated_at=row.updated_at,
        )
    return None


# --- Admin endpoint for updating system templates ---

admin_router = APIRouter(prefix="/admin/system-profiles", tags=["admin"])


def _require_admin(request: Request, db: Session = Depends(get_db)) -> str:
    """Thin DI wrapper around require_admin to avoid circular import at module level."""
    from weatherbrief.api.admin import require_admin
    return require_admin(request, db=db)


class UpdateSystemTemplateRequest(BaseModel):
    """Update a system template's settings from a profile."""

    settings: ProfileSettings


@admin_router.put("/{template_key}", response_model=SystemTemplateResponse)
def update_system_template(
    template_key: str,
    req: UpdateSystemTemplateRequest,
    _admin_id: str = Depends(_require_admin),
    db: Session = Depends(get_db),
):
    """Update a system profile template's settings. Admin only.

    Writes the new settings to the system_profiles.json config file.
    Existing user profiles are NOT automatically updated (they are copies).

    NOTE: This writes to a local JSON file.  In multi-instance deployments
    the file may not be on shared storage, so changes won't propagate to
    other workers automatically.  Fine for single-instance setups.
    """
    from weatherbrief.storage.system_profiles import (
        get_system_template,
        invalidate_cache,
        load_system_templates,
        _templates_path,
    )

    tpl = get_system_template(template_key)
    if not tpl:
        raise HTTPException(status_code=404, detail=f"System template '{template_key}' not found")

    # Work on a deep copy so the cache is not mutated before the file write
    templates = copy.deepcopy(load_system_templates())
    new_settings = req.settings.model_dump(exclude_none=True)

    for t in templates:
        if t["key"] == template_key:
            t["settings"] = new_settings
            break

    data = {"profiles": templates}
    _templates_path().write_text(json.dumps(data, indent=2, ensure_ascii=False))

    # Clear cache so next load picks up changes from disk
    invalidate_cache()

    descriptions = tpl.get("descriptions", {})
    return SystemTemplateResponse(
        key=template_key,
        name=tpl["name"],
        description=descriptions.get("en", ""),
        settings=ProfileSettings(**new_settings),
    )
