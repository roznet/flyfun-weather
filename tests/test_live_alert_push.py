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
from weatherbrief.models.live import (
    LiveChange,
    LiveChanges,
    LiveLayer,
    LivePushedAlert,
    LivePushState,
    LiveStorm,
    LiveStorms,
)
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


def apt(icao, cat="VFR", *, t, wx=(), taf=None, wind="green"):
    return AirportObservation(
        icao=icao, distance_from_route_nm=1.0, nearest_waypoint_icao=icao,
        metar_raw=f"METAR {icao} 05010KT 9999 FEW030 20/15 Q1020", metar_time=t,
        metar_report_type="METAR", metar_flight_category=cat, metar_weather=list(wx),
        metar_wind_advisory=wind, taf_flight_category_at_eta=taf,
        taf_issue_time=t - timedelta(hours=1) if taf else None,
    )


def obs(*airports, t=T0):
    return RouteObservations(
        corridor_nm=30.0, fetch_time=t, airports_found=len(airports),
        airports_with_metar=len(airports), airports_with_taf=sum(a.taf_flight_category_at_eta is not None for a in airports),
        airports=list(airports),
    )


def sig(seq, fir="ZZZZ"):
    return SigmetAlongRoute(
        fir_id=fir, hazard="TS", qualifier="EMBD",
        raw_text=f"{fir} SIGMET {seq} VALID 100800/101200",
    )


def sigmets(*items, ok=True):
    return RouteSigmets(corridor_nm=50.0, fetch_time=T0, sigmets=list(items), fetch_ok=ok)


def base_obs(dest_taf="VFR", dep_taf="VFR", dest="VFR"):
    return obs(apt("ZZDP", t=T0, taf=dep_taf), apt("ZZDS", dest, t=T0, taf=dest_taf),
               apt("ZZAL", t=T0, taf="VFR"))


BASE = base_obs()


class Replay:
    """Classifier memory and push memory carried tick to tick, as on the
    layer. ``started`` (default): the pilot pressed Start before the first
    tick, which seeds the known level from what shows then (all VFR unless
    ``first`` says otherwise) and pushes nothing for a category."""

    def __init__(self, *, base_sigmets=None, base=None, started=True, first=None):
        self.memory = ClassifierMemory()
        self.state: LivePushState | None = None
        self.now = T0
        self.base_sigmets = base_sigmets
        self.base = base or BASE
        self.started = started
        self.pack = "2026-10-10T06:00:00+00:00"
        if started:
            seed = self.tick(**(first or {}))
            assert not [c for c in seed.alerts if c.kind in live_alerts.CATEGORY_KINDS]

    def new_pack(self, base, pack="2026-10-10T09:00:00+00:00"):
        """A new briefing after Start: fresh baseline and classifier memory,
        the push's category memory carried over (``commit_live_update``)."""
        self.base, self.pack = base, pack
        self.memory = ClassifierMemory()
        self.state = self.state.carried_over()

    def tick(self, dest="VFR", dep="VFR", *, dest_taf="VFR", dep_taf="VFR", dest_wind="green",
             dep_wind="green", latest_sigmets=None, departed=False, pushed=True, failed=False):
        self.now += TICK
        latest = obs(apt("ZZDP", dep, t=self.now, taf=dep_taf, wind=dep_wind),
                     apt("ZZDS", dest, t=self.now, taf=dest_taf, wind=dest_wind),
                     apt("ZZAL", t=self.now, taf="VFR"), t=self.now)
        changes, self.memory = classify_changes(
            baseline_obs=self.base, latest_obs=latest,
            baseline_sigmets=self.base_sigmets, latest_sigmets=latest_sigmets,
            roles=ROLES, departure_at=DEP if departed else None,
            memory=self.memory, now=self.now if not departed else max(self.now, DEP),
        )
        layer = LiveLayer(flight_id="zz-flight", pack_timestamp=self.pack, pack_dir_name="p",
                          route_observations=latest, changes=changes)
        decision = decide(changes, self.state, now=self.now, layer=layer,
                          started=self.started, left_departure=departed)
        self.state = next_state(decision, pushed=pushed, failed=failed, now=self.now)
        return decision


def alerted(decision):
    return [c.key for c in decision.alerts]


def cleared(decision):
    return [p.key for p, _ in decision.clears]


def suppressed(decision):
    return dict(decision.suppressed)


# --- Category rule (#968) ----------------------------------------------------------


def test_no_start_no_category_push():
    r = Replay(started=False)
    d = r.tick(dest="IFR")
    assert d.empty and suppressed(d) == {"metar:ZZDS": "not_started"}
    assert r.state.started_at is None


def test_rows_showing_at_start_never_push():
    r = Replay(first={"dest": "IFR", "dest_taf": "LIFR"})
    assert r.state.known == {"ZZDS": "LIFR"} and r.state.started_at is not None
    assert r.tick(dest="IFR", dest_taf="LIFR").empty
    # Known is per airport, the worst of what showed: METAR LIFR is not news.
    assert r.tick(dest="LIFR", dest_taf="LIFR").empty


def test_new_alert_pushes_once_then_stays_quiet():
    r = Replay()
    d = r.tick(dest="IFR")
    assert alerted(d) == ["metar:ZZDS"] and d.reasons["metar:ZZDS"] == "known=MVFR ratchet=-"
    assert r.tick(dest="IFR").empty
    assert r.tick(dest="IFR").empty
    assert r.state.metar_pushed == {"ZZDS": "IFR"} and r.state.active == {}


def test_two_changes_on_one_tick_are_one_push():
    r = Replay()
    d = r.tick(dest="IFR", dep="IFR")
    assert sorted(alerted(d)) == ["metar:ZZDP", "metar:ZZDS"]
    payload = build_payload(_flight_row(), d, tick_at=r.now)
    assert payload["aps"]["alert"]["title"] == "ZZDP → ZZDS · 2 changes"
    lines = payload["aps"]["alert"]["body"].split("\n")
    assert len(lines) == 2
    assert "ZZDS METAR: VFR → IFR (destination) · METAR 08:20Z" in lines
    assert payload["type"] == "live_alert"
    assert sorted(payload["keys"]) == ["metar:ZZDP", "metar:ZZDS"]
    assert payload["aps"]["thread-id"] == "zz-flight"
    assert payload["aps"]["interruption-level"] == "time-sensitive"
    assert payload["flight_id"] == "zz-flight"
    assert payload["tick_at"] == r.now.isoformat()
    assert "badge" not in payload["aps"]
    assert "apns-collapse-id" not in json.dumps(payload)


def test_mvfr_never_pushes():
    r = Replay()
    d = r.tick(dest="MVFR", dest_taf="MVFR", dep="MVFR")
    assert d.empty
    assert set(suppressed(d).values()) == {"mvfr"}


def test_route_and_alternate_never_push_for_category():
    c = _change("metar:ZZAL", kind="metar_category", source="METAR", role="alternate", new=True,
                icao="ZZAL", from_value="VFR", to_value="LIFR")
    seeded = LivePushState(started_at=T0)
    d = decide(LiveChanges(computed_at=T0, changes=[c], evaluated=["metar:ZZAL"]), seeded,
               now=T0, started=True)
    assert d.empty and suppressed(d) == {"metar:ZZAL": "role=alternate"}


def test_briefed_prob40_lifr_then_ifr_does_not_push():
    """The briefing already said LIFR (a TAF PROB40) at the destination: an
    IFR METAR or an IFR TAF is not worse than what the pilot knows."""
    r = Replay(base=base_obs(dest_taf="LIFR"), first={"dest_taf": "LIFR"})
    assert r.tick(dest="IFR", dest_taf="LIFR").empty
    assert r.tick(dest="IFR", dest_taf="IFR").empty


def test_metar_ifr_mvfr_ifr_pushes_once():
    r = Replay()
    assert alerted(r.tick(dest="IFR")) == ["metar:ZZDS"]
    assert r.tick(dest="MVFR").empty
    assert r.tick(dest="MVFR").empty
    d = r.tick(dest="IFR")
    assert d.empty


def test_metar_ifr_then_lifr_pushes_twice():
    r = Replay()
    assert alerted(r.tick(dest="IFR")) == ["metar:ZZDS"]
    d = r.tick(dest="LIFR")
    assert alerted(d) == ["metar:ZZDS"] and d.reasons["metar:ZZDS"] == "known=MVFR ratchet=IFR"


def test_taf_lifr_pushed_then_metar_lifr_pushes():
    """Observed weather confirming an earlier forecast push: happening now."""
    r = Replay()
    assert alerted(r.tick(dest_taf="LIFR")) == ["taf:ZZDS"]
    assert alerted(r.tick(dest="LIFR", dest_taf="LIFR")) == ["metar:ZZDS"]


def test_metar_lifr_pushed_then_taf_lifr_is_silent():
    """A TAF catching up with the observed weather is not news."""
    r = Replay()
    assert alerted(r.tick(dest="LIFR")) == ["metar:ZZDS"]
    d = r.tick(dest="LIFR", dest_taf="LIFR")
    assert d.empty and suppressed(d) == {"taf:ZZDS": "ratchet=LIFR"}


def test_no_clear_pushes_for_category_keys():
    r = Replay()
    r.tick(dest="IFR", dest_taf="IFR")
    for _ in range(4):
        d = r.tick(dest="VFR")
        assert d.empty and d.clears == []
    assert r.state.rearmed == {}


def test_old_category_entries_are_dropped_without_a_clear():
    """A shadow-mode memory from before the rule: no "Cleared" ever."""
    state = _active("metar:ZZDS", kind="metar_category", icao="ZZDS", to_value="IFR")
    state.active["metar:ZZDS"].clear_ticks = CLEAR_SUSTAIN_TICKS - 1
    d = decide(LiveChanges(computed_at=T0, changes=[], evaluated=["metar:ZZDS"]), state, now=T0)
    assert d.empty and d.state.active == {}


def test_new_pack_after_start_keeps_the_ratchet():
    """A refresh after an IFR push: the new briefing has a VFR baseline
    again, but an IFR METAR must not push twice in one flight."""
    r = Replay()
    assert alerted(r.tick(dest="IFR")) == ["metar:ZZDS"]
    r.new_pack(base_obs())
    assert r.tick(dest="MVFR").empty      # the new pack's first tick: re-raises known
    assert r.state.known_pack == r.pack
    d = r.tick(dest="IFR")
    assert d.empty and suppressed(d) == {"metar:ZZDS": "ratchet=IFR"}
    assert alerted(r.tick(dest="LIFR")) == ["metar:ZZDS"]


def test_new_pack_raises_the_known_level_with_what_shows():
    r = Replay()
    r.new_pack(base_obs(dest_taf="IFR"))
    assert r.tick(dest_taf="IFR").empty
    assert r.state.known == {"ZZDS": "IFR"}
    assert r.tick(dest="IFR", dest_taf="IFR").empty


def test_carried_over_resets_the_row_memory():
    state = _active("sigmet:LFMM|T01")
    state.rearmed = {"wind:ZZDS": T0}
    state.retry = {"sigmet:LFMM|T02": T0}
    state.started_at, state.known, state.metar_pushed = T0, {"ZZDS": "IFR"}, {"ZZDS": "IFR"}
    after = state.carried_over()
    assert after.active == {} and after.rearmed == {} and after.retry == {}
    assert after.started_at == T0 and after.known == {"ZZDS": "IFR"}
    assert after.metar_pushed == {"ZZDS": "IFR"}


def test_departure_row_after_take_off_does_not_push():
    row = _change("wind:ZZDP", kind="metar_wind", source="METAR", role="departure", new=True,
                  icao="ZZDP", to_value="red")
    d = decide(LiveChanges(computed_at=T0, changes=[row], evaluated=["wind:ZZDP"]),
               LivePushState(started_at=T0), now=T0, started=True, left_departure=True)
    assert d.empty and suppressed(d) == {"wind:ZZDP": "after_takeoff"}


def test_skipped_category_push_still_advances_the_ratchet():
    """Muted (or pref off) when IFR arrived: unmuting later must not push it."""
    r = Replay()
    d = r.tick(dest="IFR", pushed=False)
    assert alerted(d) == ["metar:ZZDS"]
    assert r.state.metar_pushed == {"ZZDS": "IFR"} and r.state.retry == {}
    assert r.tick(dest="IFR").empty


def test_failed_category_push_is_retried():
    r = Replay()
    assert alerted(r.tick(dest="IFR", pushed=False, failed=True)) == ["metar:ZZDS"]
    d = r.tick(dest="IFR")
    assert alerted(d) == ["metar:ZZDS"] and d.retried == {"metar:ZZDS"}
    assert r.state.retry == {}
    assert r.tick(dest="IFR").empty


# --- Storms over the top (#968) ------------------------------------------------------


def _storm_layer(top_fl, truncated=False):
    storm = LiveStorm(id="zz1", cell_ids=["c1"], lat=50.0, lon=1.0, peak_dbz=44.0, top_fl=top_fl,
                      truncated=truncated, along_nm=40.0, offtrack_nm=4.0, cross_nm=4.0)
    storms = LiveStorms(status="available", corridor_nm=30.0, route_nm=150.0, storms=[storm])
    return LiveLayer(flight_id="zz-flight", pack_timestamp="p", pack_dir_name="p", storms=storms)


def _storm_row(role="route"):
    return _change("storm:zz1", kind="storm", source="RADAR", role=role, new=True,
                   message="Storm 4 NM off track", storm_ids=["zz1"], alert_storm_ids=["zz1"])


@pytest.mark.parametrize("top_fl, truncated, role, cruise, pushes", [
    (96, False, "route", 25000, False),        # EDDC: FL096 tops, FL250 cruise
    (96, False, "route", 13000, True),         # under 5000 ft clearance
    (96, True, "route", 25000, True),          # truncated tops: unknown
    (None, False, "route", 25000, True),       # no tops
    (96, False, "destination", 25000, True),   # descends through it
    (96, False, "route", None, True),          # no cruise known
])
def test_storm_tops_check(top_fl, truncated, role, cruise, pushes):
    changes = LiveChanges(computed_at=T0, changes=[_storm_row(role)], evaluated=["storm:"])
    d = decide(changes, None, now=T0, layer=_storm_layer(top_fl, truncated), cruise_ft=cruise)
    assert (alerted(d) == ["storm:zz1"]) is pushes
    if not pushes:
        assert suppressed(d) == {"storm:zz1": "tops=FL096<FL250"}


# --- Cleared, sustain, re-arm (non-category keys, §47) ---------------------------


def test_clear_needs_two_evaluated_ticks_then_rearms():
    """The §45 conflict, end to end: after a pushed clear, a return to red
    wind must push again even though the classifier keeps it quiet on screen."""
    r = Replay()
    assert alerted(r.tick(dest_wind="red")) == ["wind:ZZDS"]
    # Back to green: one tick is not enough (flicker).
    assert r.tick().empty
    d = r.tick()
    assert cleared(d) == ["wind:ZZDS"]
    line = d.clears[0][1]
    assert line.startswith("Cleared: ZZDS wind no longer red (destination)")
    assert r.state.active == {} and "wind:ZZDS" in r.state.rearmed
    assert r.tick().empty
    d = r.tick(dest_wind="red")
    assert [c.new_alert for c in d.alerts] == [False]
    assert alerted(d) == ["wind:ZZDS"] and d.rearmed == {"wind:ZZDS"}
    assert r.state.rearms_fired == 1 and r.state.clears_pushed == 1
    assert "wind:ZZDS" in r.state.active and "wind:ZZDS" not in r.state.rearmed


def test_flicker_resets_the_sustain_counter():
    r = Replay()
    r.tick(dest_wind="red")
    assert r.tick().empty                    # clear tick 1
    assert r.tick(dest_wind="red").empty     # back: §45 quiet, counter reset, no re-arm yet
    assert r.tick().empty                    # clear tick 1 again
    assert cleared(r.tick()) == ["wind:ZZDS"]


def test_no_other_section45_behaviour_changes():
    """The re-arm lives in the push memory only: the classifier's own
    memory still holds the worst level alerted."""
    r = Replay()
    r.tick(dest_wind="red")
    r.tick()
    r.tick()
    assert r.memory.alerted["wind:ZZDS"] == "red"


def test_departure_after_take_off_is_not_cleared():
    """After take-off the departure airport is no longer evaluated: its rows
    go, but that is not "cleared"."""
    r = Replay()
    assert "wind:ZZDP" in alerted(r.tick(dep_wind="red"))
    for _ in range(3):
        assert r.tick(dep_wind="red", departed=True).empty
    assert "wind:ZZDP" in r.state.active


def test_skipped_ticks_do_not_count():
    r = Replay()
    r.tick(dest_wind="red")
    changes = LiveChanges(computed_at=r.now, changes=[], evaluated=None)
    for _ in range(3):
        d = decide(changes, r.state, now=r.now)
        r.state = next_state(d, pushed=True, now=r.now)
        assert d.empty
    assert r.state.active["wind:ZZDS"].clear_ticks == 0


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
    d = r.tick(dest_wind="red", pushed=False)
    assert alerted(d) == ["wind:ZZDS"] and r.state.active == {}
    st = _active("wind:ZZDS", kind="metar_wind", icao="ZZDS", to_value="red")
    st.active["wind:ZZDS"].clear_ticks = CLEAR_SUSTAIN_TICKS - 1
    d = decide(LiveChanges(computed_at=T0, changes=[], evaluated=["wind:ZZDS"]), st, now=T0)
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
    from weatherbrief.notify.push import PushDevice

    reason, devices = skip_reason(db_session, eligible, trigger="tick", started=True)
    assert reason is None and devices == [PushDevice("zz-token", "sandbox", "ios")]


def test_live_alerts_default_on_without_the_key(db_session, dev_user):
    from weatherbrief.api.preferences import load_notify_prefs

    assert load_notify_prefs(db_session, dev_user)["notify_live_alerts"] is True


def test_auto_refresh_alone_is_no_longer_consent(db_session, eligible):
    """#968: only Start consents to live alerts; auto-refresh is on here."""
    assert eligible.auto_refresh
    assert skip_reason(db_session, eligible, trigger="tick")[0] == "no_consent"
    eligible.auto_refresh = False
    assert skip_reason(db_session, eligible, trigger="tick", started=True)[0] is None


@pytest.mark.parametrize("setup, reason", [
    (lambda db, u, f: setattr(f, "notify_override", "mute"), "muted"),
    (lambda db, u, f: _prefs(db, u, notify_live_alerts=False), "pref_off"),
    (lambda db, u, f: _prefs(db, u, notify_push=False), "push_off"),
    (lambda db, u, f: db.query(DeviceTokenRow).delete(), "no_device"),
])
def test_ineligible(db_session, dev_user, eligible, setup, reason):
    """Started, but every opt-out still applies (#968)."""
    setup(db_session, dev_user, eligible)
    db_session.flush()
    assert skip_reason(db_session, eligible, trigger="tick", started=True)[0] == reason


def test_user_trigger_never_pushes(db_session, eligible):
    assert skip_reason(db_session, eligible, trigger="user", started=True)[0] == "user_trigger"


def test_scope_off_does_not_silence_live_alerts(db_session, dev_user, eligible):
    _prefs(db_session, dev_user, notify_scope="off", notify_change_only=True)
    assert skip_reason(db_session, eligible, trigger="tick", started=True)[0] is None


def test_trip_flags_apply(db_session, dev_user):
    _prefs(db_session, dev_user, notify_push=True)
    _device(db_session, dev_user)
    db_session.add(FlightTripRow(id="zztrip", user_id=dev_user, name="ZZ trip", auto_refresh=True))
    db_session.flush()
    leg = _persist_flight(db_session, dev_user, auto=False, trip="zztrip")
    # The trip's auto-refresh is no consent on its own (#968); its mute applies.
    assert skip_reason(db_session, leg, trigger="tick")[0] == "no_consent"
    assert skip_reason(db_session, leg, trigger="tick", started=True)[0] is None
    db_session.get(FlightTripRow, "zztrip").notify_override = "mute"
    db_session.flush()
    assert skip_reason(db_session, leg, trigger="tick", started=True)[0] == "muted"


# --- The sink: shadow mode, sending, the ledger -------------------------------------


@pytest.fixture(autouse=True)
def _started(request, monkeypatch):
    """The sink tests' flight has pressed Start (a fix the layer files do not
    hold); ``@pytest.mark.not_started`` opts out."""
    started = request.node.get_closest_marker("not_started") is None
    monkeypatch.setattr(live_alerts, "_start_and_takeoff", lambda *a: (started, False))


def _stored_layer(tmp_path, changes):
    pack_dir = tmp_path / DEV / "zz-flight" / "p1"
    pack_dir.mkdir(parents=True)
    pack_ts = "2026-10-10T06:00:00+00:00"
    layer = LiveLayer(
        flight_id="zz-flight", pack_timestamp=pack_ts,
        pack_dir_name="p1", live_updated_at=T0 + TICK, changes=changes,
        # Seeded on an earlier tick after Start (all VFR then).
        push_state=LivePushState(started_at=T0, known_pack=pack_ts),
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
    assert "rule=['metar:ZZDS:known=MVFR ratchet=-']" in caplog.text
    # The memory advances as if sent, so a shadow run measures the ratchet.
    stored = load_live(pack_dir.parent)
    assert stored.push_state.metar_pushed == {"ZZDS": "IFR"}


@pytest.mark.not_started
def test_no_start_is_logged_and_never_pushes(db_session, eligible, tmp_path, caplog):
    pack_dir, layer = _stored_layer(tmp_path, _alert_changes())
    with patch("weatherbrief.notify.push._dispatch") as dispatch, caplog.at_level(logging.INFO):
        outcome = notify_live_alerts(db_session, eligible, layer, pack_dir=pack_dir, now=T0 + TICK)
    assert outcome == "none"
    dispatch.assert_not_called()
    assert "LIVE_PUSH_SUPPRESSED flight=zz-flight started=False rows=['metar:ZZDS:not_started']" in caplog.text


def test_mute_after_start_skips(db_session, eligible, tmp_path):
    eligible.notify_override = "mute"
    db_session.flush()
    pack_dir, layer = _stored_layer(tmp_path, _alert_changes())
    assert notify_live_alerts(db_session, eligible, layer, pack_dir=pack_dir,
                              now=T0 + TICK) == "skipped:muted"


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
    assert kwargs["expires_at"] == now + timedelta(minutes=30)
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
    assert stored.push_state.retry == {"metar:ZZDS": DEP}
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


# --- Review follow-ups: failed-push retry, shadow -> sending ---------------------


def test_failed_push_is_retried_while_still_an_alert():
    row = _change("wind:ZZDS", kind="metar_wind", role="destination", new=True,
                  icao="ZZDS", to_value="red")
    d = decide(LiveChanges(computed_at=T0, changes=[row], evaluated=["wind:ZZDS"]), None, now=T0)
    state = next_state(d, pushed=False, failed=True, now=T0)
    assert state.retry == {"wind:ZZDS": T0} and state.active == {}
    # Next tick: new_alert is spent (§45 keeps it quiet), but the row is still alert-tier.
    again = row.model_copy(update={"new_alert": False})
    d = decide(LiveChanges(computed_at=T0, changes=[again], evaluated=["wind:ZZDS"]),
               state, now=T0 + TICK)
    assert alerted(d) == ["wind:ZZDS"] and d.retried == {"wind:ZZDS"}
    state = next_state(d, pushed=True, now=T0 + TICK)
    assert state.retry == {} and "wind:ZZDS" in state.active


def test_failed_push_retry_expires_with_the_push():
    row = _change("metar:ZZDS", kind="metar_category", role="destination", icao="ZZDS",
                  from_value="VFR", to_value="IFR")
    state = LivePushState(retry={"metar:ZZDS": T0}, started_at=T0, metar_pushed={"ZZDS": "IFR"})
    late = T0 + live_alerts.PUSH_TTL
    d = decide(LiveChanges(computed_at=late, changes=[row], evaluated=["metar:ZZDS"]),
               state, now=late, started=True)
    assert d.empty and d.state.retry == {}


def test_skipped_push_is_not_retried():
    row = _change("wind:ZZDS", kind="metar_wind", role="destination", new=True)
    d = decide(LiveChanges(computed_at=T0, changes=[row], evaluated=["wind:ZZDS"]), None, now=T0)
    assert next_state(d, pushed=False, now=T0).retry == {}


def test_clear_of_a_shadow_alert_is_dropped_once_sending():
    """Flipping WB_LIVE_PUSH_SEND mid-flight must not push "Cleared" for an
    alert the pilot never received."""
    state = _active("wind:ZZDS", kind="metar_wind", icao="ZZDS", to_value="red", shadow=True)
    state.active["wind:ZZDS"].clear_ticks = CLEAR_SUSTAIN_TICKS - 1
    changes = LiveChanges(computed_at=T0, changes=[], evaluated=["wind:ZZDS"])
    d = decide(changes, state, now=T0, sending=True)
    assert d.clears == [] and d.silent_clears == ["wind:ZZDS"]
    after = next_state(d, pushed=False, now=T0)
    assert after.active == {} and after.rearmed == {}
    # Still in shadow mode, the same clear is measured as usual.
    d = decide(changes, state, now=T0, sending=False)
    assert cleared(d) == ["wind:ZZDS"]


def test_shadow_flag_follows_the_mode():
    row = _change("wind:ZZDS", kind="metar_wind", role="destination", new=True)
    d = decide(LiveChanges(computed_at=T0, changes=[row], evaluated=["wind:ZZDS"]), None, now=T0)
    assert next_state(d, pushed=True, now=T0, shadow=True).active["wind:ZZDS"].shadow
    assert not next_state(d, pushed=True, now=T0, shadow=False).active["wind:ZZDS"].shadow
