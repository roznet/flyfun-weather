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
                 "cross_check": "Thermo Convective shows MODERATE instability, but the model's own NWP Convective forecast stays quiet — over 40nm"},
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

def _point(idx, dist, icao, valid, eta, gfs, ecmwf):
    return {
        "point_index": idx,
        "distance_from_origin_nm": dist,
        "waypoint_icao": icao,
        "forecast_hour": valid,
        "interpolated_time": eta,
        "sounding": {"gfs": gfs, "ecmwf": ecmwf},
    }


ROUTE_ANALYSES = {
    "analyses": [
        _point(
            0, 0.0, "LFMD", "2026-06-21T13:00:00+00:00", "2026-06-21T13:05:00+00:00",
            gfs={
                "convective": {"risk_level": "low", "cape_jkg": 800.0, "method": "thermo", "top_ft": 18000.0},
                "convective_thermo": {"risk_level": "low", "cape_jkg": 800.0, "top_ft": 18000.0},
                "convective_nwp": {"risk_level": "none", "cape_jkg": None, "cover_pct": 5.0, "top_ft": None},
            },
            ecmwf={
                "convective": {"risk_level": "moderate", "cape_jkg": 2600.0, "method": "thermo", "top_ft": 25000.0},
                "convective_thermo": {"risk_level": "moderate", "cape_jkg": 2600.0, "top_ft": 25000.0},
                "convective_nwp": {"risk_level": "none", "cape_jkg": None, "cover_pct": 0.0, "top_ft": None},
            },
        ),
        _point(
            1, 40.0, "PERUS", "2026-06-21T14:00:00+00:00", "2026-06-21T15:20:00+00:00",
            gfs={
                "convective": {"risk_level": "low", "cape_jkg": 950.0, "method": "thermo", "top_ft": 19000.0},
                "convective_thermo": {"risk_level": "low", "cape_jkg": 950.0, "top_ft": 19000.0},
                "convective_nwp": {"risk_level": "none", "cape_jkg": None, "cover_pct": 10.0, "top_ft": None},
            },
            ecmwf={
                "convective": {"risk_level": "extreme", "cape_jkg": 2970.0, "method": "thermo", "top_ft": 27000.0},
                "convective_thermo": {"risk_level": "extreme", "cape_jkg": 2970.0, "top_ft": 27000.0},
                "convective_nwp": {"risk_level": "none", "cape_jkg": None, "cover_pct": 0.0, "top_ft": None},
            },
        ),
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
        self._freshness = overrides.get("freshness", {"fresh": True})

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def get_refresh_status(self, flight_id):
        return self._refresh

    def get_latest_pack(self, flight_id):
        return self._pack

    def get_freshness(self, flight_id):
        return self._freshness

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

    # Layer A hints on the red advisory with a cross-check. Flags are neutral
    # (no "model_disagreement" that would prime a red→amber downgrade).
    assert conv["cross_check_present"] is True
    assert conv["per_model_present"] is True
    assert "model_disagreement" not in conv
    assert conv["detail_tool"] == "get_advisory_detail"
    assert conv["name"] == "Convective Activity"

    # Quiet, all-green advisory: no drill pointer, no cross-check signal
    assert green["cross_check_present"] is False
    assert green["per_model_present"] is True
    assert "detail_tool" not in green
    # Mitigation hook always present and False when there are none.
    assert conv["aggregate_mitigations_present"] is False
    assert green["aggregate_mitigations_present"] is False


# Manifest with advisory mitigations (advice only — never changes the grade).
MITIGATION_ADVISORIES = {
    "advisories": [
        {
            "advisory_id": "vfr_feasibility",
            "aggregate_status": "red",
            "aggregate_detail": "VFR not feasible — departure deck",
            "parameters_used": {},
            "per_model": [
                {"model": "gfs", "status": "red", "detail": "OVC deck below cruise",
                 "affected_pct": 25.0, "affected_nm": 22.0, "total_nm": 90.0,
                 "mitigations": [
                     {"kind": "route_position", "addresses": "climb_deck",
                      "detail": "Climb to cruise after ~40 nm to clear the deck.",
                      "mitigated_status": "amber", "distance_nm": 40.0,
                      "reference": "departure"}
                 ]},
            ],
            "aggregate_mitigations": [
                {"kind": "altitude", "addresses": "cruise_imc",
                 "detail": "Fly 6,000 ft to stay below the cloud base.",
                 "mitigated_status": "green", "altitude_ft": 6000},
            ],
        },
    ],
    "catalog": [
        {"id": "vfr_feasibility", "name": "VFR Feasibility", "category": "feasibility",
         "description": "Composite VFR go/no-go."},
    ],
}


def test_summarize_emits_mitigation_present_hook_and_expands_objects():
    out = server._summarize_advisories(MITIGATION_ADVISORIES)
    vfr = next(a for a in out if a["id"] == "vfr_feasibility")
    assert vfr["aggregate_mitigations_present"] is True
    # RED → full objects expand alongside per_model.
    assert vfr["aggregate_mitigations"][0]["addresses"] == "cruise_imc"
    assert vfr["detail_tool"] == "get_advisory_detail"


# --------------------------------------------------------------------------
# Tier 2: _convective_detail + get_advisory_detail
# --------------------------------------------------------------------------

def test_convective_detail_cape_peak_cover_method():
    detail = server._convective_detail(ROUTE_ANALYSES, ["gfs", "ecmwf"])

    ec = detail["ecmwf"]
    # thermo (parcel/CAPE) view
    assert ec["thermo"]["cape_range_jkg"] == [2600, 2970]
    assert ec["thermo"]["peak"]["cape_jkg"] == 2970
    # parcel-derived "tops" the digest narrates (EL), reconciles with cover ~0
    assert ec["thermo"]["peak"]["el_top_ft"] == 27000
    assert ec["thermo"]["peak"]["distance_nm"] == 40.0
    assert ec["thermo"]["peak"]["waypoint_icao"] == "PERUS"
    # diurnal/time axis: forecast valid-time vs flight ETA at the peak
    assert ec["thermo"]["peak"]["valid_time"] == "2026-06-21T14:00:00+00:00"
    assert ec["thermo"]["peak"]["eta"] == "2026-06-21T15:20:00+00:00"
    # nwp scheme: cover ~0 = "blue sky" signal preserved
    assert ec["nwp"]["max_cover_pct"] == 0
    assert ec["assessment_method"] == "thermo"

    gfs = detail["gfs"]
    assert gfs["thermo"]["cape_range_jkg"] == [800, 950]
    assert gfs["nwp"]["max_cover_pct"] == 10


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
    # convective specialization attached, with provenance note
    assert res["convective"]["ecmwf"]["thermo"]["peak"]["cape_jkg"] == 2970
    assert res["convective"]["ecmwf"]["thermo"]["peak"]["point_index"] == 1
    assert "blue sky" in res["convective_note"]
    assert res["flight_id"] == "flight-1"
    # Deep link (#308): Skew-T view, convective lens, at the highest-CAPE
    # peak point (ECMWF @ point 1).
    assert "view=skewt" in res["web_url"]
    assert "advisory=convective" in res["web_url"]
    assert "point=1" in res["web_url"]
    assert "model=ecmwf" in res["web_url"]


def test_get_advisory_detail_surfaces_mitigations(patch_client):
    patch_client(advisories=MITIGATION_ADVISORIES)
    res = server.get_advisory_detail("flight-1", "vfr_feasibility")
    assert res["aggregate_status"] == "red"  # mitigation never changes the grade
    assert res["aggregate_mitigations"][0]["addresses"] == "cruise_imc"
    assert res["per_model"][0]["mitigations"][0]["addresses"] == "climb_deck"
    # The advice-only guardrail note is present and load-bearing.
    assert "never change the grade" in res["mitigation_note"]


def test_advisory_web_url_deep_links():
    # carries the advisory param + optional point/model
    url = server._advisory_web_url("f1", "fiki_icing")
    assert "flight=f1" in url and "view=skewt" in url and "advisory=fiki_icing" in url
    assert "point=" not in url
    url2 = server._advisory_web_url("f1", "convective", point_index=3, model="icon_eu")
    assert "advisory=convective" in url2 and "point=3" in url2 and "model=icon_eu" in url2
    # The builder always carries the raw advisory id and still opens the Skew-T;
    # the frontend decides whether a lens exists for it (single source of truth,
    # no server-side mapping). An id with no lens just opens the bare Skew-T.
    url3 = server._advisory_web_url("f1", "model_quality")
    assert "advisory=model_quality" in url3 and "view=skewt" in url3


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


def test_get_advisory_detail_surfaces_staleness(patch_client):
    patch_client(freshness={
        "fresh": False,
        "stale_models": ["ecmwf", "gfs"],
        "model_init_times": {"ecmwf": 0, "gfs": 6},
    })
    res = server.get_advisory_detail("flight-1", "convective")
    assert res["stale"] is True
    assert "ecmwf" in res["stale_models"]
    assert "refresh_briefing" in res["stale_note"]


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
