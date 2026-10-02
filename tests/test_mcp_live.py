"""MCP ``get_briefing`` carries the flight-day ``live`` block (#641).

The block is shaped server-side by ``live_layer.live_summary`` and fetched over
``GET /api/flights/{id}/live/summary``; the shaping is covered by
``test_live_summary`` and the endpoint by ``test_agent_endpoints``. Here the
client is faked: the tool must pass the block through, null when absent, and
never fail the briefing when the live endpoint does.
"""

from __future__ import annotations

import httpx
import pytest

from weatherbrief.mcp import server

_LIVE = {
    "note": "…",
    "alert_count": 1,
    "changes": [{"tier": "alert", "kind": "sigmet_issued", "role": "destination",
                 "message": "New SIGMET ZZZZ 3: EMBD TS (at destination)"}],
    "sigmets": [],
    "airports": [],
}


class FakeClient:
    def __init__(self, live=None, live_error=None):
        self._live = live
        self._live_error = live_error

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_refresh_status(self, flight_id):
        return {"active": False}

    def get_latest_pack(self, flight_id):
        return {"fetch_timestamp": "2026-10-02T06:52:55+00:00", "assessment": "AMBER",
                "assessment_reason": "x", "days_out": 0}

    def get_freshness(self, flight_id):
        return {"is_fresh": True}

    def get_advisories(self, flight_id, ts):
        return None

    def get_digest_json(self, flight_id, ts):
        return {"summary": "briefing-time digest"}

    def get_digest_text(self, flight_id, ts):
        return None

    def get_altitude_table(self, flight_id, ts):
        return None

    def get_alternates(self, flight_id, ts):
        return None

    def get_live_summary(self, flight_id):
        if self._live_error is not None:
            raise self._live_error
        return self._live


@pytest.fixture
def patch_client(monkeypatch):
    def _install(**kw):
        client = FakeClient(**kw)
        monkeypatch.setattr(server, "_get_client", lambda: client)
        return client
    return _install


def test_live_block_passed_through(patch_client):
    patch_client(live=_LIVE)
    res = server.get_briefing("flight-1")
    assert res["live"] == _LIVE
    assert res["digest"] == {"summary": "briefing-time digest"}


def test_live_null_when_absent(patch_client):
    patch_client(live=None)
    res = server.get_briefing("flight-1")
    assert "live" in res and res["live"] is None


def test_live_failure_does_not_fail_briefing(patch_client):
    req = httpx.Request("GET", "http://x/api/flights/flight-1/live/summary")
    err = httpx.HTTPStatusError("boom", request=req, response=httpx.Response(500, request=req))
    patch_client(live_error=err)
    res = server.get_briefing("flight-1")
    assert res["assessment"] == "AMBER"
    assert res["live"] is None


def test_instructions_and_docstring_mention_live():
    assert "'live' block" in server.mcp.instructions
    assert "re-grade" in server.mcp.instructions
    assert "live" in server.get_briefing.__doc__
