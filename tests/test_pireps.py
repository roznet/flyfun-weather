"""Tests for PIREP API endpoints and storage layer."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from weatherbrief.api.app import create_app  # must import before flyfun_common.db
from flyfun_common.db import DEV_USER_ID, current_user_id, get_db
from flyfun_common.db.models import UserPreferencesRow, UserRow
from weatherbrief.db.models import (
    BriefingPackRow,
    FlightRow,
    PirepRow,
    UserAircraftRow,
)

# A second user for ownership tests
OTHER_USER_ID = "other-user-001"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _seed_user(session, user_id, *, pirep_view=True, pirep_publish=True):
    """Insert a user with PIREP permissions enabled."""
    session.add(UserRow(
        id=user_id, provider="local", provider_sub=user_id,
        email=f"{user_id}@test", display_name="Test", approved=True,
    ))
    session.flush()
    prefs = {"pirep_can_view": pirep_view, "pirep_can_publish": pirep_publish}
    session.add(UserPreferencesRow(user_id=user_id, app_prefs_json=json.dumps(prefs)))
    session.commit()


@pytest.fixture
def app_db():
    from conftest import make_app_engine
    engine = make_app_engine()
    TestSession = sessionmaker(bind=engine)
    session = TestSession()
    _seed_user(session, DEV_USER_ID)
    session.close()
    yield TestSession
    engine.dispose()


@pytest.fixture
def client(app_db, tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("JWT_SECRET", "test-secret")

    app = create_app()

    def _override_get_db():
        session = app_db()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    app.dependency_overrides[get_db] = _override_get_db
    app.dependency_overrides[current_user_id] = lambda: DEV_USER_ID

    # Disable rate limiters for tests
    from weatherbrief.api.throttle import pirep_burst_limiter, pirep_daily_limiter
    pirep_burst_limiter.max_requests = 10000
    pirep_daily_limiter.max_requests = 10000

    yield TestClient(app, raise_server_exceptions=False)

    # Restore defaults
    pirep_burst_limiter.max_requests = 1
    pirep_daily_limiter.max_requests = 50


def _pirep_payload(**overrides) -> dict:
    """Minimal valid PIREP payload."""
    base = {
        "observed_at": datetime.now(timezone.utc).isoformat(),
        "latitude": 48.0,
        "longitude": 2.0,
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# PIREP submission — single
# ---------------------------------------------------------------------------


class TestTimestampSerialization:
    """``observed_at``/``submitted_at`` must round-trip as UTC-qualified ISO.

    The columns were ``DateTime(timezone=True)`` — a no-op on MySQL — so reads
    came back naive and the API emitted "2026-04-11T08:14:19" with no offset.
    The web turns these into an *age* ("2h ago") and a staleness tag, so a fresh
    report read ~2 h old for a UTC+2 pilot; iOS parses them with a strict
    ``ISO8601DateFormatter()``, which returns nil outright for an offset-less
    string. Both columns are ``TZDateTime`` now (#520).

    These all read back through ``GET /api/pireps`` on purpose. The POST
    response is serialised from the still-in-session object, whose in-memory
    value is the aware datetime that was just assigned — so it looked correct
    even while the bug was live. Only a fresh read of the persisted row exposes
    it, which is also the path the map and the age labels actually use.
    """

    @staticmethod
    def _only_listed(client) -> dict:
        r = client.get("/api/pireps?hours=24")
        assert r.status_code == 200, r.text
        items = r.json()["items"]
        assert len(items) == 1, f"expected exactly one PIREP, got {len(items)}"
        return items[0]

    def test_roundtrip_preserves_the_instant(self, client):
        observed = datetime.now(timezone.utc) - timedelta(minutes=10)
        assert client.post(
            "/api/pireps", json=_pirep_payload(observed_at=observed.isoformat())
        ).status_code == 201

        listed = self._only_listed(client)
        for field in ("observed_at", "submitted_at"):
            raw = listed[field]
            parsed = datetime.fromisoformat(raw)
            assert parsed.tzinfo is not None, f"naive {field} leaked to the client: {raw!r}"

        assert datetime.fromisoformat(listed["observed_at"]) == observed

    def test_offset_input_is_normalised_not_shifted(self, client):
        """A client sending +02:00 local must land on the same instant.

        iOS/web send the pilot's own offset; the stored value is UTC, so a
        08:52+02:00 report must read back as 06:52Z, never 08:52Z.

        The instant is relative to now, deliberately. A fixed calendar date is
        a time bomb here: `_only_listed` queries `?hours=24`, so the report
        ages out of the listing window a day after the date was written and the
        test starts failing with "got 0" — which says nothing about offsets.
        The offset is what is under test, not the date.
        """
        local_tz = timezone(timedelta(hours=2))
        observed_utc = (
            datetime.now(timezone.utc) - timedelta(hours=2)
        ).replace(microsecond=0)
        local = observed_utc.astimezone(local_tz)
        assert local.utcoffset() == timedelta(hours=2), "fixture must send +02:00"

        assert client.post("/api/pireps", json=_pirep_payload(
            observed_at=local.isoformat(),
        )).status_code == 201

        parsed = datetime.fromisoformat(self._only_listed(client)["observed_at"])
        assert parsed == observed_utc
        # The wall-clock hour must be the UTC one, not the +02:00 one.
        assert parsed.astimezone(timezone.utc).strftime("%H:%M") == (
            observed_utc.strftime("%H:%M")
        )
        assert parsed.astimezone(local_tz).hour == (observed_utc.hour + 2) % 24

    def test_fresh_pirep_is_not_aged_by_the_utc_offset(self, client):
        """The symptom a pilot would actually see: a report filed a minute ago
        must read as ~0 minutes old, not one UTC offset old.

        This is what drives the web's "2h ago" label and its staleness tag.
        """
        observed = datetime.now(timezone.utc) - timedelta(minutes=1)
        assert client.post(
            "/api/pireps", json=_pirep_payload(observed_at=observed.isoformat())
        ).status_code == 201

        parsed = datetime.fromisoformat(self._only_listed(client)["observed_at"])
        age_minutes = (datetime.now(timezone.utc) - parsed).total_seconds() / 60
        assert 0 <= age_minutes < 5, f"fresh PIREP aged {age_minutes:.0f} min by a tz bug"

    def test_flight_window_query_accepts_a_naive_flightrow(self, client, app_db):
        """``observed_at`` is TZDateTime, which *raises* on a naive bind.

        The flight-scoped query derives its window from a raw ``FlightRow``,
        whose ``departure_time`` is still ``DateTime(timezone=True)`` and so
        reads back naive — that bind has to be promoted or this 500s.
        """
        session = app_db()
        session.add(FlightRow(
            id="tz-window", user_id=DEV_USER_ID, route_name="r",
            waypoints_json=json.dumps(["LFPG", "EGLL"]),
            departure_time=datetime.now(timezone.utc) + timedelta(hours=1),
            cruise_altitude_ft=8000, flight_duration_hours=2.0, private=False,
        ))
        session.commit()
        session.close()

        r = client.get("/api/pireps?flight_id=tz-window")
        assert r.status_code == 200, r.text


class TestSubmitPirep:
    def test_submit_basic(self, client):
        r = client.post("/api/pireps", json=_pirep_payload(
            icing_intensity="light", turbulence_intensity="none",
        ))
        assert r.status_code == 201
        data = r.json()
        assert data["icing_intensity"] == "light"
        assert data["is_own"] is True

    def test_submit_with_client_uuid(self, client):
        uid = str(uuid.uuid4())
        r = client.post("/api/pireps", json=_pirep_payload(client_uuid=uid))
        assert r.status_code == 201
        assert r.json()["client_uuid"] == uid

    def test_duplicate_client_uuid_returns_409(self, client):
        uid = str(uuid.uuid4())
        r1 = client.post("/api/pireps", json=_pirep_payload(client_uuid=uid))
        assert r1.status_code == 201
        r2 = client.post("/api/pireps", json=_pirep_payload(client_uuid=uid))
        assert r2.status_code == 409

    def test_out_of_bounds_returns_400(self, client):
        r = client.post("/api/pireps", json=_pirep_payload(latitude=80.0, longitude=2.0))
        assert r.status_code == 400
        assert "outside" in r.json()["detail"].lower()

    def test_invalid_uuid_format_returns_422(self, client):
        r = client.post("/api/pireps", json=_pirep_payload(client_uuid="not-a-uuid"))
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# PIREP submission — batch
# ---------------------------------------------------------------------------


class TestBatchSubmit:
    def test_batch_basic(self, client):
        items = [_pirep_payload(client_uuid=str(uuid.uuid4())) for _ in range(3)]
        r = client.post("/api/pireps/batch", json=items)
        assert r.status_code == 201
        assert len(r.json()) == 3

    def test_batch_skips_out_of_bounds(self, client):
        items = [
            _pirep_payload(client_uuid=str(uuid.uuid4()), latitude=48.0),
            _pirep_payload(client_uuid=str(uuid.uuid4()), latitude=80.0),  # out of bounds
        ]
        r = client.post("/api/pireps/batch", json=items)
        assert r.status_code == 201
        assert len(r.json()) == 1

    def test_batch_dedup_returns_existing(self, client):
        uid = str(uuid.uuid4())
        r1 = client.post("/api/pireps", json=_pirep_payload(client_uuid=uid))
        assert r1.status_code == 201

        # Batch with same uuid — should return existing, not fail
        r2 = client.post("/api/pireps/batch", json=[_pirep_payload(client_uuid=uid)])
        assert r2.status_code == 201
        assert len(r2.json()) == 1
        assert r2.json()[0]["id"] == r1.json()["id"]

    def test_batch_max_50(self, client):
        items = [_pirep_payload() for _ in range(51)]
        r = client.post("/api/pireps/batch", json=items)
        assert r.status_code == 400

    def test_batch_skips_invalid_aircraft(self, client, app_db):
        """Aircraft not owned by user is silently skipped."""
        items = [_pirep_payload(client_uuid=str(uuid.uuid4()), aircraft_id=99999)]
        r = client.post("/api/pireps/batch", json=items)
        assert r.status_code == 201
        assert len(r.json()) == 0

    def test_batch_skips_invalid_pack(self, client, app_db):
        """Pack not owned by user is silently skipped."""
        # Create another user and their flight/pack
        session = app_db()
        _seed_user(session, OTHER_USER_ID)
        flight = FlightRow(
            id="other-flight", user_id=OTHER_USER_ID,
            route_name="Test",
            waypoints_json='[{"icao":"EGTK","name":"Oxford","lat":51.8,"lon":-1.3}]',
            departure_time=datetime.now(timezone.utc),
            cruise_altitude_ft=8000, flight_ceiling_ft=18000,
            flight_duration_hours=2.0,
        )
        session.add(flight)
        session.flush()
        pack = BriefingPackRow(
            flight_id="other-flight", days_out=0,
            fetch_timestamp=datetime.now(timezone.utc),
        )
        session.add(pack)
        session.flush()
        other_pack_id = pack.id
        session.commit()
        session.close()

        items = [_pirep_payload(client_uuid=str(uuid.uuid4()), pack_id=other_pack_id)]
        r = client.post("/api/pireps/batch", json=items)
        assert r.status_code == 201
        assert len(r.json()) == 0  # skipped because pack belongs to other user


# ---------------------------------------------------------------------------
# Ownership validation — single submit
# ---------------------------------------------------------------------------


class TestOwnershipValidation:
    def test_aircraft_not_owned_returns_400(self, client, app_db):
        session = app_db()
        _seed_user(session, OTHER_USER_ID)
        ac = UserAircraftRow(user_id=OTHER_USER_ID, icao_type="C172")
        session.add(ac)
        session.flush()
        ac_id = ac.id
        session.commit()
        session.close()

        r = client.post("/api/pireps", json=_pirep_payload(aircraft_id=ac_id))
        assert r.status_code == 400

    def test_pack_not_owned_returns_400(self, client, app_db):
        session = app_db()
        _seed_user(session, OTHER_USER_ID)
        flight = FlightRow(
            id="other-flight-2", user_id=OTHER_USER_ID,
            route_name="Test",
            waypoints_json='[{"icao":"EGTK","name":"Oxford","lat":51.8,"lon":-1.3}]',
            departure_time=datetime.now(timezone.utc),
            cruise_altitude_ft=8000, flight_ceiling_ft=18000,
            flight_duration_hours=2.0,
        )
        session.add(flight)
        session.flush()
        pack = BriefingPackRow(
            flight_id="other-flight-2", days_out=0,
            fetch_timestamp=datetime.now(timezone.utc),
        )
        session.add(pack)
        session.flush()
        pack_id = pack.id
        session.commit()
        session.close()

        r = client.post("/api/pireps", json=_pirep_payload(pack_id=pack_id))
        assert r.status_code == 400

    def test_own_aircraft_accepted(self, client, app_db):
        session = app_db()
        ac = UserAircraftRow(user_id=DEV_USER_ID, icao_type="SR22")
        session.add(ac)
        session.flush()
        ac_id = ac.id
        session.commit()
        session.close()

        r = client.post("/api/pireps", json=_pirep_payload(aircraft_id=ac_id))
        assert r.status_code == 201
        assert r.json()["aircraft_type"] == "SR22"


# ---------------------------------------------------------------------------
# Permission gating
# ---------------------------------------------------------------------------


class TestPermissions:
    def test_publish_denied_without_permission(self, app_db, tmp_path, monkeypatch):
        """User without pirep_can_publish gets 403."""
        monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("JWT_SECRET", "test-secret")

        no_publish_user = "no-publish-user"
        session = app_db()
        _seed_user(session, no_publish_user, pirep_publish=False)
        session.close()

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
        app.dependency_overrides[current_user_id] = lambda: no_publish_user

        c = TestClient(app, raise_server_exceptions=False)
        r = c.post("/api/pireps", json=_pirep_payload())
        assert r.status_code == 403

    def test_view_denied_without_permission(self, app_db, tmp_path, monkeypatch):
        """User without pirep_can_view gets 403 on query."""
        monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
        monkeypatch.setenv("ENVIRONMENT", "production")
        monkeypatch.setenv("JWT_SECRET", "test-secret")

        no_view_user = "no-view-user"
        session = app_db()
        _seed_user(session, no_view_user, pirep_view=False, pirep_publish=False)
        session.close()

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
        app.dependency_overrides[current_user_id] = lambda: no_view_user

        c = TestClient(app, raise_server_exceptions=False)
        r = c.get("/api/pireps?hours=6")
        assert r.status_code == 403


# ---------------------------------------------------------------------------
# PIREP query
# ---------------------------------------------------------------------------


class TestQueryPireps:
    def test_query_by_hours(self, client):
        # Submit a PIREP
        client.post("/api/pireps", json=_pirep_payload())
        r = client.get("/api/pireps?hours=6")
        assert r.status_code == 200
        data = r.json()
        assert data["count"] >= 1
        assert len(data["items"]) >= 1

    def _add_flight(self, session, fid, owner, *, private):
        if session.get(UserRow, owner) is None:
            _seed_user(session, owner)
        now = datetime.now(timezone.utc)
        session.add(FlightRow(
            id=fid, user_id=owner, route_name="X",
            waypoints_json='[{"icao":"LFPG","name":"P","lat":49.0,"lon":2.5}]',
            departure_time=now - timedelta(hours=1),
            cruise_altitude_ft=8000, flight_ceiling_ft=18000,
            flight_duration_hours=2.0, private=private,
        ))
        session.commit()

    def test_query_other_users_private_flight_is_404(self, client, app_db):
        """Scoping by another user's private flight must not leak its PIREPs."""
        session = app_db()
        self._add_flight(session, "vis-private", OTHER_USER_ID, private=True)
        session.close()
        assert client.get("/api/pireps?flight_id=vis-private").status_code == 404

    def test_query_other_users_public_flight_ok(self, client, app_db):
        """Public/shared flights stay viewable by anyone (community model)."""
        session = app_db()
        self._add_flight(session, "vis-public", OTHER_USER_ID, private=False)
        session.close()
        assert client.get("/api/pireps?flight_id=vis-public").status_code == 200

    def test_query_nonexistent_flight_is_404(self, client, app_db):
        # Same 404 as private-not-owned → no existence oracle.
        assert client.get("/api/pireps?flight_id=ghost").status_code == 404

    def test_visible_linked_pack_ids_strips_unseen_flight(self, app_db):
        """Part 2: a community PIREP on a flight you can't see keeps its content
        but loses its pack linkage."""
        from weatherbrief.api.pireps import _visible_linked_pack_ids

        now = datetime.now(timezone.utc)
        session = app_db()
        self._add_flight(session, "pub", OTHER_USER_ID, private=False)
        self._add_flight(session, "priv", OTHER_USER_ID, private=True)
        self._add_flight(session, "mine", DEV_USER_ID, private=True)
        packs = {}
        for fid in ("pub", "priv", "mine"):
            p = BriefingPackRow(flight_id=fid, days_out=0, fetch_timestamp=now)
            session.add(p)
            session.flush()
            packs[fid] = p.id
        session.commit()

        rows = [
            PirepRow(pack_id=packs["pub"], user_id=OTHER_USER_ID),
            PirepRow(pack_id=packs["priv"], user_id=OTHER_USER_ID),
            PirepRow(pack_id=packs["mine"], user_id=DEV_USER_ID),
        ]
        visible = _visible_linked_pack_ids(session, rows, DEV_USER_ID)
        session.close()

        assert packs["pub"] in visible        # public flight → linkage shown
        assert packs["priv"] not in visible    # other's private → linkage hidden
        assert packs["mine"] in visible        # own flight → linkage shown

    def test_query_by_flight_id_includes_pireps_without_pack(self, client, app_db):
        """PIREPs without pack_id are found by flight_id via user + time window."""
        now = datetime.now(timezone.utc)
        session = app_db()
        # Create a flight owned by DEV_USER_ID
        flight = FlightRow(
            id="pirep-test-flight", user_id=DEV_USER_ID,
            route_name="Test",
            waypoints_json='[{"icao":"LFPG","name":"Paris","lat":49.0,"lon":2.5}]',
            departure_time=now - timedelta(hours=1),
            cruise_altitude_ft=8000, flight_ceiling_ft=18000,
            flight_duration_hours=2.0,
        )
        session.add(flight)
        session.flush()
        # Create a pack for the flight
        pack = BriefingPackRow(
            flight_id="pirep-test-flight", days_out=0,
            fetch_timestamp=now - timedelta(hours=2),
        )
        session.add(pack)
        session.flush()
        pack_id = pack.id
        session.commit()
        session.close()

        # Submit PIREP WITH pack_id
        r1 = client.post("/api/pireps", json=_pirep_payload(
            observed_at=now.isoformat(), pack_id=pack_id,
        ))
        assert r1.status_code == 201

        # Submit PIREP WITHOUT pack_id (within flight window)
        r2 = client.post("/api/pireps", json=_pirep_payload(
            observed_at=now.isoformat(),
        ))
        assert r2.status_code == 201

        # Query by flight_id — both should appear
        r = client.get("/api/pireps?flight_id=pirep-test-flight")
        assert r.status_code == 200
        ids = {p["id"] for p in r.json()["items"]}
        assert r1.json()["id"] in ids, "PIREP with pack_id should be found"
        assert r2.json()["id"] in ids, "PIREP without pack_id should be found via time window"

    def test_query_by_flight_id_excludes_outside_window(self, client, app_db):
        """PIREPs outside the flight time window are not included."""
        now = datetime.now(timezone.utc)
        session = app_db()
        flight = FlightRow(
            id="pirep-window-test", user_id=DEV_USER_ID,
            route_name="Test",
            waypoints_json='[{"icao":"LFPG","name":"Paris","lat":49.0,"lon":2.5}]',
            departure_time=now - timedelta(hours=10),
            cruise_altitude_ft=8000, flight_ceiling_ft=18000,
            flight_duration_hours=1.0,
        )
        session.add(flight)
        session.commit()
        session.close()

        # Submit PIREP far outside the flight window (now, but flight was 10h ago)
        r1 = client.post("/api/pireps", json=_pirep_payload(
            observed_at=now.isoformat(),
        ))
        assert r1.status_code == 201

        # Query by flight_id — PIREP should NOT appear
        r = client.get("/api/pireps?flight_id=pirep-window-test")
        assert r.status_code == 200
        assert r.json()["count"] == 0

    def test_query_by_bounds(self, client):
        client.post("/api/pireps", json=_pirep_payload(latitude=48.0, longitude=2.0))
        r = client.get("/api/pireps?bounds=47,1,49,3&hours=6")
        assert r.status_code == 200
        assert r.json()["count"] >= 1

    def test_query_outside_bounds_empty(self, client):
        client.post("/api/pireps", json=_pirep_payload(latitude=48.0, longitude=2.0))
        r = client.get("/api/pireps?bounds=55,10,60,15&hours=6")
        assert r.status_code == 200
        assert r.json()["count"] == 0

    def test_query_filters_hazard(self, client):
        client.post("/api/pireps", json=_pirep_payload(icing_intensity="moderate"))
        client.post("/api/pireps", json=_pirep_payload(turbulence_intensity="light"))

        r_icing = client.get("/api/pireps?hours=6&hazard=icing")
        r_turb = client.get("/api/pireps?hours=6&hazard=turbulence")
        assert r_icing.status_code == 200
        assert r_turb.status_code == 200
        # At least the one we submitted for each type
        assert any(p["icing_intensity"] == "moderate" for p in r_icing.json()["items"])
        assert any(p["turbulence_intensity"] == "light" for p in r_turb.json()["items"])

    def test_query_altitude_filter(self, client):
        client.post("/api/pireps", json=_pirep_payload(reported_altitude_ft=8000))
        client.post("/api/pireps", json=_pirep_payload(reported_altitude_ft=2000))

        r = client.get("/api/pireps?hours=6&altitude_min=5000&altitude_max=10000")
        assert r.status_code == 200
        for p in r.json()["items"]:
            alt = p.get("reported_altitude_ft") or p.get("gps_altitude_ft")
            if alt is not None:
                assert 5000 <= alt <= 10000


# ---------------------------------------------------------------------------
# Storage layer
# ---------------------------------------------------------------------------


class TestPirepStorage:
    def test_validate_european_bounds(self):
        from weatherbrief.storage.pireps import validate_european_bounds

        # Valid positions
        validate_european_bounds(48.0, 2.0)   # Paris
        validate_european_bounds(51.5, -0.1)  # London
        validate_european_bounds(34.0, -25.0)  # edge: SW corner

        # Out of bounds
        with pytest.raises(ValueError, match="Latitude"):
            validate_european_bounds(80.0, 2.0)
        with pytest.raises(ValueError, match="Longitude"):
            validate_european_bounds(48.0, 50.0)

    def test_create_pirep(self, db_session, dev_user):
        from weatherbrief.storage.pireps import create_pirep

        row = create_pirep(
            db_session, dev_user,
            observed_at=datetime.now(timezone.utc),
            latitude=48.0, longitude=2.0,
            icing_intensity="light",
        )
        assert row.id is not None
        assert row.user_id == dev_user
        assert row.icing_intensity == "light"

    def test_list_pireps_by_bounds(self, db_session, dev_user):
        from weatherbrief.storage.pireps import create_pirep, list_pireps

        create_pirep(
            db_session, dev_user,
            observed_at=datetime.now(timezone.utc),
            latitude=48.0, longitude=2.0,
        )
        create_pirep(
            db_session, dev_user,
            observed_at=datetime.now(timezone.utc),
            latitude=55.0, longitude=10.0,
        )

        results = list_pireps(db_session, bounds=(47, 1, 49, 3))
        assert len(results) == 1
        assert results[0].latitude == 48.0


# ---------------------------------------------------------------------------
# Aircraft API
# ---------------------------------------------------------------------------


class TestAircraftAPI:
    def test_create_and_list(self, client):
        r = client.post("/api/aircraft", json={"icao_type": "SR22", "tail_number": "N123AB"})
        assert r.status_code == 201
        data = r.json()
        assert data["icao_type"] == "SR22"
        assert data["tail_number"] == "N123AB"

        r2 = client.get("/api/aircraft")
        assert r2.status_code == 200
        assert len(r2.json()) == 1

    def test_update_aircraft(self, client):
        r = client.post("/api/aircraft", json={"icao_type": "C172"})
        ac_id = r.json()["id"]

        r2 = client.put(f"/api/aircraft/{ac_id}", json={"nickname": "My Skyhawk"})
        assert r2.status_code == 200
        assert r2.json()["nickname"] == "My Skyhawk"

    def test_delete_aircraft(self, client):
        r = client.post("/api/aircraft", json={"icao_type": "C172"})
        ac_id = r.json()["id"]

        r2 = client.delete(f"/api/aircraft/{ac_id}")
        assert r2.status_code == 204

        r3 = client.get("/api/aircraft")
        assert len(r3.json()) == 0

    def test_cannot_access_other_users_aircraft(self, client, app_db):
        session = app_db()
        _seed_user(session, OTHER_USER_ID)
        ac = UserAircraftRow(user_id=OTHER_USER_ID, icao_type="P28A")
        session.add(ac)
        session.flush()
        ac_id = ac.id
        session.commit()
        session.close()

        r = client.put(f"/api/aircraft/{ac_id}", json={"nickname": "hijack"})
        assert r.status_code == 404

        r2 = client.delete(f"/api/aircraft/{ac_id}")
        assert r2.status_code == 404

    def test_default_aircraft_toggle(self, client):
        r1 = client.post("/api/aircraft", json={"icao_type": "SR22", "is_default": True})
        r2 = client.post("/api/aircraft", json={"icao_type": "C172", "is_default": True})
        id1 = r1.json()["id"]

        # Second aircraft should now be default; first should not
        r3 = client.get("/api/aircraft")
        aircraft = {a["id"]: a for a in r3.json()}
        assert aircraft[id1]["is_default"] is False
        assert aircraft[r2.json()["id"]]["is_default"] is True

    def test_invalid_icao_type(self, client):
        r = client.post("/api/aircraft", json={"icao_type": "TOOLONG"})
        assert r.status_code == 422

    def test_invalid_tail_number(self, client):
        r = client.post("/api/aircraft", json={"icao_type": "C172", "tail_number": "a"})
        assert r.status_code == 422


# ---------------------------------------------------------------------------
# Aircraft storage
# ---------------------------------------------------------------------------


class TestAircraftStorage:
    def test_create_and_list(self, db_session, dev_user):
        from weatherbrief.storage.aircraft import create_aircraft, list_aircraft

        create_aircraft(db_session, dev_user, icao_type="SR22", tail_number="N123AB")
        create_aircraft(db_session, dev_user, icao_type="C172")

        results = list_aircraft(db_session, dev_user)
        assert len(results) == 2

    def test_default_cleared_on_new_default(self, db_session, dev_user):
        from weatherbrief.storage.aircraft import create_aircraft, list_aircraft

        ac1 = create_aircraft(db_session, dev_user, icao_type="SR22", is_default=True)
        ac2 = create_aircraft(db_session, dev_user, icao_type="C172", is_default=True)

        db_session.refresh(ac1)
        assert ac1.is_default is False
        assert ac2.is_default is True

    def test_delete_aircraft(self, db_session, dev_user):
        from weatherbrief.storage.aircraft import create_aircraft, delete_aircraft, list_aircraft

        ac = create_aircraft(db_session, dev_user, icao_type="C172")
        delete_aircraft(db_session, ac)
        assert len(list_aircraft(db_session, dev_user)) == 0
