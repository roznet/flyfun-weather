"""Tests for briefing-refresh notifications: gate, badge, APNs, endpoints, prefs.

Covers the server half of ios-app-briefing-notifications.md (issue #366).
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta, timezone

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from flyfun_common.db import DEV_USER_ID, current_user_id, get_db
from flyfun_common.db.models import UserPreferencesRow, UserRow

from weatherbrief.api.app import create_app
from weatherbrief.db.models import (
    BriefingPackRow,
    DeviceTokenRow,
    FlightBriefingSeenRow,
    FlightRow,
)
from weatherbrief.models import BriefingPackMeta, Flight
from weatherbrief.notify import badge as badge_mod
from weatherbrief.notify import dispatch as dispatch_mod
from weatherbrief.notify import push as push_mod


# ---------------------------------------------------------------------------
# Pure gate: notify_qualifies
# ---------------------------------------------------------------------------

Q = dispatch_mod.notify_qualifies


def test_gate_mute_always_stops():
    assert not Q(notify_override="mute", scope="all", change_only=False,
                changed=True)


def test_gate_notify_override_sends_on_any_completion():
    # even when global scope is off
    assert Q(notify_override="notify", scope="off", change_only=False,
             changed=True)


def test_gate_scope_auto_treated_as_on():
    # Legacy "auto" scope now means "notifications on" — the shared gate no
    # longer draws the manual-vs-scheduler line (the per-channel email rule
    # does). Both scheduler and user completions qualify.
    assert Q(notify_override="default", scope="auto", change_only=False,
             changed=True)


def test_gate_scope_all_qualifies():
    assert Q(notify_override="default", scope="all", change_only=False,
             changed=True)


def test_gate_scope_off_stops():
    assert not Q(notify_override="default", scope="off", change_only=False,
                 changed=True)


def test_gate_change_only_suppresses_unchanged():
    assert not Q(notify_override="default", scope="all", change_only=True,
                 changed=False)
    assert Q(notify_override="default", scope="all", change_only=True,
             changed=True)


def test_gate_change_only_applies_even_under_notify_override():
    # The content filter is applied after the scope/override decision, so it
    # gates the "notify" override too (design pseudocode order).
    assert not Q(notify_override="notify", scope="off", change_only=True,
                 changed=False)
    assert Q(notify_override="notify", scope="off", change_only=True,
             changed=True)


def test_email_should_send_skips_only_user_present():
    # The user's own in-app manual refresh self-suppresses email; every other
    # trigger (scheduler / Siri / MCP / background) is non-user-present.
    assert not dispatch_mod.email_should_send("user")
    assert dispatch_mod.email_should_send("scheduler")
    assert dispatch_mod.email_should_send("siri")
    assert dispatch_mod.email_should_send("mcp")
    assert dispatch_mod.email_should_send("background")


# ---------------------------------------------------------------------------
# Change detection
# ---------------------------------------------------------------------------

def _add_flight(db, flight_id="f1", user_id=DEV_USER_ID):
    db.add(FlightRow(
        id=flight_id, user_id=user_id, route_name="EGTF-LFAT",
        waypoints_json='["EGTF", "LFAT"]',
        departure_time=datetime(2026, 7, 10, 12, 0, tzinfo=timezone.utc),
    ))
    db.flush()


def _add_pack(db, flight_id, ts, assessment=None, outlook=None):
    row = BriefingPackRow(
        flight_id=flight_id, fetch_timestamp=ts, days_out=2,
        assessment=assessment, outlook=outlook,
    )
    db.add(row)
    db.flush()
    return row


def test_detect_change_first_pack_is_change(db_session, dev_user):
    _add_flight(db_session)
    ts = datetime(2026, 7, 8, 10, 0, tzinfo=timezone.utc)
    _add_pack(db_session, "f1", ts, assessment="GREEN")
    meta = BriefingPackMeta(flight_id="f1", fetch_timestamp=ts, days_out=2, assessment="GREEN")
    changed, delta = dispatch_mod.detect_change(db_session, "f1", meta)
    assert changed is True
    assert delta is None  # first briefing has no "worsened" delta


def test_detect_change_same_assessment_no_change(db_session, dev_user):
    _add_flight(db_session)
    t0 = datetime(2026, 7, 8, 8, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 7, 8, 10, 0, tzinfo=timezone.utc)
    _add_pack(db_session, "f1", t0, assessment="GREEN")
    _add_pack(db_session, "f1", t1, assessment="GREEN")
    meta = BriefingPackMeta(flight_id="f1", fetch_timestamp=t1, days_out=2, assessment="GREEN")
    changed, delta = dispatch_mod.detect_change(db_session, "f1", meta)
    assert changed is False
    assert delta is None


def test_detect_change_worsened_carries_delta(db_session, dev_user):
    _add_flight(db_session)
    t0 = datetime(2026, 7, 8, 8, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 7, 8, 10, 0, tzinfo=timezone.utc)
    _add_pack(db_session, "f1", t0, assessment="GREEN")
    _add_pack(db_session, "f1", t1, assessment="AMBER")
    meta = BriefingPackMeta(flight_id="f1", fetch_timestamp=t1, days_out=2, assessment="AMBER")
    changed, delta = dispatch_mod.detect_change(db_session, "f1", meta)
    assert changed is True
    assert delta is not None and delta.worsened
    assert "GREEN" in delta.messages[0]


def test_detect_change_improved_is_change_without_delta(db_session, dev_user):
    _add_flight(db_session)
    t0 = datetime(2026, 7, 8, 8, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 7, 8, 10, 0, tzinfo=timezone.utc)
    _add_pack(db_session, "f1", t0, assessment="RED")
    _add_pack(db_session, "f1", t1, assessment="GREEN")
    meta = BriefingPackMeta(flight_id="f1", fetch_timestamp=t1, days_out=2, assessment="GREEN")
    changed, delta = dispatch_mod.detect_change(db_session, "f1", meta)
    assert changed is True          # a change, worth notifying
    assert delta is None            # ...but not "worsened"


# ---------------------------------------------------------------------------
# Badge semantics
# ---------------------------------------------------------------------------

def test_badge_counts_once_per_flight(db_session, dev_user):
    _add_flight(db_session, "f1")
    t0 = datetime(2026, 7, 8, 8, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 7, 8, 10, 0, tzinfo=timezone.utc)
    _add_pack(db_session, "f1", t0)
    _add_pack(db_session, "f1", t1)
    # Two notify-qualifying refreshes on one flight...
    badge_mod.record_notify_qualifying(db_session, DEV_USER_ID, "f1", t0)
    badge_mod.record_notify_qualifying(db_session, DEV_USER_ID, "f1", t1)
    db_session.flush()
    # ...still count once.
    assert badge_mod.compute_badge_count(db_session, DEV_USER_ID) == 1


def test_badge_cleared_by_open(db_session, dev_user):
    _add_flight(db_session, "f1")
    t0 = datetime(2026, 7, 8, 8, 0, tzinfo=timezone.utc)
    _add_pack(db_session, "f1", t0)
    badge_mod.record_notify_qualifying(db_session, DEV_USER_ID, "f1", t0)
    db_session.flush()
    assert badge_mod.compute_badge_count(db_session, DEV_USER_ID) == 1

    changed = badge_mod.mark_flight_seen(db_session, DEV_USER_ID, "f1")
    db_session.flush()
    assert changed is True  # transitioned unseen -> seen
    assert badge_mod.compute_badge_count(db_session, DEV_USER_ID) == 0


def test_badge_open_clears_even_after_multiple_packs(db_session, dev_user):
    _add_flight(db_session, "f1")
    t0 = datetime(2026, 7, 8, 8, 0, tzinfo=timezone.utc)
    t1 = datetime(2026, 7, 8, 10, 0, tzinfo=timezone.utc)
    _add_pack(db_session, "f1", t0)
    _add_pack(db_session, "f1", t1)
    badge_mod.record_notify_qualifying(db_session, DEV_USER_ID, "f1", t1)
    db_session.flush()
    # Opening marks the *latest* pack seen, clearing the flight.
    badge_mod.mark_flight_seen(db_session, DEV_USER_ID, "f1")
    db_session.flush()
    assert badge_mod.compute_badge_count(db_session, DEV_USER_ID) == 0


def test_badge_unchanged_later_refresh_does_not_relight(db_session, dev_user):
    _add_flight(db_session, "f1")
    t0 = datetime(2026, 7, 8, 8, 0, tzinfo=timezone.utc)
    _add_pack(db_session, "f1", t0)
    badge_mod.record_notify_qualifying(db_session, DEV_USER_ID, "f1", t0)
    badge_mod.mark_flight_seen(db_session, DEV_USER_ID, "f1")
    db_session.flush()
    assert badge_mod.compute_badge_count(db_session, DEV_USER_ID) == 0
    # A later non-qualifying refresh never calls record_notify_qualifying, so
    # the flight stays cleared.
    assert badge_mod.compute_badge_count(db_session, DEV_USER_ID) == 0


def test_badge_multiple_flights(db_session, dev_user):
    _add_flight(db_session, "f1")
    _add_flight(db_session, "f2")
    t0 = datetime(2026, 7, 8, 8, 0, tzinfo=timezone.utc)
    _add_pack(db_session, "f1", t0)
    _add_pack(db_session, "f2", t0)
    badge_mod.record_notify_qualifying(db_session, DEV_USER_ID, "f1", t0)
    badge_mod.record_notify_qualifying(db_session, DEV_USER_ID, "f2", t0)
    db_session.flush()
    assert badge_mod.compute_badge_count(db_session, DEV_USER_ID) == 2
    badge_mod.mark_flight_seen(db_session, DEV_USER_ID, "f1")
    db_session.flush()
    assert badge_mod.compute_badge_count(db_session, DEV_USER_ID) == 1


# ---------------------------------------------------------------------------
# APNs sender: config, provider token, payloads
# ---------------------------------------------------------------------------

@pytest.fixture
def apns_env(monkeypatch):
    """Configure APNs with a throwaway EC P-256 key."""
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    monkeypatch.setenv("APNS_KEY_P8", pem)
    monkeypatch.setenv("APNS_KEY_ID", "ABC1234567")
    monkeypatch.setenv("APNS_TEAM_ID", "TEAM123456")
    monkeypatch.setenv("APNS_BUNDLE_ID", "aero.flyfun.weather")
    push_mod._reset_token_cache()
    yield key
    push_mod._reset_token_cache()


def test_apns_config_missing_raises(monkeypatch):
    for k in ("APNS_KEY_P8", "APNS_KEY_P8_PATH", "APNS_KEY_ID", "APNS_TEAM_ID", "APNS_BUNDLE_ID"):
        monkeypatch.delenv(k, raising=False)
    with pytest.raises(ValueError):
        push_mod.ApnsConfig.from_env()


def _throwaway_pem() -> str:
    return (
        ec.generate_private_key(ec.SECP256R1())
        .private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        .decode()
    )


def test_normalize_key_pem_passthrough_raw():
    pem = _throwaway_pem()
    assert push_mod._normalize_key_pem(pem) == pem.strip()


def test_normalize_key_pem_restores_escaped_newlines():
    pem = _throwaway_pem()
    escaped = pem.replace("\n", "\\n")  # the \n-literal form a naive .env paste yields
    assert "\\n" in escaped and "\n" not in escaped.replace("\\n", "")
    assert push_mod._normalize_key_pem(escaped).strip() == pem.strip()


def test_normalize_key_pem_decodes_base64():
    pem = _throwaway_pem()
    b64 = base64.b64encode(pem.encode()).decode()  # `base64 -i AuthKey.p8` output
    assert "-----BEGIN" not in b64
    assert push_mod._normalize_key_pem(b64).strip() == pem.strip()


def test_normalize_key_pem_rejects_garbage():
    with pytest.raises(ValueError):
        push_mod._normalize_key_pem("not a pem and !!! not base64 %%%")


def test_from_env_accepts_base64_key_and_signs(monkeypatch):
    """The prod-recommended base64 form must round-trip to a key that actually
    signs an ES256 JWT — the compose-``env_file`` path can't carry raw PEM."""
    key = ec.generate_private_key(ec.SECP256R1())
    pem = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ).decode()
    monkeypatch.setenv("APNS_KEY_P8", base64.b64encode(pem.encode()).decode())
    monkeypatch.setenv("APNS_KEY_ID", "ABC1234567")
    monkeypatch.setenv("APNS_TEAM_ID", "TEAM123456")
    monkeypatch.setenv("APNS_BUNDLE_ID", "aero.flyfun.weather")
    push_mod._reset_token_cache()
    try:
        config = push_mod.ApnsConfig.from_env()
        token = push_mod._provider_token(config)
        jwt.decode(token, key.public_key(), algorithms=["ES256"])  # verifies signature
    finally:
        push_mod._reset_token_cache()


def test_provider_token_is_valid_es256_jwt(apns_env):
    config = push_mod.ApnsConfig.from_env()
    token = push_mod._provider_token(config)
    header = jwt.get_unverified_header(token)
    assert header["alg"] == "ES256"
    assert header["kid"] == "ABC1234567"
    pub = apns_env.public_key()
    claims = jwt.decode(token, pub, algorithms=["ES256"])
    assert claims["iss"] == "TEAM123456"
    assert "iat" in claims


def test_provider_token_cached(apns_env):
    config = push_mod.ApnsConfig.from_env()
    assert push_mod._provider_token(config) == push_mod._provider_token(config)


def test_briefing_payload_shape(apns_env):
    flight = Flight(id="f1", user_id=DEV_USER_ID, route_name="EGTF-LFAT",
                    waypoints=["EGTF", "LFAT"],
                    departure_time=datetime(2026, 7, 10, 12, tzinfo=timezone.utc),
                    created_at=datetime(2026, 7, 1, tzinfo=timezone.utc))
    ts = datetime(2026, 7, 8, 10, tzinfo=timezone.utc)
    meta = BriefingPackMeta(flight_id="f1", fetch_timestamp=ts, days_out=2, assessment="AMBER")
    payload = push_mod._briefing_payload(flight, meta, None, badge=3)
    assert payload["aps"]["alert"]["title"] == "EGTF → LFAT"
    assert "AMBER" in payload["aps"]["alert"]["body"]
    assert payload["aps"]["badge"] == 3
    assert payload["flight_id"] == "f1"
    assert payload["timestamp"] == ts.isoformat()


def test_dispatch_skips_when_unconfigured(db_session, dev_user, monkeypatch):
    for k in ("APNS_KEY_P8", "APNS_KEY_P8_PATH", "APNS_KEY_ID", "APNS_TEAM_ID", "APNS_BUNDLE_ID"):
        monkeypatch.delenv(k, raising=False)
    db_session.add(DeviceTokenRow(user_id=DEV_USER_ID, token="tok", environment="sandbox"))
    db_session.flush()
    flight = Flight(id="f1", user_id=DEV_USER_ID, route_name="R",
                    departure_time=datetime(2026, 7, 10, 12, tzinfo=timezone.utc),
                    created_at=datetime(2026, 7, 1, tzinfo=timezone.utc))
    meta = BriefingPackMeta(flight_id="f1",
                            fetch_timestamp=datetime(2026, 7, 8, tzinfo=timezone.utc), days_out=2)
    # No config -> returns 0, never raises.
    assert push_mod.send_briefing_push(db_session, DEV_USER_ID, flight, meta, badge=1) == 0


def test_dead_token_prune_triggers_decay(db_session, dev_user, apns_env, monkeypatch):
    # A push-only user (email off, notifications on) whose only device APNs
    # reports dead during a send: pruning it drops the last device → the same
    # decay fail-safe fires as an explicit unregister (re-enable email + notice).
    prefs = db_session.get(UserPreferencesRow, DEV_USER_ID)
    prefs.app_prefs_json = '{"notify_push": true, "notify_email": false, "notify_scope": "all"}'
    db_session.add(DeviceTokenRow(user_id=DEV_USER_ID, token="dead", environment="sandbox"))
    db_session.flush()

    monkeypatch.setattr(
        push_mod, "_send_one",
        lambda *a, **k: push_mod.ApnsResult(
            ok=False, status_code=410, reason="Unregistered", dead=True,
        ),
    )

    flight = Flight(id="f1", user_id=DEV_USER_ID, route_name="R",
                    departure_time=datetime(2026, 7, 10, 12, tzinfo=timezone.utc),
                    created_at=datetime(2026, 7, 1, tzinfo=timezone.utc))
    meta = BriefingPackMeta(flight_id="f1",
                            fetch_timestamp=datetime(2026, 7, 8, tzinfo=timezone.utc),
                            days_out=2, assessment="AMBER")

    sent = push_mod.send_briefing_push(db_session, DEV_USER_ID, flight, meta, badge=1)
    db_session.flush()

    assert sent == 0
    assert (
        db_session.query(DeviceTokenRow).filter(DeviceTokenRow.token == "dead").first()
        is None
    )  # pruned
    import json as _json
    data = _json.loads(db_session.get(UserPreferencesRow, DEV_USER_ID).app_prefs_json)
    assert data["notify_email"] is True          # decay re-enabled email
    assert data["notify_decay_notice"] is True


# ---------------------------------------------------------------------------
# Orchestration: notify_briefing_refresh
# ---------------------------------------------------------------------------

def _spy_channels(monkeypatch):
    calls = {"email": 0, "push": []}

    def fake_email(db, user_id, flight, meta, pack_dir):
        calls["email"] += 1

    def fake_push(db, user_id, flight, meta, delta, badge):
        calls["push"].append(badge)

    monkeypatch.setattr(dispatch_mod, "_send_email", fake_email)
    monkeypatch.setattr(dispatch_mod, "_send_push", fake_push)
    return calls


def test_notify_scheduler_auto_qualifies_and_lights_badge(db_session, dev_user, monkeypatch):
    calls = _spy_channels(monkeypatch)
    _add_flight(db_session)
    ts = datetime(2026, 7, 8, 10, tzinfo=timezone.utc)
    _add_pack(db_session, "f1", ts, assessment="AMBER")
    flight = Flight(id="f1", user_id=DEV_USER_ID, route_name="R",
                    departure_time=datetime(2026, 7, 10, 12, tzinfo=timezone.utc),
                    created_at=datetime(2026, 7, 1, tzinfo=timezone.utc))
    meta = BriefingPackMeta(flight_id="f1", fetch_timestamp=ts, days_out=2, assessment="AMBER")

    dispatch_mod.notify_briefing_refresh(
        db_session, flight, meta, pack_dir=None, user_id=DEV_USER_ID, triggered_by="scheduler",
    )
    db_session.flush()
    assert calls["email"] == 1          # default prefs: email on
    assert calls["push"] == []          # push off by default
    assert badge_mod.compute_badge_count(db_session, DEV_USER_ID) == 1


def test_notify_manual_suppresses_email_but_lights_badge(db_session, dev_user, monkeypatch):
    # A user's own in-app manual refresh (triggered_by="user") never emails —
    # they're already looking — but it still qualifies, so the badge lights
    # (push, if on, would fire too; foreground-suppression handles the banner).
    calls = _spy_channels(monkeypatch)
    _add_flight(db_session)
    ts = datetime(2026, 7, 8, 10, tzinfo=timezone.utc)
    _add_pack(db_session, "f1", ts, assessment="AMBER")
    flight = Flight(id="f1", user_id=DEV_USER_ID, route_name="R",
                    departure_time=datetime(2026, 7, 10, 12, tzinfo=timezone.utc),
                    created_at=datetime(2026, 7, 1, tzinfo=timezone.utc))
    meta = BriefingPackMeta(flight_id="f1", fetch_timestamp=ts, days_out=2, assessment="AMBER")
    dispatch_mod.notify_briefing_refresh(
        db_session, flight, meta, pack_dir=None, user_id=DEV_USER_ID, triggered_by="user",
    )
    db_session.flush()
    assert calls["email"] == 0
    assert badge_mod.compute_badge_count(db_session, DEV_USER_ID) == 1


def test_notify_siri_manual_still_emails(db_session, dev_user, monkeypatch):
    # A Siri/MCP refresh is non-user-present, so it emails (closing the Siri
    # refresh-intent loop) even though it is not a scheduler auto-refresh.
    calls = _spy_channels(monkeypatch)
    _add_flight(db_session)
    ts = datetime(2026, 7, 8, 10, tzinfo=timezone.utc)
    _add_pack(db_session, "f1", ts, assessment="AMBER")
    flight = Flight(id="f1", user_id=DEV_USER_ID, route_name="R",
                    departure_time=datetime(2026, 7, 10, 12, tzinfo=timezone.utc),
                    created_at=datetime(2026, 7, 1, tzinfo=timezone.utc))
    meta = BriefingPackMeta(flight_id="f1", fetch_timestamp=ts, days_out=2, assessment="AMBER")
    dispatch_mod.notify_briefing_refresh(
        db_session, flight, meta, pack_dir=None, user_id=DEV_USER_ID, triggered_by="siri",
    )
    db_session.flush()
    assert calls["email"] == 1
    assert badge_mod.compute_badge_count(db_session, DEV_USER_ID) == 1


def test_notify_manual_push_fires_when_push_on(db_session, dev_user, monkeypatch):
    # Push fires on the user's own manual refresh (email does not).
    calls = _spy_channels(monkeypatch)
    prefs = db_session.get(UserPreferencesRow, DEV_USER_ID)
    prefs.app_prefs_json = '{"notify_push": true, "notify_scope": "all"}'
    db_session.flush()
    _add_flight(db_session)
    ts = datetime(2026, 7, 8, 10, tzinfo=timezone.utc)
    _add_pack(db_session, "f1", ts, assessment="AMBER")
    flight = Flight(id="f1", user_id=DEV_USER_ID, route_name="R",
                    departure_time=datetime(2026, 7, 10, 12, tzinfo=timezone.utc),
                    created_at=datetime(2026, 7, 1, tzinfo=timezone.utc))
    meta = BriefingPackMeta(flight_id="f1", fetch_timestamp=ts, days_out=2, assessment="AMBER")
    dispatch_mod.notify_briefing_refresh(
        db_session, flight, meta, pack_dir=None, user_id=DEV_USER_ID, triggered_by="user",
    )
    db_session.flush()
    assert calls["email"] == 0          # suppressed for user-present manual
    assert calls["push"] == [1]         # push fired, badge=1
    assert badge_mod.compute_badge_count(db_session, DEV_USER_ID) == 1


def test_notify_per_flight_notify_override_fires_on_manual(db_session, dev_user, monkeypatch):
    calls = _spy_channels(monkeypatch)
    _add_flight(db_session)
    ts = datetime(2026, 7, 8, 10, tzinfo=timezone.utc)
    _add_pack(db_session, "f1", ts, assessment="AMBER")
    flight = Flight(id="f1", user_id=DEV_USER_ID, route_name="R",
                    notify_override="notify",
                    departure_time=datetime(2026, 7, 10, 12, tzinfo=timezone.utc),
                    created_at=datetime(2026, 7, 1, tzinfo=timezone.utc))
    meta = BriefingPackMeta(flight_id="f1", fetch_timestamp=ts, days_out=2, assessment="AMBER")
    dispatch_mod.notify_briefing_refresh(
        db_session, flight, meta, pack_dir=None, user_id=DEV_USER_ID, triggered_by="user",
    )
    db_session.flush()
    assert calls["email"] == 1
    assert badge_mod.compute_badge_count(db_session, DEV_USER_ID) == 1


def test_notify_per_flight_notify_override_emails_only_when_non_user(db_session, dev_user, monkeypatch):
    # A per-flight "notify" override qualifies for any completion, but the
    # per-channel email rule still applies: a Siri refresh emails, the user's
    # own in-app manual refresh does not (badge lights either way).
    calls = _spy_channels(monkeypatch)
    _add_flight(db_session)
    ts = datetime(2026, 7, 8, 10, tzinfo=timezone.utc)
    _add_pack(db_session, "f1", ts, assessment="AMBER")
    flight = Flight(id="f1", user_id=DEV_USER_ID, route_name="R",
                    notify_override="notify",
                    departure_time=datetime(2026, 7, 10, 12, tzinfo=timezone.utc),
                    created_at=datetime(2026, 7, 1, tzinfo=timezone.utc))
    meta = BriefingPackMeta(flight_id="f1", fetch_timestamp=ts, days_out=2, assessment="AMBER")
    dispatch_mod.notify_briefing_refresh(
        db_session, flight, meta, pack_dir=None, user_id=DEV_USER_ID, triggered_by="user",
    )
    db_session.flush()
    assert calls["email"] == 0   # user-present manual → email suppressed
    assert badge_mod.compute_badge_count(db_session, DEV_USER_ID) == 1


def test_notify_mute_blocks_even_scheduler(db_session, dev_user, monkeypatch):
    calls = _spy_channels(monkeypatch)
    _add_flight(db_session)
    ts = datetime(2026, 7, 8, 10, tzinfo=timezone.utc)
    _add_pack(db_session, "f1", ts, assessment="AMBER")
    flight = Flight(id="f1", user_id=DEV_USER_ID, route_name="R",
                    notify_override="mute",
                    departure_time=datetime(2026, 7, 10, 12, tzinfo=timezone.utc),
                    created_at=datetime(2026, 7, 1, tzinfo=timezone.utc))
    meta = BriefingPackMeta(flight_id="f1", fetch_timestamp=ts, days_out=2, assessment="AMBER")
    dispatch_mod.notify_briefing_refresh(
        db_session, flight, meta, pack_dir=None, user_id=DEV_USER_ID, triggered_by="scheduler",
    )
    db_session.flush()
    assert calls["email"] == 0
    assert badge_mod.compute_badge_count(db_session, DEV_USER_ID) == 0


# ---------------------------------------------------------------------------
# Endpoint integration: devices, badge, seen, preferences
# ---------------------------------------------------------------------------

@pytest.fixture
def app_db():
    from conftest import make_app_engine
    engine = make_app_engine()
    TestSession = sessionmaker(bind=engine)
    s = TestSession()
    s.add(UserRow(id=DEV_USER_ID, provider="google", provider_sub="sub",
                  email="dev@localhost", display_name="Dev", approved=True))
    s.flush()
    s.add(UserPreferencesRow(user_id=DEV_USER_ID))
    s.add(FlightRow(id="f1", user_id=DEV_USER_ID, route_name="EGTF-LFAT",
                    waypoints_json='["EGTF", "LFAT"]',
                    departure_time=datetime(2026, 7, 10, 12, tzinfo=timezone.utc),
                    created_at=datetime(2026, 7, 1, tzinfo=timezone.utc)))
    s.flush()
    s.add(BriefingPackRow(flight_id="f1",
                          fetch_timestamp=datetime(2026, 7, 8, 10, tzinfo=timezone.utc),
                          days_out=2, assessment="AMBER"))
    s.commit()
    s.close()
    yield TestSession
    engine.dispose()


@pytest.fixture
def client(app_db, tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET", "test")
    app = create_app()

    def _override_get_db():
        s = app_db()
        try:
            yield s
            s.commit()
        except Exception:
            s.rollback()
            raise
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[current_user_id] = lambda: DEV_USER_ID
    return TestClient(app, raise_server_exceptions=False)


def test_register_and_unregister_device(client, app_db):
    r = client.post("/api/devices", json={"token": "abc123", "environment": "sandbox"})
    assert r.status_code == 204
    s = app_db()
    row = s.query(DeviceTokenRow).filter(DeviceTokenRow.token == "abc123").first()
    assert row is not None and row.environment == "sandbox" and row.user_id == DEV_USER_ID
    s.close()

    # Re-register updates environment (upsert), no duplicate row.
    r = client.post("/api/devices", json={"token": "abc123", "environment": "production"})
    assert r.status_code == 204
    s = app_db()
    rows = s.query(DeviceTokenRow).filter(DeviceTokenRow.token == "abc123").all()
    assert len(rows) == 1 and rows[0].environment == "production"
    s.close()

    r = client.delete("/api/devices/abc123")
    assert r.status_code == 204
    s = app_db()
    assert s.query(DeviceTokenRow).filter(DeviceTokenRow.token == "abc123").first() is None
    s.close()


def test_register_device_rejects_empty_token(client):
    r = client.post("/api/devices", json={"token": "", "environment": "sandbox"})
    assert r.status_code == 422


def _make_push_only(client, token="d1"):
    """Put the account in a valid push-only state: a device registered, push on,
    email off, notifications on. (Push on keeps the channel-invariant backstop
    from clamping scope to off, so this is a real scope≠off / email-off state.)"""
    assert client.post("/api/devices", json={"token": token, "environment": "sandbox"}).status_code == 204
    r = client.put("/api/user/preferences", json={
        "notify_push": True, "notify_email": False, "notify_scope": "all",
    }).json()
    assert r["notify_email"] is False and r["notify_scope"] == "all"


def test_last_device_unregister_reenables_email(client):
    # A push-only user (email off, notifications on) who loses their last device
    # gets email re-enabled + a one-time notice (channel-invariant decay branch).
    _make_push_only(client, "d1")

    assert client.delete("/api/devices/d1").status_code == 204

    prefs = client.get("/api/user/preferences").json()
    assert prefs["notify_email"] is True
    assert prefs["notify_decay_notice"] is True

    # Dismissing the notice clears it (client acknowledges).
    updated = client.put("/api/user/preferences", json={"notify_decay_notice": False}).json()
    assert updated["notify_decay_notice"] is False


def test_last_device_unregister_respects_explicit_off(client):
    # If the user has explicitly silenced everything (scope off), losing the last
    # device must NOT resurrect email — the invariant only promises a channel
    # while notifications are on.
    assert client.post("/api/devices", json={"token": "d1", "environment": "sandbox"}).status_code == 204
    r = client.put("/api/user/preferences", json={
        "notify_scope": "off", "notify_email": False, "notify_push": False,
    }).json()
    assert r["notify_scope"] == "off" and r["notify_email"] is False

    assert client.delete("/api/devices/d1").status_code == 204
    prefs = client.get("/api/user/preferences").json()
    assert prefs["notify_email"] is False          # not resurrected
    assert prefs["notify_decay_notice"] is False


def test_non_last_device_unregister_keeps_email_off(client):
    _make_push_only(client, "d1")
    client.post("/api/devices", json={"token": "d2", "environment": "sandbox"})

    # One of two devices removed — a device remains, so no fail-safe fires.
    assert client.delete("/api/devices/d1").status_code == 204
    prefs = client.get("/api/user/preferences").json()
    assert prefs["notify_email"] is False
    assert prefs["notify_decay_notice"] is False


def test_badge_and_seen_flow(client, app_db):
    # No unseen yet.
    assert client.get("/api/flights/badge").json()["count"] == 0

    # Simulate a notify-qualifying refresh landing (advance last_notified_ts).
    s = app_db()
    badge_mod.record_notify_qualifying(
        s, DEV_USER_ID, "f1", datetime(2026, 7, 8, 10, tzinfo=timezone.utc),
    )
    s.commit()
    s.close()

    assert client.get("/api/flights/badge").json()["count"] == 1

    # Opening the briefing (web or app) clears it, returns fresh count.
    r = client.post("/api/flights/f1/seen")
    assert r.status_code == 200 and r.json()["count"] == 0
    assert client.get("/api/flights/badge").json()["count"] == 0


def test_badge_route_not_shadowed_by_flight_route(client):
    # /flights/badge must resolve to the badge endpoint, not /flights/{id}.
    r = client.get("/api/flights/badge")
    assert r.status_code == 200
    assert "count" in r.json()


def test_preferences_notify_roundtrip(client):
    # Defaults preserve today's behaviour.
    prefs = client.get("/api/user/preferences").json()
    assert prefs["notify_email"] is True
    assert prefs["notify_push"] is False
    assert prefs["notify_scope"] == "auto"
    assert prefs["notify_change_only"] is True
    assert prefs["notify_decay_notice"] is False

    r = client.put("/api/user/preferences", json={
        "notify_push": True, "notify_scope": "all", "notify_change_only": False,
    })
    assert r.status_code == 200
    updated = r.json()
    assert updated["notify_push"] is True
    assert updated["notify_scope"] == "all"
    assert updated["notify_change_only"] is False
    # email untouched.
    assert updated["notify_email"] is True


def test_channel_invariant_backstop_clamps_scope_off(client):
    # Both channels off while scope is on would be the silent dead-state — the
    # server clamps scope to off (mirrors the client reroute) for ANY writer.
    r = client.put("/api/user/preferences", json={
        "notify_email": False, "notify_push": False, "notify_scope": "all",
    })
    assert r.status_code == 200
    updated = r.json()
    assert updated["notify_scope"] == "off"
    assert updated["notify_email"] is False
    assert updated["notify_push"] is False


def test_channel_invariant_backstop_leaves_one_channel_alone(client):
    # One channel on (push) with email off is a valid push-only state — no clamp.
    r = client.put("/api/user/preferences", json={
        "notify_email": False, "notify_push": True, "notify_scope": "all",
    })
    assert r.status_code == 200
    updated = r.json()
    assert updated["notify_scope"] == "all"
    assert updated["notify_email"] is False
    assert updated["notify_push"] is True


def test_flight_notify_override_update(client):
    r = client.patch("/api/flights/f1", json={"notify_override": "mute"})
    assert r.status_code == 200
    assert r.json()["notify_override"] == "mute"
