"""MCP tool-call logging middleware (#873): one log per call, ok vs error."""

import asyncio

import pytest
from fastmcp import Client

from weatherbrief.mcp import server


class _FakeClient:
    def __init__(self, flights):
        self._flights = flights

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def list_flights(self):
        if isinstance(self._flights, Exception):
            raise self._flights
        return self._flights


@pytest.fixture
def logged(monkeypatch):
    calls: list[tuple[str, bool, int]] = []

    async def _capture(tool, ok, elapsed_ms):
        calls.append((tool, ok, elapsed_ms))

    monkeypatch.setattr(server, "_log_tool_call", _capture)
    return calls


def _call_list_flights(monkeypatch, flights):
    monkeypatch.setattr(server, "_get_client", lambda: _FakeClient(flights))

    async def run():
        async with Client(server.mcp) as client:
            await client.call_tool("list_flights", {}, raise_on_error=False)

    asyncio.run(run())


def test_successful_call_is_logged_ok(monkeypatch, logged):
    _call_list_flights(monkeypatch, [])
    assert [(t, ok) for t, ok, _ in logged] == [("list_flights", True)]
    assert logged[0][2] >= 0


def test_error_result_is_logged_not_ok(monkeypatch, logged):
    import httpx

    request = httpx.Request("GET", "http://x/api/flights")
    err = httpx.HTTPStatusError("boom", request=request, response=httpx.Response(500, request=request))
    _call_list_flights(monkeypatch, err)
    assert [(t, ok) for t, ok, _ in logged] == [("list_flights", False)]


def test_log_failure_never_breaks_the_tool(monkeypatch):
    # No token in a test process: the real logger must swallow the failure.
    monkeypatch.delenv("WEATHERBRIEF_TOKEN", raising=False)
    asyncio.run(server._log_tool_call("list_flights", True, 5))
