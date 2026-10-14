"""Manual take-off / landed and position sharing in the live layer (#760).

The fusion itself is in tests/test_flight_progress.py; this file checks that
every consumer moves with it (ribbon, classifier cut-offs, glance, highlight
gate, storms, TAF-at-ETA, the live window, push expiry) and that viewers get
what the owner shares.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

from live_scenario_replay import load_scenario, replay
from weatherbrief.analysis.flight_progress import ProgressEvent
from weatherbrief.models.observations import RefreshDelta
from weatherbrief.models.live import (
    LiveChange,
    LiveChanges,
    LiveGlance,
    LiveGlanceLine,
    LiveHighlight,
    LiveLayerResponse,
    LiveProgress,
    LiveRibbon,
    LiveStorm,
    LiveStorms,
    RibbonSegment,
    RibbonStation,
    RibbonWaypoint,
    StormEstimate,
)
from weatherbrief.tasks.live_layer import (
    load_progress_events,
    record_progress_event,
    redact_live,
    redact_summary,
    sharing_for_viewer,
    strip_eta_text,
)

LELL_LEMI = "2026-10-02_lell_lemi"
DEP = datetime(2026, 10, 2, 8, 0, tzinfo=timezone.utc)  # the scenario's planned departure, 2 h flight


def _record(kind: str, at: datetime | None, recorded: datetime) -> dict:
    return ProgressEvent(kind=kind, at=at, recorded_at=recorded).history_record()


def _at(ticks, hhmm):
    return next(t for t in ticks if t.at.strftime("%H:%M") == hhmm)


@pytest.fixture(scope="module")
def plan_and_late(tmp_path_factory):
    """The LELL → LEMI morning twice: on the plan, and with a take-off 40 min
    late entered at 08:41."""
    plan = replay(load_scenario(LELL_LEMI), tmp_path_factory.mktemp("plan") / "u" / "flight")
    scenario = load_scenario(LELL_LEMI)
    scenario["inputs"]["progress_events"] = [
        _record("takeoff", DEP + timedelta(minutes=40), DEP + timedelta(minutes=41)),
    ]
    late = replay(scenario, tmp_path_factory.mktemp("late") / "u" / "flight")
    return plan, late


# --- The scenario: a take-off 40 min late --------------------------------------


def test_no_events_is_the_plan(plan_and_late):
    plan, _ = plan_and_late
    for t in plan:
        assert t.layer.progress.source == "plan"
        assert t.layer.progress.departed_at is None and t.layer.progress.delay_min is None
    p = _at(plan, "09:00").layer.progress
    assert p.along_nm == _at(plan, "09:00").layer.ribbon.flown_nm
    assert p.eta_destination == DEP + timedelta(hours=2)


def test_ticks_before_the_entry_are_unchanged(plan_and_late):
    """Up to the tick the take-off was entered, both replays are identical:
    an entry never rewrites what was already shown."""
    plan, late = plan_and_late
    for a, b in zip(plan, late):
        if a.at >= DEP + timedelta(minutes=41):
            break
        assert a.layer.model_dump(exclude={"progress"}) == b.layer.model_dump(exclude={"progress"})


def test_late_takeoff_shifts_ribbon_etas_and_arrival(plan_and_late):
    plan, late = plan_and_late
    p, l = _at(plan, "09:00").layer, _at(late, "09:00").layer
    assert l.progress.source == "plan+takeoff" and l.progress.delay_min == 40
    assert l.progress.departed_at == DEP + timedelta(minutes=40)
    assert l.ribbon.arrival_at == p.ribbon.arrival_at + timedelta(minutes=40)
    assert l.progress.eta_destination == l.ribbon.arrival_at
    for wp_plan, wp_late in zip(p.ribbon.waypoints, l.ribbon.waypoints):
        assert wp_late.eta == wp_plan.eta + timedelta(minutes=40)
    for st_plan, st_late in zip(p.ribbon.stations, l.ribbon.stations):
        if st_plan.eta is not None:
            assert st_late.eta == st_plan.eta + timedelta(minutes=40)
    # The ribbon's departure_at stays the planned one; the take-off is on progress.
    assert l.ribbon.departure_at == p.ribbon.departure_at == DEP


def test_late_takeoff_shifts_the_position_by_40_min(plan_and_late):
    """Distance flown, and with it which airports are passed, at 09:40 late
    is what the plan had at 09:00."""
    plan, late = plan_and_late
    assert _at(late, "09:40").layer.ribbon.flown_nm == _at(plan, "09:00").layer.ribbon.flown_nm
    assert _at(late, "09:40").layer.progress.along_nm == _at(plan, "09:00").layer.progress.along_nm
    # At 08:50 the late flight (off at 08:40) has flown what the plan had at 08:10.
    assert _at(late, "08:50").layer.ribbon.flown_nm == _at(plan, "08:10").layer.ribbon.flown_nm


def test_late_takeoff_moves_the_after_arrival_cutoff(plan_and_late):
    """At 10:20 the plan has landed (arrival 10:00): the arrival glance line
    is passed. The late flight lands at 10:40, so it is not."""
    plan, late = plan_and_late

    def arrival_line(layer):
        return next(ln for ln in layer.glance.lines if ln.phase == "arrival")

    assert arrival_line(_at(plan, "10:20").layer).passed is True
    assert arrival_line(_at(late, "10:20").layer).passed is False
    assert arrival_line(_at(late, "10:50").layer).passed is True


def test_history_keeps_the_entry_and_inputs_from_history_reads_it(plan_and_late, tmp_path):
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))
    from build_live_scenario import inputs_from_history

    _, late = plan_and_late
    flight_dir = late[-1].pack_dir.parent
    events = load_progress_events(flight_dir)
    assert [(e.kind, e.at) for e in events] == [("takeoff", DEP + timedelta(minutes=40))]
    inputs = inputs_from_history(flight_dir)
    assert [r["event"] for r in inputs["progress_events"]] == ["takeoff"]
    # The entry is not a tick: the window is the ticks'.
    assert inputs["window"]["start"] == "2026-10-02T05:00:00+00:00"


# --- Storm abeam times and the estimate's source --------------------------------


def test_storm_abeam_eta_moves_with_the_takeoff():
    import test_live_storms as ts

    from weatherbrief.analysis.flight_progress import FlightProgress
    from weatherbrief.observed.storms import CellFrames, build_storms

    now = ts.DEP + timedelta(minutes=50)
    frames = CellFrames("available", newest=ts.frame(now - timedelta(minutes=5), [ts.cell("a", along=120)]))
    plan = FlightProgress(ts.TRACK, ts.DEP, 1.5, now)
    late = FlightProgress(ts.TRACK, ts.DEP, 1.5, now, (
        ProgressEvent("takeoff", ts.DEP + timedelta(minutes=40), ts.DEP + timedelta(minutes=41)),
    ))
    a = build_storms(frames, plan, end_icaos=("ZZDP", "ZZDS")).storms[0]
    b = build_storms(frames, late, end_icaos=("ZZDP", "ZZDS")).storms[0]
    assert b.abeam_eta == a.abeam_eta + timedelta(minutes=40)


def test_estimate_records_carry_the_progress_source():
    from weatherbrief.models.live import LiveLayer
    from weatherbrief.tasks.live_layer import _estimate_records

    t = datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc)
    layer = LiveLayer(
        flight_id="f", pack_timestamp="p", pack_dir_name="p",
        progress=LiveProgress(source="plan+takeoff", as_of=t),
        storms=LiveStorms(status="available", frame_time=t, corridor_nm=30.0, storms=[LiveStorm(
            id="s1", cell_ids=["s1"], lat=0, lon=0, peak_dbz=45, along_nm=10, offtrack_nm=2, cross_nm=2,
            estimate=StormEstimate(cpa_nm=1.0, cpa_time=t, horizon_min=10),
        )]),
    )
    (rec,) = _estimate_records({"tick_at": t.isoformat()}, [], layer)
    assert rec["progress_source"] == "plan+takeoff"


# --- Landed: the highlight stops ------------------------------------------------


def test_landed_reads_as_arrived_for_the_highlight_gate(plan_and_late, tmp_path):
    """A landing at 09:30 (planned 10:00) puts the flight at the end of the
    route on the next tick, so the #706 gate reads "arrived" and no highlight
    is generated."""
    from weatherbrief.tasks import live_highlight as lh

    scenario = load_scenario(LELL_LEMI)
    scenario["inputs"]["progress_events"] = [
        _record("takeoff", DEP, DEP + timedelta(minutes=1)),
        _record("landed", DEP + timedelta(minutes=90), DEP + timedelta(minutes=91)),
    ]
    scenario["inputs"]["window"]["end"] = "2026-10-02T09:40:00+00:00"
    ticks = replay(scenario, tmp_path / "u" / "flight")
    layer = _at(ticks, "09:40").layer
    assert layer.progress.landed_at == DEP + timedelta(minutes=90)
    assert layer.ribbon.flown_nm == layer.ribbon.route_nm
    _, gate = lh.facts_and_gate(layer.model_dump(mode="json"))
    assert lh.arrived(gate)
    # On the plan the same tick is still en route.
    plan, _ = plan_and_late
    _, plan_gate = lh.facts_and_gate(_at(plan, "09:40").layer.model_dump(mode="json"))
    assert not lh.arrived(plan_gate)


# --- TAF at ETA ------------------------------------------------------------------


def test_realtime_refresh_reads_tafs_at_the_effective_departure(tmp_path, monkeypatch):
    from weatherbrief.models.observations import RouteObservations
    from weatherbrief.tasks import route_weather as rw

    pack_dir = tmp_path / "u" / "flight" / "pack"
    pack_dir.mkdir(parents=True)
    route = load_scenario(LELL_LEMI)["inputs"]["route"]
    (pack_dir / "briefing.json").write_text(json.dumps({
        "route": route, "departure_time": DEP.isoformat(), "days_out": 0,
    }))
    seen = []

    def fake_weather(**kw):
        seen.append(kw["target_time"])
        return RouteObservations(corridor_nm=30.0, fetch_time=DEP, airports_found=0,
                                 airports_with_metar=0, airports_with_taf=0)

    monkeypatch.setattr(rw, "run_route_weather", fake_weather)
    monkeypatch.setattr(rw, "run_observation_comparison", lambda observations, **kw: observations)
    monkeypatch.setattr(rw, "run_route_sigmets", lambda **kw: (_ for _ in ()).throw(RuntimeError("off")))

    rw.run_realtime_refresh(pack_dir, str(tmp_path / "airports.db"), persist=False)
    record_progress_event(pack_dir.parent, ProgressEvent(
        "takeoff", DEP + timedelta(minutes=40), DEP + timedelta(minutes=41),
    ))
    rw.run_realtime_refresh(pack_dir, str(tmp_path / "airports.db"), persist=False)
    assert seen == [DEP, DEP + timedelta(minutes=40)]


# --- The live window and push expiry --------------------------------------------


def _ev(kind, at_min, rec_min):
    return ProgressEvent(kind, DEP + timedelta(minutes=at_min) if at_min is not None else None,
                         DEP + timedelta(minutes=rec_min))


def test_window_end_follows_the_entries():
    from weatherbrief.tasks.live_tick import LANDED_GRACE, in_live_window, live_window_end

    now = DEP + timedelta(hours=3, minutes=20)
    assert live_window_end(DEP, 2.0, 1.0, (), now) == DEP + timedelta(hours=3)
    late = (_ev("takeoff", 40, 41),)
    assert live_window_end(DEP, 2.0, 1.0, late, now) == DEP + timedelta(hours=3, minutes=40)
    assert not in_live_window(DEP, 2.0, now, 3.0, 1.0)
    assert in_live_window(DEP, 2.0, now, 3.0, 1.0, late)
    # A landing closes it one grace period after it was known.
    landed = (_ev("takeoff", 0, 1), _ev("landed", 90, 95))
    assert live_window_end(DEP, 2.0, 1.0, landed, now) == DEP + timedelta(minutes=95) + LANDED_GRACE
    assert not in_live_window(DEP, 2.0, DEP + timedelta(minutes=115), 3.0, 1.0, landed)
    # Entered long after the fact: the grace counts from the entry.
    entered_late = (_ev("landed", 90, 200),)
    assert live_window_end(DEP, 2.0, 1.0, entered_late, now) == DEP + timedelta(minutes=200) + LANDED_GRACE
    # Before it was entered, the window was the plan's.
    assert live_window_end(DEP, 2.0, 1.0, entered_late, DEP + timedelta(minutes=150)) == DEP + timedelta(hours=3)


def test_find_live_flights_reads_the_entries(db_session, dev_user, tmp_path):
    import test_live_tick as tt

    from weatherbrief.tasks.live_tick import find_live_flights

    now = tt.NOW

    def flight(fid, departure):
        pack_dir = tmp_path / "packs" / dev_user / fid / "pack"
        return tt._flight(db_session, dev_user, fid, departure, artifact_path=str(pack_dir)), pack_dir.parent

    # Planned 2 h flight departed 3 h 20 ago: outside the plan's window...
    late, late_dir = flight("zz-late", now - timedelta(hours=3, minutes=20))
    landed, landed_dir = flight("zz-landed", now - timedelta(hours=1))
    record_progress_event(late_dir, ProgressEvent(
        "takeoff", late.departure_time + timedelta(minutes=40), late.departure_time + timedelta(minutes=41),
    ))
    # ...and one that landed early, 30 min ago.
    record_progress_event(landed_dir, ProgressEvent(
        "landed", now - timedelta(minutes=30), now - timedelta(minutes=29),
    ))
    assert {f.id for f in find_live_flights(db_session, now)} == {"zz-late"}


def test_push_expiry_follows_the_entries():
    import test_live_alert_push as tp

    from weatherbrief.notify.live_alerts import push_expiry

    dep = tp.DEP
    now = dep + timedelta(hours=2, minutes=50)
    events = (ProgressEvent("takeoff", dep + timedelta(minutes=40), dep + timedelta(minutes=41)),)
    assert push_expiry(tp._flight_row(), now) == dep + timedelta(hours=3)
    assert push_expiry(tp._flight_row(), now, events) == now + timedelta(minutes=30)


# --- Position sharing ------------------------------------------------------------


T = datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc)


def _response() -> LiveLayerResponse:
    return LiveLayerResponse(
        flight_id="f", pack_timestamp="p",
        progress=LiveProgress(source="plan+takeoff", as_of=T, along_nm=40.0, route_nm=275.6,
                              lat=40.1, lon=1.2, departed_at=T, eta_destination=T, delay_min=40),
        storms=LiveStorms(status="available", corridor_nm=30.0, storms=[LiveStorm(
            id="s1", cell_ids=["s1"], lat=0, lon=0, peak_dbz=45, along_nm=100, offtrack_nm=5, cross_nm=5,
            abeam_eta=T, minutes_to_abeam=30.0,
            estimate=StormEstimate(cpa_nm=1.0, cpa_time=T, horizon_min=10),
        )]),
        ribbon=LiveRibbon(
            route_nm=275.6, flown_nm=40.0, departure_at=T, arrival_at=T, segment_nm=10, radar_radius_nm=5,
            waypoints=[RibbonWaypoint(icao="LELL", along_nm=0, eta=T)],
            segments=[RibbonSegment(index=0, from_nm=0, to_nm=10, eta_from=T, eta_to=T)],
            stations=[RibbonStation(icao="LEMI", role="destination", eta=T)],
        ),
        glance=LiveGlance(
            as_of=T, headline="Observed 09:00Z · worse", comparison="worse",
            lines=[LiveGlanceLine(phase="enroute", text="2 storms ahead; nearest 5 NM right at 100 NM ~09:30Z (45 dBZ), steady")],
            highlight=LiveHighlight(text="Storm abeam ~09:30Z.", model="m", facts_hash="h", generated_at=T),
        ),
        changes=LiveChanges(computed_at=T, changes=[LiveChange(
            key="storm:s1", kind="storm", tier="alert", direction="worse", source="RADAR", role="route",
            message="Storm (45 dBZ) 5 NM right of track at 100 NM, abeam ~09:30Z, closing",
        )], recently_cleared=[LiveChange(
            key="storms:later", kind="storm", tier="highlight", direction="worse", source="RADAR", role="route",
            message="3 storms later on the route 150–200 NM, reached ~09:50Z–10:10Z",
        )]),
        last_refresh_delta=RefreshDelta(worsened=True, messages=["Storm 5 NM right at 100 NM, abeam ~09:30Z"]),
    )


def test_sharing_for_viewer():
    assert sharing_for_viewer("owner", "owner", "none") == "full"
    assert sharing_for_viewer("owner", "viewer", "none") == "none"
    assert sharing_for_viewer("owner", "viewer", "full") == "full"
    assert sharing_for_viewer("owner", "viewer", "bogus") == "along_route"
    assert sharing_for_viewer("owner", None, "full") == "full"
    assert sharing_for_viewer("owner", None, None) == "along_route"


def test_strip_eta_text():
    assert strip_eta_text("x at 100 NM, abeam ~09:30Z, closing") == "x at 100 NM, closing"
    assert strip_eta_text("later 150–200 NM, reached ~09:50Z–10:10Z") == "later 150–200 NM"
    assert strip_eta_text("nearest 5 NM right at 100 NM ~09:30Z (45 dBZ)") == "nearest 5 NM right at 100 NM (45 dBZ)"
    assert strip_eta_text("dest storm, arrival ~10:00Z") == "dest storm"
    assert strip_eta_text("METAR 12:50Z") == "METAR 12:50Z"  # not an estimate


def test_full_is_untouched():
    resp = _response()
    assert redact_live(resp, "full") == resp


def test_along_route_drops_the_position_only():
    resp = _response()
    out = redact_live(resp, "along_route")
    assert out.progress.lat is None and out.progress.lon is None
    assert out.progress.along_nm == 40.0 and out.progress.eta_destination == T
    assert out.model_dump(exclude={"progress"}) == resp.model_dump(exclude={"progress"})
    assert resp.progress.lat == 40.1  # the input is not mutated


def test_none_strips_progress_etas_and_abeam_times():
    resp = _response()
    out = redact_live(resp, "none")
    assert out.progress is None
    st = out.storms.storms[0]
    assert st.abeam_eta is None and st.minutes_to_abeam is None and st.estimate is None
    r = out.ribbon
    assert r.flown_nm is None and r.arrival_at is None
    assert r.waypoints[0].eta is None and r.stations[0].eta is None
    assert r.segments[0].eta_from is None and r.segments[0].eta_to is None
    assert "~" not in out.glance.lines[0].text and "~" not in out.glance.highlight.text
    assert "~" not in out.changes.changes[0].message
    assert "~" not in out.changes.recently_cleared[0].message
    assert "~" not in out.last_refresh_delta.messages[0]
    assert resp.progress is not None and resp.storms.storms[0].abeam_eta == T


def test_snapshot_overlay_strips_times_for_none():
    from weatherbrief.models.live import LiveLayer
    from weatherbrief.tasks.live_layer import overlay_live

    resp = _response()
    layer = LiveLayer(flight_id="f", pack_timestamp="p", pack_dir_name="p", live_updated_at=T,
                      changes=resp.changes, last_refresh_delta=resp.last_refresh_delta)
    full = overlay_live({}, layer)
    assert "abeam ~09:30Z" in full["live_changes"]["changes"][0]["message"]
    for sharing in ("along_route", "full"):
        assert overlay_live({}, layer, sharing) == full
    none = overlay_live({}, layer, "none")
    assert "~" not in json.dumps(none["live_changes"]) and "~" not in json.dumps(none["last_refresh_delta"])
    assert "~" in layer.changes.changes[0].message


def test_summary_redaction():
    summary = {
        "progress": {"source": "plan", "lat": 1.0, "lon": 2.0, "along_nm": 3.0},
        "glance": {"lines": [{"phase": "enroute", "text": "at 100 NM ~09:30Z (45 dBZ)"}]},
        "highlight": {"text": "abeam ~09:30Z", "written_at": None},
        "changes": [{"message": "Storm, abeam ~09:30Z, closing"}],
        "recently_cleared": [{"message": "reached ~09:50Z"}],
    }
    assert redact_summary(summary, "full") is summary
    along = redact_summary(summary, "along_route")
    assert along["progress"] == {"source": "plan", "lat": None, "lon": None, "along_nm": 3.0}
    assert along["changes"] == summary["changes"]
    none = redact_summary(summary, "none")
    assert none["progress"] is None
    assert "~" not in json.dumps(none)
    assert summary["progress"]["lat"] == 1.0
    assert redact_summary(None, "none") is None


def test_none_leaves_no_eta_anywhere(plan_and_late):
    """Structural guard for the allow-by-default redaction (#775 review): on
    real ticks, under ``none`` no field named like an ETA or abeam time keeps
    a value and no "~HH:MMZ" estimate survives in any text. A new ETA field
    added without redaction fails here instead of leaking."""
    import re

    from live_scenario_replay import live_response

    estimate = re.compile(r"~\d{2}:\d{2}Z")

    iso = re.compile(r"^\d{4}-\d{2}-\d{2}T")
    # Position-bearing fields that must be gone, whatever their type.
    gone = {"progress", "flown_nm", "minutes_to_abeam", "estimate"}

    def walk(node, path=""):
        if isinstance(node, dict):
            for k, v in node.items():
                key = k.lower()
                if key in gone:
                    assert v is None, f"{path}.{k} = {v!r} survives under none"
                # A clock time under an ETA-like name (eta, eta_from,
                # abeam_eta, arrival_at…). TAF-at-ETA categories and the
                # plan-relative eta_hour_offset are not times and may stay.
                timed = bool({"eta", "abeam", "arrival"} & set(key.split("_")))
                if timed and isinstance(v, str) and iso.match(v):
                    raise AssertionError(f"{path}.{k} = {v!r} survives under none")
                walk(v, f"{path}.{k}")
        elif isinstance(node, list):
            for i, v in enumerate(node):
                walk(v, f"{path}[{i}]")
        elif isinstance(node, str):
            assert not estimate.search(node), f"{path}: {node!r}"

    _, late = plan_and_late
    checked = 0
    for t in late:
        body = LiveLayerResponse.model_validate(live_response(t))
        before = body.model_dump(mode="json")
        if not estimate.search(json.dumps(before)) and before["progress"] is None:
            continue
        walk(redact_live(body, "none").model_dump(mode="json"))
        # The guard itself bites: the unredacted body fails it.
        with pytest.raises(AssertionError):
            walk(before)
        checked += 1
    assert checked > 0
