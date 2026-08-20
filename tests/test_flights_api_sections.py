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
from weatherbrief.api.flights import (
    _classify_section,
    _compute_recent_section,
    _flight_has_ended,
)
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


def _flt_hours(idx: int, hours_offset: float, duration: float) -> Flight:
    """Build a Flight departing hours_offset hours from _NOW, with a given duration."""
    dep = _NOW + timedelta(hours=hours_offset)
    return Flight(
        id=f"h-{idx}",
        user_id="u",
        route_name="r",
        departure_time=dep,
        cruise_altitude_ft=8000,
        flight_duration_hours=duration,
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


class TestDurationAwareBoundary:
    """The future/past boundary is departure + duration, not departure (#536).

    Matches the web's ``isFlightPast``, which has always been duration-aware.
    """

    def test_in_progress_flight_has_not_ended(self):
        # Departed 30 minutes ago on a 3-hour trip — still flying.
        f = _flt_hours(1, hours_offset=-0.5, duration=3.0)
        assert _flight_has_ended(f, _NOW) is False

    def test_flight_past_its_duration_has_ended(self):
        f = _flt_hours(2, hours_offset=-4.0, duration=3.0)
        assert _flight_has_ended(f, _NOW) is True

    def test_zero_duration_is_past_the_instant_it_departs(self):
        # Zero duration is a real case (the web add-flight flow confirms it) and
        # must behave exactly as before: past as soon as it departs.
        f = _flt_hours(3, hours_offset=-0.01, duration=0.0)
        assert _flight_has_ended(f, _NOW) is True

    def test_departure_exactly_now_is_not_ended(self):
        # Unchanged from the old ``departure_time >= now`` boundary.
        f = _flt_hours(4, hours_offset=0.0, duration=0.0)
        assert _flight_has_ended(f, _NOW) is False

    def test_none_duration_treated_as_zero(self):
        f = _flt_hours(5, hours_offset=-0.01, duration=1.0)
        f.flight_duration_hours = None  # type: ignore[assignment]
        assert _flight_has_ended(f, _NOW) is True

    def test_in_progress_flight_classifies_as_future(self):
        f = _flt_hours(6, hours_offset=-0.5, duration=3.0)
        assert _classify_section(f, has_debrief=False, recent_set=set(), now=_NOW) == "future"

    def test_classify_and_recent_agree_for_in_progress_flight(self):
        # The two functions test the boundary independently; if they disagree an
        # airborne flight is ``future`` to one and a ``recent`` candidate to the
        # other. Both must go through ``_flight_has_ended``.
        f = _flt_hours(7, hours_offset=-0.5, duration=3.0)
        recent = _compute_recent_section([(f, None)], now=_NOW)
        assert recent == set()
        assert _classify_section(f, has_debrief=False, recent_set=recent, now=_NOW) == "future"

    def test_just_ended_flight_becomes_recent(self):
        f = _flt_hours(8, hours_offset=-3.5, duration=3.0)
        recent = _compute_recent_section([(f, None)], now=_NOW)
        assert recent == {f.id}
        assert _classify_section(f, has_debrief=False, recent_set=recent, now=_NOW) == "recent"


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


def _save_pack(
    app_db,
    flight_id: str,
    *,
    assessment: str | None,
    days_out: int,
    artifact_path: str = "",
    fetch_timestamp: datetime | None = None,
) -> None:
    """Insert a minimal latest briefing pack row for a flight."""
    from weatherbrief.db.models import BriefingPackRow

    s = app_db()
    s.add(BriefingPackRow(
        flight_id=flight_id,
        fetch_timestamp=fetch_timestamp or datetime.now(timezone.utc),
        days_out=days_out,
        assessment=assessment,
        assessment_reason="test",
        has_digest=True,
        artifact_path=artifact_path,
    ))
    s.commit()
    s.close()


class TestLatestBriefingInline:
    """The list response carries enough per-flight briefing data to paint the
    card and drive the debrief form — no per-flight /packs/latest calls."""

    def test_card_data_present_inline(self, client, app_db):
        # assessment / days_out / fetch_timestamp are exactly the three fields
        # the card consumed from the old /packs/latest round-trip.
        f = _save_flight(app_db, route="rcard", days_offset=+5, idx=1)
        _save_pack(app_db, f.id, assessment="AMBER", days_out=3)
        rec = next(x for x in client.get("/api/flights").json() if x["id"] == f.id)
        lb = rec["latest_briefing"]
        assert lb is not None
        assert lb["assessment"] == "AMBER"
        assert lb["days_out"] == 3
        assert lb["fetch_timestamp"] is not None
        # No route_advisories.json on disk → flag is False.
        assert lb["has_advisories"] is False

    def test_fetch_timestamp_is_utc_qualified(self, client, app_db):
        """``fetch_timestamp`` must carry its UTC offset.

        The column is ``DateTime(timezone=True)``, a no-op on MySQL, so the row
        reads back naive and a bare ``isoformat()`` emits no offset. JS
        ``new Date()`` reads an offset-less timestamp as *local* time, so a
        UTC+2 user saw an 06:52Z refresh rendered as "04:52 UTC" on the
        flights-list card. Pin the instant, not just its presence.
        """
        stamp = datetime(2026, 8, 20, 6, 52, 0, tzinfo=timezone.utc)
        f = _save_flight(app_db, route="rtz", days_offset=+5, idx=41)
        _save_pack(app_db, f.id, assessment="GREEN", days_out=2, fetch_timestamp=stamp)

        rec = next(x for x in client.get("/api/flights").json() if x["id"] == f.id)
        raw = rec["latest_briefing"]["fetch_timestamp"]

        # Offset-qualified: this is precisely what JS `new Date()` keys off.
        parsed = datetime.fromisoformat(raw)
        assert parsed.tzinfo is not None, f"naive timestamp leaked to the client: {raw!r}"
        assert parsed == stamp
        assert parsed.astimezone(timezone.utc).strftime("%H:%M") == "06:52"

    def test_fetch_timestamp_matches_packs_endpoint(self, client, app_db):
        """The same field on /flights and /flights/{id}/packs must agree.

        They diverged: the packs route went through ``ensure_utc`` and the list
        route did not, so one screen said 06:52 UTC and the other 04:52 UTC for
        the same pack.
        """
        stamp = datetime(2026, 8, 20, 6, 52, 0, tzinfo=timezone.utc)
        f = _save_flight(app_db, route="rtz2", days_offset=+5, idx=42)
        _save_pack(app_db, f.id, assessment="GREEN", days_out=2, fetch_timestamp=stamp)

        rec = next(x for x in client.get("/api/flights").json() if x["id"] == f.id)
        from_list = rec["latest_briefing"]["fetch_timestamp"]

        packs = client.get(f"/api/flights/{f.id}/packs").json()
        from_packs = packs[0]["fetch_timestamp"]

        assert datetime.fromisoformat(from_list) == datetime.fromisoformat(from_packs)
        assert from_list == from_packs

    def test_no_pack_yields_null_briefing(self, client, app_db):
        f = _save_flight(app_db, route="rnone", days_offset=+5, idx=2)
        rec = next(x for x in client.get("/api/flights").json() if x["id"] == f.id)
        assert rec["latest_briefing"] is None

    def test_has_advisories_true_when_manifest_on_disk(self, client, app_db, tmp_path):
        f = _save_flight(app_db, route="radv", days_offset=+5, idx=3)
        adv_dir = tmp_path / "pack-radv"
        adv_dir.mkdir(parents=True, exist_ok=True)
        (adv_dir / "route_advisories.json").write_text("{}")
        _save_pack(app_db, f.id, assessment="GREEN", days_out=2, artifact_path=str(adv_dir))
        rec = next(x for x in client.get("/api/flights").json() if x["id"] == f.id)
        assert rec["latest_briefing"]["has_advisories"] is True


class TestPastPagination:
    """Only the past section paginates; future + recent always come back full."""

    def test_past_limit_slices_and_sets_total_header(self, client, app_db):
        # 5 flights older than the 30-day recent window (all "past"), plus one
        # future flight that must appear on every page.
        for i in range(5):
            _save_flight(app_db, route=f"p{i}", days_offset=-(40 + i), idx=i)
        _save_flight(app_db, route="fut", days_offset=+5, idx=99)

        resp = client.get("/api/flights?past_limit=2&past_offset=0")
        assert resp.headers["X-Past-Total"] == "5"
        body = resp.json()
        page1 = [f["id"] for f in body if f["section"] == "past"]
        assert len(page1) == 2
        assert sum(1 for f in body if f["section"] == "future") == 1

        resp2 = client.get("/api/flights?past_limit=2&past_offset=2")
        page2 = [f["id"] for f in resp2.json() if f["section"] == "past"]
        assert len(page2) == 2
        assert set(page1).isdisjoint(page2)  # no overlap across pages
        # Future still present on the second page.
        assert sum(1 for f in resp2.json() if f["section"] == "future") == 1

        resp3 = client.get("/api/flights?past_limit=2&past_offset=4")
        assert len([f for f in resp3.json() if f["section"] == "past"]) == 1

    def test_no_past_limit_returns_all_past(self, client, app_db):
        for i in range(3):
            _save_flight(app_db, route=f"q{i}", days_offset=-(40 + i), idx=i)
        resp = client.get("/api/flights")
        assert resp.headers["X-Past-Total"] == "3"
        assert len([f for f in resp.json() if f["section"] == "past"]) == 3


def _set_flight_order(app_db, order: str) -> None:
    """Write the ordering preference straight into ``app_prefs_json``."""
    s = app_db()
    row = s.get(UserPreferencesRow, DEV_USER_ID)
    data = json.loads(row.app_prefs_json) if row.app_prefs_json else {}
    data["flight_order"] = order
    row.app_prefs_json = json.dumps(data)
    s.commit()
    s.close()


class TestFlightOrderPreference:
    """Upcoming-flights ordering (#536): only the future section reorders."""

    def _future_ids(self, client) -> list[str]:
        return [f["id"] for f in client.get("/api/flights").json() if f["section"] == "future"]

    def test_default_is_furthest_first(self, client, app_db):
        near = _save_flight(app_db, route="fo-near", days_offset=+2, idx=1)
        far = _save_flight(app_db, route="fo-far", days_offset=+9, idx=2)
        mid = _save_flight(app_db, route="fo-mid", days_offset=+5, idx=3)
        assert self._future_ids(client) == [far.id, mid.id, near.id]

    def test_soonest_first_reverses_future_only(self, client, app_db):
        near = _save_flight(app_db, route="so-near", days_offset=+2, idx=1)
        far = _save_flight(app_db, route="so-far", days_offset=+9, idx=2)
        mid = _save_flight(app_db, route="so-mid", days_offset=+5, idx=3)
        _set_flight_order(app_db, "soonest_first")
        assert self._future_ids(client) == [near.id, mid.id, far.id]

    def test_recent_and_past_unchanged_under_both(self, client, app_db):
        # 2 recent (within 30 days, undebriefed, capped at 2) + 2 past.
        for i in range(4):
            _save_flight(app_db, route=f"ro{i}", days_offset=-(i + 1), idx=i)
        for i in range(2):
            _save_flight(app_db, route=f"po{i}", days_offset=-(40 + i), idx=10 + i)
        _save_flight(app_db, route="fo-a", days_offset=+3, idx=20)
        _save_flight(app_db, route="fo-b", days_offset=+8, idx=21)

        def sectioned(section: str) -> list[str]:
            return [f["id"] for f in client.get("/api/flights").json() if f["section"] == section]

        default_recent, default_past = sectioned("recent"), sectioned("past")
        _set_flight_order(app_db, "soonest_first")
        assert sectioned("recent") == default_recent
        assert sectioned("past") == default_past

    def test_past_pagination_unaffected(self, client, app_db):
        for i in range(5):
            _save_flight(app_db, route=f"pp{i}", days_offset=-(40 + i), idx=i)
        _save_flight(app_db, route="pp-fut", days_offset=+5, idx=99)
        _set_flight_order(app_db, "soonest_first")

        resp = client.get("/api/flights?past_limit=2&past_offset=0")
        assert resp.headers["X-Past-Total"] == "5"
        page1 = [f["id"] for f in resp.json() if f["section"] == "past"]
        page2 = [
            f["id"]
            for f in client.get("/api/flights?past_limit=2&past_offset=2").json()
            if f["section"] == "past"
        ]
        assert len(page1) == 2 and len(page2) == 2
        assert set(page1).isdisjoint(page2)
        # The full-order past list is still most-recent-first.
        full = [f["id"] for f in client.get("/api/flights").json() if f["section"] == "past"]
        assert full[:2] == page1
        assert full[2:4] == page2

    def test_unknown_stored_value_falls_back_to_default(self, client, app_db):
        near = _save_flight(app_db, route="uk-near", days_offset=+2, idx=1)
        far = _save_flight(app_db, route="uk-far", days_offset=+9, idx=2)
        _set_flight_order(app_db, "sideways")
        assert self._future_ids(client) == [far.id, near.id]

    def test_in_progress_flight_leads_under_soonest_first(self, client, app_db):
        # Departed 30 min ago on a 1-hour trip → still future, and the soonest
        # departure, so it sits at the very top while you're flying.
        flying = _save_flight(app_db, route="ip-now", days_offset=-0.5 / 24, idx=1)
        later = _save_flight(app_db, route="ip-later", days_offset=+3, idx=2)
        _set_flight_order(app_db, "soonest_first")
        assert self._future_ids(client) == [flying.id, later.id]


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
