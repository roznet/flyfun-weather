"""Account data export (GDPR Art. 20 — right to data portability).

Lets a signed-in user download a machine-readable (JSON) copy of all the
personal data we hold about them: account, preferences, flights, briefings,
feedback, usage, aircraft, PIREPs and device registrations.

This is the read-only counterpart to ``_on_delete_user`` in ``app.py`` — the
two should cover the same set of user-owned tables. Secrets and server-internal
fields (encrypted credentials, push-token values, integrity HMACs, AI-triage
internals, file paths) are deliberately excluded; see ``_EXCLUDE`` below.
"""

from __future__ import annotations

import json
import logging
from datetime import date, datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from sqlalchemy import inspect as sa_inspect
from sqlalchemy.orm import Session

from flyfun_common.db import current_user_id, get_db
from flyfun_common.db.models import UserRow, UserPreferencesRow
from weatherbrief.db.models import (
    BriefingUsageRow,
    DeviceTokenRow,
    FeedbackRow,
    FlightProfileRow,
    FlightRow,
    PirepRow,
    UserAircraftRow,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/account", tags=["account"])

# The export format version. Bump when the shape changes materially so consumers
# (and our own re-import tooling, if ever built) can branch on it.
EXPORT_FORMAT_VERSION = 1

# Per-table columns to omit from the export. Either a secret, a server-internal
# value with no meaning to the user, or an admin/AI-only field that is not the
# user's own personal data.
_EXCLUDE: dict[str, set[str]] = {
    "users": {"provider_sub", "tokens_valid_after"},
    "user_preferences": {"encrypted_creds_json"},
    "briefing_packs": {"artifact_path", "integrity_hmac", "digest_trace_id"},
    "feedback": {
        "ai_analysis",
        "classification",
        "confidence",
        "admin_notes",
        "triage_prompt",
        "triage_raw_response",
    },
    "device_tokens": {"token"},  # push token value is a credential, not exported
}


def _jsonify(value: Any) -> Any:
    """Make a single column value JSON-serializable."""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return value


def _row_to_dict(row: Any) -> dict[str, Any]:
    """Serialize an ORM row to a plain dict, honouring ``_EXCLUDE``.

    Columns named ``*_json`` are parsed back into objects so the export is a
    single clean JSON document rather than JSON-encoded strings inside JSON.
    """
    table = row.__tablename__
    excluded = _EXCLUDE.get(table, set())
    out: dict[str, Any] = {}
    for col in sa_inspect(row).mapper.columns:
        name = col.name
        if name in excluded:
            continue
        value = getattr(row, name)
        if name.endswith("_json") and isinstance(value, str) and value:
            try:
                value = json.loads(value)
            except (json.JSONDecodeError, ValueError):
                pass  # leave as the raw string if it isn't valid JSON
        out[name] = _jsonify(value)
    return out


@router.get("/export")
def export_account_data(
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Return all personal data held for the authenticated user as JSON.

    Served as a file download. Mirrors the deletion inventory; excludes
    secrets and server-internal fields.
    """
    user = db.get(UserRow, user_id)
    if not user:
        raise HTTPException(status_code=401, detail="User not found")

    prefs = db.get(UserPreferencesRow, user_id)

    flights = (
        db.query(FlightRow).filter(FlightRow.user_id == user_id).all()
    )
    flights_out = []
    for flight in flights:
        entry = _row_to_dict(flight)
        entry["briefing_packs"] = [_row_to_dict(p) for p in flight.packs]
        if flight.debrief is not None:
            entry["debrief"] = _row_to_dict(flight.debrief)
        flights_out.append(entry)

    aircraft = (
        db.query(UserAircraftRow).filter(UserAircraftRow.user_id == user_id).all()
    )
    profiles = (
        db.query(FlightProfileRow).filter(FlightProfileRow.user_id == user_id).all()
    )
    usage = (
        db.query(BriefingUsageRow).filter(BriefingUsageRow.user_id == user_id).all()
    )
    pireps = db.query(PirepRow).filter(PirepRow.user_id == user_id).all()
    device_tokens = (
        db.query(DeviceTokenRow).filter(DeviceTokenRow.user_id == user_id).all()
    )
    feedback = db.query(FeedbackRow).filter(FeedbackRow.user_id == user_id).all()

    export = {
        "format_version": EXPORT_FORMAT_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "user_id": user_id,
        "note": (
            "This is a complete export of the personal data FlyFun Weather holds "
            "for your account. Encrypted credentials and push-token values are "
            "intentionally omitted for security. PIREPs are anonymized (not "
            "deleted) when you delete your account."
        ),
        "account": _row_to_dict(user),
        "preferences": _row_to_dict(prefs) if prefs else None,
        "aircraft": [_row_to_dict(a) for a in aircraft],
        "flight_profiles": [_row_to_dict(p) for p in profiles],
        "flights": flights_out,
        "feedback": [_row_to_dict(f) for f in feedback],
        "usage": [_row_to_dict(u) for u in usage],
        "pireps": [_row_to_dict(p) for p in pireps],
        "device_registrations": [_row_to_dict(d) for d in device_tokens],
    }

    logger.info("Account data export generated for user %s", user_id)
    filename = f"flyfun-weather-export-{user_id}.json"
    return JSONResponse(
        content=export,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
