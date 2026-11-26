"""The past-flights record (#1031): derivation, write paths, retention, archive API."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from conftest import make_app_engine
from flyfun_common.db import DEV_USER_ID, current_user_id, get_db
from flyfun_common.db.models import UserPreferencesRow, UserRow
from weatherbrief.db.models import (
    BriefingPackRow,
    FlightDebriefAnswerRow,
    FlightDebriefReportRow,
    FlightFactsRow,
    FlightRow,
    FlightSubscriptionRow,
)
from weatherbrief.debriefs.taxonomy import ConditionTag, Decision
from weatherbrief.storage.debriefs import upsert_debrief
from weatherbrief.storage.flight_facts import (
    forecast_tags_from_manifest,
    reported_tags,
    route_fields,
    update_flight_facts,
    update_flight_outcome,
)
from weatherbrief.tasks.flight_facts_backfill import backfill
from weatherbrief.tasks.retention import RetentionConfig, run_retention

NOW = datetime.now(timezone.utc).replace(microsecond=0)
OTHER = "other-user"

ROUTE = {
    "name": "zz test",
    "waypoints": [
        {"icao": "EGTK", "name": "Oxford", "lat": 51.836, "lon": -1.320},
        {"icao": "LFAT", "name": "Le Touquet", "lat": 50.517, "lon": 1.621},
    ],
}


def _manifest(**statuses) -> dict:
    return {"advisories": [{"advisory_id": k, "aggregate_status": v} for k, v in statuses.items()]}


# --- Pure derivations -----------------------------------------------------------


class TestDerivation:
    def test_amber_kept_green_dropped_model_ignored(self):
        tags = forecast_tags_from_manifest(_manifest(
            aircraft_icing="red", icing_escape="amber", convective="amber", turbulence="green",
            model_agreement="red", vmc_cruise="unavailable",
        ))
        assert tags == {"ICE": "red", "TS": "amber"}

    def test_archive_extras_map_frz_and_vis(self):
        tags = forecast_tags_from_manifest(_manifest(freezing_precip="amber", enroute_precip="red"))
        assert tags == {"FRZ": "amber", "VIS": "red"}

    def test_reported_tags_from_answers(self):
        answers = [
            {"kind": "icing", "answer": "light"},
            {"kind": "turbulence", "answer": "light"},  # light turbulence is not reported
            {"kind": "cloud", "answer": "in_and_out"},
            {"kind": "showers", "answer": "build_ups"},
        ]
        assert reported_tags("flown", [], answers) == ["ICE", "IMC", "TS"]

    def test_reported_tags_from_cancel_reasons(self):
        assert reported_tags("cancelled", ["WIND", "ICE"], []) == ["ICE", "WIND"]
        # Reasons count only for a cancelled flight.
        assert reported_tags("flown", ["WIND"], []) == []

    def test_route_fields(self):
        f = route_fields([("egtk", 51.836, -1.32), ("LFAT", 50.517, 1.621)])
        assert f["departure_icao"] == "EGTK" and f["destination_icao"] == "LFAT"
        assert 130 < f["distance_nm"] < 140
        assert json.loads(f["route_line_json"]) == [[51.836, -1.32], [50.517, 1.621]]


# --- DB fixtures ------------------------------------------------------------------


@pytest.fixture
def Session_(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    engine = make_app_engine()
    S = sessionmaker(bind=engine)
    s = S()
    for uid in (DEV_USER_ID, OTHER):
        s.add(UserRow(id=uid, provider="local", provider_sub=uid, email=f"{uid}@localhost",
                      display_name=uid, approved=True))
    s.flush()
    s.add(UserPreferencesRow(user_id=DEV_USER_ID))
    s.commit()
    s.close()
    yield S
    engine.dispose()


@pytest.fixture
def db(Session_):
    s = Session_()
    yield s
    s.close()


def _flight(db, fid, days_ago, *, user=DEV_USER_ID, waypoints=("EGTK", "LFAT"), duration=1.0):
    row = FlightRow(
        id=fid, user_id=user, route_name="zz " + fid, waypoints_json=json.dumps(list(waypoints)),
        departure_time=NOW - timedelta(days=days_ago), cruise_altitude_ft=8000,
        flight_ceiling_ft=18000, flight_duration_hours=duration,
    )
    db.add(row)
    db.flush()
    return row


def _pack(db, tmp_path, flight, fetched_at, manifest: dict | None, *, route=True):
    d = tmp_path / "packs" / flight.id / fetched_at.strftime("%Y%m%dT%H%M%S")
    d.mkdir(parents=True)
    if route:
        (d / "briefing.json").write_text(json.dumps({
            "route": ROUTE, "departure_time": flight.departure_time.isoformat(),
        }))
    if manifest is not None:
        (d / "route_advisories.json").write_text(json.dumps(manifest))
    db.add(BriefingPackRow(flight_id=flight.id, fetch_timestamp=fetched_at, days_out=1,
                           artifact_path=str(d)))
    db.flush()
    return d


# --- Write paths ------------------------------------------------------------------


class TestUpdateFlightFacts:
    def test_tags_from_default_pack_and_route_from_briefing(self, db, tmp_path):
        f = _flight(db, "f1", 3)
        dep = f.departure_time.replace(tzinfo=timezone.utc)
        _pack(db, tmp_path, f, dep - timedelta(hours=20), _manifest(convective="red"))
        _pack(db, tmp_path, f, dep - timedelta(hours=2), _manifest(aircraft_icing="amber"))
        # Fetched after departure: never the default pack.
        _pack(db, tmp_path, f, dep + timedelta(hours=1), _manifest(airport_wind="red"))
        row = update_flight_facts(db, "f1", now=NOW)
        assert json.loads(row.forecast_tags_json) == {"ICE": "amber"}
        assert row.tags_recorded is True
        assert row.departure_icao == "EGTK" and row.destination_lat == pytest.approx(50.517)
        assert row.distance_nm > 100
        # Ended more than a day ago: frozen on any refresh.
        assert row.forecast_frozen_at is not None

    def test_follows_latest_pack_until_departure_then_freezes(self, db, tmp_path):
        f = _flight(db, "f2", -2)  # departs in two days
        _pack(db, tmp_path, f, NOW - timedelta(hours=5), _manifest(convective="amber"))
        row = update_flight_facts(db, "f2", now=NOW)
        assert json.loads(row.forecast_tags_json) == {"TS": "amber"}
        assert row.forecast_frozen_at is None  # still in the future, even with freeze=True below
        update_flight_facts(db, "f2", now=NOW, freeze=True)
        assert row.forecast_frozen_at is None
        _pack(db, tmp_path, f, NOW - timedelta(hours=1), _manifest(convective="red"))
        update_flight_facts(db, "f2", now=NOW)
        assert json.loads(row.forecast_tags_json) == {"TS": "red"}

        # After departure, frozen at window close: a later refresh changes nothing.
        later = NOW + timedelta(days=2, hours=3)
        update_flight_facts(db, "f2", now=later, freeze=True)
        assert row.forecast_frozen_at == later
        assert row.forecast_pack_ts.replace(tzinfo=timezone.utc) == NOW - timedelta(hours=1)
        for p in db.query(BriefingPackRow).filter_by(flight_id="f2").all():
            Path(p.artifact_path, "route_advisories.json").write_text(json.dumps(_manifest(turbulence="red")))
        update_flight_facts(db, "f2", now=later + timedelta(hours=1))
        assert json.loads(row.forecast_tags_json) == {"TS": "red"}

    def test_no_pack_is_not_recorded(self, db):
        _flight(db, "f3", 400)
        row = update_flight_facts(db, "f3", now=NOW)
        assert row.tags_recorded is False and row.forecast_tags_json is None
        assert row.departure_icao == "EGTK" and row.route_line_json is None

    def test_outcome_from_debrief_answers_and_report(self, db):
        _flight(db, "f4", 5)
        db.add(FlightDebriefAnswerRow(
            flight_id="f4", question_id="icing-0-50", kind="icing", answer="moderate_plus",
            answered_at=NOW, rules_version="1", question_json="{}",
        ))
        db.add(FlightDebriefReportRow(flight_id="f4", built_at=NOW, rules_version="1", status="ok",
                                      differs_from_forecast=True, interesting=True))
        upsert_debrief(db, flight_id="f4", decision=Decision.FLOWN)
        row = update_flight_outcome(db, "f4", now=NOW)
        assert row.decision == "flown" and row.debriefed is True
        assert json.loads(row.reported_tags_json) == ["ICE"]
        assert row.differs_from_forecast is True and row.interesting is True


class TestBackfill:
    def test_marks_flights_without_packs_not_recorded(self, db, tmp_path):
        f = _flight(db, "b1", 10)
        _flight(db, "b2", 300)
        dep = f.departure_time.replace(tzinfo=timezone.utc)
        _pack(db, tmp_path, f, dep - timedelta(hours=3), _manifest(airport_wind="amber"))
        db.commit()
        stats = backfill(db, now=NOW)
        assert stats["flights"] == 2 and stats["recorded"] == 1 and stats["not_recorded"] == 1
        assert db.get(FlightFactsRow, "b2").tags_recorded is False
        assert json.loads(db.get(FlightFactsRow, "b1").forecast_tags_json) == {"WIND": "amber"}
        # Idempotent.
        assert backfill(db, now=NOW)["recorded"] == 1

    def test_dry_run_writes_nothing(self, db):
        _flight(db, "b3", 10)
        db.commit()
        assert backfill(db, dry_run=True, now=NOW)["flights"] == 1
        assert db.get(FlightFactsRow, "b3") is None


class TestRetention:
    def test_t2_writes_facts_first_and_leaves_them(self, db, tmp_path):
        f = _flight(db, "r1", 200)
        dep = f.departure_time.replace(tzinfo=timezone.utc)
        d = _pack(db, tmp_path, f, dep - timedelta(hours=3), _manifest(aircraft_icing="red"))
        db.commit()
        stats = run_retention(db, RetentionConfig())
        db.commit()
        assert stats.packs_t2 == 1 and not d.exists()
        row = db.get(FlightFactsRow, "r1")
        assert row is not None and json.loads(row.forecast_tags_json) == {"ICE": "red"}
        assert row.forecast_frozen_at is not None
        # A second run (no packs left) keeps the record as it was.
        run_retention(db, RetentionConfig())
        db.commit()
        assert json.loads(db.get(FlightFactsRow, "r1").forecast_tags_json) == {"ICE": "red"}

    def test_failed_facts_write_keeps_packs(self, db, tmp_path, monkeypatch):
        f = _flight(db, "r2", 100)  # the test user is inactive: T2 at 90 days
        dep = f.departure_time.replace(tzinfo=timezone.utc)
        d = _pack(db, tmp_path, f, dep - timedelta(hours=3), _manifest(aircraft_icing="red"))
        db.commit()
        import weatherbrief.storage.flight_facts as ff

        monkeypatch.setattr(ff, "update_flight_facts", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
        stats = run_retention(db, RetentionConfig())
        assert stats.packs_t2 == 0 and d.exists()

    def test_unreadable_manifest_keeps_packs_until_grace(self, db, tmp_path, monkeypatch):
        import weatherbrief.tasks.retention as retention

        cfg = RetentionConfig()
        f = _flight(db, "r3", cfg.t2_inactive_days + 1)  # the test user is inactive
        dep = f.departure_time.replace(tzinfo=timezone.utc)
        d = _pack(db, tmp_path, f, dep - timedelta(hours=3), _manifest(aircraft_icing="red"))
        Path(d, "route_advisories.json").write_text("{not json")
        db.commit()
        stats = run_retention(db, cfg)
        db.commit()
        assert stats.packs_t2 == 0 and d.exists()
        row = db.get(FlightFactsRow, "r3")
        assert row.tags_recorded is False and row.forecast_frozen_at is None

        # Past the grace, T2 goes ahead (a corrupt pack cannot pin disk forever).
        monkeypatch.setattr(retention, "T2_FACTS_GRACE_DAYS", 1)
        stats = run_retention(db, cfg)
        assert stats.packs_t2 == 1 and not d.exists()

    def test_unreadable_manifest_is_not_frozen(self, db, tmp_path):
        f = _flight(db, "r4", 5)
        dep = f.departure_time.replace(tzinfo=timezone.utc)
        d = _pack(db, tmp_path, f, dep - timedelta(hours=3), _manifest(aircraft_icing="red"))
        Path(d, "route_advisories.json").write_text("{not json")
        row = update_flight_facts(db, "r4", now=NOW, freeze=True)
        assert row.forecast_frozen_at is None
        Path(d, "route_advisories.json").write_text(json.dumps(_manifest(aircraft_icing="red")))
        row = update_flight_facts(db, "r4", now=NOW, freeze=True)
        assert row.tags_recorded is True and row.forecast_frozen_at is not None


# --- Archive API --------------------------------------------------------------------


@pytest.fixture
def client(Session_, monkeypatch):
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET", "test")
    from weatherbrief.api.app import create_app

    app = create_app()

    def _override():
        s = Session_()
        try:
            yield s
            s.commit()
        finally:
            s.close()

    app.dependency_overrides[get_db] = _override
    app.dependency_overrides[current_user_id] = lambda: DEV_USER_ID
    return TestClient(app, raise_server_exceptions=True)


_COORDS = {"EGTK": (51.8, -1.3), "LFAT": (50.5, 1.6), "EDDS": (48.7, 9.2), "LFMD": (43.5, 6.9)}


def _facts(db, fid, *, tags=None, reported=None, recorded=True, ends=("EGTK", "LFAT")):
    (a, b), (la, lb) = ends, (_COORDS[ends[0]], _COORDS[ends[1]])
    db.add(FlightFactsRow(
        flight_id=fid, departure_icao=a, destination_icao=b,
        departure_lat=la[0], departure_lon=la[1], destination_lat=lb[0], destination_lon=lb[1],
        route_line_json=json.dumps([list(la), list(lb)]),
        forecast_tags_json=json.dumps(tags or {}) if recorded else None, tags_recorded=recorded,
        reported_tags_json=json.dumps(reported or []), updated_at=NOW,
    ))


@pytest.fixture
def archive(Session_):
    """Owned past flights (all debriefed, so none sits in the Recent nudge),
    a future one, and a subscribed one."""
    s = Session_()
    _flight(s, "a-ice", 40)
    _facts(s, "a-ice", tags={"ICE": "red", "TS": "amber"}, reported=["ICE"])
    upsert_debrief(s, flight_id="a-ice", decision=Decision.FLOWN)
    _flight(s, "a-ts", 50, waypoints=("LFAT", "EGTK"))
    _facts(s, "a-ts", tags={"TS": "amber"}, ends=("LFAT", "EGTK"))
    upsert_debrief(s, flight_id="a-ts", decision=Decision.FLOWN)
    _flight(s, "a-cxl", 60, waypoints=("EDDS", "LFMD"))
    _facts(s, "a-cxl", tags={"WIND": "amber"}, reported=["WIND"], ends=("EDDS", "LFMD"))
    upsert_debrief(s, flight_id="a-cxl", decision=Decision.CANCELLED, reasons=[ConditionTag.WIND])
    _flight(s, "a-mon", 70)
    _facts(s, "a-mon", recorded=False)
    upsert_debrief(s, flight_id="a-mon", decision=Decision.MONITORING)
    _flight(s, "a-future", -3)
    _flight(s, "s-other", 45, user=OTHER)
    upsert_debrief(s, flight_id="s-other", decision=Decision.FLOWN)
    _facts(s, "s-other", tags={"ICE": "red"})
    s.add(FlightSubscriptionRow(flight_id="s-other", user_id=DEV_USER_ID))
    s.commit()
    s.close()


def _get(client, **params):
    r = client.get("/api/flights/archive", params=params)
    assert r.status_code == 200, r.text
    return r.json()


class TestArchiveApi:
    def test_default_lists_owned_past_incl_cancelled_and_monitor(self, client, archive):
        body = _get(client)
        assert [r["id"] for r in body["rows"]] == ["a-ice", "a-ts", "a-cxl", "a-mon"]
        assert body["total"] == 4
        first = body["rows"][0]
        assert first["forecast_tags"] == {"ICE": "red", "TS": "amber"} and first["forecast_worst"] == "red"
        assert body["rows"][3]["tags_recorded"] is False
        assert body["facets"]["not_recorded"] == 1

    def test_tags_filter_is_or_and_facets_ignore_it(self, client, archive):
        body = _get(client, tags="ICE")
        assert [r["id"] for r in body["rows"]] == ["a-ice"]
        assert body["facets"]["tags"]["TS"] == 2 and body["facets"]["tags"]["ICE"] == 1
        assert {r["id"] for r in _get(client, tags="ice,wind")["rows"]} == {"a-ice", "a-cxl"}

    def test_reported_source(self, client, archive):
        body = _get(client, source="reported", tags="WIND")
        assert [r["id"] for r in body["rows"]] == ["a-cxl"]
        assert body["facets"]["source"] == "reported" and body["facets"]["tags"]["ICE"] == 1

    def test_outcome_and_hide(self, client, archive):
        assert [r["id"] for r in _get(client, outcome="cancelled")["rows"]] == ["a-cxl"]
        body = _get(client, hide="cancelled,monitoring")
        assert [r["id"] for r in body["rows"]] == ["a-ice", "a-ts"]
        assert body["facets"]["outcomes"]["flown"] == 2 and body["facets"]["outcomes"]["cancelled"] == 0
        assert _get(client)["facets"]["outcomes"]["debriefed"] == 4

    def test_q_and_dates(self, client, archive):
        assert [r["id"] for r in _get(client, q="EDDS")["rows"]] == ["a-cxl"]
        frm = (NOW - timedelta(days=55)).date().isoformat()
        to = (NOW - timedelta(days=45)).date().isoformat()
        assert [r["id"] for r in _get(client, **{"from": frm, "to": to})["rows"]] == ["a-ts"]

    def test_paging(self, client, archive):
        body = _get(client, limit=2, offset=2)
        assert [r["id"] for r in body["rows"]] == ["a-cxl", "a-mon"] and body["total"] == 4

    def test_calendar(self, client, archive):
        year = (NOW - timedelta(days=40)).year
        body = _get(client, view="calendar", year=year)
        day = (NOW - timedelta(days=40)).date().isoformat()
        assert {"date": day, "count": 1, "worst": "red"} in body["days"]

    def test_map_bundles_unordered_pairs(self, client, archive):
        body = _get(client, view="map")
        pair = next(r for r in body["routes"] if {r["a"], r["b"]} == {"EGTK", "LFAT"})
        # a-ice EGTK->LFAT, a-ts LFAT->EGTK, a-mon EGTK->LFAT
        assert pair["count"] == 3 and pair["worst"] == "red" and pair["line"][0] == [51.8, -1.3]
        egtk = next(a for a in body["airports"] if a["icao"] == "EGTK")
        assert egtk["count"] == 3 and len(body["routes"]) == 2 and body["unplaced"] == 0

    def test_unknown_tag_is_422(self, client, archive):
        assert client.get("/api/flights/archive", params={"tags": "XYZ"}).status_code == 422
        assert client.get("/api/flights/archive", params={"from": "2026-05-02", "to": "2026-05-01"}).status_code == 422

    def test_flights_list_unchanged(self, client, archive):
        assert client.get("/api/flights").status_code == 200

    def test_debrief_put_records_outcome(self, client, Session_):
        s = Session_()
        _flight(s, "p1", 20)
        s.commit()
        s.close()
        r = client.put("/api/flights/p1/debrief", json={"decision": "cancelled", "reasons": ["ICE"]})
        assert r.status_code == 200, r.text
        s = Session_()
        row = s.get(FlightFactsRow, "p1")
        assert row.decision == "cancelled" and json.loads(row.reported_tags_json) == ["ICE"]
        s.close()
        assert client.delete("/api/flights/p1/debrief").status_code == 204
        s = Session_()
        assert s.get(FlightFactsRow, "p1").decision is None
        s.close()
