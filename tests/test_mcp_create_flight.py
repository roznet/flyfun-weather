"""MCP create_flight: pending-coverage flights must not trigger a briefing."""

import pytest

from weatherbrief.mcp import server


class FakeClient:
    def __init__(self, flight):
        self._flight = flight
        self.refresh_called = False

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def create_flight(self, **kwargs):
        return self._flight

    def refresh_briefing(self, flight_id):
        self.refresh_called = True
        return {"status": "processing"}


@pytest.fixture
def patch_client(monkeypatch):
    def _install(flight):
        client = FakeClient(flight)
        monkeypatch.setattr(server, "_get_client", lambda: client)
        return client
    return _install


def _flight(**overrides):
    base = {
        "id": "egtk_lsgs-2026-08-05-54cd",
        "route_name": "egtk_lsgs",
        "waypoints": ["EGTK", "LSGS"],
        "departure_time": "2026-08-05T09:00:00+00:00",
        "cruise_altitude_ft": 8000,
        "flight_duration_hours": 2.0,
        "coverage": None,
    }
    base.update(overrides)
    return base


def test_pending_flight_does_not_trigger_briefing(patch_client):
    coverage = {
        "available_date": "2026-07-27",
        "full_briefing_date": "2026-07-29",
        "days_until_available": 21,
    }
    client = patch_client(_flight(coverage=coverage))
    res = server.create_flight(
        waypoints=["EGTK", "LSGS"],
        departure_time="2026-08-05T09:00:00+00:00",
        flight_duration_hours=2.0,
    )
    assert client.refresh_called is False
    assert res["briefing"]["status"] == "pending_coverage"
    assert res["briefing"]["available_date"] == "2026-07-27"
    assert "2026-07-27" in res["briefing"]["message"]
    # Coverage is surfaced on the flight block for the agent.
    assert res["flight"]["coverage"] == coverage


def test_in_range_flight_triggers_briefing(patch_client):
    client = patch_client(_flight(coverage=None))
    res = server.create_flight(
        waypoints=["EGTK", "LSGS"],
        departure_time="2026-07-08T09:00:00+00:00",
        flight_duration_hours=2.0,
    )
    assert client.refresh_called is True
    assert res["briefing"]["status"] == "processing"
    assert res["flight"]["coverage"] is None
