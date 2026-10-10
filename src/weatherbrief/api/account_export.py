"""Account data export (GDPR Art. 20 — right to data portability).

Lets a signed-in user download a machine-readable (JSON) copy of all the
personal data we hold about them: account, preferences, flights, briefings,
trips, followed flights, feedback, usage, aircraft, PIREPs, device
registrations, API tokens and connected apps, donations and cost records.

Every table carrying a ``user_id`` column must appear in either
``_USER_SECTIONS`` (exported) or ``_NOT_EXPORTED`` (with a reason);
``tests/test_account_export.py`` enforces this so a new user-linked table can't
silently drift out of the export. Secrets and server-internal fields
(encrypted credentials, token hashes, push-token values, integrity HMACs,
AI-triage internals, file paths) are deliberately excluded; see ``_EXCLUDE``.
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
from flyfun_common.db.models import (
    ApiTokenRow,
    CostLedgerRow,
    DonationRow,
    UserPreferencesRow,
    UserRow,
)
from flyfun_common.oauth.models import OAuthRefreshTokenRow
from weatherbrief.db.models import (
    ApiUsageRow,
    BriefingRefreshJobRow,
    BriefingUsageRow,
    DeviceTokenRow,
    FeedbackRow,
    FlightBriefingSeenRow,
    FlightProfileRow,
    FlightRow,
    FlightSubscriptionRow,
    FlightTripRow,
    LiveDeliveryRow,
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
    "api_tokens": {"token_hash"},
    "oauth_refresh_tokens": {"token_hash", "access_token_hash"},
    "flight_trips": {
        "ai_summary_key", "refresh_id", "refresh_state_json", "refresh_started_at",
    },
    "briefing_refresh_jobs": {"pack_path"},
}

# Flat per-user sections: export key -> table queried by ``user_id``. The
# ``users``/``user_preferences``/``flights`` tables are serialized separately
# (single rows, or nested with their packs and debrief).
_USER_SECTIONS: dict[str, type] = {
    "aircraft": UserAircraftRow,
    "flight_profiles": FlightProfileRow,
    "trips": FlightTripRow,
    "followed_flights": FlightSubscriptionRow,
    "briefing_seen_state": FlightBriefingSeenRow,
    "refresh_jobs": BriefingRefreshJobRow,
    "feedback": FeedbackRow,
    "usage": BriefingUsageRow,
    "api_usage": ApiUsageRow,
    "cost_records": CostLedgerRow,
    "donations": DonationRow,
    "pireps": PirepRow,
    "device_registrations": DeviceTokenRow,
    "api_tokens": ApiTokenRow,
    "connected_apps": OAuthRefreshTokenRow,
    "live_deliveries": LiveDeliveryRow,
}

# Tables with a ``user_id`` column that are deliberately not exported.
_NOT_EXPORTED: dict[str, str] = {
    "oauth_authorization_codes": (
        "single-use credential living for minutes; the grant it produces is "
        "exported under connected_apps"
    ),
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

    Served as a file download. Covers every user-linked table (see
    ``_USER_SECTIONS``); excludes secrets and server-internal fields.
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

    sections = {
        key: [
            _row_to_dict(r)
            for r in db.query(model).filter(model.user_id == user_id).all()
        ]
        for key, model in _USER_SECTIONS.items()
    }

    export = {
        "format_version": EXPORT_FORMAT_VERSION,
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "user_id": user_id,
        "note": (
            "This is a complete export of the personal data FlyFun Weather holds "
            "for your account. Encrypted credentials, token hashes and push-token "
            "values are intentionally omitted for security. PIREPs are anonymized (not "
            "deleted) when you delete your account."
        ),
        "account": _row_to_dict(user),
        "preferences": _row_to_dict(prefs) if prefs else None,
        "flights": flights_out,
        **sections,
    }

    logger.info("Account data export generated for user %s", user_id)
    filename = f"flyfun-weather-export-{user_id}.json"
    return JSONResponse(
        content=export,
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
