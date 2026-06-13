"""Flight and BriefingPack storage — database-backed persistence."""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import secrets
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import delete, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, aliased

from weatherbrief.db.models import (
    BriefingPackRow,
    FlightProfileRow,
    FlightRow,
    FlightSubscriptionRow,
)
from weatherbrief.models import BriefingPackMeta, Diagnostic, Flight, FlightProfile

logger = logging.getLogger(__name__)


def _ensure_utc(dt: datetime) -> datetime:
    """Promote naive datetimes to UTC (SQLite loses timezone on round-trip)."""
    if dt is not None and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _data_dir() -> Path:
    return Path(os.environ.get("DATA_DIR", "data"))


# --- Conversion helpers ---


_SHARE_CODE_ALPHABET = (
    "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
)
_SHARE_CODE_LEN = 8


def _generate_share_code() -> str:
    """8-char base62 token. ~2.18e14 combos — collisions are vanishingly rare."""
    return "".join(secrets.choice(_SHARE_CODE_ALPHABET) for _ in range(_SHARE_CODE_LEN))


def _allocate_share_code(session: Session) -> str:
    """Generate a share_code that doesn't collide with an existing row.

    Retries on collision — at 8 base62 chars the loop body almost never
    runs more than once, but the SELECT is still cheap insurance against
    a flaky RNG or a future shorter alphabet.
    """
    for _ in range(8):
        code = _generate_share_code()
        existing = session.execute(
            select(FlightRow.id).where(FlightRow.share_code == code)
        ).first()
        if existing is None:
            return code
    raise RuntimeError("Could not allocate a unique share_code after 8 attempts")


def _flight_to_row(flight: Flight, user_id: str) -> FlightRow:
    return FlightRow(
        id=flight.id,
        user_id=user_id,
        profile_id=flight.profile_id,
        aircraft_id=flight.aircraft_id,
        route_name=flight.route_name,
        waypoints_json=json.dumps(flight.waypoints),
        departure_time=flight.departure_time,
        cruise_altitude_ft=flight.cruise_altitude_ft,
        flight_ceiling_ft=flight.flight_ceiling_ft,
        flight_duration_hours=flight.flight_duration_hours,
        alt_departure_time=flight.alt_departure_time,
        private=flight.private,
        auto_refresh=flight.auto_refresh,
        auto_refresh_hour=flight.auto_refresh_hour,
        last_auto_refresh_at=flight.last_auto_refresh_at,
        raw_route=flight.raw_route,
        parser_version=flight.parser_version,
        share_code=flight.share_code,
        created_at=flight.created_at,
    )


def _row_to_flight(row: FlightRow) -> Flight:
    return Flight(
        id=row.id,
        user_id=row.user_id,
        profile_id=row.profile_id,
        aircraft_id=row.aircraft_id,
        route_name=row.route_name,
        waypoints=json.loads(row.waypoints_json),
        departure_time=_ensure_utc(row.departure_time),
        cruise_altitude_ft=row.cruise_altitude_ft,
        flight_ceiling_ft=row.flight_ceiling_ft,
        flight_duration_hours=row.flight_duration_hours,
        alt_departure_time=_ensure_utc(row.alt_departure_time) if row.alt_departure_time else None,
        private=row.private,
        auto_refresh=row.auto_refresh,
        auto_refresh_hour=row.auto_refresh_hour,
        last_auto_refresh_at=row.last_auto_refresh_at,
        raw_route=row.raw_route,
        parser_version=row.parser_version,
        share_code=row.share_code,
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
        system_template_key=row.system_template_key,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _pack_hmac_key() -> bytes:
    """Derive HMAC key from JWT_SECRET for pack integrity checks.

    Uses flyfun-common's ``get_jwt_secret`` rather than reading the env var
    directly: it raises in production when the secret is missing, instead of
    silently deriving a forgeable key from an empty string.
    """
    from flyfun_common.auth import get_jwt_secret

    return hashlib.sha256(f"pack-integrity:{get_jwt_secret()}".encode()).digest()


def _compute_pack_hmac(row: BriefingPackRow) -> str:
    """Compute HMAC-SHA256 over key pack metadata fields."""
    # Normalize timestamp: strip tzinfo so MySQL (naive) and SQLite (aware)
    # produce the same HMAC string.
    ts = row.fetch_timestamp.replace(tzinfo=None) if row.fetch_timestamp else ""
    msg = (
        f"{row.flight_id}|{ts}|{row.days_out}|"
        f"{row.assessment}|{row.artifact_path}|"
        f"{row.model_init_times_json}|{row.grib_init_times_json}"
    )
    return hmac.new(_pack_hmac_key(), msg.encode(), hashlib.sha256).hexdigest()


def _compute_pack_hmac_legacy(row: BriefingPackRow) -> str:
    """Compute HMAC using the original format (with UTC tzinfo in timestamp).

    HMACs written before the naive-timestamp normalization used aware
    datetimes.  MySQL strips tzinfo on read, so we re-attach UTC here
    to reconstruct the original HMAC input.
    """
    from datetime import timezone
    ts = row.fetch_timestamp
    if ts and ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)
    msg = (
        f"{row.flight_id}|{ts}|{row.days_out}|"
        f"{row.assessment}|{row.artifact_path}|"
        f"{row.model_init_times_json}|{row.grib_init_times_json}"
    )
    return hmac.new(_pack_hmac_key(), msg.encode(), hashlib.sha256).hexdigest()


def _verify_pack_hmac(row: BriefingPackRow) -> bool:
    """Verify HMAC on a pack row. Returns True if valid or if no HMAC stored."""
    if not row.integrity_hmac:
        return True  # pre-existing rows without HMAC are trusted
    # Try normalized format first, fall back to legacy (aware timestamp) format
    if hmac.compare_digest(row.integrity_hmac, _compute_pack_hmac(row)):
        return True
    return hmac.compare_digest(row.integrity_hmac, _compute_pack_hmac_legacy(row))


def _apply_meta_to_row(row: BriefingPackRow, meta: BriefingPackMeta) -> None:
    """Copy every BriefingPackMeta field onto a BriefingPackRow.

    Single source of truth for the meta→row mapping, used by both
    ``_meta_to_row`` (insert path) and ``update_pack_meta`` (update path)
    so they can't drift when a new field is added to ``BriefingPackMeta``.
    Does NOT touch ``id`` or ``integrity_hmac`` — those are managed by
    SQLAlchemy and ``_compute_pack_hmac`` respectively.
    """
    row.flight_id = meta.flight_id
    row.fetch_timestamp = meta.fetch_timestamp
    row.days_out = meta.days_out
    row.has_gramet = meta.has_gramet
    row.has_skewt = meta.has_skewt
    row.has_digest = meta.has_digest
    row.llm_digest_requested = meta.llm_digest_requested
    row.assessment = meta.assessment
    row.assessment_reason = meta.assessment_reason
    row.digest_trace_id = meta.digest_trace_id
    row.artifact_path = meta.artifact_path
    row.model_init_times_json = json.dumps(meta.model_init_times)
    row.grib_init_times_json = json.dumps(meta.grib_init_times)
    row.model_sources_json = (
        json.dumps(meta.model_sources) if meta.model_sources else None
    )
    row.models_skipped_region_json = json.dumps(meta.models_skipped_region)
    row.diagnostics_json = json.dumps(
        [d.model_dump(mode="json") for d in meta.diagnostics],
    )
    row.alt_assessment = meta.alt_assessment
    row.alt_assessment_reason = meta.alt_assessment_reason
    row.has_alt_advisories = meta.has_alt_advisories
    row.dwd_charts_run_cycle = meta.dwd_charts_run_cycle
    row.dwd_charts_default_id = meta.dwd_charts_default_id
    row.dwd_charts_in_coverage = meta.dwd_charts_in_coverage
    row.dwd_charts_within_horizon = meta.dwd_charts_within_horizon
    row.metoffice_charts_run_cycle = meta.metoffice_charts_run_cycle
    row.metoffice_charts_default_id = meta.metoffice_charts_default_id
    row.metoffice_charts_in_coverage = meta.metoffice_charts_in_coverage
    row.metoffice_charts_within_horizon = meta.metoffice_charts_within_horizon


def _meta_to_row(meta: BriefingPackMeta) -> BriefingPackRow:
    row = BriefingPackRow()
    _apply_meta_to_row(row, meta)
    return row


def _resolve_artifact_path(raw: str) -> str:
    """Resolve artifact_path against the current DATA_DIR.

    Stored paths are relative to DATA_DIR (e.g. ``data/packs/...`` when
    DATA_DIR was ``data``).  When the process CWD differs from where the
    path was created (e.g. running from a worktree), the relative path
    won't resolve.  Fix by locating the ``packs/`` component and
    re-rooting it under the current ``_data_dir()``.
    """
    if not raw:
        return raw
    p = Path(raw)
    if p.exists():
        return raw
    # Find the "packs/" component and re-root under current DATA_DIR
    parts = p.parts
    try:
        idx = parts.index("packs")
        resolved = _data_dir().joinpath(*parts[idx:])
        if resolved.exists():
            return str(resolved)
    except ValueError:
        pass
    return raw


def pack_has_advisories(artifact_path: str | None) -> bool:
    """True when a pack's ``route_advisories.json`` exists on disk.

    Advisories presence is a file on disk, not a DB column — this is the
    single source of truth for that check, used by both the per-pack
    response builder and the bulk flight-list query. Resolves the stored
    path against the current DATA_DIR first (worktrees run from a different
    CWD than where the pack was written).
    """
    if not artifact_path:
        return False
    return (Path(_resolve_artifact_path(artifact_path)) / "route_advisories.json").exists()


def _parse_diagnostics(raw: str | None) -> list[Diagnostic]:
    """Parse diagnostics_json into typed Diagnostic entries.

    Tolerates legacy storage formats:
    - ``None`` / empty string -> ``[]``
    - ``"{}"`` (very old rows) -> ``[]``
    - ``[{"level": ..., "message": ...}, ...]`` (pre-typed rows) -> validates
      through Diagnostic; missing optional fields stay None.

    Defensive at two layers so one corrupt row can't take down the whole
    flight-list endpoint:
    - Outer ``json.JSONDecodeError`` (column unparseable) -> empty list.
    - Per-item ``ValidationError`` (one bad entry) -> log and skip,
      keep the rest.
    Other exceptions still propagate — they signal a real bug, not a
    legacy/corrupt diagnostic.
    """
    if not raw:
        return []
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        logger.debug("Skipping unparseable diagnostics_json: %r", raw[:200])
        return []
    # Reject any non-list shape: legacy {} dict, JSON null, or scalar.
    # Without this, `for item in None` (or in a number/string) would
    # raise TypeError and 500 the flight-list endpoint.
    if not isinstance(parsed, list):
        return []
    out: list[Diagnostic] = []
    for item in parsed:
        try:
            out.append(Diagnostic.model_validate(item))
        except PydanticValidationError:
            logger.debug("Skipping malformed diagnostic entry: %r", item)
    return out


def _row_to_meta(row: BriefingPackRow) -> BriefingPackMeta:
    return BriefingPackMeta(
        id=row.id,
        flight_id=row.flight_id,
        fetch_timestamp=_ensure_utc(row.fetch_timestamp),
        days_out=row.days_out,
        has_gramet=row.has_gramet,
        has_skewt=row.has_skewt,
        has_digest=row.has_digest,
        llm_digest_requested=row.llm_digest_requested,
        assessment=row.assessment,
        assessment_reason=row.assessment_reason,
        digest_trace_id=row.digest_trace_id,
        artifact_path=_resolve_artifact_path(row.artifact_path),
        model_init_times=json.loads(row.model_init_times_json) if row.model_init_times_json else {},
        grib_init_times=json.loads(row.grib_init_times_json) if row.grib_init_times_json else {},
        model_sources=json.loads(row.model_sources_json) if row.model_sources_json else {},
        models_skipped_region=json.loads(row.models_skipped_region_json) if row.models_skipped_region_json else [],
        diagnostics=_parse_diagnostics(row.diagnostics_json),
        alt_assessment=row.alt_assessment,
        alt_assessment_reason=row.alt_assessment_reason,
        has_alt_advisories=row.has_alt_advisories,
        dwd_charts_run_cycle=row.dwd_charts_run_cycle,
        dwd_charts_default_id=row.dwd_charts_default_id,
        dwd_charts_in_coverage=row.dwd_charts_in_coverage,
        dwd_charts_within_horizon=row.dwd_charts_within_horizon,
        metoffice_charts_run_cycle=row.metoffice_charts_run_cycle,
        metoffice_charts_default_id=row.metoffice_charts_default_id,
        metoffice_charts_in_coverage=row.metoffice_charts_in_coverage,
        metoffice_charts_within_horizon=row.metoffice_charts_within_horizon,
    )


# --- Flight CRUD ---


def save_flight(session: Session, flight: Flight, user_id: str) -> None:
    """Insert or update a flight in the database.

    On insert: a ``share_code`` is allocated when the Flight didn't carry
    one, so every newly persisted flight is reachable via the short
    ``/s/{code}`` URL. On update: ``share_code`` is preserved (callers
    don't pass it through and we don't want to rotate the share link
    out from under existing recipients).
    """
    existing = session.get(FlightRow, flight.id)
    if existing:
        existing.route_name = flight.route_name
        existing.profile_id = flight.profile_id
        existing.aircraft_id = flight.aircraft_id
        existing.waypoints_json = json.dumps(flight.waypoints)
        existing.departure_time = flight.departure_time
        existing.cruise_altitude_ft = flight.cruise_altitude_ft
        existing.flight_ceiling_ft = flight.flight_ceiling_ft
        existing.flight_duration_hours = flight.flight_duration_hours
        existing.alt_departure_time = flight.alt_departure_time
        existing.private = flight.private
        existing.auto_refresh = flight.auto_refresh
        existing.auto_refresh_hour = flight.auto_refresh_hour
        existing.last_auto_refresh_at = flight.last_auto_refresh_at
        # Lazy-mint a code for legacy rows that never had one.
        if existing.share_code is None:
            existing.share_code = _allocate_share_code(session)
    else:
        if flight.share_code is None:
            flight.share_code = _allocate_share_code(session)
        session.add(_flight_to_row(flight, user_id))
    session.flush()


def load_flight(session: Session, flight_id: str) -> Flight:
    """Load a flight by ID. Raises KeyError if not found."""
    row = session.get(FlightRow, flight_id)
    if row is None:
        raise KeyError(f"Flight not found: {flight_id}")
    return _row_to_flight(row)


def lookup_flight_id_by_share_code(session: Session, code: str) -> str | None:
    """Resolve a short share_code to its flight_id, or None if unknown.

    Returns just the flight_id (not the full Flight) — the short-link
    handler only needs to redirect, so loading the full row would be
    wasted work.
    """
    return session.execute(
        select(FlightRow.id).where(FlightRow.share_code == code)
    ).scalar_one_or_none()


def list_flights(session: Session, user_id: str) -> list[Flight]:
    """List all flights for a user, sorted by flight time descending."""
    stmt = (
        select(FlightRow)
        .where(FlightRow.user_id == user_id)
        .order_by(FlightRow.departure_time.desc())
    )
    rows = session.execute(stmt).scalars().all()
    return [_row_to_flight(r) for r in rows]


def list_flights_with_role(
    session: Session, viewer_id: str
) -> list[tuple[Flight, Literal["owner", "subscriber"], str | None]]:
    """List owned ∪ subscribed flights with role + owner display name.

    Subscribed flights are excluded when the owner has flipped the flight to
    private. Returns (flight, role, owner_display_name) tuples; owner_display_name
    is populated only when role == "subscriber" (callers don't need it for
    flights the viewer already owns). Sorted by departure_time descending.

    Joins the users table so callers don't have to issue a separate query
    per flight to resolve the owner's display name.
    """
    from flyfun_common.db.models import UserRow

    owner = aliased(UserRow)
    # Deliberately do not select owner.email: the frontend falls back to
    # `flightDetail.sharedByUnknown` when display_name is missing, and
    # leaking the owner's email to every subscriber is a privacy bug.
    stmt = (
        select(
            FlightRow,
            FlightSubscriptionRow.user_id.label("sub_user_id"),
            owner.display_name,
        )
        .outerjoin(
            FlightSubscriptionRow,
            (FlightSubscriptionRow.flight_id == FlightRow.id)
            & (FlightSubscriptionRow.user_id == viewer_id),
        )
        .outerjoin(owner, owner.id == FlightRow.user_id)
        .where(
            or_(
                FlightRow.user_id == viewer_id,
                (FlightSubscriptionRow.user_id == viewer_id)
                & (FlightRow.private.is_(False)),
            )
        )
        .order_by(FlightRow.departure_time.desc())
    )
    results = session.execute(stmt).all()
    out: list[tuple[Flight, Literal["owner", "subscriber"], str | None]] = []
    for row, _sub_user_id, display_name in results:
        if row.user_id == viewer_id:
            out.append((_row_to_flight(row), "owner", None))
        else:
            out.append((_row_to_flight(row), "subscriber", display_name or None))
    return out


# --- Subscription CRUD ---


class SubscriptionError(Exception):
    """Raised when a subscription action is invalid (e.g. owner subscribing)."""


def subscribe_flight(session: Session, flight_id: str, user_id: str) -> bool:
    """Subscribe a user to a flight. Idempotent.

    Returns True if a new subscription was created, False if it already existed.
    Raises KeyError if the flight doesn't exist, and SubscriptionError if the
    user is the flight owner.
    """
    flight = session.get(FlightRow, flight_id)
    if flight is None:
        raise KeyError(f"Flight not found: {flight_id}")
    if flight.user_id == user_id:
        raise SubscriptionError("Owners cannot subscribe to their own flights")

    existing = session.execute(
        select(FlightSubscriptionRow).where(
            FlightSubscriptionRow.flight_id == flight_id,
            FlightSubscriptionRow.user_id == user_id,
        )
    ).scalar_one_or_none()
    if existing is not None:
        return False

    # Wrap the insert in a SAVEPOINT so only this statement rolls back if two
    # concurrent subscribe calls race past the existence check and the loser
    # trips the uq_flight_subs_flight_user constraint. A full session.rollback()
    # here would discard any prior work on this session (safe today since the
    # endpoint only does a SELECT first, but fragile for future callers).
    try:
        with session.begin_nested():
            session.add(FlightSubscriptionRow(flight_id=flight_id, user_id=user_id))
        return True
    except IntegrityError:
        return False


def unsubscribe_flight(session: Session, flight_id: str, user_id: str) -> bool:
    """Remove a subscription. Returns True if a row was removed, False otherwise."""
    row = session.execute(
        select(FlightSubscriptionRow).where(
            FlightSubscriptionRow.flight_id == flight_id,
            FlightSubscriptionRow.user_id == user_id,
        )
    ).scalar_one_or_none()
    if row is None:
        return False
    session.delete(row)
    session.flush()
    return True


def is_subscribed(session: Session, flight_id: str, user_id: str) -> bool:
    """Return True if the user has an active subscription to this flight."""
    return session.execute(
        select(FlightSubscriptionRow.id).where(
            FlightSubscriptionRow.flight_id == flight_id,
            FlightSubscriptionRow.user_id == user_id,
        )
    ).first() is not None


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


def bulk_delete_flights(
    session: Session, flight_ids: list[str], user_id: str
) -> list[str]:
    """Delete multiple flights owned by ``user_id`` in a single SQL statement.

    Returns the list of actually-deleted IDs. IDs that don't exist or aren't
    owned by the user are silently skipped. Artifact directories for all
    associated packs are removed from disk after the DB delete succeeds.

    DB-level FK CASCADE (SQLite with ``PRAGMA foreign_keys=ON``, MySQL InnoDB)
    removes briefing_packs and flight_subscriptions rows.
    """
    if not flight_ids:
        return []

    stmt = (
        select(FlightRow.id, BriefingPackRow.artifact_path)
        .outerjoin(BriefingPackRow, BriefingPackRow.flight_id == FlightRow.id)
        .where(FlightRow.id.in_(flight_ids), FlightRow.user_id == user_id)
    )
    owned_ids: set[str] = set()
    artifact_paths: list[str] = []
    for flight_id, artifact_path in session.execute(stmt).all():
        owned_ids.add(flight_id)
        if artifact_path:
            artifact_paths.append(artifact_path)

    if not owned_ids:
        return []

    session.execute(
        delete(FlightRow).where(
            FlightRow.id.in_(owned_ids),
            FlightRow.user_id == user_id,
        )
    )
    session.flush()

    for path in artifact_paths:
        _rmtree(Path(path))

    return list(owned_ids)


# --- BriefingPack operations ---


def save_pack_meta(session: Session, meta: BriefingPackMeta) -> None:
    """Insert briefing pack metadata with integrity HMAC."""
    row = _meta_to_row(meta)
    row.integrity_hmac = _compute_pack_hmac(row)
    session.add(row)
    session.flush()


def update_pack_meta(session: Session, meta: BriefingPackMeta) -> bool:
    """Update an existing pack row (matched by flight_id + fetch_timestamp).

    Returns True when an existing row was updated, False when no row existed
    (caller should fall back to ``save_pack_meta``).
    """
    naive_ts = meta.fetch_timestamp.replace(tzinfo=None)
    stmt = select(BriefingPackRow).where(
        BriefingPackRow.flight_id == meta.flight_id,
        BriefingPackRow.fetch_timestamp == naive_ts,
    )
    row = session.execute(stmt).scalar_one_or_none()
    if row is None:
        return False
    _apply_meta_to_row(row, meta)
    row.integrity_hmac = _compute_pack_hmac(row)
    session.flush()
    return True


def load_pack_meta(
    session: Session, flight_id: str, fetch_timestamp: str | datetime
) -> BriefingPackMeta:
    """Load pack metadata. Raises KeyError if not found.

    ``fetch_timestamp`` can be a datetime or an ISO string (parsed automatically).
    """
    from datetime import timezone as _tz

    if isinstance(fetch_timestamp, str):
        ts = datetime.fromisoformat(fetch_timestamp)
        fetch_timestamp = ts

    # SQLite stores datetimes as naive text (no timezone suffix).
    # Strip tzinfo so the bound parameter matches the stored format.
    naive_ts = fetch_timestamp.replace(tzinfo=None)

    stmt = select(BriefingPackRow).where(
        BriefingPackRow.flight_id == flight_id,
        BriefingPackRow.fetch_timestamp == naive_ts,
    )
    row = session.execute(stmt).scalar_one_or_none()
    if row is None:
        raise KeyError(f"Pack not found: {flight_id}/{fetch_timestamp}")
    if not _verify_pack_hmac(row):
        logger.warning(
            "Pack integrity check failed: flight=%s ts=%s id=%d",
            flight_id, fetch_timestamp, row.id,
        )
    return _row_to_meta(row)


def list_packs(session: Session, flight_id: str) -> list[BriefingPackMeta]:
    """List all packs for a flight, newest first."""
    stmt = (
        select(BriefingPackRow)
        .where(BriefingPackRow.flight_id == flight_id)
        .order_by(BriefingPackRow.fetch_timestamp.desc())
    )
    rows = session.execute(stmt).scalars().all()
    for row in rows:
        if not _verify_pack_hmac(row):
            logger.warning(
                "Pack integrity check failed: flight=%s id=%d", flight_id, row.id,
            )
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

    For new users: seeds all system profile templates (VFR Only, IFR
    Conservative, IFR FIKI) with the first template marked as default.
    For existing users with legacy preferences: migrates them into a
    "Default" profile.
    """
    from weatherbrief.db.models import UserPreferencesRow

    stmt = (
        select(FlightProfileRow)
        .where(FlightProfileRow.user_id == user_id, FlightProfileRow.is_default.is_(True))
    )
    row = session.execute(stmt).scalar_one_or_none()
    if row is not None:
        _backfill_system_templates(session, user_id)
        return _row_to_profile(row)

    # Check if user has legacy preferences to migrate
    prefs_row = session.get(UserPreferencesRow, user_id)
    legacy_settings: dict = {}
    if prefs_row and prefs_row.app_prefs_json:
        try:
            data = json.loads(prefs_row.app_prefs_json)
            for key in (
                "cruise_altitude_ft", "flight_ceiling_ft",
                "models", "advisory_models",
                "gramet_enabled", "llm_digest_enabled",
                "advisories",
            ):
                if key in data:
                    legacy_settings[key] = data[key]
        except json.JSONDecodeError:
            pass

    if legacy_settings:
        # Existing user with legacy prefs: create a single "Default" profile
        new_row = FlightProfileRow(
            user_id=user_id,
            name="Default",
            is_default=True,
            settings_json=json.dumps(legacy_settings),
        )
        session.add(new_row)
        session.flush()
        return _row_to_profile(new_row)

    # New user: seed all system profile templates
    return _seed_system_profiles(session, user_id)


def _seed_system_profiles(
    session: Session, user_id: str, *, mark_first_default: bool = True,
) -> FlightProfile:
    """Create system profile templates for a user.

    Args:
        mark_first_default: If True (new users), the first template is marked
            as the default profile.  If False (backfill for existing users),
            all templates are created as non-default.

    Returns the first profile created (the default for new users).
    """
    from weatherbrief.storage.system_profiles import load_system_templates

    templates = load_system_templates()
    first_profile: FlightProfile | None = None

    for i, tpl in enumerate(templates):
        is_default = mark_first_default and (i == 0)
        row = FlightProfileRow(
            user_id=user_id,
            name=tpl["name"],
            is_default=is_default,
            settings_json=json.dumps(tpl["settings"]),
            system_template_key=tpl["key"],
        )
        session.add(row)
        session.flush()
        if first_profile is None:
            first_profile = _row_to_profile(row)

    if first_profile is None:
        # Fallback if no templates found
        row = FlightProfileRow(
            user_id=user_id,
            name="Default",
            is_default=True,
            settings_json="{}",
        )
        session.add(row)
        session.flush()
        first_profile = _row_to_profile(row)

    return first_profile


def _backfill_system_templates(session: Session, user_id: str) -> None:
    """Seed system templates for an existing user who doesn't have them yet.

    No-op if the user already has any profile with a system_template_key.
    Creates all templates as non-default so the user's existing default
    is preserved.
    """
    has_templates = session.execute(
        select(FlightProfileRow.id)
        .where(
            FlightProfileRow.user_id == user_id,
            FlightProfileRow.system_template_key.is_not(None),
        )
        .limit(1)
    ).first()
    if has_templates:
        return

    _seed_system_profiles(session, user_id, mark_first_default=False)


def safe_path_component(value: str) -> str:
    """Sanitize a string for use as a single path component.

    Strips path separators and traversal sequences, keeping only
    alphanumeric chars, hyphens, underscores, and dots (no leading dot).
    """
    import re

    sanitized = re.sub(r"[^a-zA-Z0-9._-]", "_", value)
    sanitized = sanitized.lstrip(".")
    return sanitized or "_"


def pack_dir_for(user_id: str, flight_id: str, fetch_timestamp: str | datetime) -> Path:
    """Get the directory path for a specific pack's artifacts.

    Layout: data/packs/{user_id}/{flight_id}/{safe_timestamp}/
    """
    ts_str = fetch_timestamp.isoformat() if isinstance(fetch_timestamp, datetime) else fetch_timestamp
    safe_ts = ts_str.replace(":", "-").replace("+", "p")
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
