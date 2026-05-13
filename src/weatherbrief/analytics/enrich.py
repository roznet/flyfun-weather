"""Server-side enrichment for analytics dimensions.

When a ``briefing.opened`` or ``flight.created`` event arrives, the client
sends only IDs. This module looks up the existing ``flights`` /
``briefing_packs`` rows and derives a small set of low-cardinality
dimensions (region, distance bucket, lead time, ...). The dimensions are
upserted into ``analytics_flights_dim`` / ``analytics_briefings_dim`` so
the rollup can ``JOIN`` on them without re-reading the source tables.

All derivation is best-effort: missing or malformed fields produce
``NULL`` buckets rather than failing the event ingest. The point is
graceful, not exhaustive.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from weatherbrief.analytics.models import (
    AnalyticsBriefingDimRow,
    AnalyticsFlightDimRow,
)
from weatherbrief.db.models import BriefingPackRow, FlightRow

logger = logging.getLogger(__name__)

_MAX_SMALLINT_CLAMP = 10


# ---------------------------------------------------------------------------
# Region: ICAO first-letter heuristic
# ---------------------------------------------------------------------------


def _region_from_icao(icao: str) -> str:
    """Coarse region bucket from the first ICAO letter.

    Good enough for "EU vs US" slicing — anything more granular belongs in
    a real geography lookup, which we don't need at this volume.
    """
    if not icao:
        return "OTHER"
    first = icao[0].upper()
    if first in ("K", "P"):
        return "US"
    if first in ("E", "L"):
        return "EU"
    return "OTHER"


def _first_waypoint(flight: FlightRow) -> str:
    try:
        wpts = json.loads(flight.waypoints_json or "[]")
        if isinstance(wpts, list) and wpts:
            return str(wpts[0]).upper()
    except (ValueError, TypeError):
        pass
    return ""


def _route_points(flight: FlightRow) -> int | None:
    try:
        wpts = json.loads(flight.waypoints_json or "[]")
        if isinstance(wpts, list) and wpts:
            return min(len(wpts), _MAX_SMALLINT_CLAMP)
    except (ValueError, TypeError):
        pass
    return None


def _distance_bucket(flight: FlightRow) -> str | None:
    """Coarse 'short / medium / long' based on flight duration hours.

    Uses ``flight_duration_hours`` (already computed at flight creation
    from cruise speed × route length) as a proxy for distance — avoids
    re-resolving airports here.
    """
    h = flight.flight_duration_hours or 0.0
    if h <= 0.0:
        return None
    if h < 1.5:
        return "short"
    if h < 3.0:
        return "medium"
    return "long"


# ---------------------------------------------------------------------------
# Briefing lead time bucket
# ---------------------------------------------------------------------------


def _lead_time_bucket(
    briefing_created_at: datetime, departure_time: datetime | None,
) -> str:
    """How far ahead of departure the briefing was generated.

    Capped at ``7d_plus`` — most NWP models lose useful skill past a week,
    so finer buckets beyond that don't carry signal. Briefings created
    *after* departure (post-flight analysis, historical re-runs) land in
    ``post_departure`` rather than being collapsed into ``same_day``.
    """
    if departure_time is None:
        return "no_etd"
    if briefing_created_at.tzinfo is None:
        briefing_created_at = briefing_created_at.replace(tzinfo=timezone.utc)
    if departure_time.tzinfo is None:
        departure_time = departure_time.replace(tzinfo=timezone.utc)

    delta_h = (departure_time - briefing_created_at).total_seconds() / 3600.0
    if delta_h <= 0:
        return "post_departure"
    if delta_h < 12:
        return "same_day"
    if delta_h < 36:
        return "1d"
    if delta_h < 84:
        return "2_3d"
    if delta_h < 180:
        return "4_7d"
    return "7d_plus"


def _model_count(pack: BriefingPackRow) -> int | None:
    try:
        models = json.loads(pack.model_init_times_json or "{}")
        if isinstance(models, dict):
            return min(len(models), _MAX_SMALLINT_CLAMP)
    except (ValueError, TypeError):
        pass
    return None


# ---------------------------------------------------------------------------
# Public upsert helpers — called from the ingest path
# ---------------------------------------------------------------------------


def upsert_flight_dim(db: Session, flight_id: str) -> None:
    """Upsert ``analytics_flights_dim`` for ``flight_id`` if not present.

    Stable dimensions — region, distance, route shape, alternate ETD —
    are sampled once at first encounter and not updated thereafter. If a
    user edits the flight later we keep the original snapshot, since
    that's what was true when the briefings were generated.

    Wraps the INSERT in its own SAVEPOINT so a concurrent batch that
    won the check-then-insert race doesn't poison the caller's
    transaction (the caller is itself inside a SAVEPOINT around the
    event insert — letting the conflict propagate would lose the event).
    """
    existing = db.get(AnalyticsFlightDimRow, flight_id)
    if existing is not None:
        return

    flight = db.get(FlightRow, flight_id)
    if flight is None:
        return

    dep_icao = _first_waypoint(flight)
    row = AnalyticsFlightDimRow(
        flight_id=flight_id,
        created_at=flight.created_at or datetime.now(timezone.utc),
        region=_region_from_icao(dep_icao),
        distance_bucket=_distance_bucket(flight),
        route_points=_route_points(flight),
        has_alternate_etd=flight.alt_departure_time is not None,
    )
    try:
        with db.begin_nested():
            db.add(row)
    except IntegrityError:
        # Lost the race to another batch. Row exists; nothing to do.
        pass


def upsert_briefing_dim(db: Session, briefing_id: int) -> None:
    """Upsert ``analytics_briefings_dim`` for ``briefing_id`` if not present.

    Also upserts the parent ``flights_dim`` row as a side effect, since a
    briefing always has a flight.
    """
    existing = db.get(AnalyticsBriefingDimRow, briefing_id)
    if existing is not None:
        return

    pack = db.get(BriefingPackRow, briefing_id)
    if pack is None:
        return

    upsert_flight_dim(db, pack.flight_id)

    # Sequence number: count earlier packs for the same flight by fetch_timestamp.
    earlier_count = db.scalar(
        select(func.count(BriefingPackRow.id))
        .where(BriefingPackRow.flight_id == pack.flight_id)
        .where(BriefingPackRow.fetch_timestamp < pack.fetch_timestamp)
    ) or 0
    seq = min(int(earlier_count) + 1, _MAX_SMALLINT_CLAMP)

    flight = db.get(FlightRow, pack.flight_id)
    departure_time = flight.departure_time if flight else None

    row = AnalyticsBriefingDimRow(
        briefing_id=briefing_id,
        flight_id=pack.flight_id,
        created_at=pack.fetch_timestamp,
        briefing_seq=seq,
        is_refresh=seq > 1,
        lead_time_bucket=_lead_time_bucket(pack.fetch_timestamp, departure_time),
        model_count=_model_count(pack),
    )
    try:
        with db.begin_nested():
            db.add(row)
    except IntegrityError:
        # Lost the race to another batch. Row exists; nothing to do.
        pass
