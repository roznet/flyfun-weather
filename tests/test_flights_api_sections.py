"""Tests for the future/recent/past section assignment in /api/flights."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from flyfun_common.db import current_user_id, get_db, DEV_USER_ID
from flyfun_common.db.models import UserPreferencesRow, UserRow
from weatherbrief.api.app import create_app
from weatherbrief.api.flights import _classify_section, _compute_recent_section
from weatherbrief.debriefs.taxonomy import ConditionTag, Decision
from weatherbrief.models import Flight, FlightDebrief
from weatherbrief.storage.debriefs import upsert_debrief
from weatherbrief.storage.flights import save_flight


_NOW = datetime(2026, 4, 25, 12, 0, 0, tzinfo=timezone.utc)


# --- Pure function tests for the section logic ---


def _flt(idx: int, days_offset: int) -> Flight:
    """Build a Flight whose departure is days_offset days from _NOW (positive = future)."""
    dep = _NOW + timedelta(days=days_offset)
    return Flight(
        id=f"f-{idx}",
        user_id="u",
        route_name="r",
        departure_time=dep,
        cruise_altitude_ft=8000,
        flight_duration_hours=1.0,
        created_at=dep - timedelta(days=1),
    )


def _dbf(flight: Flight, decision: Decision) -> FlightDebrief:
    return FlightDebrief(
        flight_id=flight.id,
        decision=decision,
        reasons=[ConditionTag.IMC] if decision is Decision.CANCELLED else [],
        outcomes={},
        created_at=flight.departure_time,
        updated_at=flight.departure_time,
    )


class TestComputeRecentSection:
    def test_empty(self):
        assert _compute_recent_section([], now=_NOW) == set()

    def test_no_debriefs_takes_two_most_recent_past(self):
        f1, f2, f3 = _flt(1, -1), _flt(2, -2), _flt(3, -3)
        pairs = [(f1, None), (f2, None), (f3, None)]
        recent = _compute_recent_section(pairs, now=_NOW)
        assert recent == {f1.id, f2.id}

    def test_debriefed_flight_does_not_push_others_out(self):
        # Two recent undebriefed flights — both should appear regardless
        # of any older debriefed flight, so debriefing one doesn't cause
        # the other to vanish.
        f_dbf = _flt(0, -3)  # debriefed
        f1 = _flt(1, -1)     # undebriefed, recent → in recent
        f2 = _flt(2, -2)     # undebriefed, recent → in recent
        pairs = [(f_dbf, _dbf(f_dbf, Decision.FLOWN)), (f1, None), (f2, None)]
        assert _compute_recent_section(pairs, now=_NOW) == {f1.id, f2.id}

    def test_older_than_max_age_excluded(self):
        # A flight from > 30 days ago drops to Past, not Recent.
        old = _flt(1, -45)
        recent = _flt(2, -2)
        pairs = [(old, None), (recent, None)]
        assert _compute_recent_section(pairs, now=_NOW) == {recent.id}

    def test_all_undebriefed_but_too_old(self):
        old1, old2 = _flt(1, -60), _flt(2, -100)
        pairs = [(old1, None), (old2, None)]
        assert _compute_recent_section(pairs, now=_NOW) == set()

    def test_all_caught_up(self):
        f1, f2 = _flt(1, -1), _flt(2, -2)
        pairs = [(f1, _dbf(f1, Decision.FLOWN)), (f2, _dbf(f2, Decision.FLOWN))]
        assert _compute_recent_section(pairs, now=_NOW) == set()

    def test_cap_default_two(self):
        # 4 undebriefed past flights all within window → only 2 most recent.
        flights = [_flt(i, -i) for i in range(1, 5)]
        pairs = [(f, None) for f in flights]
        assert _compute_recent_section(pairs, now=_NOW) == {flights[0].id, flights[1].id}

    def test_future_flights_never_recent(self):
        future = _flt(1, +3)
        past = _flt(2, -3)
        pairs = [(future, None), (past, None)]
        assert _compute_recent_section(pairs, now=_NOW) == {past.id}

    def test_max_age_boundary_inclusive(self):
        # Exactly at the cutoff (30 days ago) is in.
        f = _flt(1, -30)
        pairs = [(f, None)]
        assert _compute_recent_section(pairs, now=_NOW) == {f.id}


class TestClassifySection:
    def test_future(self):
        f = _flt(1, +1)
        assert _classify_section(f, has_debrief=False, recent_set=set(), now=_NOW) == "future"

    def test_recent(self):
        f = _flt(1, -1)
        assert _classify_section(f, has_debrief=False, recent_set={f.id}, now=_NOW) == "recent"

    def test_past(self):
        f = _flt(1, -10)
        assert _classify_section(f, has_debrief=True, recent_set=set(), now=_NOW) == "past"

    def test_past_undebriefed_outside_recent(self):
        f = _flt(1, -100)
        assert _classify_section(f, has_debrief=False, recent_set=set(), now=_NOW) == "past"


# --- Integration test against the real API ---


@pytest.fixture
def app_db():
    from conftest import make_app_engine
    engine = make_app_engine()
    TestSession = sessionmaker(bind=engine)
    s = TestSession()
    s.add(UserRow(
        id=DEV_USER_ID, provider="local", provider_sub="dev",
        email="d@l", display_name="D", approved=True,
    ))
    s.flush()
    s.add(UserPreferencesRow(user_id=DEV_USER_ID))
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


def _save_flight(app_db, *, route: str, days_offset: int, idx: int) -> Flight:
    s = app_db()
    dep = datetime.now(timezone.utc) + timedelta(days=days_offset)
    h = hashlib.sha256(json.dumps(
        {"alt": 8000, "ceil": 18000, "dur": 1.0, "time": dep.strftime("%H:%M"),
         "user": DEV_USER_ID, "idx": idx},
        sort_keys=True,
    ).encode()).hexdigest()[:4]
    f = Flight(
        id=f"{route}-{dep.strftime('%Y-%m-%d')}-{h}",
        user_id=DEV_USER_ID,
        route_name=route,
        waypoints=["EGTK", "LFAT"],
        departure_time=dep,
        cruise_altitude_ft=8000,
        flight_ceiling_ft=18000,
        flight_duration_hours=1.0,
        created_at=datetime.now(timezone.utc) - timedelta(days=30),
    )
    save_flight(s, f, DEV_USER_ID)
    s.commit()
    s.close()
    return f


class TestApiSections:
    def test_listing_includes_section_field(self, client, app_db):
        _save_flight(app_db, route="rt1", days_offset=+5, idx=1)
        _save_flight(app_db, route="rt2", days_offset=-3, idx=2)
        body = client.get("/api/flights").json()
        sections = {f["section"] for f in body}
        assert "future" in sections
        assert "past" in sections or "recent" in sections

    def test_listing_includes_debrief_field(self, client, app_db):
        f = _save_flight(app_db, route="r", days_offset=-3, idx=10)
        client.put(
            f"/api/flights/{f.id}/debrief",
            json={"decision": "cancelled", "reasons": ["IMC"]},
        )
        body = client.get("/api/flights").json()
        rec = next(x for x in body if x["id"] == f.id)
        assert rec["debrief"] is not None
        assert rec["debrief"]["decision"] == "cancelled"

    def test_recent_section_caps_at_two(self, client, app_db):
        # Four past flights, none debriefed → 2 in recent, 2 in past.
        for i in range(4):
            _save_flight(app_db, route=f"r{i}", days_offset=-(i + 1), idx=i)
        body = client.get("/api/flights").json()
        sections = [f["section"] for f in body]
        assert sections.count("recent") == 2
        assert sections.count("past") == 2

    def test_recent_excludes_debriefed_keeps_undebriefed(self, client, app_db):
        # Debriefing one recent flight does NOT push the other recent
        # undebriefed flight out of the section — both are within the
        # 30-day window and the other is still pending a debrief.
        new_f = _save_flight(app_db, route="rN", days_offset=-1, idx=1)
        old_f = _save_flight(app_db, route="rO", days_offset=-5, idx=2)
        client.put(
            f"/api/flights/{new_f.id}/debrief",
            json={"decision": "flown"},
        )
        body = client.get("/api/flights").json()
        recent_ids = [f["id"] for f in body if f["section"] == "recent"]
        assert old_f.id in recent_ids
        # The debriefed one drops to past.
        assert new_f.id not in recent_ids
