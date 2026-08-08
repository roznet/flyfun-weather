"""API endpoints for user preferences and autorouter credentials."""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from flyfun_common import fx
from flyfun_common.auth.config import is_dev_mode
from flyfun_common.autorouter import get_autorouter_token
from flyfun_common.credentials import (
    load_encrypted_creds,
    save_encrypted_creds,
    clear_encrypted_creds,
)
from flyfun_common.db import current_user_id, get_db
from flyfun_common.db.models import UserPreferencesRow

from weatherbrief.analysis.advisories.engine_methods import ENGINE_METHOD_DEFAULTS
from weatherbrief.api.user_migrations import run_pending_migrations

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/user/preferences", tags=["preferences"])

#: Notification scope — which refreshes notify (single choice).
NotifyScope = Literal["auto", "all", "off"]

#: Ordering of the upcoming-flights section (#536). The default reproduces the
#: historical ``departure_time desc`` ordering exactly.
FlightOrder = Literal["furthest_first", "soonest_first"]
DEFAULT_FLIGHT_ORDER: str = "furthest_first"


def _normalize_flight_order(value: object) -> str:
    """Coerce a stored ``flight_order`` to a value every client can render.

    The single normalizer, shared by the GET response and by
    :func:`load_flight_order`, so a hand-edited or future-version blob can never
    make the reported preference disagree with the ordering actually applied.
    """
    return value if value in ("furthest_first", "soonest_first") else DEFAULT_FLIGHT_ORDER


class FxBlock(BaseModel):
    """Display-currency block carried on USD-canonical cost/donation responses.

    ``rate`` is units of ``currency`` per 1 USD; the frontend formats from it.
    Shared by the cost and donation response models so the shape stays in one
    place and shows up in the OpenAPI schema.
    """

    currency: str
    rate: float
    as_of: str | None = None


class AdvisoryPreferences(BaseModel):
    """User's advisory configuration."""

    enabled: dict[str, bool] | None = None  # advisory_id -> enabled
    params: dict[str, dict[str, float]] | None = None  # advisory_id -> {param: value}


class FlightDefaults(BaseModel):
    """User's default flight parameters."""

    cruise_altitude_ft: int | None = None
    flight_ceiling_ft: int | None = None
    models: list[str] | None = None  # e.g. ["gfs", "ecmwf", "icon"]
    advisory_models: list[str] | None = None  # subset of models for advisories


class DigestConfig(BaseModel):
    """User's digest configuration."""

    config_name: str | None = None


class PreferencesResponse(BaseModel):
    """Preferences returned to the client (never includes raw credentials)."""

    defaults: FlightDefaults
    digest_config: DigestConfig
    advisories: AdvisoryPreferences
    has_autorouter_creds: bool
    autorouter_mode: str  # "oauth" (prod) or "password" (dev)
    gramet_enabled: bool
    llm_digest_enabled: bool
    icing_severity_enhance: bool
    locale: str
    units_region: str
    # Ordering of the *upcoming* flights section only (#536). "furthest_first"
    # is the historical behaviour (departure_time desc); "soonest_first" puts
    # the next departure at the top. Past/Recent stay most-recent-first under
    # both — past pagination is offset-based over that order.
    flight_order: str
    display_currency: str  # ISO 4217 or "auto" (cost/donation display only)
    synoptic_forecast_map_enabled: bool
    defer_email_for_model_update: bool
    # Briefing-refresh notifications (ios-app-briefing-notifications.md).
    # Channels are independent (either/both/neither); scope is a single choice
    # applied to all selected channels; change-only gates on assessment change.
    notify_email: bool = True
    notify_push: bool = False
    notify_scope: NotifyScope = "auto"
    notify_change_only: bool = True
    # One-time fail-safe notice: set when the user's last push device was
    # unregistered while email was off, so email was auto-re-enabled to keep at
    # least one working channel (the channel invariant, decay branch). The
    # client shows it once, then dismisses by PUTting ``notify_decay_notice: false``.
    notify_decay_notice: bool = False
    # Count of the user's registered APNs devices. Push is only actionable with
    # ≥1 device — the web shows a disabled "install the app" row at 0, and both
    # clients use this to render the channel-invariant (email locks on when
    # push has no device to deliver to).
    push_device_count: int = 0
    pirep_can_view: bool = False
    pirep_can_publish: bool = False
    donations_enabled: bool = False  # global: Stripe configured (gates the donate UI)
    # Airports where the pilot has an unpublished/self-briefed approach — a
    # cloud break procedure or a private GNSS approach (#510). A fact about the
    # pilot and the airport, entered once and applied everywhere, which is why
    # it lives here rather than in a flight profile's advisory params. Consumed
    # ONLY by the approach_feasibility advisory: it must never reach the
    # alternate-minima calculations, where a personal assertion would turn into
    # a regulatory claim.
    declared_approach_icaos: list[str] = Field(default_factory=list)


class PreferencesUpdate(BaseModel):
    """Payload for updating preferences."""

    defaults: FlightDefaults | None = None
    digest_config: DigestConfig | None = None
    advisories: AdvisoryPreferences | None = None
    autorouter_username: str | None = None
    autorouter_password: str | None = None
    gramet_enabled: bool | None = None
    llm_digest_enabled: bool | None = None
    icing_severity_enhance: bool | None = None
    locale: str | None = None
    units_region: Literal["auto", "europe", "us"] | None = None
    flight_order: FlightOrder | None = None
    display_currency: str | None = None  # "auto" or an ISO 4217 code (e.g. "EUR")
    synoptic_forecast_map_enabled: bool | None = None
    defer_email_for_model_update: bool | None = None
    notify_email: bool | None = None
    notify_push: bool | None = None
    notify_scope: NotifyScope | None = None
    notify_change_only: bool | None = None
    notify_decay_notice: bool | None = None  # only meaningful as false, to dismiss
    # Accepts the raw free-form text the settings field holds ("EGTF, EGSX
    # EGLK") as well as a JSON array, and canonicalizes either on the way in —
    # normalization lives here rather than in the web client so every writer
    # (a direct PUT, a future iOS screen) stores the same shape.
    declared_approach_icaos: list[str] | str | None = None

    @field_validator("declared_approach_icaos")
    @classmethod
    def _normalize_declared(cls, v: list[str] | str | None) -> list[str] | None:
        """Split on commas/whitespace, uppercase, trim, dedupe, keep order."""
        return None if v is None else _normalize_icao_list(v)

    @field_validator("display_currency")
    @classmethod
    def _normalize_display_currency(cls, v: str | None) -> str | None:
        """Normalize to "auto" or a 3-letter code so the contract is explicit."""
        return None if v is None else _normalize_currency(v)


def _normalize_icao_list(value: list[str] | str) -> list[str]:
    """Canonicalize free-form ICAO input to an uppercase, deduped list.

    Accepts either the raw text of the settings field or an already-split list
    whose elements may themselves hold separators, so a client that does its own
    (partial) splitting cannot produce a different stored shape than one that
    posts the text verbatim. Input order is preserved — the user's list is
    theirs to order.
    """
    raw = value if isinstance(value, str) else " ".join(v for v in value if v)
    seen: dict[str, None] = {}
    for token in re.split(r"[\s,;]+", raw.upper()):
        if token:
            seen.setdefault(token, None)
    return list(seen)


def _parse_declared_approaches(raw: str) -> list[str]:
    """Extract the declared unpublished-approach airports from ``app_prefs_json``.

    Re-normalizes on read as well as on write: the key is small enough to have
    been hand-edited in the DB, and a stored ``"egtf"`` that reads back
    lowercase would silently match no airport in the collection step, which is
    the exact failure mode #510 exists to prevent.
    """
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return []
    stored = data.get("declared_approach_icaos")
    if not stored:
        return []
    if not isinstance(stored, (list, str)):
        return []
    return _normalize_icao_list(stored)


def _load_prefs(db: Session, user_id: str) -> UserPreferencesRow:
    """Load or create preferences row for a user."""
    row = db.get(UserPreferencesRow, user_id)
    if row is None:
        row = UserPreferencesRow(user_id=user_id)
        db.add(row)
        db.flush()
    return row


_RETIRED_MODELS = {"best_match"}


def _parse_defaults(raw: str) -> FlightDefaults:
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        data = {}
    # Strip retired models from stored preferences
    if "models" in data and isinstance(data["models"], list):
        data["models"] = [m for m in data["models"] if m not in _RETIRED_MODELS]
    if "advisory_models" in data and isinstance(data["advisory_models"], list):
        data["advisory_models"] = [m for m in data["advisory_models"] if m not in _RETIRED_MODELS]
    return FlightDefaults(**data)


def _parse_service_toggles(raw: str) -> dict:
    """Extract service toggles and locale from the app_prefs_json blob.

    #410 retired the account-level engine methods (``icing_method`` /
    ``cloud_method`` / ``convective_method``): they were empty for every user,
    never written by any client, and never read by the pipeline (which grades
    exclusively off the flight *profile*). This parser no longer surfaces them.
    """
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        data = {}
    return {
        "gramet_enabled": data.get("gramet_enabled", True),
        "llm_digest_enabled": data.get("llm_digest_enabled", True),
        "icing_severity_enhance": data.get("icing_severity_enhance", False),
        "locale": data.get("locale", "en"),
        "units_region": data.get("units_region", "auto"),
        "flight_order": _normalize_flight_order(data.get("flight_order")),
        "display_currency": data.get("display_currency", "auto"),
        "synoptic_forecast_map_enabled": data.get("synoptic_forecast_map_enabled", False),
        "defer_email_for_model_update": data.get("defer_email_for_model_update", False),
    }


def _parse_notify_prefs(raw: str) -> dict:
    """Extract briefing-notification preferences from the app_prefs blob.

    Defaults reproduce today's behaviour (ios-app-briefing-notifications.md →
    "Migration preserves today's behavior"): email on, scope ``auto``
    (scheduler-only — what the implicit email-on-completion does today),
    change-only on, push off until a device registers.
    """
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        data = {}
    scope = data.get("notify_scope", "auto")
    if scope not in ("auto", "all", "off"):
        scope = "auto"
    return {
        "notify_email": bool(data.get("notify_email", True)),
        "notify_push": bool(data.get("notify_push", False)),
        "notify_scope": scope,
        "notify_change_only": bool(data.get("notify_change_only", True)),
    }


def _parse_advisory_prefs(raw: str) -> AdvisoryPreferences:
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        data = {}
    adv_data = data.get("advisories", {})
    return AdvisoryPreferences(**adv_data)


def _parse_digest_config(raw: str) -> DigestConfig:
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        data = {}
    return DigestConfig(**data)


def _parse_digest_config_from_prefs(raw: str) -> DigestConfig:
    """Extract digest_config from the app_prefs_json blob."""
    try:
        data = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        data = {}
    dc = data.get("digest_config", {})
    return DigestConfig(**dc)


def _count_push_devices(db: Session, user_id: str) -> int:
    """Count the user's registered APNs device tokens (for conditional push UI)."""
    from weatherbrief.notify.push import count_user_devices

    return count_user_devices(db, user_id)


def _build_response(row: UserPreferencesRow, db: Session, user_id: str) -> PreferencesResponse:
    """Build a PreferencesResponse from a DB row."""
    toggles = _parse_service_toggles(row.app_prefs_json)
    try:
        prefs_data = json.loads(row.app_prefs_json) if row.app_prefs_json else {}
    except json.JSONDecodeError:
        prefs_data = {}

    if is_dev_mode():
        creds = load_encrypted_creds(db, user_id)
        has_ar = bool(creds and "username" in creds and "password" in creds)
    else:
        has_ar = get_autorouter_token(db, user_id) is not None

    return PreferencesResponse(
        defaults=_parse_defaults(row.app_prefs_json),
        digest_config=_parse_digest_config_from_prefs(row.app_prefs_json),
        advisories=_parse_advisory_prefs(row.app_prefs_json),
        **_parse_notify_prefs(row.app_prefs_json),
        notify_decay_notice=bool(prefs_data.get("notify_decay_notice", False)),
        push_device_count=_count_push_devices(db, user_id),
        has_autorouter_creds=has_ar,
        autorouter_mode="password" if is_dev_mode() else "oauth",
        pirep_can_view=prefs_data.get("pirep_can_view", False),
        pirep_can_publish=prefs_data.get("pirep_can_publish", False),
        donations_enabled=stripe_configured(),
        declared_approach_icaos=_parse_declared_approaches(row.app_prefs_json),
        **toggles,
    )


@router.get("", response_model=PreferencesResponse)
def get_preferences(
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Get the current user's preferences."""
    row = _load_prefs(db, user_id)
    run_pending_migrations(db, row)
    return _build_response(row, db, user_id)


@router.put("", response_model=PreferencesResponse)
def update_preferences(
    body: PreferencesUpdate,
    request: Request,
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Update the current user's preferences."""
    row = _load_prefs(db, user_id)

    # Merge into existing app_prefs_json to preserve sibling keys
    try:
        data = json.loads(row.app_prefs_json) if row.app_prefs_json else {}
    except json.JSONDecodeError:
        data = {}

    if body.defaults is not None:
        defaults_dict = body.defaults.model_dump(exclude_none=True)
        # Enforce advisory_models as subset of models
        if "advisory_models" in defaults_dict and "models" in defaults_dict:
            allowed = set(defaults_dict["models"])
            defaults_dict["advisory_models"] = [
                m for m in defaults_dict["advisory_models"] if m in allowed
            ]
        elif "advisory_models" in defaults_dict:
            # models not being updated — check against existing stored models
            existing_models = set(data.get("models", []))
            if existing_models:
                defaults_dict["advisory_models"] = [
                    m for m in defaults_dict["advisory_models"] if m in existing_models
                ]
        data.update(defaults_dict)

    if body.advisories is not None:
        data["advisories"] = body.advisories.model_dump(exclude_none=True)

    # Service toggles
    if body.gramet_enabled is not None:
        data["gramet_enabled"] = body.gramet_enabled
    if body.llm_digest_enabled is not None:
        data["llm_digest_enabled"] = body.llm_digest_enabled
    if body.icing_severity_enhance is not None:
        data["icing_severity_enhance"] = body.icing_severity_enhance
    if body.locale is not None:
        data["locale"] = body.locale
    if body.units_region is not None:
        data["units_region"] = body.units_region
    if body.flight_order is not None:
        data["flight_order"] = body.flight_order
    if body.display_currency is not None:
        # Already normalized by the PreferencesUpdate field validator.
        data["display_currency"] = body.display_currency
    if body.synoptic_forecast_map_enabled is not None:
        data["synoptic_forecast_map_enabled"] = body.synoptic_forecast_map_enabled
    if body.defer_email_for_model_update is not None:
        data["defer_email_for_model_update"] = body.defer_email_for_model_update
    if body.notify_email is not None:
        data["notify_email"] = body.notify_email
    if body.notify_push is not None:
        data["notify_push"] = body.notify_push
    if body.notify_scope is not None:
        data["notify_scope"] = body.notify_scope
    if body.notify_change_only is not None:
        data["notify_change_only"] = body.notify_change_only
    if body.notify_decay_notice is False:
        # Only a dismissal is honored — the server owns *setting* the notice (the
        # decay path); a client can't raise it, only acknowledge it.
        data["notify_decay_notice"] = False

    # Channel-invariant backstop (server-side): notifications can't be "on" with
    # no *deliverable* channel, or nothing would ever fire — the silent
    # dead-state. Mirror the client reroute (turning off the last channel means
    # "silence me" → scope off) so the invariant holds for ANY writer — a direct
    # PUT, or a client whose UI guard leaks — not just the web/iOS forms.
    # ``notify_push`` alone is NOT a deliverable channel: it needs a registered
    # device, so a `{email:false, push:true}` PUT with no device is still a
    # dead-state (this is what the web/iOS UIs prevent by disabling the push
    # toggle when device-less).
    if data.get("notify_scope", "auto") != "off" and not data.get("notify_email", True):
        push_deliverable = (
            data.get("notify_push", False) and _count_push_devices(db, user_id) > 0
        )
        if not push_deliverable:
            data["notify_scope"] = "off"

    if body.digest_config is not None:
        data["digest_config"] = body.digest_config.model_dump(exclude_none=True)

    if body.declared_approach_icaos is not None:
        # Validate before storing, and reject the whole write rather than
        # dropping the bad codes: a typo'd EGFT that vanished on save would
        # leave the pilot believing their home field is declared when it is not
        # — silently reinstating the very false REDs the declaration removes
        # (#510). An empty list is a legitimate write (clearing the list), and
        # skips straight through.
        from weatherbrief.airports import unknown_icaos

        db_path = getattr(request.app.state, "db_path", "") or ""
        bad = unknown_icaos(body.declared_approach_icaos, db_path)
        if bad:
            raise HTTPException(
                status_code=400,
                detail={
                    "error": "unknown_airports",
                    "codes": bad,
                    "message": (
                        "Not recognised as airport codes: " + ", ".join(bad)
                    ),
                },
            )
        data["declared_approach_icaos"] = body.declared_approach_icaos

    row.app_prefs_json = json.dumps(data)

    if is_dev_mode() and body.autorouter_username and body.autorouter_password:
        save_encrypted_creds(db, user_id, {
            "username": body.autorouter_username,
            "password": body.autorouter_password,
        })

    return _build_response(row, db, user_id)


@router.delete("/autorouter", status_code=204)
def clear_autorouter_credentials(
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Clear the user's stored autorouter credentials."""
    clear_encrypted_creds(db, user_id)


def load_autorouter_token(db: Session, user_id: str) -> str | None:
    """Load the autorouter access token for a user.

    In production (OAuth mode): retrieves the token stored by the OAuth
    authorization code flow via flyfun-common.
    In dev mode: loads username/password from encrypted creds and exchanges
    them for a token via the client_credentials grant.

    Returns the bearer token string, or None if not configured.
    """
    if is_dev_mode():
        creds = load_encrypted_creds(db, user_id)
        if not creds or "username" not in creds or "password" not in creds:
            return None
        try:
            from euro_aip.utils.autorouter_credentials import AutorouterCredentialManager
            from weatherbrief.storage.flights import _data_dir

            # Under DATA_DIR with 0700 perms — not a predictable,
            # world-readable /tmp path on shared dev machines.
            creds_dir = _data_dir() / "autorouter-dev-creds"
            creds_dir.mkdir(parents=True, exist_ok=True)
            creds_dir.chmod(0o700)
            mgr = AutorouterCredentialManager(str(creds_dir))
            mgr.set_credentials(creds["username"], creds["password"])
            return mgr.get_token()
        except Exception:
            logger.warning("Failed to exchange dev autorouter credentials for user %s", user_id)
            return None
    return get_autorouter_token(db, user_id)


def load_user_locale(db: Session, user_id: str) -> str | None:
    """Load the user's preferred locale (en/fr/de/es).

    Returns the locale string or None if not set.
    """
    row = db.get(UserPreferencesRow, user_id)
    if not row or not row.app_prefs_json:
        return None
    try:
        data = json.loads(row.app_prefs_json)
        locale = data.get("locale")
        return locale if locale and locale != "en" else None
    except json.JSONDecodeError:
        return None


def load_declared_approaches(db: Session, user_id: str) -> list[str]:
    """Airports where this user declared an unpublished approach (#510).

    The single read path for the briefing pipeline, mirroring
    :func:`load_user_locale` — another account-level preference the advisory
    stack consumes. Returns ``[]`` for a user with no row or no declaration, so
    every caller can pass the result straight through without a None check.

    Consumers must be limited to ``approach_feasibility``. This list is a
    personal operational assertion and carries no regulatory weight: a
    self-briefed approach cannot make a field qualify as a filed alternate under
    EASA NCO.OP.143, nor relieve the NCO.OP.140 trigger, so it must never reach
    ``alternate_requirement`` or the alternates candidate gate.
    """
    row = db.get(UserPreferencesRow, user_id)
    if not row or not row.app_prefs_json:
        return []
    return _parse_declared_approaches(row.app_prefs_json)


def load_units_region(db: Session, user_id: str) -> str:
    """Load the user's display-units preference ('auto', 'europe', or 'us').

    Returns the raw preference (default 'auto'); callers resolve 'auto' against
    the flight's detected region. Used at briefing generation time to format
    region-variable units (visibility) in the LLM prompt.
    """
    from weatherbrief.units import normalize_preference

    row = db.get(UserPreferencesRow, user_id)
    if not row or not row.app_prefs_json:
        return "auto"
    try:
        return normalize_preference(json.loads(row.app_prefs_json).get("units_region"))
    except json.JSONDecodeError:
        return "auto"


def load_flight_order(db: Session, user_id: str) -> str:
    """Load the user's upcoming-flights ordering ('furthest_first' | 'soonest_first').

    Read in the *body* of ``flights.list_all_flights`` rather than injected as a
    ``Depends()`` parameter: that route is also called directly as a plain
    function by the ChatGPT connector (``api/agent.py``), where a ``Depends``
    default would silently arrive as a ``fastapi.params.Depends`` instance and
    compare unequal to every valid value. See .claude/CLAUDE.md →
    "Changing Function Signatures".

    Anything unrecognized falls back to the default via
    :func:`_normalize_flight_order` — the same coercion the GET response applies,
    so what the client is told and what the list does can't diverge.
    """
    row = db.get(UserPreferencesRow, user_id)
    if not row or not row.app_prefs_json:
        return DEFAULT_FLIGHT_ORDER
    try:
        value = json.loads(row.app_prefs_json).get("flight_order")
    except json.JSONDecodeError:
        return DEFAULT_FLIGHT_ORDER
    return _normalize_flight_order(value)


# Display-currency derivation from the existing units_region preference.
_REGION_CURRENCY = {"europe": "EUR", "us": "USD"}


def usd_fx_block() -> FxBlock:
    """The USD-canonical display block (no conversion) for anonymous viewers."""
    return FxBlock(currency="USD", rate=1.0)


def stripe_configured() -> bool:
    """Donations are live only when Stripe is configured (key present in env).

    The single on/off switch for the whole donation feature: with no
    ``STRIPE_SECRET_KEY`` the donate page and the Settings link stay hidden, so
    deploying the code before Stripe is set up surfaces nothing. Add the key
    (+ restart) to turn it on — no redeploy.
    """
    return bool(os.environ.get("STRIPE_SECRET_KEY"))


def _normalize_currency(value: str | None) -> str:
    """Normalize a display-currency preference to "auto" or an ISO 4217 code.

    Accepts "auto" (case-insensitive) or a 3-letter alphabetic code; anything
    else falls back to "auto" so a bad value never breaks display.
    """
    if not value:
        return "auto"
    v = value.strip().upper()
    if v == "AUTO":
        return "auto"
    return v if (len(v) == 3 and v.isalpha()) else "auto"


def load_display_currency(db: Session, user_id: str) -> str:
    """Resolve the viewer's display currency to a concrete ISO 4217 code.

    A stored, explicit ``display_currency`` wins. "auto"/unset derives from the
    existing ``units_region`` (europe→EUR, us→USD), defaulting to USD. The
    browser-locale fallback is a frontend concern (the API can only see prefs).
    """
    row = db.get(UserPreferencesRow, user_id)
    data: dict = {}
    if row and row.app_prefs_json:
        try:
            data = json.loads(row.app_prefs_json)
        except json.JSONDecodeError:
            data = {}
    currency = _normalize_currency(data.get("display_currency"))
    if currency != "auto":
        return currency
    # EU-first app: when nothing better is known, default to EUR. (units_region
    # "us" still maps to USD; the frontend refines "auto" via browser locale and
    # persists a concrete choice.)
    return _REGION_CURRENCY.get(data.get("units_region", "auto"), "EUR")


def fx_block_for_currency(currency: str) -> FxBlock:
    """Build a display ``fx`` block for an explicit currency code.

    Degrades gracefully to a USD (rate 1.0) block if the code is bad or the rate
    source is unreachable — never fails the surrounding response.
    """
    currency = _normalize_currency(currency)
    if currency == "auto":
        currency = "EUR"
    try:
        return FxBlock(**fx.fx_block(currency))
    except Exception:
        logger.warning("FX unavailable for %s; falling back to USD", currency)
        return FxBlock(currency="USD", rate=1.0, as_of=date.today().isoformat())


def fx_block_for_user(db: Session, user_id: str) -> FxBlock:
    """Build the display ``fx`` block for a user's resolved currency.

    API responses stay USD-canonical and carry this block so the frontend can
    render the viewer's currency.
    """
    return fx_block_for_currency(load_display_currency(db, user_id))


def load_advisory_prefs(db: Session, user_id: str) -> AdvisoryPreferences:
    """Load advisory preferences for a user.

    Returns AdvisoryPreferences with None for unset fields.
    Used by the pipeline when evaluating route advisories.
    """
    row = db.get(UserPreferencesRow, user_id)
    if not row:
        return AdvisoryPreferences()
    return _parse_advisory_prefs(row.app_prefs_json)


def load_user_defaults(db: Session, user_id: str) -> FlightDefaults:
    """Load the user's flight defaults.

    Returns a FlightDefaults with None for any unset fields.
    Used by flights.py when creating a flight.
    """
    row = db.get(UserPreferencesRow, user_id)
    if not row:
        return FlightDefaults()
    return _parse_defaults(row.app_prefs_json)


def load_service_toggles(db: Session, user_id: str) -> dict[str, Any]:
    """Load service toggle preferences for a user.

    Returns a mix of bool toggles (``gramet_enabled``, ``llm_digest_enabled``,
    ``icing_severity_enhance``) and string choices (``units_region``,
    ``display_currency``). The engine methods were retired here in #410 — they
    now live only on the flight profile.
    """
    row = db.get(UserPreferencesRow, user_id)
    if not row:
        return {"gramet_enabled": True, "llm_digest_enabled": True, "icing_severity_enhance": False, "units_region": "auto", "flight_order": DEFAULT_FLIGHT_ORDER, "display_currency": "auto", "defer_email_for_model_update": False}
    run_pending_migrations(db, row)
    return _parse_service_toggles(row.app_prefs_json)


def load_defer_email_for_model_update(db: Session, user_id: str) -> bool:
    """Return the user's "defer auto-refresh email for an imminent model run".

    Opt-in account preference (default ``False`` = current behaviour). Read by
    the auto-refresh scheduler (issue #192, Lever 1). The silent NULL-default
    snap (Lever 2) does not consult this — it applies regardless.

    Reuses :func:`load_service_toggles` so the no-row fallback and the JSON
    parsing stay in one place (the toggle is part of ``_parse_service_toggles``).
    """
    return bool(load_service_toggles(db, user_id).get("defer_email_for_model_update", False))


def load_notify_prefs(db: Session, user_id: str) -> dict:
    """Return the user's briefing-notification preferences.

    Keys: ``notify_email``, ``notify_push`` (channels), ``notify_scope``
    (auto | all | off), ``notify_change_only``. Defaults preserve today's
    behaviour (email on, scope auto, change-only on, push off) on a missing row
    or blob. Read by the refresh-finalize notification dispatch.
    """
    row = db.get(UserPreferencesRow, user_id)
    return _parse_notify_prefs(row.app_prefs_json if row else "")


def apply_last_device_decay(db: Session, user_id: str) -> bool:
    """Fail-safe when a user unregisters their **last** push device.

    A push-only user (``notify_email`` off) who loses their last device would be
    left with no effective channel — a silent dead-state. Re-enable email and
    raise a one-time ``notify_decay_notice`` so scope≠off always keeps at least
    one working channel (channel invariant, decay branch of
    ios-app-briefing-notifications.md). Returns True if it re-enabled email.

    Idempotent, and scoped to the invariant it protects: a no-op when email is
    already on, or when the user has explicitly silenced everything
    (``notify_scope == "off"``) — the invariant only promises a working channel
    *while notifications are on*, so decay must not resurrect email against an
    explicit Off.
    """
    row = db.get(UserPreferencesRow, user_id)
    if row is None:
        return False
    try:
        data = json.loads(row.app_prefs_json) if row.app_prefs_json else {}
    except json.JSONDecodeError:
        data = {}
    if data.get("notify_scope", "auto") == "off":
        return False
    if data.get("notify_email", True):
        return False
    data["notify_email"] = True
    data["notify_decay_notice"] = True
    row.app_prefs_json = json.dumps(data)
    return True


def can_view_pireps(db: Session, user_id: str) -> bool:
    """Check if user has pirep_can_view permission."""
    row = db.get(UserPreferencesRow, user_id)
    if not row or not row.app_prefs_json:
        return False
    try:
        return json.loads(row.app_prefs_json).get("pirep_can_view", False)
    except json.JSONDecodeError:
        return False


def can_publish_pireps(db: Session, user_id: str) -> bool:
    """Check if user has pirep_can_publish permission."""
    row = db.get(UserPreferencesRow, user_id)
    if not row or not row.app_prefs_json:
        return False
    try:
        return json.loads(row.app_prefs_json).get("pirep_can_publish", False)
    except json.JSONDecodeError:
        return False


@router.post("/setup-complete", status_code=204)
def mark_setup_complete(
    user_id: str = Depends(current_user_id),
    db: Session = Depends(get_db),
):
    """Mark the user's initial setup as completed."""
    row = _load_prefs(db, user_id)
    row.setup_completed = True


@router.get("/advisories/catalog")
def get_advisory_catalog():
    """Return the full advisory catalog for the settings UI (#387).

    ``advisories`` are served in registry display order (category, then
    within-category); ``categories`` is the ordered category list (keys +
    diagnostics flag, labels stay client-side i18n) the settings page renders
    in the served order rather than from a client-side ``CATEGORY_KEYS`` copy.

    ``engine_method_defaults`` (#403) carries the declared icing/cloud/convective
    method defaults so the settings page reads them from the backend instead of
    hardcoding fallbacks — the UI default and the runtime default then cannot
    drift, and the client can prune a method equal to the default on save.
    """
    from weatherbrief.analysis.advisories import get_catalog, get_category_order

    catalog = get_catalog()
    return {
        "advisories": [entry.model_dump() for entry in catalog],
        "categories": get_category_order(catalog),
        "engine_method_defaults": ENGINE_METHOD_DEFAULTS,
    }


@router.get("/advisories/interview")
def get_advisory_interview():
    """Return the declarative setup-interview structure (#387, slice 3).

    Served as a sibling of the catalog so web and iOS share one preset
    definition. The client stores chosen answers under
    ``settings_json.interview`` for idempotent re-runs.
    """
    from weatherbrief.analysis.advisories import get_interview

    return get_interview().model_dump()
