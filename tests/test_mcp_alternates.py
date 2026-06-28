"""Tests for the MCP ``get_alternates`` tool (weather-based diverts).

The tool is a thin proxy: resolve the latest pack, fetch the snapshot's
alternates block over the client, shape it via ``connectors.views`` and wrap it
in the standard envelope. The client is faked rather than hitting the API — the
shaping itself is covered by ``test_connectors_alternates`` and the REST path by
``test_agent_endpoints``.
"""

from __future__ import annotations

import pytest

from weatherbrief.mcp import server

_RAW_ALTERNATES = {
    "destination_icao": "LSGS",
    "destination_category": "MVFR",
    "destination_crosswind_kt": 12.0,
    "corridor_nm": 20.0,
    "radius_nm": 50.0,
    "candidates_evaluated": 40,
    "approach_filter_relaxed": False,
    "nearest_improving": [
        {"axis": "category", "icao": "LFLP", "distance_from_dest_nm": 18.0,
         "position": "before"},
    ],
    "alternates": [
        {"icao": "LFLP", "name": "Annecy", "lat": 45.9, "lon": 6.1,
         "distance_from_dest_nm": 18.0, "position": "before", "flight_category": "VFR",
         "best_approach_type": "RNP", "point_of_entry": True, "is_major": False,
         "dominates_destination": True,
         "faa": {"verdict": "likely", "source": "nwp"},
         "easa": {"verdict": "likely", "source": "nwp"}},
    ],
    "alternate_requirement": {
        "destination_icao": "LSGS",
        "faa": {"regime": "faa", "status": "not_required", "reason": "ok", "source": "nwp",
                "ceiling": None, "visibility": None},
        "easa": {"regime": "easa", "status": "required", "reason": "low", "source": "nwp",
                 "ceiling": None, "visibility": None},
        "caveats": ["estimated minima"],
    },
}


class FakeClient:
    def __init__(self, **overrides):
        self._refresh = overrides.get("refresh", {"active": False})
        self._pack = overrides.get("pack", {"fetch_timestamp": "20260628T0600Z"})
        self._alternates = overrides.get("alternates", _RAW_ALTERNATES)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_refresh_status(self, flight_id):
        return self._refresh

    def get_latest_pack(self, flight_id):
        return self._pack

    def get_alternates(self, flight_id, timestamp):
        return self._alternates


@pytest.fixture
def patch_client(monkeypatch):
    def _install(**overrides):
        client = FakeClient(**overrides)
        monkeypatch.setattr(server, "_get_client", lambda: client)
        return client
    return _install


def test_get_alternates_ready(patch_client):
    patch_client()
    res = server.get_alternates("flight-1")

    assert res["status"] == "ready"
    assert res["flight_id"] == "flight-1"
    assert res["briefing_timestamp"] == "20260628T0600Z"
    assert res["destination"]["icao"] == "LSGS"
    assert res["requirement"]["faa"]["status"] == "not_required"
    assert res["requirement"]["easa"]["status"] == "required"
    assert res["candidates"][0]["icao"] == "LFLP"
    assert res["candidates"][0]["point_of_entry"] is True
    # The weather-vs-operational caveat is always present.
    assert "operational" in res["note"].lower()
    assert res["web_url"].endswith("flight=flight-1")


def test_get_alternates_none_when_not_computed(patch_client):
    patch_client(alternates=None)
    res = server.get_alternates("flight-1")
    assert res["status"] == "none"
    assert "compute_alternates" in res["message"]


def test_get_alternates_processing(patch_client):
    patch_client(refresh={"active": True})
    res = server.get_alternates("flight-1")
    assert res["status"] == "processing"


def test_get_alternates_no_pack(patch_client):
    patch_client(pack=None)
    res = server.get_alternates("flight-1")
    assert res["status"] == "none"
    assert "refresh_briefing" in res["message"]
