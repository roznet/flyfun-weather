"""Live-alert push on flight day (#754, ``notify/live_alerts.py``).

What reaches the pilot's phone while an auto-refresh flight is in its live
window: who is eligible, one push per tick, the expiry, the cleared push and
its sustain rule, the re-arm that amends §45 for the push path only, and
shadow mode. Every scenario drives the real classifier tick by tick
(``classify_changes`` with its memory), then the push decision, the way the
live tick does.
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from weatherbrief.db.models import (
    DeviceTokenRow,
    FlightRow,
    FlightTripRow,
    LiveDeliveryRow,
    UserPreferencesRow,
)
from weatherbrief.models.live import LiveChange, LiveChanges, LiveLayer, LivePushedAlert, LivePushState
from weatherbrief.models.observations import AirportObservation, RouteObservations, RouteSigmets
from weatherbrief.models.observations import SigmetAlongRoute
from weatherbrief.notify import live_alerts
from weatherbrief.notify.live_alerts import (
    CLEAR_SUSTAIN_TICKS,
    build_payload,
    decide,
    next_state,
    notify_live_alerts,
    push_expiry,
    skip_reason,
)
from weatherbrief.tasks.live_significance import ClassifierMemory, airport_roles, classify_changes

T0 = datetime(2026, 10, 10, 8, 0, tzinfo=timezone.utc)  # the briefing's METARs
DEP = datetime(2026, 10, 10, 10, 0, tzinfo=timezone.utc)
TICK = timedelta(minutes=10)
# ZZDP → ZZDS; ZZAL is the alternate.
ROLES = airport_roles(["ZZDP", "ZZDS"], ["ZZAL"])
DEV = "dev-user-001"


@pytest.fixture
def db_engine():
    # Per test: the send path commits (dead-token prune, the delivery row).
    from conftest import make_app_engine
    engine = make_app_engine()
    yield engine
    engine.dispose()


# --- Tick driver ----------------------------------------------------------------


def apt(icao, cat="VFR", *, t, wx=()):
    return AirportObservation(
        icao=icao, distance_from_route_nm=1.0, nearest_waypoint_icao=icao,
        metar_raw=f"METAR {icao} 05010KT 9999 FEW030 20/15 Q1020", metar_time=t,
        metar_report_type="METAR", metar_flight_category=cat, metar_weather=list(wx),
        metar_wind_advisory="green",
    )


def obs(*airports, t=T0):
    return RouteObservations(
        corridor_nm=30.0, fetch_time=t, airports_found=len(airports),
        airports_with_metar=len(airports), airports_with_taf=0, airports=list(airports),
    )


def sig(seq, fir="ZZZZ"):
    return SigmetAlongRoute(
        fir_id=fir, hazard="TS", qualifier="EMBD",
        raw_text=f"{fir} SIGMET {seq} VALID 100800/101200",
    )


def sigmets(*items, ok=True):
    return RouteSigmets(corridor_nm=50.0, fetch_time=T0, sigmets=list(items), fetch_ok=ok)


BASE = obs(apt("ZZDP", t=T0), apt("ZZDS", t=T0), apt("ZZAL", t=T0))


class Replay:
    """Classifier memory and push memory carried tick to tick, as on the layer."""

    def __init__(self, *, base_sigmets=None):
        self.memory = ClassifierMemory()
        self.state: LivePushState | None = None
        self.now = T0
        self.base_sigmets = base_sigmets

    def tick(self, dest="VFR", dep="VFR", *, latest_sigmets=None, departed=False, pushed=True):
        self.now += TICK
        latest = obs(apt("ZZDP", dep, t=self.now), apt("ZZDS", dest, t=self.now),
                     apt("ZZAL", t=self.now), t=self.now)
        changes, self.memory = classify_changes(
            baseline_obs=BASE, latest_obs=latest,
            baseline_sigmets=self.base_sigmets, latest_sigmets=latest_sigmets,
            roles=ROLES, departure_at=DEP if departed else None,
            memory=self.memory, now=self.now if not departed else max(self.now, DEP),
        )
        decision = decide(changes, self.state, now=self.now)
        self.state = next_state(decision, pushed=pushed, now=self.now)
        return decision


def alerted(decision):
    return [c.key for c in decision.alerts]


def cleared(decision):
    return [p.key for p, _ in decision.clears]


# --- New alerts -----------------------------------------------------------------


def test_new_alert_pushes_once_then_stays_quiet():
    r = Replay()
    assert alerted(r.tick(dest="IFR")) == ["metar:ZZDS"]
    # Persisting across ticks: the classifier's new_alert fires once.
    assert r.tick(dest="IFR").empty
    assert r.tick(dest="IFR").empty
    assert "metar:ZZDS" in r.state.active


def test_two_changes_on_one_tick_are_one_push():
    r = Replay()
    d = r.tick(dest="IFR", dep="IFR")
    assert sorted(alerted(d)) == ["metar:ZZDP", "metar:ZZDS"]
    payload = build_payload(_flight_row(), d, tick_at=r.now)
    assert payload["aps"]["alert"]["title"] == "ZZDP → ZZDS · 2 changes"
    lines = payload["aps"]["alert"]["body"].split("\n")
    assert len(lines) == 2
    assert "ZZDS METAR: VFR → IFR (destination) · METAR 08:10Z" in lines
    assert payload["type"] == "live_alert"
    assert sorted(payload["keys"]) == ["metar:ZZDP", "metar:ZZDS"]
    assert payload["aps"]["thread-id"] == "zz-flight"
    assert payload["aps"]["interruption-level"] == "time-sensitive"
    assert payload["flight_id"] == "zz-flight"
    assert payload["tick_at"] == r.now.isoformat()
    assert "badge" not in payload["aps"]
    assert "apns-collapse-id" not in json.dumps(payload)


def test_worse_again_pushes_again():
    r = Replay()
    r.tick(dest="IFR")
    assert alerted(r.tick(dest="LIFR")) == ["metar:ZZDS"]


# --- Cleared, sustain, re-arm ---------------------------------------------------


def test_clear_needs_two_evaluated_ticks_then_rearms():
    """The §45 conflict, end to end: after a pushed clear, a return to IFR
    must push again even though the classifier keeps it quiet on screen."""
    r = Replay()
    assert alerted(r.tick(dest="IFR")) == ["metar:ZZDS"]
    # Back to VFR: one tick is not enough (flicker).
    assert r.tick(dest="VFR").empty
    d = r.tick(dest="VFR")
    assert cleared(d) == ["metar:ZZDS"]
    line = d.clears[0][1]
    assert line.startswith("Cleared: ZZDS METAR no longer IFR (destination)")
    assert r.state.active == {} and "metar:ZZDS" in r.state.rearmed
    # Stays clear: nothing more.
    assert r.tick(dest="VFR").empty
    # IFR again: §45 keeps the classifier quiet (not worse than IFR)...
    d = r.tick(dest="IFR")
    assert [c.new_alert for c in d.alerts] == [False]
    # ...but the push path re-armed it.
    assert alerted(d) == ["metar:ZZDS"] and d.rearmed == {"metar:ZZDS"}
    assert r.state.rearms_fired == 1 and r.state.clears_pushed == 1
    assert "metar:ZZDS" in r.state.active and "metar:ZZDS" not in r.state.rearmed


def test_flicker_resets_the_sustain_counter():
    r = Replay()
    r.tick(dest="IFR")
    assert r.tick(dest="VFR").empty      # clear tick 1
    assert r.tick(dest="IFR").empty      # back: §45 quiet, counter reset, no re-arm yet
    assert r.tick(dest="VFR").empty      # clear tick 1 again
    assert cleared(r.tick(dest="VFR")) == ["metar:ZZDS"]


def test_no_other_section45_behaviour_changes():
    """The re-arm lives in the push memory only: the classifier's own
    memory still holds the worst level alerted."""
    r = Replay()
    r.tick(dest="IFR")
    r.tick(dest="VFR")
    r.tick(dest="VFR")
    assert r.memory.alerted["metar:ZZDS"] == "IFR"


def test_departure_after_take_off_is_not_cleared():
    """After take-off the departure airport is no longer evaluated: its rows
    go, but that is not "cleared"."""
    r = Replay()
    assert "metar:ZZDP" in alerted(r.tick(dep="IFR"))
    for _ in range(3):
        assert r.tick(dep="IFR", departed=True).empty
    assert "metar:ZZDP" in r.state.active


def test_skipped_ticks_do_not_count():
    r = Replay()
    r.tick(dest="IFR")
    changes = LiveChanges(computed_at=r.now, changes=[], evaluated=None)
    for _ in range(3):
        d = decide(changes, r.state, now=r.now)
        r.state = next_state(d, pushed=True, now=r.now)
        assert d.empty
    assert r.state.active["metar:ZZDS"].clear_ticks == 0


def test_sigmet_gone_clears_after_two_ticks():
    r = Replay(base_sigmets=sigmets())
    d = r.tick(latest_sigmets=sigmets(sig(3)))
    assert alerted(d) == ["sigmet:ZZZZ|3"]
    assert r.tick(latest_sigmets=sigmets()).empty
    d = r.tick(latest_sigmets=sigmets())
    assert cleared(d) == ["sigmet:ZZZZ|3"]


def test_failed_sigmet_fetch_neither_clears_nor_counts():
    r = Replay(base_sigmets=sigmets())
    r.tick(latest_sigmets=sigmets(sig(3)))
    for _ in range(3):
        assert r.tick(latest_sigmets=sigmets(ok=False)).empty
    assert r.state.active["sigmet:ZZZZ|3"].clear_ticks == 0


def _change(key, *, tier="alert", kind="sigmet_issued", new=False, **kw):
    return LiveChange(
        key=key, kind=kind, source=kw.pop("source", "SIGMET"), direction=kw.pop("direction", "worse"),
        tier=tier, role=kw.pop("role", "route"), message=kw.pop("message", key), new_alert=new, **kw,
    )


def _active(key, kind="sigmet_issued", **kw):
    return LivePushState(active={key: LivePushedAlert(
        key=key, kind=kind, role="route", message=f"New SIGMET {key}", pushed_at=T0, **kw,
    )})


def test_sigmet_reissue_follows_the_key_and_is_not_a_clear():
    state = _active("sigmet:LFMM|T01")
    reissue = _change("sigmet:LFMM|T01+sigmet:LFMM|T02", tier="highlight", direction="updated")
    changes = LiveChanges(computed_at=T0, changes=[reissue], evaluated=["sigmet:"])
    for _ in range(3):
        d = decide(changes, state, now=T0)
        state = next_state(d, pushed=True, now=T0)
        assert d.empty
    assert list(state.active) == ["sigmet:LFMM|T01+sigmet:LFMM|T02"]


def test_pending_sigmet_missing_is_not_a_clear():
    state = _active("sigmet:LFMM|T01")
    changes = LiveChanges(computed_at=T0, changes=[], evaluated=["sigmet:"])
    for _ in range(3):
        d = decide(changes, state, now=T0, is_pending=lambda k: True)
        state = next_state(d, pushed=True, now=T0)
        assert d.empty


def test_cancelled_sigmet_clear_uses_its_row():
    state = _active("sigmet:LFMM|T01")
    state.active["sigmet:LFMM|T01"].clear_ticks = CLEAR_SUSTAIN_TICKS - 1
    row = _change("sigmet:LFMM|T01", tier="highlight", kind="sigmet_cancelled", direction="better",
                  message="SIGMET LFMM T01: EMBD TS cancelled", observed_at=T0)
    d = decide(LiveChanges(computed_at=T0, changes=[row], evaluated=["sigmet:"]), state, now=T0)
    assert d.clears[0][1] == "Cleared: SIGMET LFMM T01: EMBD TS cancelled · SIGMET 08:00Z"
    payload = build_payload(_flight_row(), d, tick_at=T0)
    assert payload["type"] == "live_clear"
    assert payload["aps"]["interruption-level"] == "active"
    assert payload["aps"]["alert"]["title"] == "ZZDP → ZZDS · alert cleared"


def test_storm_alerts_push_but_never_clear():
    storm = _change("storm:zz1", kind="storm", source="RADAR", new=True, message="Storm 8 NM ahead")
    d = decide(LiveChanges(computed_at=T0, changes=[storm], evaluated=["storm:"]), None, now=T0)
    assert alerted(d) == ["storm:zz1"]
    state = next_state(d, pushed=True, now=T0)
    assert state.active == {} and state.alerts_pushed == 1
    # Gone on the next ticks: nothing to clear.
    for _ in range(3):
        d = decide(LiveChanges(computed_at=T0, changes=[], evaluated=["storm:"]), state, now=T0)
        assert d.empty


def test_skipped_push_tracks_nothing_but_still_drops_due_clears():
    r = Replay()
    d = r.tick(dest="IFR", pushed=False)
    assert alerted(d) == ["metar:ZZDS"] and r.state.active == {}
    st = _active("metar:ZZDS", kind="metar_category", icao="ZZDS", to_value="IFR")
    st.active["metar:ZZDS"].clear_ticks = CLEAR_SUSTAIN_TICKS - 1
    d = decide(LiveChanges(computed_at=T0, changes=[], evaluated=["metar:ZZDS"]), st, now=T0)
    after = next_state(d, pushed=False, now=T0)
    assert after.active == {} and after.rearmed == {}


# --- Expiry -----------------------------------------------------------------------


def _flight_row(**kw):
    return FlightRow(
        id="zz-flight", user_id=DEV, route_name="ZZDP-ZZDS",
        waypoints_json=json.dumps(["ZZDP", "ZZDS"]), departure_time=DEP,
        flight_duration_hours=2.0, **kw,
    )


def test_expiry_is_thirty_minutes_inside_the_window():
    assert push_expiry(_flight_row(), DEP) == DEP + timedelta(minutes=30)


def test_expiry_never_outlives_the_window():
    # Window ends at arrival (12:00) + 1 h.
    now = DEP + timedelta(hours=2, minutes=50)
    assert push_expiry(_flight_row(), now) == DEP + timedelta(hours=3)


# --- Eligibility ------------------------------------------------------------------


def _persist_flight(db, user, *, auto=True, override="default", trip=None):
    row = _flight_row(auto_refresh=auto, notify_override=override, trip_id=trip)
    row.user_id = user
    db.add(row)
    db.flush()
    return row


def _prefs(db, user, **prefs):
    row = db.get(UserPreferencesRow, user)
    data = json.loads(row.app_prefs_json or "{}")
    data.update(prefs)
    row.app_prefs_json = json.dumps(data)
    db.flush()


def _device(db, user, token="zz-token"):
    db.add(DeviceTokenRow(user_id=user, token=token, environment="sandbox"))
    db.flush()


@pytest.fixture
def eligible(db_session, dev_user):
    _prefs(db_session, dev_user, notify_push=True)
    _device(db_session, dev_user)
    return _persist_flight(db_session, dev_user)


def test_eligible_flight(db_session, eligible):
    reason, devices = skip_reason(db_session, eligible, trigger="tick")
    assert reason is None and devices == [("zz-token", "sandbox")]


def test_live_alerts_default_on_without_the_key(db_session, dev_user):
    from weatherbrief.api.preferences import load_notify_prefs

    assert load_notify_prefs(db_session, dev_user)["notify_live_alerts"] is True


@pytest.mark.parametrize("setup, reason", [
    (lambda db, u, f: setattr(f, "auto_refresh", False), "not_auto_refresh"),
    (lambda db, u, f: setattr(f, "notify_override", "mute"), "muted"),
    (lambda db, u, f: _prefs(db, u, notify_live_alerts=False), "pref_off"),
    (lambda db, u, f: _prefs(db, u, notify_push=False), "push_off"),
    (lambda db, u, f: db.query(DeviceTokenRow).delete(), "no_device"),
])
def test_ineligible(db_session, dev_user, eligible, setup, reason):
    setup(db_session, dev_user, eligible)
    db_session.flush()
    assert skip_reason(db_session, eligible, trigger="tick")[0] == reason


def test_user_trigger_never_pushes(db_session, eligible):
    assert skip_reason(db_session, eligible, trigger="user")[0] == "user_trigger"


def test_scope_off_does_not_silence_live_alerts(db_session, dev_user, eligible):
    _prefs(db_session, dev_user, notify_scope="off", notify_change_only=True)
    assert skip_reason(db_session, eligible, trigger="tick")[0] is None


def test_trip_flags_apply(db_session, dev_user):
    _prefs(db_session, dev_user, notify_push=True)
    _device(db_session, dev_user)
    db_session.add(FlightTripRow(id="zztrip", user_id=dev_user, name="ZZ trip", auto_refresh=True))
    db_session.flush()
    leg = _persist_flight(db_session, dev_user, auto=False, trip="zztrip")
    assert skip_reason(db_session, leg, trigger="tick")[0] is None
    db_session.get(FlightTripRow, "zztrip").notify_override = "mute"
    db_session.flush()
    assert skip_reason(db_session, leg, trigger="tick")[0] == "muted"


# --- The sink: shadow mode, sending, the ledger -------------------------------------


def _stored_layer(tmp_path, changes):
    pack_dir = tmp_path / DEV / "zz-flight" / "p1"
    pack_dir.mkdir(parents=True)
    layer = LiveLayer(
        flight_id="zz-flight", pack_timestamp="2026-10-10T06:00:00+00:00",
        pack_dir_name="p1", live_updated_at=T0 + TICK, changes=changes,
    )
    (pack_dir.parent / "live.json").write_text(layer.model_dump_json())
    return pack_dir, layer


def _alert_changes():
    return LiveChanges(computed_at=T0, evaluated=["metar:ZZDS"], changes=[_change(
        "metar:ZZDS", kind="metar_category", source="METAR", new=True, role="destination",
        icao="ZZDS", to_value="IFR", message="ZZDS METAR: VFR → IFR", observed_at=T0,
    )])


def test_shadow_mode_logs_and_sends_nothing(db_session, eligible, tmp_path, monkeypatch, caplog):
    from weatherbrief.tasks.live_layer import load_live

    monkeypatch.delenv(live_alerts.SEND_ENV, raising=False)
    pack_dir, layer = _stored_layer(tmp_path, _alert_changes())
    with patch("weatherbrief.notify.push._dispatch") as dispatch, caplog.at_level(logging.INFO):
        outcome = notify_live_alerts(db_session, eligible, layer, pack_dir=pack_dir, now=T0 + TICK)
    assert outcome == "shadow"
    dispatch.assert_not_called()
    assert "LIVE_PUSH_WOULD_SEND flight=zz-flight" in caplog.text
    assert "alerts=['metar:ZZDS']" in caplog.text and "devices=1" in caplog.text
    # The memory advances as if sent, so a shadow run measures clears and re-arms.
    stored = load_live(pack_dir.parent)
    assert "metar:ZZDS" in stored.push_state.active


def test_skip_is_logged_with_its_reason(db_session, dev_user, eligible, tmp_path, caplog):
    _prefs(db_session, dev_user, notify_live_alerts=False)
    pack_dir, layer = _stored_layer(tmp_path, _alert_changes())
    with caplog.at_level(logging.INFO):
        outcome = notify_live_alerts(db_session, eligible, layer, pack_dir=pack_dir, now=T0 + TICK)
    assert outcome == "skipped:pref_off"
    assert "LIVE_PUSH_SKIPPED flight=zz-flight user=dev-user-001 reason=pref_off" in caplog.text


def test_user_trigger_is_skipped_before_anything(db_session, eligible, tmp_path):
    pack_dir, layer = _stored_layer(tmp_path, _alert_changes())
    with patch.object(live_alerts, "decide") as dec:
        assert notify_live_alerts(db_session, eligible, layer, pack_dir=pack_dir,
                                  trigger="user") == "skipped:user_trigger"
    dec.assert_not_called()


def test_refused_commit_pushes_nothing(db_session, eligible, tmp_path):
    assert notify_live_alerts(db_session, eligible, None, pack_dir=tmp_path) == "none"


def test_send_mode_dispatches_once_with_expiry_and_records_delivery(
    db_session, eligible, tmp_path, monkeypatch,
):
    monkeypatch.setenv(live_alerts.SEND_ENV, "1")
    pack_dir, layer = _stored_layer(tmp_path, _alert_changes())
    now = DEP
    with patch("weatherbrief.notify.push._dispatch", return_value=1) as dispatch:
        outcome = notify_live_alerts(db_session, eligible, layer, pack_dir=pack_dir, now=now)
    assert outcome == "sent"
    dispatch.assert_called_once()
    kwargs = dispatch.call_args.kwargs
    assert kwargs["push_type"] == "alert" and kwargs["priority"] == 10
    assert kwargs["extra_headers"] == {
        "apns-expiration": str(int((now + timedelta(minutes=30)).timestamp())),
    }
    payload = dispatch.call_args.args[2]
    assert payload["type"] == "live_alert" and payload["keys"] == ["metar:ZZDS"]
    rows = db_session.query(LiveDeliveryRow).filter_by(flight_id="zz-flight").all()
    assert len(rows) == 1
    assert rows[0].delivered_via == "push" and rows[0].push_sent_at is not None
    assert rows[0].platform == "ios"


def test_failed_send_does_not_advance_the_memory(db_session, eligible, tmp_path, monkeypatch, caplog):
    """An undelivered alert must not later read as "cleared" (review, #756)."""
    from weatherbrief.tasks.live_layer import load_live

    monkeypatch.setenv(live_alerts.SEND_ENV, "1")
    pack_dir, layer = _stored_layer(tmp_path, _alert_changes())
    with patch("weatherbrief.notify.push._dispatch", return_value=0), caplog.at_level(logging.INFO):
        outcome = notify_live_alerts(db_session, eligible, layer, pack_dir=pack_dir, now=DEP)
    assert outcome == "failed"
    assert "reason=no_device_reached" in caplog.text
    stored = load_live(pack_dir.parent)
    assert stored.push_state is None or "metar:ZZDS" not in stored.push_state.active
    assert db_session.query(LiveDeliveryRow).count() == 0


def test_send_exception_is_logged_apart(db_session, eligible, tmp_path, monkeypatch, caplog):
    monkeypatch.setenv(live_alerts.SEND_ENV, "1")
    pack_dir, layer = _stored_layer(tmp_path, _alert_changes())
    with patch("weatherbrief.notify.push.send_live_alert_push", side_effect=RuntimeError("boom")), \
            caplog.at_level(logging.INFO):
        outcome = notify_live_alerts(db_session, eligible, layer, pack_dir=pack_dir, now=DEP)
    assert outcome == "failed" and "reason=exception" in caplog.text


def test_nothing_new_makes_no_db_query(db_session, eligible, tmp_path):
    changes = LiveChanges(computed_at=T0, changes=[], evaluated=["metar:ZZDS"])
    pack_dir, layer = _stored_layer(tmp_path, changes)
    with patch.object(live_alerts, "skip_reason") as sr:
        assert notify_live_alerts(db_session, eligible, layer, pack_dir=pack_dir) == "none"
    sr.assert_not_called()
