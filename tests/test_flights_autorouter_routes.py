"""Tests for GET /api/flights/autorouter-routes (issue #151)."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import sessionmaker

from flyfun_common.db import DEV_USER_ID, current_user_id, get_db
from flyfun_common.db.models import UserPreferencesRow, UserRow
from weatherbrief.api.app import create_app
from weatherbrief.api import flights as flights_module


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


def _patch_token(monkeypatch, value: str | None) -> None:
    """Make load_autorouter_token return ``value`` for the new endpoint."""
    from weatherbrief.api import preferences as prefs_module

    monkeypatch.setattr(prefs_module, "load_autorouter_token", lambda db, uid: value)


def _patch_httpx_get(monkeypatch, *, status_code: int, json_body=None, raise_exc=None):
    """Replace httpx.get used by the endpoint."""
    import httpx

    captured: dict = {}

    def _fake_get(url, params=None, headers=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        captured["headers"] = headers
        if raise_exc is not None:
            raise raise_exc
        resp = MagicMock(spec=httpx.Response)
        resp.status_code = status_code
        resp.text = json.dumps(json_body) if json_body is not None else ""
        resp.json = MagicMock(return_value=json_body)
        return resp

    monkeypatch.setattr(httpx, "get", _fake_get)
    return captured


# --- Tests ---


class TestAutorouterRoutes:
    def test_not_linked_returns_409(self, client, monkeypatch):
        _patch_token(monkeypatch, None)
        r = client.get("/api/flights/autorouter-routes")
        assert r.status_code == 409
        assert r.json()["detail"] == "autorouter_not_linked"

    def test_happy_path_maps_fields(self, client, monkeypatch):
        _patch_token(monkeypatch, "tok-abc")
        captured = _patch_httpx_get(monkeypatch, status_code=200, json_body=[
            {
                "routeid": "r1",
                "departure": "EGTK",
                "destination": "LFAT",
                "departurename": "Oxford Kidlington",
                "destinationname": "Le Touquet",
                "departuretime": 1714723200,  # 2024-05-03 08:00:00 UTC
                "fplan": "(FPL-N122DR-VG-S22T/L-EGTK0930-N0166F085 DCT LFAT-EGTK0033)",
                "routedistance": 142,
                "aircraftdescription": "Cirrus SR22 (2018)",
                "callsign": "N122DR",
                "gcddistance": 138,
                "routefuel": 60.0,
                "fuelunit": "L",
                "timestamp": 1714723000,
            },
            {
                "routeid": "r2",
                "departure": "LFPB",
                "destination": "LSGS",
                "departurename": None,
                "destinationname": None,
                "departuretime": None,
                "fplan": "(FPL-FOO)",
                "routedistance": None,
                "aircraftdescription": None,
                "callsign": None,
            },
        ])
        r = client.get("/api/flights/autorouter-routes?limit=10")
        assert r.status_code == 200
        body = r.json()
        # Forwarded params + bearer header.
        assert captured["url"].endswith("/v1.0/router/logs")
        assert captured["params"] == {"limit": 10, "order": "desc", "sort": "departuretime"}
        assert captured["headers"]["Authorization"] == "Bearer tok-abc"
        # Mapped routes.
        assert len(body["routes"]) == 2
        r1, r2 = body["routes"]
        assert r1["routeid"] == "r1"
        assert r1["departure"] == "EGTK"
        assert r1["destination"] == "LFAT"
        assert r1["departure_name"] == "Oxford Kidlington"
        assert r1["destination_name"] == "Le Touquet"
        assert r1["departure_time"] == "2024-05-03T08:00:00+00:00"
        assert r1["fplan"].startswith("(FPL-N122DR")
        assert r1["route_distance_nm"] == 142
        assert r1["aircraft_description"] == "Cirrus SR22 (2018)"
        assert r1["callsign"] == "N122DR"
        # Row with optional fields stripped to None.
        assert r2["departure_name"] is None
        assert r2["departure_time"] is None
        assert r2["route_distance_nm"] is None

    def test_limit_capped_to_100(self, client, monkeypatch):
        _patch_token(monkeypatch, "tok")
        captured = _patch_httpx_get(monkeypatch, status_code=200, json_body=[])
        r = client.get("/api/flights/autorouter-routes?limit=9999")
        assert r.status_code == 200
        assert captured["params"]["limit"] == 100

    def test_limit_floor_one(self, client, monkeypatch):
        _patch_token(monkeypatch, "tok")
        captured = _patch_httpx_get(monkeypatch, status_code=200, json_body=[])
        r = client.get("/api/flights/autorouter-routes?limit=0")
        assert r.status_code == 200
        assert captured["params"]["limit"] == 1

    def test_empty_list(self, client, monkeypatch):
        _patch_token(monkeypatch, "tok")
        _patch_httpx_get(monkeypatch, status_code=200, json_body=[])
        r = client.get("/api/flights/autorouter-routes")
        assert r.status_code == 200
        assert r.json() == {"routes": []}

    def test_skips_rows_missing_required_fields(self, client, monkeypatch):
        _patch_token(monkeypatch, "tok")
        _patch_httpx_get(monkeypatch, status_code=200, json_body=[
            {"routeid": "r1", "departure": "EGTK", "destination": "LFAT", "fplan": "x"},
            {"routeid": "r2", "departure": "EGTK", "destination": "LFAT"},  # no fplan
            {"departure": "EGTK", "destination": "LFAT", "fplan": "y"},  # no routeid
            "not-a-dict",
        ])
        r = client.get("/api/flights/autorouter-routes")
        assert r.status_code == 200
        ids = [row["routeid"] for row in r.json()["routes"]]
        assert ids == ["r1"]

    def test_401_clears_token_and_returns_409(self, client, monkeypatch):
        _patch_token(monkeypatch, "tok")
        _patch_httpx_get(monkeypatch, status_code=401, json_body={"error": "invalid_token"})
        cleared = {"called": False, "user_id": None}

        def _fake_clear(db, user_id):
            cleared["called"] = True
            cleared["user_id"] = user_id

        monkeypatch.setattr(flights_module, "_clear_autorouter_oauth_token", _fake_clear)
        r = client.get("/api/flights/autorouter-routes")
        assert r.status_code == 409
        assert r.json()["detail"] == "autorouter_not_linked"
        assert cleared["called"] is True
        assert cleared["user_id"] == DEV_USER_ID

    def test_500_returns_502(self, client, monkeypatch):
        _patch_token(monkeypatch, "tok")
        _patch_httpx_get(monkeypatch, status_code=500, json_body={"error": "boom"})
        r = client.get("/api/flights/autorouter-routes")
        assert r.status_code == 502
        assert r.json()["detail"] == "autorouter_upstream_error"

    def test_network_error_returns_502(self, client, monkeypatch):
        import httpx as _httpx

        _patch_token(monkeypatch, "tok")
        _patch_httpx_get(monkeypatch, status_code=0, raise_exc=_httpx.ConnectError("nope"))
        r = client.get("/api/flights/autorouter-routes")
        assert r.status_code == 502
        assert r.json()["detail"] == "autorouter_unreachable"

    def test_non_list_payload_returns_502(self, client, monkeypatch):
        _patch_token(monkeypatch, "tok")
        _patch_httpx_get(monkeypatch, status_code=200, json_body={"unexpected": "shape"})
        r = client.get("/api/flights/autorouter-routes")
        assert r.status_code == 502

    def test_dict_wrapped_list_is_unwrapped(self, client, monkeypatch):
        """Autorouter currently wraps the entries in a dict (e.g. {"logs":[...]});
        the endpoint must accept either shape."""
        _patch_token(monkeypatch, "tok")
        _patch_httpx_get(monkeypatch, status_code=200, json_body={
            "total": 1,
            "logs": [
                {
                    "routeid": "r1",
                    "departure": "EGTK",
                    "destination": "LFAT",
                    "fplan": "(FPL-N122DR-VG-...-EGTK-LFAT)",
                },
            ],
        })
        r = client.get("/api/flights/autorouter-routes")
        assert r.status_code == 200
        ids = [row["routeid"] for row in r.json()["routes"]]
        assert ids == ["r1"]

    def test_known_key_wins_when_multiple_lists_present(self, client, monkeypatch):
        """Defensive: if Autorouter ever adds a pagination/links list
        alongside the routes list, the known wrapper key (logs/items/...)
        must take precedence over the first list value in iteration order."""
        _patch_token(monkeypatch, "tok")
        _patch_httpx_get(monkeypatch, status_code=200, json_body={
            "links": [],  # iteration order: this comes first
            "logs": [
                {
                    "routeid": "rZ",
                    "departure": "EGTK",
                    "destination": "LFAT",
                    "fplan": "(FPL-...)",
                },
            ],
        })
        r = client.get("/api/flights/autorouter-routes")
        assert r.status_code == 200
        ids = [row["routeid"] for row in r.json()["routes"]]
        assert ids == ["rZ"], "known wrapper key must win over earlier non-routes list"


class TestParseFplAutorouterFormat:
    """Regression: Autorouter emits FPLs with a space before each field
    separator (e.g. "...EGTF0730 -N0164F100..."), which euro_aip's parser
    mis-aligns. The endpoint must normalise that before parsing."""

    def test_space_dash_format_recovers_departure_destination(self, client):
        fpl = (
            "(FPL-GABCD-IG -S22T/L-SYBDGR/EB1U2 -EGTF0730 "
            "-N0164F100 GWC DCT NELKO DCT LORKU DCT ABDUS DCT BETUV DCT ERCOZ "
            "-LFRQ0134 -DOF/260516 PBN/B2D2S1 "
            "-P/TBN R/E J/ D/01 004 C YELLOW A/SILVER AND WHITE C/JOHN DOE)"
        )
        r = client.post("/api/flights/parse-fpl", json={"fpl_text": fpl})
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["error"] is None
        assert body["waypoints"][0] == "EGTF"
        assert body["waypoints"][-1] == "LFRQ"
        assert "GWC" in body["waypoints"]
        assert "ERCOZ" in body["waypoints"]
        assert body["altitude_ft"] == 10000


class TestClearAutorouterOauthToken:
    def test_preserves_other_creds(self, db_session, dev_user):
        from flyfun_common.credentials import (
            load_encrypted_creds,
            save_encrypted_creds,
        )
        from weatherbrief.api.flights import _clear_autorouter_oauth_token

        save_encrypted_creds(db_session, dev_user, {
            "autorouter": {"access_token": "stale"},
            "other_service": {"key": "value"},
        })
        _clear_autorouter_oauth_token(db_session, dev_user)
        remaining = load_encrypted_creds(db_session, dev_user)
        assert remaining == {"other_service": {"key": "value"}}

    def test_no_op_when_unset(self, db_session, dev_user):
        from weatherbrief.api.flights import _clear_autorouter_oauth_token

        # Should not raise even if no creds exist.
        _clear_autorouter_oauth_token(db_session, dev_user)
