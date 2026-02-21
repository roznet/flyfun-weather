"""Flight and BriefingPack storage — database-backed persistence."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from weatherbrief.db.models import BriefingPackRow, FlightProfileRow, FlightRow
from weatherbrief.models import BriefingPackMeta, Flight, FlightProfile


def _data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR", "data"))


# --- Conversion helpers ---


def _flight_to_row(flight: Flight, user_id: str) -> FlightRow:
    return FlightRow(
        id=flight.id,
        user_id=user_id,
        profile_id=flight.profile_id,
        route_name=flight.route_name,
        waypoints_json=json.dumps(flight.waypoints),
        target_date=flight.target_date,
        target_time_utc=flight.target_time_utc,
        cruise_altitude_ft=flight.cruise_altitude_ft,
        flight_ceiling_ft=flight.flight_ceiling_ft,
        flight_duration_hours=flight.flight_duration_hours,
        auto_refresh=flight.auto_refresh,
        auto_refresh_hour=flight.auto_refresh_hour,
        last_auto_refresh_date=flight.last_auto_refresh_date,
        created_at=flight.created_at,
    )


def _row_to_flight(row: FlightRow) -> Flight:
    return Flight(
        id=row.id,
        user_id=row.user_id,
        profile_id=row.profile_id,
        route_name=row.route_name,
        waypoints=json.loads(row.waypoints_json),
        target_date=row.target_date,
        target_time_utc=row.target_time_utc,
        cruise_altitude_ft=row.cruise_altitude_ft,
        flight_ceiling_ft=row.flight_ceiling_ft,
        flight_duration_hours=row.flight_duration_hours,
        auto_refresh=row.auto_refresh,
        auto_refresh_hour=row.auto_refresh_hour,
        last_auto_refresh_date=row.last_auto_refresh_date,
        created_at=row.created_at,
    )


def _profile_to_row(profile: FlightProfile, user_id: str) -> FlightProfileRow:
    return FlightProfileRow(
        user_id=user_id,
        name=profile.name,
        is_default=profile.is_default,
        settings_json=json.dumps(profile.settings),
    )


def _row_to_profile(row: FlightProfileRow) -> FlightProfile:
    return FlightProfile(
        id=row.id,
        user_id=row.user_id,
        name=row.name,
        is_default=row.is_default,
        settings=json.loads(row.settings_json) if row.settings_json else {},
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _meta_to_row(meta: BriefingPackMeta) -> BriefingPackRow:
    return BriefingPackRow(
        flight_id=meta.flight_id,
        fetch_timestamp=meta.fetch_timestamp,
        days_out=meta.days_out,
        has_gramet=meta.has_gramet,
        has_skewt=meta.has_skewt,
        has_digest=meta.has_digest,
        assessment=meta.assessment,
        assessment_reason=meta.assessment_reason,
        artifact_path=meta.artifact_path,
        model_init_times_json=json.dumps(meta.model_init_times),
    )


def _row_to_meta(row: BriefingPackRow) -> BriefingPackMeta:
    return BriefingPackMeta(
        id=row.id,
        flight_id=row.flight_id,
        fetch_timestamp=row.fetch_timestamp,
        days_out=row.days_out,
        has_gramet=row.has_gramet,
        has_skewt=row.has_skewt,
        has_digest=row.has_digest,
        assessment=row.assessment,
        assessment_reason=row.assessment_reason,
        artifact_path=row.artifact_path,
        model_init_times=json.loads(row.model_init_times_json) if row.model_init_times_json else {},
    )


# --- Flight CRUD ---


def save_flight(session: Session, flight: Flight, user_id: str) -> None:
    """Insert or update a flight in the database."""
    existing = session.get(FlightRow, flight.id)
    if existing:
        existing.route_name = flight.route_name
        existing.profile_id = flight.profile_id
        existing.waypoints_json = json.dumps(flight.waypoints)
        existing.target_date = flight.target_date
        existing.target_time_utc = flight.target_time_utc
        existing.cruise_altitude_ft = flight.cruise_altitude_ft
        existing.flight_ceiling_ft = flight.flight_ceiling_ft
        existing.flight_duration_hours = flight.flight_duration_hours
        existing.auto_refresh = flight.auto_refresh
        existing.auto_refresh_hour = flight.auto_refresh_hour
        existing.last_auto_refresh_date = flight.last_auto_refresh_date
    else:
        session.add(_flight_to_row(flight, user_id))
    session.flush()


def load_flight(session: Session, flight_id: str) -> Flight:
    """Load a flight by ID. Raises KeyError if not found."""
    row = session.get(FlightRow, flight_id)
    if row is None:
        raise KeyError(f"Flight not found: {flight_id}")
    return _row_to_flight(row)


def list_flights(session: Session, user_id: str) -> list[Flight]:
    """List all flights for a user, sorted by flight time descending."""
    stmt = (
        select(FlightRow)
        .where(FlightRow.user_id == user_id)
        .order_by(FlightRow.target_date.desc(), FlightRow.target_time_utc.desc())
    )
    rows = session.execute(stmt).scalars().all()
    return [_row_to_flight(r) for r in rows]


def delete_flight(session: Session, flight_id: str) -> None:
    """Delete a flight and all its packs. Raises KeyError if not found."""
    row = session.get(FlightRow, flight_id)
    if row is None:
        raise KeyError(f"Flight not found: {flight_id}")

    # Remove artifact directories for all packs
    for pack in row.packs:
        if pack.artifact_path:
            _rmtree(Path(pack.artifact_path))

    session.delete(row)  # cascades to briefing_packs
    session.flush()


# --- BriefingPack operations ---


def save_pack_meta(session: Session, meta: BriefingPackMeta) -> None:
    """Insert briefing pack metadata."""
    session.add(_meta_to_row(meta))
    session.flush()


def load_pack_meta(
    session: Session, flight_id: str, fetch_timestamp: str
) -> BriefingPackMeta:
    """Load pack metadata. Raises KeyError if not found."""
    stmt = select(BriefingPackRow).where(
        BriefingPackRow.flight_id == flight_id,
        BriefingPackRow.fetch_timestamp == fetch_timestamp,
    )
    row = session.execute(stmt).scalar_one_or_none()
    if row is None:
        raise KeyError(f"Pack not found: {flight_id}/{fetch_timestamp}")
    return _row_to_meta(row)


def list_packs(session: Session, flight_id: str) -> list[BriefingPackMeta]:
    """List all packs for a flight, newest first."""
    stmt = (
        select(BriefingPackRow)
        .where(BriefingPackRow.flight_id == flight_id)
        .order_by(BriefingPackRow.fetch_timestamp.desc())
    )
    rows = session.execute(stmt).scalars().all()
    return [_row_to_meta(r) for r in rows]


# --- FlightProfile CRUD ---


def list_profiles(session: Session, user_id: str) -> list[FlightProfile]:
    """List all profiles for a user, default first then by name."""
    stmt = (
        select(FlightProfileRow)
        .where(FlightProfileRow.user_id == user_id)
        .order_by(FlightProfileRow.is_default.desc(), FlightProfileRow.name)
    )
    rows = session.execute(stmt).scalars().all()
    return [_row_to_profile(r) for r in rows]


def load_profile(session: Session, profile_id: int) -> FlightProfile:
    """Load a profile by ID. Raises KeyError if not found."""
    row = session.get(FlightProfileRow, profile_id)
    if row is None:
        raise KeyError(f"Profile not found: {profile_id}")
    return _row_to_profile(row)


def save_profile(session: Session, profile: FlightProfile, user_id: str) -> FlightProfile:
    """Insert a new profile. Returns the profile with its generated ID."""
    row = FlightProfileRow(
        user_id=user_id,
        name=profile.name,
        is_default=profile.is_default,
        settings_json=json.dumps(profile.settings),
    )
    session.add(row)
    session.flush()
    return _row_to_profile(row)


def update_profile(session: Session, profile_id: int, **kwargs) -> FlightProfile:
    """Update profile fields. Raises KeyError if not found."""
    row = session.get(FlightProfileRow, profile_id)
    if row is None:
        raise KeyError(f"Profile not found: {profile_id}")
    if "name" in kwargs:
        row.name = kwargs["name"]
    if "is_default" in kwargs:
        row.is_default = kwargs["is_default"]
    if "settings" in kwargs:
        row.settings_json = json.dumps(kwargs["settings"])
    session.flush()
    return _row_to_profile(row)


def delete_profile(session: Session, profile_id: int) -> None:
    """Delete a profile. Raises KeyError if not found."""
    row = session.get(FlightProfileRow, profile_id)
    if row is None:
        raise KeyError(f"Profile not found: {profile_id}")
    session.delete(row)
    session.flush()


def ensure_default_profile(session: Session, user_id: str) -> FlightProfile:
    """Ensure a default profile exists for a user, creating one if needed.

    If the user has existing preferences in defaults_json, migrates them
    to the default profile's settings.
    """
    from weatherbrief.db.models import UserPreferencesRow

    stmt = (
        select(FlightProfileRow)
        .where(FlightProfileRow.user_id == user_id, FlightProfileRow.is_default.is_(True))
    )
    row = session.execute(stmt).scalar_one_or_none()
    if row is not None:
        return _row_to_profile(row)

    # Migrate existing preferences if available
    settings: dict = {}
    prefs_row = session.get(UserPreferencesRow, user_id)
    if prefs_row and prefs_row.defaults_json:
        try:
            data = json.loads(prefs_row.defaults_json)
            # Extract profile-relevant fields
            for key in (
                "cruise_altitude_ft", "flight_ceiling_ft",
                "models", "advisory_models",
                "gramet_enabled", "llm_digest_enabled",
                "advisories",
            ):
                if key in data:
                    settings[key] = data[key]
        except json.JSONDecodeError:
            pass

    new_row = FlightProfileRow(
        user_id=user_id,
        name="Default",
        is_default=True,
        settings_json=json.dumps(settings),
    )
    session.add(new_row)
    session.flush()
    return _row_to_profile(new_row)


def safe_path_component(value: str) -> str:
    """Sanitize a string for use as a single path component.

    Strips path separators and traversal sequences, keeping only
    alphanumeric chars, hyphens, underscores, and dots (no leading dot).
    """
    import re

    sanitized = re.sub(r"[^a-zA-Z0-9._-]", "_", value)
    sanitized = sanitized.lstrip(".")
    return sanitized or "_"


def pack_dir_for(user_id: str, flight_id: str, fetch_timestamp: str) -> Path:
    """Get the directory path for a specific pack's artifacts.

    Layout: data/packs/{user_id}/{flight_id}/{safe_timestamp}/
    """
    safe_ts = fetch_timestamp.replace(":", "-").replace("+", "p")
    return (
        _data_dir()
        / "packs"
        / safe_path_component(user_id)
        / safe_path_component(flight_id)
        / safe_path_component(safe_ts)
    )


# --- Utilities ---


def _rmtree(path: Path) -> None:
    """Recursively remove a directory tree."""
    if path.exists():
        shutil.rmtree(path)
