"""Tests for /api/trips and the trip-related flight behaviours (#602)."""

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
from weatherbrief.db.models import FlightProfileRow, FlightRow, FlightTripRow
from weatherbrief.models import Flight
from weatherbrief.storage.flights import save_flight

_NOW = datetime.now(timezone.utc)

#: Every route scoped to a single trip id, with a body where one is required.
#: Held as an explicit table so each entry is a deliberate claim about what a
#: foreign caller gets; ``test_the_foreign_route_table_covers_every_trip_scoped_route``
#: stops it drifting behind the router.
_FOREIGN_TRIP_ROUTES = [
    ("get", "/api/trips/otherstrip", None),
    ("get", "/api/trips/otherstrip/summary", None),
    ("patch", "/api/trips/otherstrip", {"name": "mine now"}),
    ("delete", "/api/trips/otherstrip", None),
    ("post", "/api/trips/otherstrip/legs", {"flight_ids": []}),
    ("delete", "/api/trips/otherstrip/legs/whatever", None),
    ("post", "/api/trips/otherstrip/refresh", None),
    ("get", "/api/trips/otherstrip/refresh/status", None),
    ("post", "/api/trips/otherstrip/ai-summary", None),
]


@pytest.fixture
def app_db():
    from conftest import make_app_engine
    engine = make_app_engine()
    TestSession = sessionmaker(bind=engine)
    s = TestSession()
    s.add(UserRow(
        id=DEV_USER_ID, provider="local", provider_sub="dev",
        email="dev@localhost", display_name="Dev", approved=True,
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


def _make_flight(session, waypoints: list[str], days_ahead: float, hour: int = 9) -> Flight:
    dep = (_NOW + timedelta(days=days_ahead)).replace(hour=hour, minute=0, second=0, microsecond=0)
    route_name = "_".join(w.lower() for w in waypoints)
    h = hashlib.sha256(json.dumps(
        {"alt": 8000, "ceil": 18000, "dur": 1.0, "time": dep.strftime("%H:%M"),
         "route": route_name, "user": DEV_USER_ID},
        sort_keys=True,
    ).encode()).hexdigest()[:6]
    flight = Flight(
        id=f"{route_name}-{dep.strftime('%Y-%m-%d')}-{h}",
        user_id=DEV_USER_ID,
        route_name=route_name,
        waypoints=waypoints,
        departure_time=dep,
        cruise_altitude_ft=8000,
        flight_ceiling_ft=18000,
        flight_duration_hours=1.0,
        created_at=_NOW,
    )
    save_flight(session, flight, DEV_USER_ID)
    return flight


@pytest.fixture
def chain(app_db):
    """Fri EGTF→LSGS, Sun LSGS→LFAT→EGTF — the worked example from the design."""
    s = app_db()
    out = _make_flight(s, ["EGTF", "LSGS"], 3)
    mid = _make_flight(s, ["LSGS", "LFAT"], 5, hour=10)
    back = _make_flight(s, ["LFAT", "EGTF"], 5, hour=13)
    s.commit()
    s.close()
    return [out, mid, back]


class TestTripCrud:
    def test_group_as_trip_derives_a_name_from_the_chain(self, client, chain):
        r = client.post("/api/trips", json={"flight_ids": [f.id for f in chain]})
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["summary"]["chain_label"] == "EGTF → LSGS → LFAT → EGTF"
        # Derived default: the chain plus its date span.
        assert body["name"].startswith("EGTF → LSGS → LFAT → EGTF, ")
        assert len(body["flight_ids"]) == 3

    def test_a_one_leg_trip_is_valid(self, client, chain):
        r = client.post("/api/trips", json={"flight_ids": [chain[0].id]})
        assert r.status_code == 201, r.text
        assert r.json()["summary"]["total_legs"] == 1

    def test_an_empty_create_survives_its_own_prune(self, client, app_db):
        """`prune_empty_trips` must not delete the trip created a line earlier.

        It ran unscoped, so `flight_ids: []` created a row and immediately
        removed it — returning 201 for a trip that 404s on the very next GET.
        """
        r = client.post("/api/trips", json={"flight_ids": [], "name": "Later"})
        assert r.status_code == 201, r.text
        trip_id = r.json()["id"]
        assert client.get(f"/api/trips/{trip_id}").status_code == 200

    def test_an_empty_create_without_a_name_also_survives(self, client, app_db):
        r = client.post("/api/trips", json={"flight_ids": []})
        assert r.status_code == 201, r.text
        assert client.get(f"/api/trips/{r.json()['id']}").status_code == 200

    def test_readding_an_existing_leg_does_not_trip_the_cap(self, client, chain):
        trip = client.post("/api/trips", json={"flight_ids": [chain[0].id]}).json()
        # A client retry re-sending a leg already in the trip is not growth.
        r = client.post(
            f"/api/trips/{trip['id']}/legs", json={"flight_ids": [chain[0].id]},
        )
        assert r.status_code == 200, r.text
        assert r.json()["flight_ids"] == [chain[0].id]

    def test_add_legs_with_an_empty_body_is_a_no_op_not_a_500(self, client):
        """An empty `flight_ids` on an empty trip used to destroy it.

        `prune_empty_trips` ran without `keep`, removing the trip mid-request;
        the response builder then dereferenced None and surfaced a raw 500,
        with the trip gone as a side effect of what should have been a no-op.
        """
        trip = client.post(
            "/api/trips", json={"flight_ids": [], "name": "Placeholder"},
        ).json()
        r = client.post(f"/api/trips/{trip['id']}/legs", json={"flight_ids": []})
        assert r.status_code == 200, r.text
        assert r.json()["flight_ids"] == []
        assert client.get(f"/api/trips/{trip['id']}").status_code == 200

    def test_unknown_flight_is_a_404_not_a_partial_group(self, client, chain):
        r = client.post(
            "/api/trips", json={"flight_ids": [chain[0].id, "does-not-exist"]},
        )
        assert r.status_code == 404
        # Nothing was grouped.
        assert client.get("/api/trips").json() == []

    def test_add_legs_moves_a_leg_out_of_its_previous_trip(self, client, chain, app_db):
        first = client.post("/api/trips", json={"flight_ids": [chain[0].id]}).json()
        second = client.post("/api/trips", json={"flight_ids": [chain[1].id]}).json()

        r = client.post(f"/api/trips/{second['id']}/legs", json={"flight_ids": [chain[0].id]})
        assert r.status_code == 200, r.text
        assert set(r.json()["flight_ids"]) == {chain[0].id, chain[1].id}

        # One trip per leg — and the emptied container is pruned rather than
        # left as something that can never render.
        s = app_db()
        assert s.get(FlightTripRow, first["id"]) is None
        s.close()

    def test_remove_leg_unlinks_and_never_deletes_the_flight(self, client, chain, app_db):
        trip = client.post(
            "/api/trips", json={"flight_ids": [chain[0].id, chain[1].id]},
        ).json()
        r = client.delete(f"/api/trips/{trip['id']}/legs/{chain[0].id}")
        assert r.status_code == 200, r.text
        assert r.json()["flight_ids"] == [chain[1].id]

        s = app_db()
        row = s.get(FlightRow, chain[0].id)
        assert row is not None          # the flight survives
        assert row.trip_id is None      # only the membership went
        s.close()

    def test_delete_trip_keeps_the_flights(self, client, chain, app_db):
        trip = client.post("/api/trips", json={"flight_ids": [f.id for f in chain]}).json()
        assert client.delete(f"/api/trips/{trip['id']}").status_code == 204

        s = app_db()
        assert s.get(FlightTripRow, trip["id"]) is None
        for flight in chain:
            row = s.get(FlightRow, flight.id)
            assert row is not None
            assert row.trip_id is None
        s.close()

    def test_patch_updates_the_trip_controls(self, client, chain):
        trip = client.post("/api/trips", json={"flight_ids": [chain[0].id]}).json()
        r = client.patch(
            f"/api/trips/{trip['id']}",
            json={"name": "Sion weekend", "auto_refresh": True, "notify_override": "mute"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["name"] == "Sion weekend"
        assert body["auto_refresh"] is True
        assert body["notify_override"] == "mute"

    def test_another_users_trip_is_a_404(self, client, chain, app_db):
        s = app_db()
        s.add(UserRow(
            id="other", provider="local", provider_sub="other",
            email="other@localhost", display_name="Other", approved=True,
        ))
        s.add(FlightTripRow(
            id="otherstrip", user_id="other", name="Theirs", created_at=_NOW,
        ))
        s.commit()
        s.close()
        # Every trip-id-scoped route, not just the two that were covered here
        # before. The guard is re-applied per handler, so a dropped call site on
        # any one of them is a cross-user read/write — this codebase has already
        # shipped exactly that failure mode once, on the refresh guard.
        for method, path, body in _FOREIGN_TRIP_ROUTES:
            r = getattr(client, method)(path, **({"json": body} if body else {}))
            assert r.status_code == 404, f"{method.upper()} {path} -> {r.status_code}"

    def test_the_foreign_route_table_covers_every_trip_scoped_route(self, client):
        """The table above must not drift behind the router.

        A new ``/{trip_id}/...`` endpoint added without a line here would be
        untested for cross-user access and nothing would say so.
        """
        registered = {
            (m.lower(), r.path)
            for r in client.app.routes
            for m in getattr(r, "methods", set()) or set()
            if "/trips/{trip_id}" in getattr(r, "path", "")
        }
        covered = {
            (m, p.replace("/api/trips/otherstrip", "/api/trips/{trip_id}")
                 .replace("/whatever", "/{flight_id}"))
            for m, p, _ in _FOREIGN_TRIP_ROUTES
        }
        assert registered - covered == set(), (
            "trip-scoped routes with no cross-user 404 test: "
            f"{sorted(registered - covered)}"
        )


class TestTripOnFlightResponse:
    def test_flights_list_carries_the_trip_block(self, client, chain):
        client.post("/api/trips", json={"flight_ids": [f.id for f in chain]})
        flights = client.get("/api/flights").json()
        by_id = {f["id"]: f for f in flights}
        first = by_id[chain[0].id]
        assert first["trip"] is not None
        assert first["trip"]["position"] == 1
        assert first["trip"]["total"] == 3
        assert by_id[chain[2].id]["trip"]["position"] == 3

    def test_ungrouped_flight_has_no_trip_block(self, client, chain):
        flights = client.get("/api/flights").json()
        assert all(f["trip"] is None for f in flights)

    def test_the_trip_block_carries_the_trips_auto_refresh(self, client, chain):
        """The briefing page shows the *trip's* switch on a member leg, and keeps
        the per-leg hour editable beside it — it cannot do either without this."""
        trip = client.post(
            "/api/trips", json={"flight_ids": [f.id for f in chain]},
        ).json()
        flights = client.get("/api/flights").json()
        assert all(f["trip"]["auto_refresh"] is False for f in flights)
        # Single-flight endpoint builds the ref by the other code path.
        assert client.get(
            f"/api/flights/{chain[0].id}"
        ).json()["trip"]["auto_refresh"] is False

        client.patch(f"/api/trips/{trip['id']}", json={"auto_refresh": True})
        flights = client.get("/api/flights").json()
        assert all(f["trip"]["auto_refresh"] is True for f in flights)
        assert client.get(
            f"/api/flights/{chain[0].id}"
        ).json()["trip"]["auto_refresh"] is True


class TestReadTimeConsentGate:
    """A stored paragraph must not survive a leg switching AI off.

    `ai_summary_key` is built from packs and debriefs, so flipping
    `llm_digest_enabled` leaves the key untouched and the stored text un-stale.
    `_trip_to_response` therefore re-checks consent on every read; without that,
    `GET /api/trips/{id}` keeps serving an LLM paragraph to a pilot who opted
    out. Exercised end-to-end through a real profile rather than a patched gate.
    """

    def _trip_with_stored_paragraph(self, client, chain, app_db):
        trip = client.post("/api/trips", json={"flight_ids": [f.id for f in chain]}).json()
        s = app_db()
        row = s.get(FlightTripRow, trip["id"])
        row.ai_summary_text = "Sunday's return is the one to watch."
        s.commit()
        s.close()
        return trip["id"]

    def test_a_stored_paragraph_is_served_while_every_leg_consents(
        self, client, chain, app_db,
    ):
        """Positive control — without it the assertion below proves nothing."""
        trip_id = self._trip_with_stored_paragraph(client, chain, app_db)
        body = client.get(f"/api/trips/{trip_id}").json()
        assert body["ai_summary"] == "Sunday's return is the one to watch."

    def test_one_leg_with_ai_off_withholds_the_stored_paragraph(
        self, client, chain, app_db,
    ):
        trip_id = self._trip_with_stored_paragraph(client, chain, app_db)
        s = app_db()
        profile = FlightProfileRow(
            user_id=DEV_USER_ID, name="No AI",
            settings_json=json.dumps({"llm_digest_enabled": False}),
        )
        s.add(profile)
        s.flush()
        # Just one leg opts out — consent is unanimous or it is not consent.
        s.get(FlightRow, chain[1].id).profile_id = profile.id
        s.commit()
        s.close()

        body = client.get(f"/api/trips/{trip_id}").json()
        assert body["ai_summary"] is None
        # Withheld at read time, not deleted: the row still holds the text, so
        # turning AI back on restores it without re-billing Haiku.
        s = app_db()
        assert s.get(FlightTripRow, trip_id).ai_summary_text is not None
        s.close()


class TestMoveAndDuplicate:
    def test_move_keeps_trip_membership_by_default(self, client, chain, app_db):
        trip = client.post("/api/trips", json={"flight_ids": [f.id for f in chain]}).json()
        new_departure = (_NOW + timedelta(days=4)).replace(
            hour=9, minute=0, second=0, microsecond=0,
        )
        r = client.post(
            f"/api/flights/{chain[0].id}/move",
            json={"departure_time": new_departure.isoformat()},
        )
        assert r.status_code == 200, r.text
        moved = r.json()
        # /move recreates the row, so this only holds because trip_id is
        # carried explicitly.
        assert moved["trip"] is not None
        assert moved["trip"]["id"] == trip["id"]

        s = app_db()
        assert s.get(FlightRow, moved["id"]).trip_id == trip["id"]
        s.close()

    def test_move_can_drop_the_leg_out_of_the_trip(self, client, chain):
        client.post("/api/trips", json={"flight_ids": [f.id for f in chain]})
        new_departure = (_NOW + timedelta(days=4)).replace(
            hour=9, minute=0, second=0, microsecond=0,
        )
        r = client.post(
            f"/api/flights/{chain[0].id}/move",
            json={"departure_time": new_departure.isoformat(), "keep_in_trip": False},
        )
        assert r.status_code == 200, r.text
        assert r.json()["trip"] is None

    def test_move_reorders_the_chain_with_no_position_fixup(self, client, chain):
        """Order derives from departure_time — the payoff for storing no position."""
        trip = client.post("/api/trips", json={"flight_ids": [f.id for f in chain]}).json()
        # Push the outbound past both return legs.
        later = (_NOW + timedelta(days=6)).replace(
            hour=9, minute=0, second=0, microsecond=0,
        )
        moved = client.post(
            f"/api/flights/{chain[0].id}/move",
            json={"departure_time": later.isoformat()},
        ).json()
        summary = client.get(f"/api/trips/{trip['id']}/summary").json()
        assert [l["flight_id"] for l in summary["legs"]][-1] == moved["id"]

    def test_duplicate_does_not_inherit_the_trip(self, client, chain):
        client.post("/api/trips", json={"flight_ids": [f.id for f in chain]})
        departure = (_NOW + timedelta(days=17)).replace(
            hour=9, minute=0, second=0, microsecond=0,
        )
        r = client.post("/api/flights", json={
            "waypoints": ["EGTF", "LSGS"],
            "departure_time": departure.isoformat(),
            "flight_duration_hours": 1.0,
        })
        assert r.status_code == 201, r.text
        assert r.json()["trip"] is None

    def test_duplicate_can_opt_in_to_the_trip(self, client, chain):
        trip = client.post("/api/trips", json={"flight_ids": [f.id for f in chain]}).json()
        departure = (_NOW + timedelta(days=18)).replace(
            hour=9, minute=0, second=0, microsecond=0,
        )
        r = client.post("/api/flights", json={
            "waypoints": ["EGTF", "LSGS"],
            "departure_time": departure.isoformat(),
            "flight_duration_hours": 1.0,
            "trip_id": trip["id"],
        })
        assert r.status_code == 201, r.text
        assert r.json()["trip"]["id"] == trip["id"]

    def test_creating_into_someone_elses_trip_is_a_404(self, client, chain, app_db):
        s = app_db()
        s.add(UserRow(
            id="other2", provider="local", provider_sub="other2",
            email="other2@localhost", display_name="Other", approved=True,
        ))
        s.add(FlightTripRow(id="theirtrip", user_id="other2", name="T", created_at=_NOW))
        s.commit()
        s.close()
        departure = (_NOW + timedelta(days=19)).replace(
            hour=9, minute=0, second=0, microsecond=0,
        )
        r = client.post("/api/flights", json={
            "waypoints": ["EGTF", "LSGS"],
            "departure_time": departure.isoformat(),
            "flight_duration_hours": 1.0,
            "trip_id": "theirtrip",
        })
        assert r.status_code == 404


class TestDeleteLeavesNoStrandedTrips:
    def test_bulk_delete_prunes_the_emptied_container(self, client, chain, app_db):
        trip = client.post(
            "/api/trips", json={"flight_ids": [chain[0].id, chain[1].id]},
        ).json()
        r = client.post(
            "/api/flights/bulk-delete", json={"ids": [chain[0].id, chain[1].id]},
        )
        assert r.status_code == 200, r.text
        s = app_db()
        assert s.get(FlightTripRow, trip["id"]) is None
        s.close()

    def test_bulk_delete_keeps_a_trip_that_still_has_a_leg(self, client, chain, app_db):
        trip = client.post(
            "/api/trips", json={"flight_ids": [chain[0].id, chain[1].id]},
        ).json()
        client.post("/api/flights/bulk-delete", json={"ids": [chain[0].id]})
        s = app_db()
        # A 1-leg trip is valid; deleting a user-named container as a side
        # effect of a flight delete would be surprising.
        assert s.get(FlightTripRow, trip["id"]) is not None
        s.close()

    def test_single_delete_prunes_too(self, client, chain, app_db):
        trip = client.post("/api/trips", json={"flight_ids": [chain[0].id]}).json()
        assert client.delete(f"/api/flights/{chain[0].id}").status_code == 204
        s = app_db()
        assert s.get(FlightTripRow, trip["id"]) is None
        s.close()


class TestTripRefreshEndpoint:
    def test_refresh_with_no_remaining_legs_is_a_benign_no_op(self, client, app_db):
        s = app_db()
        past = _make_flight(s, ["EGTF", "LSGS"], -5)
        s.commit()
        s.close()
        trip = client.post("/api/trips", json={"flight_ids": [past.id]}).json()
        r = client.post(f"/api/trips/{trip['id']}/refresh")
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["active"] is False
        assert "No remaining legs" in body["message"]

    def test_a_second_press_while_running_is_a_409(self, client, chain, app_db, monkeypatch):
        trip = client.post("/api/trips", json={"flight_ids": [f.id for f in chain]}).json()
        # Don't actually run the pipeline — this test is about admission.
        from weatherbrief.api import trip_refresh
        monkeypatch.setattr(trip_refresh, "kick", lambda *a, **k: None)

        first = client.post(f"/api/trips/{trip['id']}/refresh")
        assert first.status_code == 200, first.text
        assert first.json()["active"] is True
        assert first.json()["total"] == 3

        second = client.post(f"/api/trips/{trip['id']}/refresh")
        assert second.status_code == 409

    def test_status_reports_progress(self, client, chain, monkeypatch):
        trip = client.post("/api/trips", json={"flight_ids": [f.id for f in chain]}).json()
        from weatherbrief.api import trip_refresh
        monkeypatch.setattr(trip_refresh, "kick", lambda *a, **k: None)
        client.post(f"/api/trips/{trip['id']}/refresh")
        r = client.get(f"/api/trips/{trip['id']}/refresh/status")
        assert r.status_code == 200, r.text
        assert r.json()["total"] == 3
        assert r.json()["completed"] == 0


class TestManualRefreshYieldsToTheTripDriver:
    """The per-flight Refresh button must respect a live trip run.

    ``leg_is_claimed`` is unit-tested in tests/test_trip_refresh.py, but the
    invariant that matters is that both refresh endpoints actually *call* it.
    The design doc records that fixing only the scheduler once already left
    this reopened through the path a user is most likely to take, so the guard
    is pinned here at the endpoint, where a refactor that reorders or drops it
    is what would ship silently.
    """

    @pytest.fixture
    def running_trip(self, client, chain, tmp_path, monkeypatch):
        from weatherbrief.api import trip_refresh

        # Admission only — the pipeline itself is not the subject here.
        monkeypatch.setattr(trip_refresh, "kick", lambda *a, **k: None)
        client.app.state.db_path = str(tmp_path / "airports.db")
        trip = client.post(
            "/api/trips", json={"flight_ids": [f.id for f in chain]},
        ).json()
        started = client.post(f"/api/trips/{trip['id']}/refresh")
        assert started.json()["active"] is True
        return trip

    def test_queued_refresh_of_a_claimed_leg_is_a_409(self, client, chain, running_trip):
        # leg 2 is pending, not yet current: the registry cannot see it, so
        # only the explicit guard stands between it and a second in-flight leg.
        r = client.post(f"/api/flights/{chain[1].id}/packs/refresh")
        assert r.status_code == 409, r.text
        assert "trip refresh" in r.json()["detail"]

    def test_the_stream_endpoint_refuses_the_same_leg(
        self, client, chain, running_trip, app_db, monkeypatch,
    ):
        # The stream manages its own session rather than taking Depends(get_db).
        from weatherbrief.api import packs as packs_api

        monkeypatch.setattr(packs_api, "SessionLocal", app_db)
        r = client.post(f"/api/flights/{chain[1].id}/packs/refresh/stream")
        assert r.status_code == 409, r.text
        # Also delivered *as an SSE error event*, not only as the status, so a
        # client that has already opened the stream renders the reason rather
        # than surfacing a bare failed fetch.
        assert '"type": "error"' in r.text or '"type":"error"' in r.text
        assert "trip refresh" in r.text

    def test_an_ungrouped_flight_is_not_blocked(self, client, app_db, running_trip):
        s = app_db()
        loner = _make_flight(s, ["EGTF", "EGLL"], 4, hour=15)
        s.commit()
        s.close()
        r = client.post(f"/api/flights/{loner.id}/packs/refresh")
        assert r.status_code != 409, r.text
