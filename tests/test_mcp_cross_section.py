"""MCP App cross-section (#819): the tool / resource / app-only data split.

The client is faked over the ``web/tests/fixtures/egtf_eglf`` pack (the same
fixture the vitest suite reads), as in ``test_mcp_live.py``. What matters:
``show_cross_section`` points at the widget and carries view parameters only;
``xsection_data`` is app-only and returns one model's slice; pack data never
reaches a model-visible result.
"""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

import httpx
import pytest
from fastmcp import Client

from weatherbrief.connectors.views import XSECTION_SNAPSHOT_KEYS, slice_route_analyses, slice_snapshot
from weatherbrief.mcp import server

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "web" / "tests" / "fixtures" / "egtf_eglf"
TS = "2026-02-25T16:10:07.255073+00:00"
FLIGHT = "egtf_eglf-2026-02-25-45ed"


def _load(name: str) -> dict:
    return json.loads((FIXTURE / name).read_text())


class FakeClient:
    def __init__(self, *, active=False, packs=True, analyses=True, flight=True):
        self.active = active
        self.packs = packs
        self.analyses = analyses
        self.flight = flight

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_refresh_status(self, flight_id):
        return {"active": self.active}

    def get_latest_pack(self, flight_id):
        return _load("pack_meta.json") if self.packs else None

    def get_pack(self, flight_id, ts):
        return _load("pack_meta.json") if self.packs and ts == TS else None

    def get_flight(self, flight_id):
        return _load("flight.json") if self.flight else None

    def get_route_analyses(self, flight_id, ts):
        return _load("route_analyses.json") if self.analyses else None

    def get_snapshot(self, flight_id, ts):
        return _load("snapshot.json")

    def get_elevation(self, flight_id, ts):
        return _load("elevation.json")

    def get_route_fronts(self, flight_id, ts):
        return None


@pytest.fixture
def patch_client(monkeypatch):
    def _install(**kw):
        client = FakeClient(**kw)
        monkeypatch.setattr(server, "_get_client", lambda: client)
        return client
    return _install


def _list(coro_name: str):
    async def run():
        async with Client(server.mcp) as c:
            return await getattr(c, coro_name)()
    return asyncio.run(run())


# --- Wiring -------------------------------------------------------------------


def test_show_cross_section_points_at_the_widget():
    tool = next(t for t in _list("list_tools") if t.name == "show_cross_section")
    assert tool.meta["ui"]["resourceUri"] == server.XSECTION_RESOURCE_URI
    assert "visibility" not in tool.meta["ui"]  # default: model and app


def test_xsection_data_is_app_only():
    # fastmcp lists app-only tools (the MCP Apps spec leaves the filtering to
    # the host), so the contract is the declared visibility.
    tool = next(t for t in _list("list_tools") if t.name == "xsection_data")
    assert tool.meta["ui"]["visibility"] == ["app"]
    assert "resourceUri" not in tool.meta["ui"]


def test_resource_is_an_mcp_app(monkeypatch, tmp_path):
    resource = next(r for r in _list("list_resources") if str(r.uri) == server.XSECTION_RESOURCE_URI)
    assert resource.mime_type == "text/html;profile=mcp-app"
    assert resource.meta["ui"]["prefersBorder"] is True
    assert "csp" not in resource.meta["ui"]  # no connectDomains: data comes over tools/call


def test_resource_serves_built_html_or_stub(monkeypatch, tmp_path):
    monkeypatch.setattr(server, "_xsection_html", None)
    monkeypatch.setattr(server, "_XSECTION_HTML_PATH", tmp_path / "missing.html")
    assert "not built" in server.cross_section_widget()
    built = tmp_path / "mcp-cross-section.html"
    built.write_text("<html>widget</html>")
    monkeypatch.setattr(server, "_XSECTION_HTML_PATH", built)
    assert server.cross_section_widget() == "<html>widget</html>"
    built.unlink()  # read once per process: still served after the file goes
    assert server.cross_section_widget() == "<html>widget</html>"


def test_layer_ids_exist_in_the_web_registry():
    sources = "".join(p.read_text() for p in (REPO / "web/ts/visualization/cross-section").rglob("*.ts"))
    # Most layers declare `id: '…'`; the line factories take the id as their
    # first string argument, so match any quoted literal.
    ids = set(re.findall(r"'([a-z0-9-]+)'", sources))
    missing = [layer for layer in server.XSECTION_LAYER_IDS if layer not in ids]
    assert not missing


def test_instructions_and_get_briefing_mention_the_tool():
    assert "show_cross_section" in server.mcp.instructions
    assert "show_cross_section" in server.get_briefing.__doc__


# --- show_cross_section -------------------------------------------------------


def test_show_cross_section_carries_view_parameters_only(patch_client):
    patch_client()
    res = server.show_cross_section(FLIGHT, model="gfs", layers=["sld-bands", "bogus"], point_index=3)
    assert res.structured_content == {
        "flight_id": FLIGHT,
        "pack_timestamp": TS,
        "model": "gfs",
        "layers": ["sld-bands"],
        "point_index": 3,
        "web_url": server._flight_web_url(FLIGHT),
    }
    text = res.content[0].text
    assert "EGTF → EGLF" in text and TS in text and server._flight_web_url(FLIGHT) in text
    assert "bogus" in text  # the model is told what was ignored
    # No pack data in anything the model can see.
    for blob in (text, json.dumps(res.structured_content)):
        assert "sounding" not in blob and "analyses" not in blob


def test_show_cross_section_explicit_pack(patch_client):
    patch_client()
    assert server.show_cross_section(FLIGHT, pack_timestamp=TS).structured_content["pack_timestamp"] == TS
    res = server.show_cross_section(FLIGHT, pack_timestamp="2020-01-01T00:00:00+00:00")
    assert res.structured_content["http_status"] == 404 and "error" in res.structured_content


def test_show_cross_section_processing_and_none(patch_client):
    patch_client(active=True)
    assert server.show_cross_section(FLIGHT).structured_content["status"] == "processing"
    patch_client(packs=False)
    assert server.show_cross_section(FLIGHT).structured_content["status"] == "none"


def test_show_cross_section_unknown_flight(patch_client):
    patch_client(flight=False)
    res = server.show_cross_section("nope")
    assert res.structured_content == {"error": "Flight nope not found.", "http_status": 404}


def test_show_cross_section_api_error(monkeypatch):
    req = httpx.Request("GET", "http://x/api/flights/f")
    err = httpx.HTTPStatusError("boom", request=req, response=httpx.Response(403, request=req, text="no"))

    class Denied(FakeClient):
        def get_flight(self, flight_id):
            raise err

    monkeypatch.setattr(server, "_get_client", lambda: Denied())
    assert server.show_cross_section(FLIGHT).structured_content == {"error": "API error: no", "http_status": 403}


# --- xsection_data ------------------------------------------------------------


def test_xsection_data_slices_to_one_model(patch_client, caplog):
    patch_client()
    with caplog.at_level("INFO", logger="weatherbrief.mcp.server"):
        res = server.xsection_data(FLIGHT, TS, model="gfs")
    data = res.structured_content
    assert data["model"] == "gfs"
    assert data["ceiling_ft"] == 18000
    assert data["fronts"] is None
    assert set(data["snapshot"]) == set(XSECTION_SNAPSHOT_KEYS)
    ra = data["route_analyses"]
    assert ra["models"] == _load("route_analyses.json")["models"]  # switch still offered
    for point in ra["analyses"]:
        assert set(point["sounding"]) <= {"gfs"} and set(point["wind_components"]) <= {"gfs"}
        assert point["altitude_advisories"] is None
        assert all(set(d["model_values"]) <= {"gfs"} for d in point["model_divergence"])
    # The payload rides structuredContent; the text is a one-liner, not a copy.
    assert len(res.content) == 1 and "analyses" not in res.content[0].text
    line = next(r.getMessage() for r in caplog.records if r.getMessage().startswith("xsection_data"))
    assert f"flight={FLIGHT}" in line and "model=gfs" in line and re.search(r"bytes=\d+", line)


def test_xsection_data_defaults_to_ecmwf(patch_client):
    patch_client()
    assert server.xsection_data(FLIGHT, TS).structured_content["model"] == "ecmwf"
    assert server.xsection_data(FLIGHT, TS, model="nope").structured_content["model"] == "ecmwf"


def test_xsection_data_errors(patch_client):
    patch_client()
    assert server.xsection_data(FLIGHT, "2020-01-01T00:00:00+00:00").structured_content["http_status"] == 404
    patch_client(analyses=False)
    assert "error" in server.xsection_data(FLIGHT, TS).structured_content


# --- Pure slicers -------------------------------------------------------------


def test_slice_route_analyses_keeps_shape_and_shrinks():
    manifest = _load("route_analyses.json")
    full = len(json.dumps(manifest))
    sliced = slice_route_analyses(manifest, "ecmwf")
    assert len(json.dumps(sliced)) < full * 0.6
    point, src = sliced["analyses"][0], manifest["analyses"][0]
    assert point["sounding"] == {"ecmwf": src["sounding"]["ecmwf"]}
    assert point["model_divergence"][0]["agreement"] == src["model_divergence"][0]["agreement"]
    assert point["model_divergence"][0]["model_values"] == {"ecmwf": src["model_divergence"][0]["model_values"]["ecmwf"]}
    # The input is not mutated (the slicer shares sub-objects, never edits them).
    assert set(manifest["analyses"][0]["sounding"]) == {"ecmwf", "gfs"}
    assert manifest["analyses"][0]["altitude_advisories"] is not None


def test_slice_route_analyses_missing_model_is_empty():
    sliced = slice_route_analyses(_load("route_analyses.json"), "icon")
    assert all(p["sounding"] == {} for p in sliced["analyses"])


def test_slice_snapshot():
    snap = _load("snapshot.json")
    out = slice_snapshot(snap)
    assert list(out) == list(XSECTION_SNAPSHOT_KEYS)
    assert out["route_observations"] == snap["route_observations"]
    assert out["route_sigmets"] is None
    assert slice_snapshot(None) is None
