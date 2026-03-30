"""PIREP storage: CRUD operations and query filters."""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from weatherbrief.db.models import (
    BriefingPackRow,
    FlightRow,
    PirepRow,
    UserAircraftRow,
    ICING_INTENSITIES,
    TURBULENCE_INTENSITIES,
)

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# European airspace bounding box (lat 34-72, lon -25 to 45)
# Covers Iceland to Turkey, Canaries to western Russia.
# ---------------------------------------------------------------------------

EUROPE_BOUNDS = {
    "min_lat": 34.0,
    "max_lat": 72.0,
    "min_lon": -25.0,
    "max_lon": 45.0,
}


def validate_european_bounds(lat: float, lon: float) -> None:
    """Raise ValueError if position is outside European airspace."""
    if not (EUROPE_BOUNDS["min_lat"] <= lat <= EUROPE_BOUNDS["max_lat"]):
        raise ValueError(
            f"Latitude {lat} outside European airspace "
            f"({EUROPE_BOUNDS['min_lat']}–{EUROPE_BOUNDS['max_lat']})"
        )
    if not (EUROPE_BOUNDS["min_lon"] <= lon <= EUROPE_BOUNDS["max_lon"]):
        raise ValueError(
            f"Longitude {lon} outside European airspace "
            f"({EUROPE_BOUNDS['min_lon']}–{EUROPE_BOUNDS['max_lon']})"
        )


# ---------------------------------------------------------------------------
# Severity ordering for min_severity filtering
# ---------------------------------------------------------------------------

_ICING_SEVERITY_ORDER = {v: i for i, v in enumerate(ICING_INTENSITIES)}
_TURB_SEVERITY_ORDER = {v: i for i, v in enumerate(TURBULENCE_INTENSITIES)}

# Unified severity ordering (highest across both fields)
_SEVERITY_ORDER = {"none": 0, "trace": 1, "light": 2, "moderate": 3, "severe": 4}


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def create_pirep(session: Session, user_id: str, **kwargs: Any) -> PirepRow:
    """Create a single PIREP."""
    row = PirepRow(user_id=user_id, **kwargs)
    session.add(row)
    session.flush()
    return row


def get_pirep(session: Session, pirep_id: int) -> PirepRow | None:
    """Load a PIREP by ID."""
    return session.get(PirepRow, pirep_id)


# ---------------------------------------------------------------------------
# Queries
# ---------------------------------------------------------------------------


def list_pireps(
    session: Session,
    *,
    flight_id: str | None = None,
    pack_id: int | None = None,
    bounds: tuple[float, float, float, float] | None = None,
    airport_lat_lon: tuple[float, float] | None = None,
    airport_radius_deg: float = 0.3,
    hours: int | None = None,
    from_dt: datetime | None = None,
    to_dt: datetime | None = None,
    hazard: str | None = None,
    min_severity: str | None = None,
    altitude_min: int | None = None,
    altitude_max: int | None = None,
    aircraft_type: str | None = None,
    limit: int = 500,
) -> list[PirepRow]:
    """Query PIREPs with optional filters.

    Exactly one spatial/scope filter should be provided:
    - flight_id: PIREPs linked to a specific flight (via pack_id)
    - pack_id: PIREPs linked to a specific briefing pack
    - bounds: (sw_lat, sw_lon, ne_lat, ne_lon) bounding box
    - airport_lat_lon: (lat, lon) center point for airport proximity query
    - from_dt/to_dt: time range (without spatial constraint)

    Additional filters narrow results further.
    """
    stmt = select(PirepRow)

    # Scope filters
    if flight_id is not None:
        pack_ids = (
            select(BriefingPackRow.id)
            .where(BriefingPackRow.flight_id == flight_id)
        )
        stmt = stmt.where(PirepRow.pack_id.in_(pack_ids))
    elif pack_id is not None:
        stmt = stmt.where(PirepRow.pack_id == pack_id)
    elif bounds is not None:
        sw_lat, sw_lon, ne_lat, ne_lon = bounds
        stmt = stmt.where(
            PirepRow.latitude >= sw_lat,
            PirepRow.latitude <= ne_lat,
            PirepRow.longitude >= sw_lon,
            PirepRow.longitude <= ne_lon,
        )
    elif airport_lat_lon is not None:
        lat, lon = airport_lat_lon
        stmt = stmt.where(
            PirepRow.latitude >= lat - airport_radius_deg,
            PirepRow.latitude <= lat + airport_radius_deg,
            PirepRow.longitude >= lon - airport_radius_deg,
            PirepRow.longitude <= lon + airport_radius_deg,
        )

    # Time filters
    if hours is not None and from_dt is None and to_dt is None:
        cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
        stmt = stmt.where(PirepRow.observed_at >= cutoff)
    else:
        if from_dt is not None:
            stmt = stmt.where(PirepRow.observed_at >= from_dt)
        if to_dt is not None:
            stmt = stmt.where(PirepRow.observed_at <= to_dt)

    # Hazard type filter
    if hazard == "icing":
        stmt = stmt.where(PirepRow.icing_intensity.isnot(None))
    elif hazard == "turbulence":
        stmt = stmt.where(PirepRow.turbulence_intensity.isnot(None))
    elif hazard == "cloud":
        stmt = stmt.where(
            (PirepRow.in_cloud.isnot(None))
            | (PirepRow.ceiling_msl_ft.isnot(None))
            | (PirepRow.tops_msl_ft.isnot(None))
        )

    # Severity filter
    if min_severity is not None:
        min_idx = _SEVERITY_ORDER.get(min_severity, 0)
        if min_idx > 0:
            # Include rows where icing OR turbulence meets the threshold
            icing_vals = [k for k, v in _SEVERITY_ORDER.items() if v >= min_idx and k in _ICING_SEVERITY_ORDER]
            turb_vals = [k for k, v in _SEVERITY_ORDER.items() if v >= min_idx and k in _TURB_SEVERITY_ORDER]
            stmt = stmt.where(
                PirepRow.icing_intensity.in_(icing_vals)
                | PirepRow.turbulence_intensity.in_(turb_vals)
            )

    # Altitude filter
    if altitude_min is not None:
        stmt = stmt.where(
            (PirepRow.reported_altitude_ft >= altitude_min)
            | (
                PirepRow.reported_altitude_ft.is_(None)
                & (PirepRow.gps_altitude_ft >= altitude_min)
            )
        )
    if altitude_max is not None:
        stmt = stmt.where(
            (PirepRow.reported_altitude_ft <= altitude_max)
            | (
                PirepRow.reported_altitude_ft.is_(None)
                & (PirepRow.gps_altitude_ft <= altitude_max)
            )
        )

    # Aircraft type filter (join to user_aircraft)
    if aircraft_type is not None:
        stmt = stmt.join(UserAircraftRow, PirepRow.aircraft_id == UserAircraftRow.id).where(
            UserAircraftRow.icao_type == aircraft_type.upper()
        )

    stmt = stmt.order_by(PirepRow.observed_at.desc()).limit(limit)
    return list(session.execute(stmt).scalars().all())


def pack_ids_with_pireps(session: Session) -> set[int]:
    """Return set of pack IDs that have linked PIREPs (for retention exemption)."""
    stmt = select(PirepRow.pack_id).where(PirepRow.pack_id.isnot(None)).distinct()
    return set(session.execute(stmt).scalars().all())
