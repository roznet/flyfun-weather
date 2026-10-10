"""Observed latency (#751): tick rows, delivery rows, the end-to-end join."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from sqlalchemy import select

from weatherbrief.db.models import BriefingPackRow, FlightRow, LiveDeliveryRow, LiveTickTimingRow
from weatherbrief.models.observations import (
    AirportObservation,
    RouteObservations,
    RouteSigmets,
    SigmetAlongRoute,
)
from weatherbrief.tasks import live_timing
from weatherbrief.tasks.live_layer import CommitTrace, commit_live_update
from weatherbrief.tasks.live_timing import (
    build_tick_row,
    latency_report,
    new_items,
    purge_old,
    record_delivery,
    write_tick_rows,
)

NOW = datetime(2026, 10, 1, 9, 0, tzinfo=timezone.utc)
ROUTE = {
    "name": "ZZ test",
    "waypoints": [
        {"icao": "ZZDP", "name": "Dep", "lat": 50.0, "lon": 1.0},
        {"icao": "ZZDS", "name": "Dest", "lat": 51.0, "lon": 2.0},
    ],
    "cruise_altitude_ft": 6000,
    "flight_duration_hours": 1.0,
}


@pytest.fixture
def db_engine():
    # Per test: the writers here commit.
    from conftest import make_app_engine

    engine = make_app_engine()
    yield engine
    engine.dispose()


@pytest.fixture(autouse=True)
def _fresh_cache():
    live_timing._reset_cache()
    yield
    live_timing._reset_cache()


def _obs(cat, t, *, fetch=None, icao="ZZDS", taf_issued=None):
    return RouteObservations(
        corridor_nm=30.0, fetch_time=fetch or t, airports_found=1, airports_with_metar=1,
        airports_with_taf=1 if taf_issued else 0,
        airports=[AirportObservation(
            icao=icao, distance_from_route_nm=0.0, nearest_waypoint_icao=icao,
            metar_flight_category=cat, metar_time=t, metar_report_type="METAR",
            metar_raw=f"METAR {icao} {t:%d%H%M}Z {cat}",
            taf_raw=f"TAF {icao} {taf_issued:%d%H%M}Z" if taf_issued else None,
            taf_issue_time=taf_issued,
        )],
    )


def _pack(tmp_path: Path, name="p1", base_t=None):
    pack_dir = tmp_path / "user" / "flight-zz" / name
    pack_dir.mkdir(parents=True)
    briefing = {
        "route": ROUTE,
        "departure_time": (NOW + timedelta(hours=2)).isoformat(),
        "days_out": 0,
        "route_observations": _obs("VFR", base_t or NOW - timedelta(hours=1)).model_dump(mode="json"),
    }
    (pack_dir / "briefing.json").write_text(json.dumps(briefing))
    return pack_dir, briefing


def _flight(db, user_id, fid="zz-flight"):
    db.add(FlightRow(
        id=fid, user_id=user_id, route_name="ZZDP-ZZDS",
        waypoints_json=json.dumps(["ZZDP", "ZZDS"]), departure_time=NOW + timedelta(hours=1),
        cruise_altitude_ft=6000, flight_ceiling_ft=12000, flight_duration_hours=1.0,
    ))
    db.flush()


# --- The commit trace ---------------------------------------------------------


def test_commit_fills_the_trace_with_the_version_and_what_was_new(tmp_path):
    pack, briefing = _pack(tmp_path)
    ts = "2026-10-01T05:00:00+00:00"
    first = CommitTrace()
    commit_live_update(pack, briefing_data=briefing, observations=_obs("VFR", NOW - timedelta(minutes=20)),
                       sigmets=None, observed=None, started_at=NOW, pack_timestamp=ts, now=NOW, trace=first)
    # The first write shows everything for the first time, however old: no items.
    assert first.records and new_items(first.records) == []

    later = NOW + timedelta(minutes=10)
    metar_t = later - timedelta(minutes=8)
    obs = _obs("IFR", metar_t, fetch=later - timedelta(minutes=1))
    trace = CommitTrace()
    layer = commit_live_update(pack, briefing_data=briefing, observations=obs, sigmets=None, observed=None,
                               started_at=later, pack_timestamp=ts, now=later, trace=trace)
    assert trace.committed_at == layer.live_updated_at == later
    items = new_items(trace.records, observations=obs)
    assert [i["report_at"] for i in items if i["kind"] == "metar"] == [metar_t.isoformat()]
    # The destination going IFR is an alert-tier change appearing this tick.
    assert [i["kind"] for i in items if i["kind"] == "alert"] == ["alert"]


def test_a_pack_switch_does_not_count_the_briefing_reports(tmp_path):
    """A full refresh mid-day records the new briefing's own reports with the
    pack record; they were shown with the briefing, not by this tick."""
    p1, b1 = _pack(tmp_path, "p1")
    commit_live_update(p1, briefing_data=b1, observations=_obs("VFR", NOW - timedelta(minutes=20)),
                       sigmets=None, observed=None, started_at=NOW, pack_timestamp="2026-10-01T05:00:00+00:00",
                       now=NOW)
    later = NOW + timedelta(minutes=30)
    p2, b2 = _pack(tmp_path, "p2", base_t=later - timedelta(minutes=15))
    obs = _obs("VFR", later - timedelta(minutes=15), fetch=later)  # same METAR the briefing has
    trace = CommitTrace()
    commit_live_update(p2, briefing_data=b2, observations=obs, sigmets=None, observed=None,
                       started_at=later, pack_timestamp="2026-10-01T09:15:00+00:00", now=later, trace=trace)
    assert any(r["type"] == "pack" for r in trace.records)
    assert [i for i in new_items(trace.records, observations=obs) if i["kind"] == "metar"] == []


def test_refused_commit_leaves_the_trace_empty(tmp_path):
    pack, briefing = _pack(tmp_path)
    commit_live_update(pack, briefing_data=briefing, observations=_obs("VFR", NOW), sigmets=None,
                       observed=None, started_at=NOW, pack_timestamp="2026-10-01T05:00:00+00:00", now=NOW)
    trace = CommitTrace()
    assert commit_live_update(
        pack, briefing_data=briefing, observations=_obs("VFR", NOW), sigmets=None, observed=None,
        started_at=NOW - timedelta(minutes=5), pack_timestamp="2026-10-01T05:00:00+00:00",
        now=NOW + timedelta(minutes=1), trace=trace,
    ) is None
    assert trace.committed_at is None
    assert build_tick_row(flight_id="f", pack_timestamp=None, tick_started_at=NOW,
                          flight_ms=1, trace=trace) is None


# --- Tick rows ------------------------------------------------------------------


def test_tick_row_uses_the_shared_fetch_time_and_skips_pending_sigmets():
    metar_t = NOW - timedelta(minutes=12)
    fetched = NOW - timedelta(minutes=3)
    obs = _obs("VFR", metar_t, fetch=NOW - timedelta(minutes=1), taf_issued=NOW - timedelta(hours=2))
    sigmets = RouteSigmets(corridor_nm=50.0, fetch_time=NOW, sigmets=[
        SigmetAlongRoute(fir_id="ZZZZ", valid_from=NOW - timedelta(minutes=40), raw_text="A"),
        SigmetAlongRoute(fir_id="ZZZZ", valid_from=NOW + timedelta(hours=1), raw_text="B"),  # pending
    ])
    trace = CommitTrace(committed_at=NOW, records=[
        {"type": "report", "kind": "metar", "icao": "ZZDS", "observed_at": metar_t.isoformat(),
         "seen_at": obs.fetch_time.isoformat(), "tick_at": NOW.isoformat()},
        {"type": "report", "kind": "sigmet", "fir_id": "ZZZZ", "valid_from": (NOW - timedelta(minutes=40)).isoformat(),
         "tick_at": NOW.isoformat()},
        {"type": "event", "event": "appeared", "change": {"key": "metar:ZZDS", "tier": "highlight"}},
    ])
    row = build_tick_row(
        flight_id="f", pack_timestamp="p", tick_started_at=NOW - timedelta(seconds=30), flight_ms=120,
        trace=trace, observations=obs, sigmets=sigmets, fetched_at={"ZZDS": fetched},
        sigmet_fetched_at=NOW - timedelta(seconds=20),
    )
    assert row.metar_observed_at == metar_t and row.metar_fetched_at == fetched
    assert row.taf_issued_at == NOW - timedelta(hours=2) and row.taf_fetched_at == fetched
    assert row.sigmet_issued_at == NOW - timedelta(minutes=40)
    assert row.sigmet_fetched_at == NOW - timedelta(seconds=20)
    # A highlight-tier event is not an alert.
    assert (row.new_reports, row.new_alerts) == (2, 0)
    items = json.loads(row.new_items_json)
    assert items[0] == {"kind": "metar", "report_at": metar_t.isoformat(), "fetched_at": fetched.isoformat()}


def test_failed_tick_row_write_is_swallowed_and_the_session_stays_usable(db_session, dev_user):
    bad = LiveTickTimingRow(flight_id="f", tick_started_at=NOW, committed_at=NOW.replace(tzinfo=None))
    assert write_tick_rows(db_session, [bad]) == 0  # naive datetime: TZDateTime refuses it
    good = LiveTickTimingRow(flight_id="f", tick_started_at=NOW, committed_at=NOW)
    assert write_tick_rows(db_session, [good]) == 1
    assert db_session.execute(select(LiveTickTimingRow)).scalars().all()[0].committed_at == NOW


# --- Deliveries -----------------------------------------------------------------


def test_same_version_polled_twice_gives_one_row(db_session, dev_user):
    kw = dict(flight_id="f", user_id=dev_user, platform="ios")
    assert record_delivery(db_session, served=NOW, now=NOW + timedelta(minutes=2), **kw)
    assert not record_delivery(db_session, served=NOW, now=NOW + timedelta(minutes=7), **kw)
    # After a restart the table answers instead of the cache.
    live_timing._reset_cache()
    assert not record_delivery(db_session, served=NOW, now=NOW + timedelta(minutes=12), **kw)
    # A newer version, and the same version on another platform, are rows.
    assert record_delivery(db_session, served=NOW + timedelta(minutes=10), now=NOW + timedelta(minutes=12), **kw)
    assert record_delivery(db_session, flight_id="f", user_id=dev_user, platform="web",
                           served=NOW, now=NOW + timedelta(minutes=3))
    rows = db_session.execute(select(LiveDeliveryRow)).scalars().all()
    assert sorted((r.platform, r.served_live_updated_at) for r in rows) == [
        ("ios", NOW), ("ios", NOW + timedelta(minutes=10)), ("web", NOW),
    ]
    assert {r.delivered_via for r in rows} == {"poll"} and all(r.push_sent_at is None for r in rows)


def test_failed_delivery_write_never_raises(db_session):
    db = MagicMock()
    db.execute.side_effect = RuntimeError("db down")
    assert record_delivery(db, flight_id="f", user_id="u", platform="ios", served=NOW) is False


def test_delivery_write_leaves_the_callers_session_alone(db_session, dev_user, monkeypatch):
    """Review on #752: the write used to commit / roll back the request's own
    session, so a failure could drop the caller's pending work."""
    pending = LiveDeliveryRow(flight_id="other", user_id=dev_user, platform="web",
                              served_live_updated_at=NOW, requested_at=NOW, delivered_via="poll")
    db_session.add(pending)
    # A success commits only its own row: the caller's object is still pending.
    assert record_delivery(db_session, flight_id="f", user_id=dev_user, platform="ios", served=NOW)
    assert pending in db_session.new
    # A failure leaves it pending too, and the caller can still commit it.
    monkeypatch.setattr(live_timing, "Session", MagicMock(side_effect=RuntimeError("db down")))
    assert record_delivery(db_session, flight_id="f", user_id=dev_user, platform="ios",
                           served=NOW + timedelta(minutes=10)) is False
    assert pending in db_session.new
    db_session.commit()
    assert {r.flight_id for r in db_session.execute(select(LiveDeliveryRow)).scalars()} == {"f", "other"}


def test_platform_of_user_agent_classes():
    assert [live_timing.platform_of(c) for c in ("ios", "web", "other", None)] == ["ios", "web", "other", "other"]


# --- The report ---------------------------------------------------------------


def _tick(db, *, committed, started, items, outcome=None, written=None, tick_ms=4000):
    row = LiveTickTimingRow(
        flight_id="f", tick_started_at=started, committed_at=committed, tick_ms=tick_ms,
        highlight_outcome=outcome, highlight_written_at=written,
        new_items_json=json.dumps(items), new_reports=len(items),
    )
    db.add(row)
    db.flush()
    return row


def test_end_to_end_metar_seen_at_tick_n_delivered_at_the_next_poll(db_session, dev_user):
    obs_t = NOW - timedelta(minutes=10)
    fetched = NOW - timedelta(minutes=4)
    c1 = NOW
    c2 = NOW + timedelta(minutes=10)
    _tick(db_session, committed=c1, started=c1 - timedelta(seconds=5), items=[
        {"kind": "metar", "report_at": obs_t.isoformat(), "fetched_at": fetched.isoformat()},
    ], outcome="written", written=c1 + timedelta(seconds=2))
    _tick(db_session, committed=c2, started=c2 - timedelta(seconds=5), items=[], outcome="gated")
    # The pilot's next poll, 3 min after tick N, serves it; a later poll a newer version.
    record_delivery(db_session, flight_id="f", user_id=dev_user, platform="ios", served=c1,
                    now=c1 + timedelta(minutes=3))
    record_delivery(db_session, flight_id="f", user_id=dev_user, platform="ios", served=c2,
                    now=c2 + timedelta(minutes=1))
    # A ↻ press version: no tick row to join.
    record_delivery(db_session, flight_id="f", user_id=dev_user, platform="web",
                    served=c1 + timedelta(minutes=1, microseconds=7), now=c1 + timedelta(minutes=2))

    report = latency_report(db_session, now=NOW + timedelta(hours=1))
    hops = {h["key"]: h for h in report["hops"]}
    assert hops["report_to_fetched:metar"]["p50"] == 360.0
    assert hops["fetched_to_available:metar"]["p50"] == 240.0
    assert hops["end_to_end:metar:ios"] == {"key": "end_to_end:metar:ios", "label": hops["end_to_end:metar:ios"]["label"],
                                            "n": 1, "p50": 780.0, "p95": 780.0, "max": 780.0}
    # The web client only saw a version committed after tick N, at c1+2min.
    assert hops["end_to_end:metar:web"]["p50"] == 720.0
    assert hops["available_to_delivered:ios"]["n"] == 2
    assert hops["available_to_delivered:ios"]["max"] == 180.0
    assert hops["available_to_highlight"]["p50"] == 2.0
    assert hops["tick"]["n"] == 2
    assert report["counts"]["dropped"] == {"available_to_delivered:untracked": 1}
    assert report["daily"][0]["day"] == "2026-10-01"


def test_alert_without_evidence_time_is_counted_not_mixed_in(db_session, dev_user):
    """Review on #752: an alert with no evidence time measured only commit →
    delivery inside the report-time series, understating it."""
    _tick(db_session, committed=NOW, started=NOW, items=[
        {"kind": "alert", "report_at": (NOW - timedelta(minutes=8)).isoformat()},
        {"kind": "alert", "report_at": None},
    ], outcome="gated")
    record_delivery(db_session, flight_id="f", user_id=dev_user, platform="ios", served=NOW,
                    now=NOW + timedelta(minutes=2))
    report = latency_report(db_session, now=NOW + timedelta(hours=1))
    hops = {h["key"]: h for h in report["hops"]}
    assert hops["end_to_end:alert:ios"]["n"] == 1
    assert hops["end_to_end:alert:ios"]["p50"] == 600.0
    assert report["counts"]["dropped"] == {"end_to_end:alert:no_evidence_time": 1}


def test_rejected_highlight_is_not_a_written_hop(db_session):
    _tick(db_session, committed=NOW, started=NOW, items=[], outcome="rejected")
    hops = {h["key"] for h in latency_report(db_session, now=NOW + timedelta(hours=1))["hops"]}
    assert "available_to_highlight" not in hops


def test_account_deletion_removes_the_users_rows(db_session, dev_user):
    from weatherbrief.tasks.live_timing import delete_for_user

    _tick(db_session, committed=NOW, started=NOW, items=[])
    record_delivery(db_session, flight_id="f", user_id=dev_user, platform="ios", served=NOW, now=NOW)
    delete_for_user(db_session, dev_user, ["f"])
    assert db_session.execute(select(LiveTickTimingRow)).scalars().all() == []
    assert db_session.execute(select(LiveDeliveryRow)).scalars().all() == []


def test_purge_drops_rows_past_retention(db_session, dev_user):
    _tick(db_session, committed=NOW - timedelta(days=200), started=NOW - timedelta(days=200), items=[])
    _tick(db_session, committed=NOW, started=NOW, items=[])
    record_delivery(db_session, flight_id="f", user_id=dev_user, platform="ios",
                    served=NOW - timedelta(days=200), now=NOW - timedelta(days=200))
    assert purge_old(db_session, now=NOW) == 2
    assert len(db_session.execute(select(LiveTickTimingRow)).scalars().all()) == 1


# --- The tick writes them -----------------------------------------------------------


def _write_tick_pack(tmp_path):
    pack_dir = tmp_path / "user" / "zz-flight" / "pack"
    pack_dir.mkdir(parents=True)
    (pack_dir / "briefing.json").write_text(json.dumps({
        "route": {"name": "ZZ", "waypoints": [{"icao": "ZZDP", "name": "a", "lat": 50.0, "lon": 1.0},
                                              {"icao": "ZZDS", "name": "b", "lat": 51.0, "lon": 2.0}],
                  "cruise_altitude_ft": 6000, "flight_duration_hours": 1.0},
    }))
    return pack_dir


def _add_tick_flight(db, user_id, pack_dir):
    db.add(FlightRow(
        id="zz-flight", user_id=user_id, route_name="ZZDP-ZZDS",
        waypoints_json=json.dumps(["ZZDP", "ZZDS"]), departure_time=datetime.now(timezone.utc) + timedelta(hours=1),
        cruise_altitude_ft=6000, flight_ceiling_ft=12000, flight_duration_hours=1.0,
    ))
    db.flush()
    db.add(BriefingPackRow(flight_id="zz-flight", fetch_timestamp=datetime.now(timezone.utc) - timedelta(hours=3),
                           days_out=0, artifact_path=str(pack_dir)))
    db.flush()


def _run_tick(db, *, highlight=None, write_fails=False):
    from weatherbrief.tasks.live_highlight import HighlightOutcome
    from weatherbrief.tasks.live_tick import LiveTick

    committed_at = datetime.now(timezone.utc)

    def fake_refresh(pack_dir, db_path, *, trace=None, **kwargs):
        trace.committed_at = committed_at
        return SimpleNamespace(observations=_obs("VFR", committed_at - timedelta(minutes=9)),
                               sigmets=None, observed=None)

    layer = SimpleNamespace(glance=SimpleNamespace(highlight=None))
    upstream = MagicMock()
    upstream.fetch_weather.return_value = []
    tick = LiveTick(upstream=upstream, sigmet_upstream=MagicMock())
    patches = [
        patch("euro_aip.briefing.weather.route_weather.RouteWeatherService.fetch_route_weather",
              lambda self, **kw: SimpleNamespace(airports=[])),
        patch("weatherbrief.airports._load_airport_model", return_value=MagicMock()),
        patch("weatherbrief.tasks.route_weather.run_realtime_refresh", fake_refresh),
        patch("weatherbrief.tasks.live_highlight.highlight_enabled", return_value=highlight is not None),
        patch("weatherbrief.tasks.live_layer.live_for_pack", return_value=layer),
        patch("weatherbrief.tasks.live_highlight.ensure_highlight",
              return_value=highlight or HighlightOutcome("skipped")),
    ]
    if write_fails:
        patches.append(patch.object(LiveTickTimingRow, "__init__", side_effect=RuntimeError("boom")))
    for p in patches:
        p.start()
    try:
        result = tick.run(db, "/fake/db")
    finally:
        for p in reversed(patches):
            p.stop()
    return result, committed_at


def test_tick_writes_one_row_per_committed_flight_with_a_rejected_highlight(db_session, dev_user, tmp_path):
    from weatherbrief.tasks.live_highlight import HighlightOutcome

    _add_tick_flight(db_session, dev_user, _write_tick_pack(tmp_path))
    result, committed_at = _run_tick(
        db_session, highlight=HighlightOutcome("rejected", text="x", reason="ungrounded", latency_ms=900),
    )
    assert result["updated"] == 1 and result["highlighted"] == 0
    [row] = db_session.execute(select(LiveTickTimingRow)).scalars().all()
    assert row.flight_id == "zz-flight" and row.committed_at == committed_at
    assert row.highlight_outcome == "rejected" and row.highlight_latency_ms == 900
    assert row.highlight_requested_at is not None and row.highlight_written_at is None
    assert row.tick_ms is not None and row.flight_ms is not None
    assert row.metar_observed_at == committed_at - timedelta(minutes=9)


def test_a_failed_latency_row_does_not_break_the_tick(db_session, dev_user, tmp_path):
    _add_tick_flight(db_session, dev_user, _write_tick_pack(tmp_path))
    result, _ = _run_tick(db_session, write_fails=True)
    assert result["updated"] == 1
    assert db_session.execute(select(LiveTickTimingRow)).scalars().all() == []


def test_highlight_cost_survives_a_failed_latency_insert(db_session, dev_user, tmp_path):
    """Review of #752: the ledger rows are committed before, and apart from,
    the latency insert, so its rollback cannot take them."""
    from sqlalchemy.orm import Session

    from weatherbrief.db.models import CostLedgerRow
    from weatherbrief.tasks.live_highlight import DEFAULT_MODEL, HighlightOutcome

    _add_tick_flight(db_session, dev_user, _write_tick_pack(tmp_path))
    usage = {"model": DEFAULT_MODEL, "input_tokens": 1200, "output_tokens": 40}
    real_add_all = Session.add_all

    def add_all(self, rows, *a, **kw):
        if any(isinstance(r, LiveTickTimingRow) for r in rows):
            raise RuntimeError("latency table missing")
        return real_add_all(self, rows, *a, **kw)

    with patch.object(Session, "add_all", add_all):
        result, _ = _run_tick(db_session, highlight=HighlightOutcome("written", text="ok", usage=usage,
                                                                     latency_ms=800))
    assert result["highlighted"] == 1
    db_session.rollback()  # anything not committed is gone
    assert db_session.execute(select(LiveTickTimingRow)).scalars().all() == []
    [cost] = db_session.execute(select(CostLedgerRow).where(CostLedgerRow.action == "live_highlight")).scalars().all()
    assert cost.reference_id == "zz-flight"
