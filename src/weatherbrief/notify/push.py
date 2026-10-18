"""Token-based APNs (iOS) and FCM HTTP v1 (Android) sender for push notifications.

APNs is HTTP/2 + a provider token (an ES256 JWT signed with the account's
``.p8`` key, cached ~50 min). FCM HTTP v1 is HTTPS + a short-lived Google OAuth2
access token exchanged from an RS256 service-account JWT assertion (also cached
~50 min). We roll both on ``httpx`` (already a dependency) + ``PyJWT`` rather
than pulling in heavier SDKs — full control over retries and dead-token cleanup,
no bit-rot risk from unmaintained transports (see
ios-app-briefing-notifications.md → "APNs provider library" & "Android FCM").

Two payload shapes:

- **alert** — the visible "briefing updated" banner (``send_briefing_push``).
- **background** — a silent ``content-available`` / data-only badge sync so a
  read on the web drops the app badge (``send_silent_badge_push``).

Live alerts on flight day (#754, ``send_live_alert_push``) are an alert push
of their own: no badge, grouped per flight, with an expiry (``apns-expiration``
on iOS, ``android.ttl`` on Android).

Routing is per **device** row (`PushDevice(token, environment, platform)`):
iOS rows route to APNs (``production`` vs ``sandbox`` host), Android rows route
to FCM HTTP v1. On a dead-token response (APNs ``BadDeviceToken`` /
``Unregistered`` / ``DeviceTokenNotForTopic``, or FCM ``UNREGISTERED`` /
``SENDER_ID_MISMATCH``) the offending row is deleted so the table self-heals.

Everything here is best-effort: a push must NEVER break a refresh, so the
high-level senders log-and-skip on any failure and the caller wraps them too.
"""

from __future__ import annotations

import base64
from collections.abc import Sequence
import json
import logging
import os
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import NamedTuple

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


def _normalize_key_pem(raw: str) -> str:
    """Coerce an ``APNS_KEY_P8`` env value into real PEM text.

    Accepts three shapes so the same secret works whether it arrives via
    docker-compose ``env_file`` (no ``\\n`` expansion), a dotenv parser, or a
    file read:

    - **base64** (recommended for prod, e.g. ``base64 -i AuthKey.p8``) — anything
      without a ``-----BEGIN`` marker is decoded; single-line, parser-proof.
    - **``\\n``-escaped** PEM — literal backslash-n restored to real newlines.
    - **raw PEM** — already has newlines; passed through untouched.
    """
    raw = raw.strip()
    if "-----BEGIN" not in raw:
        try:
            return base64.b64decode(raw).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as e:
            raise ValueError(
                "APNS_KEY_P8 is neither PEM nor valid base64 — expected the "
                "raw .p8 contents or `base64 -i AuthKey.p8` output."
            ) from e
    if "\\n" in raw:
        return raw.replace("\\n", "\n")
    return raw


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

        ``APNS_KEY_P8`` carries the key directly (deployment secret, same handling
        as ``RESEND_API_KEY``); ``APNS_KEY_P8_PATH`` is a local-dev convenience
        pointing at the ``.p8`` file on disk.

        The ``APNS_KEY_P8`` value may be **base64** (recommended for prod) or the
        raw PEM. Prod injects env via docker-compose ``env_file``, which does NOT
        expand ``\\n`` escapes into real newlines — a raw multi-line PEM can't be
        represented on one env line, and a ``\\n``-escaped one would reach
        ``cryptography`` with literal backslash-n and fail to parse (silently, as
        push is best-effort). base64 is single-line and survives every env parser
        intact. ``_normalize_key_pem`` handles base64, ``\\n``-escaped, and raw PEM.
        """
        key_p8 = os.environ.get("APNS_KEY_P8")
        key_path = os.environ.get("APNS_KEY_P8_PATH")
        if not key_p8 and key_path:
            try:
                key_p8 = open(key_path, encoding="utf-8").read()
            except OSError as e:
                raise ValueError(f"APNS_KEY_P8_PATH unreadable: {e}") from e
        if key_p8:
            key_p8 = _normalize_key_pem(key_p8)

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
    """Clear the provider-token caches (test hook)."""
    with _token_lock:
        _token_cache.clear()
        _fcm_token_cache.clear()


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
    extra_headers: dict[str, str] | None = None,
) -> ApnsResult:
    """POST a single notification to APNs for one device token.

    ``push_type`` is ``alert`` or ``background``; ``priority`` 10 for alerts,
    5 for silent background pushes (Apple requires 5 for ``content-available``).
    ``extra_headers`` adds optional APNs headers (e.g. ``apns-expiration``);
    it cannot override the four above.
    """
    host = _APNS_HOSTS.get(environment, _APNS_HOSTS["production"])
    url = f"{host}/3/device/{token}"
    headers = {
        **(extra_headers or {}),
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


@dataclass(frozen=True)
class FcmConfig:
    """Firebase Cloud Messaging (HTTP v1) service-account settings."""

    project_id: str
    client_email: str
    private_key: str
    token_uri: str = "https://oauth2.googleapis.com/token"

    @classmethod
    def from_env(cls) -> "FcmConfig":
        """Load from ``FCM_SERVICE_ACCOUNT_JSON`` (raw JSON or base64),
        ``FCM_SERVICE_ACCOUNT_PATH``, or ``FCM_PROJECT_ID`` +
        ``FCM_CLIENT_EMAIL`` + ``FCM_PRIVATE_KEY``."""
        raw_json = os.environ.get("FCM_SERVICE_ACCOUNT_JSON")
        json_path = os.environ.get("FCM_SERVICE_ACCOUNT_PATH")
        if not raw_json and json_path:
            try:
                raw_json = open(json_path, encoding="utf-8").read()
            except OSError as e:
                raise ValueError(f"FCM_SERVICE_ACCOUNT_PATH unreadable: {e}") from e
        if raw_json:
            raw_json = raw_json.strip()
            if not raw_json.startswith("{"):
                try:
                    raw_json = base64.b64decode(raw_json).decode("utf-8")
                except (ValueError, UnicodeDecodeError) as e:
                    raise ValueError("FCM_SERVICE_ACCOUNT_JSON is not valid JSON or base64") from e
            try:
                data = json.loads(raw_json)
            except ValueError as e:
                raise ValueError("FCM_SERVICE_ACCOUNT_JSON is not valid JSON") from e
            project_id = data.get("project_id")
            client_email = data.get("client_email")
            private_key = data.get("private_key")
            token_uri = data.get("token_uri") or "https://oauth2.googleapis.com/token"
        else:
            project_id = os.environ.get("FCM_PROJECT_ID")
            client_email = os.environ.get("FCM_CLIENT_EMAIL")
            private_key = os.environ.get("FCM_PRIVATE_KEY")
            token_uri = os.environ.get("FCM_TOKEN_URI", "https://oauth2.googleapis.com/token")

        if private_key:
            private_key = _normalize_key_pem(private_key)
        if not all([project_id, client_email, private_key]):
            raise ValueError(
                "FCM not configured. Set FCM_SERVICE_ACCOUNT_JSON (or "
                "FCM_SERVICE_ACCOUNT_PATH) or FCM_PROJECT_ID, FCM_CLIENT_EMAIL, "
                "and FCM_PRIVATE_KEY."
            )
        return cls(
            project_id=project_id,
            client_email=client_email,
            private_key=private_key,
            token_uri=token_uri,
        )


class PushDevice(NamedTuple):
    """A registered push target from ``device_tokens``."""

    token: str
    environment: str
    platform: str = "ios"


_fcm_token_cache: dict[str, tuple[str, float]] = {}

# Android notification channels (created by PushNotificationManager.kt on startup).
_FCM_CHANNEL_BRIEFING = "flyfun_briefing_updates"
_FCM_CHANNEL_FLIGHT_DAY = "flyfun_flight_day"
_FCM_CHANNEL_LIVE_ALERTS = "flyfun_live_alerts"
_FCM_CHANNEL_LIVE_CLEAR = "flyfun_live_clear"

# FCM errorCode values that mean this registration token will never succeed.
_FCM_DEAD_ERROR_CODES = {"UNREGISTERED", "SENDER_ID_MISMATCH"}


def _fcm_access_token(client: httpx.Client, config: FcmConfig) -> str:
    """Return a cached OAuth2 access token for FCM HTTP v1, minting via RS256 JWT assertion."""
    now = time.monotonic()
    with _token_lock:
        cached = _fcm_token_cache.get(config.client_email)
        if cached is not None and (now - cached[1]) < _TOKEN_TTL_SECONDS:
            return cached[0]

    iat = int(time.time())
    assertion = jwt.encode(
        {
            "iss": config.client_email,
            "scope": "https://www.googleapis.com/auth/firebase.messaging",
            "aud": config.token_uri,
            "iat": iat,
            "exp": iat + 3600,
        },
        config.private_key,
        algorithm="RS256",
    )
    resp = client.post(
        config.token_uri,
        data={
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": assertion,
        },
    )
    resp.raise_for_status()
    access_token = resp.json()["access_token"]
    with _token_lock:
        _fcm_token_cache[config.client_email] = (access_token, now)
    return access_token


def _evict_fcm_token(config: FcmConfig) -> None:
    """Evict a cached FCM OAuth token after a 401 rejection (e.g. key rotation)."""
    with _token_lock:
        _fcm_token_cache.pop(config.client_email, None)


def _fcm_channel_id_for_payload(payload: dict) -> str:
    """Pick the Android notification channel for an alert payload so pilots can
    mute briefing updates without muting flight-day live alerts, and so active
    clears don't use the high-urgency time-sensitive live-alert channel."""
    msg_type = payload.get("type")
    aps = payload.get("aps") or {}
    interruption = aps.get("interruption-level")
    if msg_type == "live_clear" or (msg_type == "live_alert" and interruption == "active"):
        return _FCM_CHANNEL_LIVE_CLEAR
    if msg_type == "live_alert" or interruption == "time-sensitive":
        return _FCM_CHANNEL_LIVE_ALERTS
    if msg_type == "flight_day":
        return _FCM_CHANNEL_FLIGHT_DAY
    return _FCM_CHANNEL_BRIEFING


def _apns_payload_to_fcm_message(
    token: str,
    payload: dict,
    *,
    push_type: str,
    expires_at: datetime | None = None,
    now: datetime | None = None,
) -> dict:
    """Convert our canonical ``aps`` + custom keys dict into an FCM v1 ``message``."""
    aps = payload.get("aps") or {}
    alert = aps.get("alert") if isinstance(aps.get("alert"), dict) else {}
    interruption = aps.get("interruption-level")
    data: dict[str, str] = {}
    for k, v in payload.items():
        if k == "aps" or v is None:
            continue
        data[str(k)] = v if isinstance(v, str) else json.dumps(v)
    if "badge" in aps and aps["badge"] is not None:
        data["badge"] = str(aps["badge"])
    if interruption:
        data["interruption_level"] = str(interruption)

    is_high = push_type == "alert" and interruption != "active"
    android_block: dict = {
        "priority": "HIGH" if is_high else "NORMAL",
    }
    if expires_at is not None:
        ref_now = now or datetime.now(timezone.utc)
        ttl_s = max(0, int((expires_at - ref_now).total_seconds()))
        android_block["ttl"] = f"{ttl_s}s"

    msg: dict = {
        "token": token,
        "data": data,
        "android": android_block,
    }
    if push_type == "alert" and (alert.get("title") or alert.get("body")):
        msg["notification"] = {
            "title": alert.get("title") or "",
            "body": alert.get("body") or "",
        }
        android_block["notification"] = {
            "channel_id": _fcm_channel_id_for_payload(payload),
        }
    return {"message": msg}


def _send_fcm_one(
    client: httpx.Client,
    config: FcmConfig,
    token: str,
    payload: dict,
    *,
    push_type: str,
    access_token: str | None = None,
    expires_at: datetime | None = None,
) -> ApnsResult:
    """POST one notification to FCM HTTP v1 for an Android device token."""
    bearer = access_token if access_token is not None else _fcm_access_token(client, config)
    url = f"https://fcm.googleapis.com/v1/projects/{config.project_id}/messages:send"
    headers = {
        "authorization": f"Bearer {bearer}",
        "content-type": "application/json",
    }
    body = _apns_payload_to_fcm_message(
        token, payload, push_type=push_type, expires_at=expires_at,
    )
    resp = client.post(url, headers=headers, content=json.dumps(body).encode())
    if resp.status_code == 200:
        return ApnsResult(ok=True, status_code=200)

    if resp.status_code == 401:
        _evict_fcm_token(config)

    reason: str | None = None
    dead = resp.status_code in {404, 410}
    try:
        err = resp.json().get("error") or {}
        reason = err.get("status") or err.get("message")
        for detail in err.get("details") or []:
            code = detail.get("errorCode")
            if code in _FCM_DEAD_ERROR_CODES or code == "INVALID_ARGUMENT":
                if code in _FCM_DEAD_ERROR_CODES:
                    dead = True
                reason = code
    except Exception:
        reason = resp.text or None
    if reason in _FCM_DEAD_ERROR_CODES:
        dead = True
    if reason == "SENDER_ID_MISMATCH":
        logger.warning(
            "FCM token belongs to a different Firebase project (SENDER_ID_MISMATCH) — pruning"
        )
    else:
        logger.warning("FCM send failed: status=%d reason=%s", resp.status_code, reason)
    return ApnsResult(ok=False, status_code=resp.status_code, reason=reason, dead=dead)


def _delete_dead_tokens(db: Session, tokens: list[str]) -> None:
    """Remove device rows APNs/FCM reported as dead (Unregistered/BadDeviceToken)."""
    if not tokens:
        return
    from weatherbrief.db.models import DeviceTokenRow

    db.query(DeviceTokenRow).filter(DeviceTokenRow.token.in_(tokens)).delete(
        synchronize_session=False
    )
    logger.info("Pruned %d dead device token(s)", len(tokens))


def _dispatch(
    db: Session,
    devices: Sequence[PushDevice],
    payload: dict,
    *,
    push_type: str,
    priority: int,
    config: ApnsConfig | None = None,
    fcm_config: FcmConfig | None = None,
    user_id: str | None = None,
    expires_at: datetime | None = None,
    extra_headers: dict[str, str] | None = None,
) -> int:
    """Send ``payload`` to each device (APNs for iOS, FCM for Android); prune dead ones.

    Returns the number of successful sends. Never raises — the whole thing is
    best-effort (a push must not break a refresh).

    ``expires_at`` (when given) becomes ``apns-expiration`` on iOS and
    ``android.ttl`` on Android so stale flight-day live alerts are dropped if a
    phone was offline for the whole leg.

    ``user_id`` (when known) lets the channel-invariant decay fail-safe fire if
    pruning a dead token removes the user's LAST device — the far more common way
    a device disappears (app deleted without signing out) than an explicit
    unregister. Mirrors ``api/devices.py::unregister_device`` so the invariant
    holds on both device-loss paths.
    """
    if not devices:
        return 0

    ios_devices: list[tuple[str, str]] = []
    android_tokens: list[str] = []
    for dev in devices:
        if dev.platform == "android":
            android_tokens.append(dev.token)
        else:
            ios_devices.append((dev.token, dev.environment))

    sent = 0
    dead: list[str] = []

    if ios_devices:
        apns_cfg = config
        if apns_cfg is None:
            try:
                apns_cfg = ApnsConfig.from_env()
            except ValueError:
                logger.debug("APNs not configured — skipping iOS push")
        if apns_cfg is not None:
            apns_headers = dict(extra_headers) if extra_headers else {}
            if expires_at is not None:
                apns_headers["apns-expiration"] = str(int(expires_at.timestamp()))
            try:
                with httpx.Client(http2=True, timeout=15.0) as client:
                    for token, environment in ios_devices:
                        try:
                            result = _send_one(
                                client, apns_cfg, token, environment, payload,
                                push_type=push_type, priority=priority,
                                extra_headers=apns_headers or None,
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

    if android_tokens:
        fcm_cfg = fcm_config
        if fcm_cfg is None:
            try:
                fcm_cfg = FcmConfig.from_env()
            except ValueError:
                logger.debug("FCM not configured — skipping Android push")
        if fcm_cfg is not None:
            try:
                with httpx.Client(timeout=15.0) as client:
                    try:
                        access_token = _fcm_access_token(client, fcm_cfg)
                    except Exception:
                        logger.warning(
                            "FCM OAuth token mint failed — skipping Android push batch",
                            exc_info=True,
                        )
                        access_token = None
                    if access_token is not None:
                        for token in android_tokens:
                            try:
                                result = _send_fcm_one(
                                    client, fcm_cfg, token, payload,
                                    push_type=push_type,
                                    access_token=access_token,
                                    expires_at=expires_at,
                                )
                            except Exception:
                                logger.warning("FCM send raised for a device", exc_info=True)
                                continue
                            if result.ok:
                                sent += 1
                            elif result.dead:
                                dead.append(token)
            except Exception:
                logger.warning("FCM dispatch failed", exc_info=True)

    if dead:
        try:
            _delete_dead_tokens(db, dead)
            # If pruning dropped the user's last device, apply the same decay
            # fail-safe as an explicit unregister — otherwise a push-only user
            # who deleted the app (email off) is silently stranded.
            if user_id is not None and count_user_devices(db, user_id) == 0:
                from weatherbrief.api.preferences import apply_last_device_decay

                if apply_last_device_decay(db, user_id):
                    logger.info(
                        "Re-enabled briefing email for %s after pruning last device",
                        user_id,
                    )
        except Exception:
            logger.warning("Failed to prune dead device tokens", exc_info=True)
    return sent


def _load_devices(db: Session, user_id: str) -> list[PushDevice]:
    """Return ``PushDevice(token, environment, platform)`` for all of a user's
    registered devices."""
    from weatherbrief.db.models import DeviceTokenRow

    rows = db.query(DeviceTokenRow).filter(DeviceTokenRow.user_id == user_id).all()
    return [PushDevice(r.token, r.environment, r.platform) for r in rows]


def count_user_devices(db: Session, user_id: str) -> int:
    """Count a user's registered push device tokens.

    Single source of truth for the "how many devices?" query used by the
    conditional-push UI, the sign-out decay path, and the dead-token-prune decay
    path (so the three don't drift).
    """
    from weatherbrief.db.models import DeviceTokenRow

    return (
        db.query(DeviceTokenRow)
        .filter(DeviceTokenRow.user_id == user_id)
        .count()
    )


# --- High-level payload builders --------------------------------------------


def _route_title(flight: Flight) -> str:
    """Compact route line for the notification title (e.g. "EGTF → LFAT")."""
    if flight.waypoints:
        return " → ".join(flight.waypoints)
    return flight.route_name or "FlyFun briefing"


def build_alert_body(pack: BriefingPackMeta, delta: RefreshDelta | None) -> str:
    """Human body: the new assessment plus the worst worsening detail, if any.

    Language-neutral aviation shorthand from the live layer's classifier needs no
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
    """Send the visible "briefing updated" alert to all the user's registered devices.

    Mirrors ``send_briefing_email``. Returns the number of devices reached
    (0 when APNs/FCM is not configured or the user has no registered devices).
    Best-effort: logs and returns 0 on any failure.
    """
    devices = _load_devices(db, user_id)
    if not devices:
        return 0
    payload = _briefing_payload(flight, pack, delta, badge)
    return _dispatch(db, devices, payload, push_type="alert", priority=10, user_id=user_id)


def send_trip_push(
    db: Session,
    user_id: str,
    *,
    trip_id: str,
    trip_name: str,
    title: str,
    body: str,
    badge: int | None = None,
) -> int:
    """One coalesced alert for a completed **trip** refresh.

    A 3-leg trip refresh must not fire 3 pushes, so the driver collects the
    per-leg outcomes and sends this once when the last leg lands. The custom
    payload carries ``trip_id`` (not ``flight_id``) so a tap deep-links to the
    trip page — the whole point being that the chain, not one leg, is the unit
    of attention.
    """
    devices = _load_devices(db, user_id)
    if not devices:
        return 0
    aps: dict = {"alert": {"title": title, "body": body}, "sound": "default"}
    if badge is not None:
        aps["badge"] = badge
    payload = {
        "aps": aps,
        "trip_id": trip_id,
        "trip_name": trip_name,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    return _dispatch(db, devices, payload, push_type="alert", priority=10, user_id=user_id)


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
    return _dispatch(db, devices, payload, push_type="background", priority=5, user_id=user_id)


def send_live_alert_push(
    db: Session,
    user_id: str,
    payload: dict,
    *,
    expires_at: datetime,
    devices: Sequence[PushDevice] | None = None,
) -> int:
    """Send one flight-day live-alert push (#754) to all the user's devices.

    The payload is built by ``notify/live_alerts.py`` (one push per flight per
    tick). ``expires_at`` becomes ``apns-expiration`` on iOS and ``android.ttl``
    on Android: both transports drop a pending push after this timestamp, so a
    phone offline for a whole leg lands to nothing stale. No ``apns-collapse-id``:
    each push stays its own entry in Notification Center. Best-effort, like
    every sender here; returns the number of devices reached.
    """
    devices = devices if devices is not None else _load_devices(db, user_id)
    if not devices:
        return 0
    return _dispatch(
        db, devices, payload, push_type="alert", priority=10,
        user_id=user_id, expires_at=expires_at,
    )


def send_flight_day_push(
    db: Session,
    user_id: str,
    flight: Flight,
    pack: BriefingPackMeta,
    *,
    headline: str | None,
    grade: str | None,
    badge: int | None = None,
) -> int:
    """The flight-day brief's alert (#753): "Flight day · 09:00Z EGTK → LFAT" /
    "AMBER · LFAT METAR: VFR → IFR".

    ``type: "flight_day"`` is the payload contract shared with the live-alert
    pushes (#754): the app opens the flight's Observed tab on a tap. Sent
    through the unchanged ``_dispatch``; no extra APNs headers.
    """
    devices = _load_devices(db, user_id)
    if not devices:
        return 0
    dep = flight.departure_time.astimezone(timezone.utc).strftime("%H:%MZ")
    body = " · ".join(p for p in (grade, headline) if p) or "Observed conditions and forecast"
    aps: dict = {
        "alert": {"title": f"Flight day · {dep} {_route_title(flight)}", "body": body},
        "sound": "default",
    }
    if badge is not None:
        aps["badge"] = badge
    payload = {
        "aps": aps,
        "flight_id": flight.id,
        "type": "flight_day",
        "timestamp": pack.fetch_timestamp.isoformat(),
    }
    return _dispatch(db, devices, payload, push_type="alert", priority=10, user_id=user_id)
