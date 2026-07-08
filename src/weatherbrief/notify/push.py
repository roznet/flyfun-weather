"""Token-based APNs sender for briefing-refresh push notifications.

APNs is just HTTP/2 + a provider token (an ES256 JWT signed with the account's
``.p8`` key, cached ~50 min). We roll it on ``httpx`` (already a dependency) +
``PyJWT`` rather than pulling in a heavier client — full control over retries
and dead-token cleanup, no bit-rot risk from an unmaintained transport (see
ios-app-briefing-notifications.md → "APNs provider library").

Two payload shapes:

- **alert** — the visible "briefing updated" banner (``send_briefing_push``).
- **background** — a silent ``content-available`` badge sync so a read on the
  web drops the app badge (``send_silent_badge_push``).

Routing is per **token**, from the ``environment`` the client reported at
register time (a TestFlight/App-Store build → ``production``; an Xcode debug
build → ``sandbox``). A single ``.p8`` token-auth key serves both hosts. On a
``BadDeviceToken`` / ``Unregistered`` response the offending row is deleted so
the table self-heals.

Everything here is best-effort: a push must NEVER break a refresh, so the
high-level senders log-and-skip on any failure and the caller wraps them too.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx
import jwt
from sqlalchemy.orm import Session

from weatherbrief.models import BriefingPackMeta, Flight
from weatherbrief.models.observations import RefreshDelta

logger = logging.getLogger(__name__)

# APNs HTTP/2 endpoints. Per-token routing decides which one to use; TestFlight
# is production (a common trap) — the client reports the environment.
_APNS_HOSTS = {
    "production": "https://api.push.apple.com",
    "sandbox": "https://api.sandbox.push.apple.com",
}

# Provider tokens are valid up to 60 min; Apple rejects tokens older than that
# and rate-limits frequent regeneration. Refresh a little early.
_TOKEN_TTL_SECONDS = 50 * 60

# APNs responses that mean "this token is dead — stop sending to it".
# 410 Gone (Unregistered) or 400 with BadDeviceToken.
_DEAD_TOKEN_REASONS = {"BadDeviceToken", "Unregistered", "DeviceTokenNotForTopic"}


@dataclass(frozen=True)
class ApnsConfig:
    """APNs token-auth settings loaded from environment variables."""

    key_p8: str          # the .p8 private key PEM contents
    key_id: str          # 10-char Key ID (JWT header `kid`)
    team_id: str         # 10-char Apple Team ID (JWT claim `iss`)
    bundle_id: str       # app bundle id -> `apns-topic`

    @classmethod
    def from_env(cls) -> "ApnsConfig":
        """Load from environment variables. Raises ValueError if not configured.

        ``APNS_KEY_P8`` carries the PEM contents directly (deployment secret,
        same handling as ``RESEND_API_KEY``); ``APNS_KEY_P8_PATH`` is a local-dev
        convenience pointing at the ``.p8`` file on disk.
        """
        key_p8 = os.environ.get("APNS_KEY_P8")
        key_path = os.environ.get("APNS_KEY_P8_PATH")
        if not key_p8 and key_path:
            try:
                key_p8 = open(key_path, encoding="utf-8").read()
            except OSError as e:
                raise ValueError(f"APNS_KEY_P8_PATH unreadable: {e}") from e

        key_id = os.environ.get("APNS_KEY_ID")
        team_id = os.environ.get("APNS_TEAM_ID")
        bundle_id = os.environ.get("APNS_BUNDLE_ID")

        if not all([key_p8, key_id, team_id, bundle_id]):
            raise ValueError(
                "APNs not fully configured. Set APNS_KEY_P8 (or "
                "APNS_KEY_P8_PATH), APNS_KEY_ID, APNS_TEAM_ID, and "
                "APNS_BUNDLE_ID."
            )
        return cls(key_p8=key_p8, key_id=key_id, team_id=team_id, bundle_id=bundle_id)


# --- Provider-token cache (ES256 JWT) ---------------------------------------

_token_lock = threading.Lock()
# (jwt, issued_at_monotonic) keyed by key_id so a key rotation invalidates cache.
_token_cache: dict[str, tuple[str, float]] = {}


def _provider_token(config: ApnsConfig) -> str:
    """Return a cached ES256 provider JWT, minting a fresh one when stale.

    Header ``{alg: ES256, kid}``; claims ``{iss: team_id, iat}``. Cached for
    ``_TOKEN_TTL_SECONDS`` — Apple rate-limits token regeneration, so we must
    NOT sign one per request.
    """
    now = time.monotonic()
    with _token_lock:
        cached = _token_cache.get(config.key_id)
        if cached is not None and (now - cached[1]) < _TOKEN_TTL_SECONDS:
            return cached[0]
        token = jwt.encode(
            {"iss": config.team_id, "iat": int(time.time())},
            config.key_p8,
            algorithm="ES256",
            headers={"kid": config.key_id, "alg": "ES256"},
        )
        _token_cache[config.key_id] = (token, now)
        return token


def _reset_token_cache() -> None:
    """Clear the provider-token cache (test hook)."""
    with _token_lock:
        _token_cache.clear()


# --- Low-level send ----------------------------------------------------------


@dataclass
class ApnsResult:
    """Outcome of one device send."""

    ok: bool
    status_code: int
    reason: str | None = None  # APNs `reason` string on failure
    dead: bool = False         # token should be deleted (Unregistered/BadDeviceToken)


def _send_one(
    client: httpx.Client,
    config: ApnsConfig,
    token: str,
    environment: str,
    payload: dict,
    *,
    push_type: str,
    priority: int,
) -> ApnsResult:
    """POST a single notification to APNs for one device token.

    ``push_type`` is ``alert`` or ``background``; ``priority`` 10 for alerts,
    5 for silent background pushes (Apple requires 5 for ``content-available``).
    """
    host = _APNS_HOSTS.get(environment, _APNS_HOSTS["production"])
    url = f"{host}/3/device/{token}"
    headers = {
        "authorization": f"bearer {_provider_token(config)}",
        "apns-topic": config.bundle_id,
        "apns-push-type": push_type,
        "apns-priority": str(priority),
    }
    resp = client.post(url, headers=headers, content=json.dumps(payload).encode())
    if resp.status_code == 200:
        return ApnsResult(ok=True, status_code=200)

    reason: str | None = None
    try:
        reason = resp.json().get("reason")
    except Exception:
        reason = resp.text or None
    dead = resp.status_code == 410 or (reason in _DEAD_TOKEN_REASONS)
    logger.warning(
        "APNs send failed (%s env): status=%d reason=%s",
        environment, resp.status_code, reason,
    )
    return ApnsResult(ok=False, status_code=resp.status_code, reason=reason, dead=dead)


def _delete_dead_tokens(db: Session, tokens: list[str]) -> None:
    """Remove device rows APNs reported as dead (Unregistered/BadDeviceToken)."""
    if not tokens:
        return
    from weatherbrief.db.models import DeviceTokenRow

    db.query(DeviceTokenRow).filter(DeviceTokenRow.token.in_(tokens)).delete(
        synchronize_session=False
    )
    logger.info("Pruned %d dead device token(s)", len(tokens))


def _dispatch(
    db: Session,
    devices: list[tuple[str, str]],
    payload: dict,
    *,
    push_type: str,
    priority: int,
    config: ApnsConfig | None = None,
) -> int:
    """Send ``payload`` to each ``(token, environment)`` device; prune dead ones.

    Returns the number of successful sends. Never raises — the whole thing is
    best-effort (a push must not break a refresh).
    """
    if not devices:
        return 0
    try:
        config = config or ApnsConfig.from_env()
    except ValueError:
        logger.debug("APNs not configured — skipping push")
        return 0

    sent = 0
    dead: list[str] = []
    try:
        with httpx.Client(http2=True, timeout=15.0) as client:
            for token, environment in devices:
                try:
                    result = _send_one(
                        client, config, token, environment, payload,
                        push_type=push_type, priority=priority,
                    )
                except Exception:
                    logger.warning("APNs send raised for a device", exc_info=True)
                    continue
                if result.ok:
                    sent += 1
                elif result.dead:
                    dead.append(token)
    except Exception:
        logger.warning("APNs dispatch failed", exc_info=True)

    if dead:
        try:
            _delete_dead_tokens(db, dead)
        except Exception:
            logger.warning("Failed to prune dead device tokens", exc_info=True)
    return sent


def _load_devices(db: Session, user_id: str) -> list[tuple[str, str]]:
    """Return ``(token, environment)`` for all of a user's registered devices."""
    from weatherbrief.db.models import DeviceTokenRow

    rows = db.query(DeviceTokenRow).filter(DeviceTokenRow.user_id == user_id).all()
    return [(r.token, r.environment) for r in rows]


# --- High-level payload builders --------------------------------------------


def _route_title(flight: Flight) -> str:
    """Compact route line for the notification title (e.g. "EGTF → LFAT")."""
    if flight.waypoints:
        return " → ".join(flight.waypoints)
    return flight.route_name or "FlyFun briefing"


def build_alert_body(pack: BriefingPackMeta, delta: RefreshDelta | None) -> str:
    """Human body: the new assessment plus the worst worsening detail, if any.

    Language-neutral aviation shorthand from ``compute_refresh_delta`` needs no
    per-locale translation; we surface at most the first message to keep the
    banner short.
    """
    if pack.assessment:
        head = f"Now {pack.assessment}"
    elif pack.outlook:
        head = "Early outlook updated"
    else:
        head = "Briefing updated"
    if delta and delta.worsened and delta.messages:
        return f"{head} — {delta.messages[0]}"
    if pack.assessment_reason:
        return f"{head} — {pack.assessment_reason}"
    return head


def _briefing_payload(
    flight: Flight,
    pack: BriefingPackMeta,
    delta: RefreshDelta | None,
    badge: int | None,
) -> dict:
    """APNs alert payload for a completed briefing refresh."""
    aps: dict = {
        "alert": {
            "title": _route_title(flight),
            "body": build_alert_body(pack, delta),
        },
        "sound": "default",
    }
    if badge is not None:
        aps["badge"] = badge
    return {
        "aps": aps,
        # Custom data the app reads on tap to deep-link to the updated briefing.
        "flight_id": flight.id,
        "timestamp": pack.fetch_timestamp.isoformat(),
    }


def send_briefing_push(
    db: Session,
    user_id: str,
    flight: Flight,
    pack: BriefingPackMeta,
    *,
    delta: RefreshDelta | None = None,
    badge: int | None = None,
) -> int:
    """Send the visible "briefing updated" alert to all the user's iOS devices.

    Mirrors ``send_briefing_email``. Returns the number of devices reached
    (0 when APNs is not configured or the user has no registered devices).
    Best-effort: logs and returns 0 on any failure.
    """
    devices = _load_devices(db, user_id)
    if not devices:
        return 0
    payload = _briefing_payload(flight, pack, delta, badge)
    return _dispatch(db, devices, payload, push_type="alert", priority=10)


def send_silent_badge_push(db: Session, user_id: str, badge: int) -> int:
    """Send a silent ``content-available`` push carrying the current badge count.

    Used when the unseen count changes for a reason OTHER than a new alert —
    e.g. the user read a flight on the web, or on another device — so the app
    badge drops to match without a visible banner. Best-effort (Apple coalesces
    silent pushes); the foreground reconcile endpoint is the correctness
    backstop.
    """
    devices = _load_devices(db, user_id)
    if not devices:
        return 0
    payload = {"aps": {"content-available": 1, "badge": badge}}
    return _dispatch(db, devices, payload, push_type="background", priority=5)
