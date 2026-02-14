"""Flight and BriefingPack storage — database-backed persistence."""

from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from weatherbrief.db.models import BriefingPackRow, FlightRow
from weatherbrief.models import BriefingPackMeta, Flight


def _data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR", "data"))


# --- Conversion helpers ---


def _flight_to_row(flight: Flight, user_id: str) -> FlightRow:
    return FlightRow(
        id=flight.id,
        user_id=user_id,
        route_name=flight.route_name,
        waypoints_json=json.dumps(flight.waypoints),
        target_date=flight.target_date,
        target_time_utc=flight.target_time_utc,
        cruise_altitude_ft=flight.cruise_altitude_ft,
        flight_ceiling_ft=flight.flight_ceiling_ft,
        flight_duration_hours=flight.flight_duration_hours,
        created_at=flight.created_at,
    )


def _row_to_flight(row: FlightRow) -> Flight:
    return Flight(
        id=row.id,
        user_id=row.user_id,
        route_name=row.route_name,
        waypoints=json.loads(row.waypoints_json),
        target_date=row.target_date,
        target_time_utc=row.target_time_utc,
        cruise_altitude_ft=row.cruise_altitude_ft,
        flight_ceiling_ft=row.flight_ceiling_ft,
        flight_duration_hours=row.flight_duration_hours,
        created_at=row.created_at,
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
        model_init_times=json.loads(row.model_init_times_json),
    )


# --- Flight CRUD ---


def save_flight(session: Session, flight: Flight, user_id: str) -> None:
    """Insert or update a flight in the database."""
    existing = session.get(FlightRow, flight.id)
    if existing:
        existing.route_name = flight.route_name
        existing.waypoints_json = json.dumps(flight.waypoints)
        existing.target_date = flight.target_date
        existing.target_time_utc = flight.target_time_utc
        existing.cruise_altitude_ft = flight.cruise_altitude_ft
        existing.flight_ceiling_ft = flight.flight_ceiling_ft
        existing.flight_duration_hours = flight.flight_duration_hours
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
    """List all flights for a user, newest first."""
    stmt = (
        select(FlightRow)
        .where(FlightRow.user_id == user_id)
        .order_by(FlightRow.created_at.desc())
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
