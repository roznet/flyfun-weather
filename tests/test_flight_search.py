"""Tests for the flights-list route filter (#542).

Covers the pure matcher and the ``past_q`` wiring on ``GET /api/flights``.
The matcher cases are mirrored in ``web/tests/unit/flight-search.test.ts`` —
the same rule runs client-side for the future + recent sections, so the two
implementations must agree. Keep the two case lists in step.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from flyfun_common.db import DEV_USER_ID, current_user_id, get_db
from flyfun_common.db.models import UserPreferencesRow, UserRow
from weatherbrief.api.app import create_app
from weatherbrief.api.flight_search import matches, parse_query
from weatherbrief.models import Flight
from weatherbrief.storage.flights import save_flight


class TestParseQuery:
    def test_blank_inputs_mean_no_filter(self):
        assert parse_query(None) == []
        assert parse_query("") == []
        assert parse_query("   ") == []

    def test_uppercases_and_splits(self):
        assert parse_query("lfmd egtf") == ["LFMD", "EGTF"]

    def test_collapses_runs_of_whitespace(self):
        assert parse_query("  LFMD \t  EGTF \n") == ["LFMD", "EGTF"]


class TestMatches:
    WPS = ["LFMD", "MTL", "POGOL", "SITET", "EGTF"]

    def test_no_tokens_matches_everything(self):
        assert matches(self.WPS, "", [])

    def test_endpoint_match(self):
        assert matches(self.WPS, "", parse_query("LFMD"))
        assert matches(self.WPS, "", parse_query("EGTF"))

    def test_intermediate_waypoint_matches(self):
        # The headline case: a fix in the middle of the route is findable even
        # though the compact route line elides it.
        assert matches(self.WPS, "", parse_query("POGOL"))

    def test_case_insensitive(self):
        assert matches(self.WPS, "", parse_query("lfmd"))

    def test_prefix_match_gives_country_filter(self):
        assert matches(self.WPS, "", parse_query("LF"))
        assert matches(self.WPS, "", parse_query("EG"))
        assert not matches(self.WPS, "", parse_query("ED"))

    def test_tokens_are_anded(self):
        assert matches(self.WPS, "", parse_query("LFMD EGTF"))
        # LFAT is not on this route, so the pair must not match.
        assert not matches(self.WPS, "", parse_query("LFMD LFAT"))

    def test_token_order_is_irrelevant(self):
        assert matches(self.WPS, "", parse_query("EGTF LFMD"))

    def test_suffix_does_not_match(self):
        # Prefix-only: "GTF" must not find EGTF, or every token would be a
        # substring search and "LF" would stop meaning "France".
        assert not matches(self.WPS, "", parse_query("GTF"))

    def test_route_name_words_match(self):
        assert matches([], "Alps trip", parse_query("alps"))
        assert matches([], "Alps trip", parse_query("trip"))
        assert not matches([], "Alps trip", parse_query("pyrenees"))

    def test_no_haystack_never_matches(self):
        assert not matches([], "", parse_query("LFMD"))
        assert not matches(None, None, parse_query("LFMD"))

    def test_blank_waypoints_ignored(self):
        assert matches(["", "LFMD"], "", parse_query("LFMD"))


# --- Endpoint wiring -------------------------------------------------------


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


def _save(app_db, *, waypoints: list[str], days_offset: int, idx: int) -> Flight:
    """Persist a flight `days_offset` days from now (negative = past)."""
    s = app_db()
    dep = datetime.now(timezone.utc) + timedelta(days=days_offset)
    route = "_".join(w.lower() for w in waypoints)
    h = hashlib.sha256(json.dumps({"idx": idx}, sort_keys=True).encode()).hexdigest()[:4]
    f = Flight(
        id=f"{route}-{dep.strftime('%Y-%m-%d')}-{h}",
        user_id=DEV_USER_ID,
        route_name=route,
        waypoints=waypoints,
        departure_time=dep,
        cruise_altitude_ft=8000,
        flight_ceiling_ft=18000,
        flight_duration_hours=1.0,
        created_at=datetime.now(timezone.utc) - timedelta(days=60),
    )
    save_flight(s, f, DEV_USER_ID)
    s.commit()
    s.close()
    return f


def _sections(payload: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {"future": [], "recent": [], "past": []}
    for f in payload:
        out.setdefault(f["section"], []).append(f)
    return out


class TestPastQueryEndpoint:
    def test_filters_past_section(self, client, app_db):
        # Old enough to be "past", not "recent" (recent window is 30 days).
        _save(app_db, waypoints=["LESB", "LFMD"], days_offset=-90, idx=1)
        _save(app_db, waypoints=["EGTF", "LFAT"], days_offset=-91, idx=2)

        resp = client.get("/api/flights?past_q=LESB")
        assert resp.status_code == 200
        past = _sections(resp.json())["past"]
        assert [f["waypoints"] for f in past] == [["LESB", "LFMD"]]

    def test_past_total_header_reports_matches(self, client, app_db):
        for i in range(4):
            _save(app_db, waypoints=["EGTF", "LFAT"], days_offset=-90 - i, idx=i)
        _save(app_db, waypoints=["LESB", "LFMD"], days_offset=-100, idx=9)

        unfiltered = client.get("/api/flights")
        assert unfiltered.headers["X-Past-Total"] == "5"

        # The header must describe the *filtered* set, otherwise "show more"
        # offers pages that don't exist.
        filtered = client.get("/api/flights?past_q=LESB")
        assert filtered.headers["X-Past-Total"] == "1"

    def test_filter_applies_before_pagination(self, client, app_db):
        # 3 matches + 3 non-matches; a page of 2 must return 2 *matches*, not
        # whatever survives filtering the first 2 rows of the whole history.
        for i in range(3):
            _save(app_db, waypoints=["EGTF", "LFAT"], days_offset=-90 - i, idx=i)
        for i in range(3):
            _save(app_db, waypoints=["LESB", "LFMD"], days_offset=-95 - i, idx=10 + i)

        resp = client.get("/api/flights?past_q=LESB&past_limit=2")
        past = _sections(resp.json())["past"]
        assert len(past) == 2
        assert all(f["waypoints"] == ["LESB", "LFMD"] for f in past)
        assert resp.headers["X-Past-Total"] == "3"

    def test_does_not_touch_future_section(self, client, app_db):
        # past_q is past-scoped on purpose: the web client filters the
        # always-fully-loaded future + recent sections in the browser.
        _save(app_db, waypoints=["EGTF", "LFAT"], days_offset=+5, idx=1)
        _save(app_db, waypoints=["LESB", "LFMD"], days_offset=-90, idx=2)

        resp = client.get("/api/flights?past_q=LESB")
        sections = _sections(resp.json())
        assert [f["waypoints"] for f in sections["future"]] == [["EGTF", "LFAT"]]
        assert len(sections["past"]) == 1

    def test_intermediate_waypoint_is_findable(self, client, app_db):
        _save(app_db, waypoints=["LFMD", "MTL", "POGOL", "EGTF"], days_offset=-90, idx=1)
        resp = client.get("/api/flights?past_q=POGOL")
        assert len(_sections(resp.json())["past"]) == 1

    def test_blank_query_is_no_filter(self, client, app_db):
        _save(app_db, waypoints=["EGTF", "LFAT"], days_offset=-90, idx=1)
        _save(app_db, waypoints=["LESB", "LFMD"], days_offset=-91, idx=2)

        for q in ("", "   "):
            resp = client.get("/api/flights", params={"past_q": q})
            assert len(_sections(resp.json())["past"]) == 2, q

    def test_no_matches_is_empty_not_error(self, client, app_db):
        _save(app_db, waypoints=["EGTF", "LFAT"], days_offset=-90, idx=1)
        resp = client.get("/api/flights?past_q=KJFK")
        assert resp.status_code == 200
        assert _sections(resp.json())["past"] == []
        assert resp.headers["X-Past-Total"] == "0"

    def test_overlong_query_rejected(self, client):
        resp = client.get("/api/flights", params={"past_q": "L" * 65})
        assert resp.status_code == 422
