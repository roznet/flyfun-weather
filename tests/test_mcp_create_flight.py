"""MCP create_flight: route-string interpretation (#745) and briefing trigger.

Pending-coverage flights must not trigger a briefing.
"""

import httpx
import pytest

from weatherbrief.mcp import server


def _interpretation(interpreted, skipped=(), off_route=()):
    return {
        "original_tokens": list(interpreted) + list(skipped) + list(off_route),
        "interpreted": list(interpreted),
        "skipped": list(skipped),
        "off_route": list(off_route),
        "waypoints": [],
    }


class FakeClient:
    def __init__(self, flight, interpretation=None):
        self._flight = flight
        self._interpretation = interpretation or _interpretation(flight["waypoints"])
        self.refresh_called = False
        self.interpret_calls: list[str] = []
        self.create_kwargs: dict | None = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def interpret_route(self, raw_route):
        self.interpret_calls.append(raw_route)
        if isinstance(self._interpretation, Exception):
            raise self._interpretation
        return self._interpretation

    def create_flight(self, **kwargs):
        self.create_kwargs = kwargs
        return self._flight

    def refresh_briefing(self, flight_id):
        self.refresh_called = True
        return {"status": "processing"}


@pytest.fixture
def patch_client(monkeypatch):
    def _install(flight, interpretation=None):
        client = FakeClient(flight, interpretation)
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
        route="EGTK LSGS",
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
        route="EGTK LSGS",
        departure_time="2026-07-08T09:00:00+00:00",
        flight_duration_hours=2.0,
    )
    assert client.refresh_called is True
    assert res["briefing"]["status"] == "processing"
    assert res["flight"]["coverage"] is None


def test_filed_route_is_interpreted_before_create(patch_client):
    """A Field-15 route goes through interpret-route; only the resolved
    waypoints reach create, and the original string is kept as raw_route."""
    filed = "EGTK SAPRE1D SAPRE/N0190F180 IFR L615 DJL LSGS"
    client = patch_client(
        _flight(waypoints=["EGTK", "SAPRE", "DJL", "LSGS"]),
        _interpretation(["EGTK", "SAPRE", "DJL", "LSGS"]),
    )
    res = server.create_flight(
        route=filed,
        departure_time="2026-07-08T09:00:00+00:00",
        flight_duration_hours=2.0,
    )
    assert client.interpret_calls == [filed]
    assert client.create_kwargs["waypoints"] == ["EGTK", "SAPRE", "DJL", "LSGS"]
    assert client.create_kwargs["raw_route"] == filed
    assert res["route"] == {
        "interpreted": ["EGTK", "SAPRE", "DJL", "LSGS"],
        "skipped": [],
        "off_route": [],
    }


def test_dropped_waypoints_are_reported(patch_client):
    """Skipped and off-route tokens are surfaced so the agent can tell the pilot."""
    client = patch_client(
        _flight(),
        _interpretation(["EGTK", "LSGS"], skipped=["ZZQX"], off_route=["LFMN"]),
    )
    res = server.create_flight(
        route="EGTK ZZQX LFMN LSGS",
        departure_time="2026-07-08T09:00:00+00:00",
        flight_duration_hours=2.0,
    )
    assert client.create_kwargs["waypoints"] == ["EGTK", "LSGS"]
    assert res["route"]["skipped"] == ["ZZQX"]
    assert res["route"]["off_route"] == ["LFMN"]
    assert "ZZQX" in res["warning"] and "LFMN" in res["warning"]


def test_clean_route_has_no_warning(patch_client):
    patch_client(_flight())
    res = server.create_flight(
        route="EGTK LSGS",
        departure_time="2026-07-08T09:00:00+00:00",
        flight_duration_hours=2.0,
    )
    assert "warning" not in res


def test_unresolvable_route_creates_nothing(patch_client):
    """Fewer than two resolved points → error, no flight, no briefing."""
    client = patch_client(
        _flight(),
        _interpretation(["LSGS"], skipped=["ZZQX"]),
    )
    res = server.create_flight(
        route="ZZQX L615 LSGS",
        departure_time="2026-07-08T09:00:00+00:00",
        flight_duration_hours=2.0,
    )
    assert "error" in res
    assert res["route"]["skipped"] == ["ZZQX"]
    assert client.create_kwargs is None
    assert client.refresh_called is False


def test_interpret_http_error_is_returned(patch_client):
    request = httpx.Request("POST", "http://test/api/flights/interpret-route")
    response = httpx.Response(500, request=request, text="Airport database not configured")
    client = patch_client(
        _flight(),
        httpx.HTTPStatusError("boom", request=request, response=response),
    )
    res = server.create_flight(
        route="EGTK LSGS",
        departure_time="2026-07-08T09:00:00+00:00",
        flight_duration_hours=2.0,
    )
    assert res["http_status"] == 500
    assert "interpret" in res["error"]
    assert client.create_kwargs is None


def test_interpret_connection_error_is_returned(patch_client):
    request = httpx.Request("POST", "http://test/api/flights/interpret-route")
    client = patch_client(_flight(), httpx.ConnectError("refused", request=request))
    res = server.create_flight(
        route="EGTK LSGS",
        departure_time="2026-07-08T09:00:00+00:00",
        flight_duration_hours=2.0,
    )
    assert "interpret" in res["error"]
    assert client.create_kwargs is None
