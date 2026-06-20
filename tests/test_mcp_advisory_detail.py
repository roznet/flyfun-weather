"""Tests for the MCP advisory-detail / digest-context tools (issue #278).

Covers the in-process logic of the MCP server's drill-down surface:
- Tier 1: ``_summarize_advisories`` retains per_model / cross_check /
  parameters_used and emits discoverability hints (Layer A).
- Tier 2: ``get_advisory_detail`` + ``_convective_detail`` build a server-side
  per-model summary (CAPE range, peak, cover %, thermo-vs-NWP method).
- Tier 3: ``get_digest_context`` returns the persisted LLM context.

The tools are thin proxies, so the WeatherbriefClient is faked rather than
hitting the API.
"""

from __future__ import annotations

import pytest

from weatherbrief.mcp import server


# --------------------------------------------------------------------------
# Fixtures: a fake client + sample manifests
# --------------------------------------------------------------------------

ADVISORIES = {
    "advisories": [
        {
            "advisory_id": "convective",
            "aggregate_status": "red",
            "aggregate_detail": "EXTREME over 57%",
            "parameters_used": {"min_risk": 2, "affected_pct_red": 50},
            "per_model": [
                {"model": "gfs", "status": "amber", "detail": "MODERATE over 20%",
                 "affected_pct": 20.0, "affected_nm": 30.0, "total_nm": 150.0,
                 "cross_check": None},
                {"model": "ecmwf", "status": "red", "detail": "EXTREME over 57%",
                 "affected_pct": 57.0, "affected_nm": 85.0, "total_nm": 150.0,
                 "cross_check": "DD MODERATE not corroborated — model scheme quiet over 40nm"},
                {"model": "meteofrance", "status": "red", "detail": "EXTREME over 60%",
                 "affected_pct": 60.0, "affected_nm": 90.0, "total_nm": 150.0,
                 "cross_check": None},
            ],
        },
        {
            "advisory_id": "cloud_top",
            "aggregate_status": "green",
            "aggregate_detail": "Can climb above cloud",
            "parameters_used": {"margin_ft": 1000},
            "per_model": [
                {"model": "gfs", "status": "green", "detail": "clear",
                 "affected_pct": 0.0, "affected_nm": 0.0, "total_nm": 150.0},
                {"model": "ecmwf", "status": "green", "detail": "clear",
                 "affected_pct": 0.0, "affected_nm": 0.0, "total_nm": 150.0},
            ],
        },
    ],
    "catalog": [
        {"id": "convective", "name": "Convective Activity", "category": "convective",
         "description": "Convective risk per point."},
        {"id": "cloud_top", "name": "Cloud Top", "category": "cloud",
         "description": "Can we fly above the clouds?"},
    ],
}

ROUTE_ANALYSES = {
    "analyses": [
        {
            "point_index": 0,
            "distance_from_origin_nm": 0.0,
            "waypoint_icao": "LFMD",
            "sounding": {
                "gfs": {"convective": {"risk_level": "low", "cape_jkg": 800.0,
                                       "cover_pct": 5.0, "method": "thermo"}},
                "ecmwf": {"convective": {"risk_level": "moderate", "cape_jkg": 2600.0,
                                         "cover_pct": 0.0, "method": "thermo"}},
            },
        },
        {
            "point_index": 1,
            "distance_from_origin_nm": 40.0,
            "waypoint_icao": "PERUS",
            "sounding": {
                "gfs": {"convective": {"risk_level": "low", "cape_jkg": 950.0,
                                       "cover_pct": 10.0, "method": "thermo"}},
                "ecmwf": {"convective": {"risk_level": "extreme", "cape_jkg": 2970.0,
                                         "cover_pct": 0.0, "method": "thermo"}},
            },
        },
    ],
}


class FakeClient:
    """Stand-in for WeatherbriefClient that returns canned payloads."""

    def __init__(self, **overrides):
        self._refresh = overrides.get("refresh", {"active": False})
        self._pack = overrides.get("pack", {"fetch_timestamp": "20260620T0600Z"})
        self._advisories = overrides.get("advisories", ADVISORIES)
        self._route_analyses = overrides.get("route_analyses", ROUTE_ANALYSES)
        self._digest_context = overrides.get("digest_context", "ADVISORIES\nconvective: red\n")

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_refresh_status(self, flight_id):
        return self._refresh

    def get_latest_pack(self, flight_id):
        return self._pack

    def get_advisories(self, flight_id, timestamp):
        return self._advisories

    def get_route_analyses(self, flight_id, timestamp):
        return self._route_analyses

    def get_digest_context(self, flight_id, timestamp):
        return self._digest_context


@pytest.fixture
def patch_client(monkeypatch):
    def _install(**overrides):
        client = FakeClient(**overrides)
        monkeypatch.setattr(server, "_get_client", lambda: client)
        return client
    return _install


# --------------------------------------------------------------------------
# Tier 1: _summarize_advisories
# --------------------------------------------------------------------------

def test_summarize_retains_per_model_and_cross_check():
    out = server._summarize_advisories(ADVISORIES)
    conv = next(a for a in out if a["id"] == "convective")

    # per_model retained with each model's status/detail
    models = {m["model"]: m for m in conv["per_model"]}
    assert models["gfs"]["status"] == "amber"
    assert models["ecmwf"]["status"] == "red"
    # cross_check carried through only where present
    assert "cross_check" in models["ecmwf"]
    assert "cross_check" not in models["gfs"]

    # parameters_used retained
    assert conv["parameters_used"]["affected_pct_red"] == 50


def test_summarize_emits_discoverability_hints():
    out = server._summarize_advisories(ADVISORIES)
    conv = next(a for a in out if a["id"] == "convective")
    green = next(a for a in out if a["id"] == "cloud_top")

    # Layer A hints on the red advisory with disagreement + cross-check
    assert conv["cross_check_present"] is True
    assert conv["model_disagreement"] is True  # amber + red
    assert conv["detail_tool"] == "get_advisory_detail"
    assert conv["name"] == "Convective Activity"

    # Quiet, all-green advisory: no drill pointer, no false signals
    assert green["cross_check_present"] is False
    assert green["model_disagreement"] is False
    assert "detail_tool" not in green


# --------------------------------------------------------------------------
# Tier 2: _convective_detail + get_advisory_detail
# --------------------------------------------------------------------------

def test_convective_detail_cape_peak_cover_method():
    detail = server._convective_detail(ROUTE_ANALYSES, ["gfs", "ecmwf"])

    ec = detail["ecmwf"]
    assert ec["cape_range_jkg"] == [2600, 2970]
    assert ec["peak"]["cape_jkg"] == 2970
    assert ec["peak"]["distance_nm"] == 40.0
    assert ec["peak"]["waypoint_icao"] == "PERUS"
    # cover ~0 = "blue sky" signal preserved
    assert ec["max_cover_pct"] == 0
    assert ec["peak"]["cover_pct"] == 0
    assert ec["assessment_method"] == "thermo"

    gfs = detail["gfs"]
    assert gfs["cape_range_jkg"] == [800, 950]
    assert gfs["max_cover_pct"] == 10


def test_convective_detail_skips_absent_model():
    detail = server._convective_detail(ROUTE_ANALYSES, ["meteofrance"])
    assert detail == {}


def test_get_advisory_detail_convective(patch_client):
    patch_client()
    res = server.get_advisory_detail("flight-1", "convective")

    assert res["advisory_id"] == "convective"
    assert res["aggregate_status"] == "red"
    assert res["name"] == "Convective Activity"
    # generic per-model carries extent + cross_check
    ec = next(m for m in res["per_model"] if m["model"] == "ecmwf")
    assert ec["affected_nm"] == 85.0
    assert "cross_check" in ec
    # guardrail framing present
    assert "not a downgrade" in res["cross_check_note"]
    # convective specialization attached
    assert res["convective"]["ecmwf"]["peak"]["cape_jkg"] == 2970
    assert res["flight_id"] == "flight-1"


def test_get_advisory_detail_unknown_id_lists_available(patch_client):
    patch_client()
    res = server.get_advisory_detail("flight-1", "nonexistent")
    assert "error" in res
    assert "convective" in res["error"]


def test_get_advisory_detail_no_pack(patch_client):
    patch_client(pack=None)
    res = server.get_advisory_detail("flight-1", "convective")
    assert res["status"] == "none"


def test_get_advisory_detail_processing(patch_client):
    patch_client(refresh={"active": True})
    res = server.get_advisory_detail("flight-1", "convective")
    assert res["status"] == "processing"


# --------------------------------------------------------------------------
# Tier 3: get_digest_context
# --------------------------------------------------------------------------

def test_get_digest_context_ready(patch_client):
    patch_client()
    res = server.get_digest_context("flight-1")
    assert res["status"] == "ready"
    assert "convective: red" in res["digest_context"]


def test_get_digest_context_missing(patch_client):
    patch_client(digest_context=None)
    res = server.get_digest_context("flight-1")
    assert res["status"] == "none"
