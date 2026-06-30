"""Tests for the shared connector shapers (weatherbrief.connectors.views).

These pure dict->dict transforms are shared by both agent front-doors — the
Claude MCP server and the ChatGPT OpenAPI router — so the guardrails verified
here (cross-check stays context-not-downgrade; convective provenance split)
apply identically to both.
"""

from __future__ import annotations

from weatherbrief.connectors import views


# --------------------------------------------------------------------------
# briefing_freshness_status — tiered refresh-gate -> status + stale note
# --------------------------------------------------------------------------

def test_freshness_stale_uses_gate_reason_and_note():
    out = views.briefing_freshness_status(
        {
            "refresh_decision": {
                "mode": "full",
                "reason": "ECMWF run updated.",
                "pending_models": ["icon"],
                "eta_useful": "14:00Z",
            },
            "stale_models": ["ecmwf"],
        }
    )
    assert out["status"] == "stale"
    assert out["is_fresh"] is False
    assert out["stale_models"] == ["ecmwf"]
    assert out["stale_note"].startswith("ECMWF run updated.")
    assert "Call refresh_briefing" in out["stale_note"]
    assert "Awaiting updated runs: icon" in out["stale_note"]
    assert "Useful refresh ETA: 14:00Z" in out["stale_note"]


def test_freshness_gate_none_is_fresh_even_if_min_rule_stale():
    # The tiered gate ("none" = nothing worthwhile to refresh) wins over the raw
    # min-rule ``fresh=False`` — the false "needs refresh" right after a manual
    # refresh must not resurface.
    out = views.briefing_freshness_status({"refresh_decision": {"mode": "none"}, "fresh": False})
    assert out["status"] == "ready"
    assert out["is_fresh"] is True
    assert "stale_note" not in out


def test_freshness_falls_back_to_min_rule_when_gate_absent():
    assert views.briefing_freshness_status({"fresh": True})["is_fresh"] is True
    assert views.briefing_freshness_status({"fresh": False})["is_fresh"] is False


# --------------------------------------------------------------------------
# summarize_advisories — discoverability hints + neutral guardrail flags
# --------------------------------------------------------------------------

def _mitigation(kind: str = "altitude", addresses: str = "cruise_imc",
                mitigated_status: str = "green"):
    return {
        "kind": kind,
        "addresses": addresses,
        "detail": "Fly 6,000 ft to stay below the deck.",
        "mitigated_status": mitigated_status,
        "altitude_ft": 6000,
    }


def _adv_manifest(status: str, cross_check: str | None = None,
                  aggregate_mitigations: list | None = None):
    per_model = [{"model": "gfs", "status": status, "detail": "d", "affected_pct": 40}]
    if cross_check:
        per_model[0]["cross_check"] = cross_check
    adv = {
        "advisory_id": "convective",
        "aggregate_status": status,
        "aggregate_detail": "agg",
        "per_model": per_model,
        "parameters_used": {"cape_red": 1500},
    }
    if aggregate_mitigations is not None:
        adv["aggregate_mitigations"] = aggregate_mitigations
    return {
        "advisories": [adv],
        "catalog": [{"id": "convective", "name": "Convective", "category": "convective"}],
    }


def test_summarize_advisories_expands_red_with_detail_tool():
    out = views.summarize_advisories(_adv_manifest("red"))
    entry = out[0]
    assert entry["id"] == "convective"
    assert entry["status"] == "red"
    assert entry["per_model_present"] is True
    assert entry["detail_tool"] == "get_advisory_detail"
    assert entry["parameters_used"] == {"cape_red": 1500}


def test_summarize_advisories_green_stays_compact():
    out = views.summarize_advisories(_adv_manifest("green"))
    entry = out[0]
    # Green-and-quiet: no per_model expansion, no drill-down pointer.
    assert "per_model" not in entry
    assert "detail_tool" not in entry
    assert entry["cross_check_present"] is False
    # The mitigation hook is always present and False when there are none.
    assert entry["aggregate_mitigations_present"] is False


def test_summarize_advisories_red_with_mitigations_expands_full_objects():
    out = views.summarize_advisories(
        _adv_manifest("red", aggregate_mitigations=[_mitigation()])
    )
    entry = out[0]
    assert entry["aggregate_mitigations_present"] is True
    # Full objects expand in the same non-green window as cross_check/per_model.
    assert entry["aggregate_mitigations"][0]["addresses"] == "cruise_imc"
    assert entry["aggregate_mitigations"][0]["mitigated_status"] == "green"
    assert entry["detail_tool"] == "get_advisory_detail"


def test_summarize_advisories_green_with_mitigation_flags_present_but_stays_compact():
    # A mitigation on an otherwise-green-and-quiet advisory sets the present hook
    # (so the agent knows to drill in) but keeps the summary compact — the full
    # objects live in get_advisory_detail. Mitigation presence is NOT a new
    # expansion trigger, mirroring how green-and-quiet stays compact.
    out = views.summarize_advisories(
        _adv_manifest("green", aggregate_mitigations=[_mitigation()])
    )
    entry = out[0]
    assert entry["aggregate_mitigations_present"] is True
    assert "aggregate_mitigations" not in entry
    assert "detail_tool" not in entry


def test_summarize_advisories_green_with_cross_check_expands():
    # A cross-check on an otherwise-green advisory is still surfaced (the hook
    # that provokes the follow-up question) without priming a downgrade.
    out = views.summarize_advisories(_adv_manifest("green", cross_check="CAPE 1800 J/kg"))
    entry = out[0]
    assert entry["cross_check_present"] is True
    assert entry["detail_tool"] == "get_advisory_detail"
    assert entry["per_model"][0]["cross_check"] == "CAPE 1800 J/kg"


# --------------------------------------------------------------------------
# advisory_detail — per-model drill-down + load-bearing guardrail note
# --------------------------------------------------------------------------

def test_advisory_detail_injects_cross_check_note_and_per_model():
    adv = {
        "advisory_id": "convective",
        "aggregate_status": "red",
        "aggregate_detail": "EXTREME over 57%",
        "per_model": [
            {"model": "gfs", "status": "red", "detail": "d", "affected_pct": 57,
             "affected_nm": 80, "total_nm": 140, "cross_check": "CAPE 2200 J/kg"},
        ],
        "parameters_used": {"cape_red": 1500},
    }
    catalog = {"name": "Convective", "category": "convective", "description": "desc"}
    out = views.advisory_detail(adv, catalog)
    # The cross-check guardrail note is deliberately load-bearing — always present.
    assert out["cross_check_note"] == views.CROSS_CHECK_NOTE
    assert out["aggregate_status"] == "red"
    assert out["per_model"][0]["cross_check"] == "CAPE 2200 J/kg"
    assert out["per_model"][0]["affected_nm"] == 80
    assert out["name"] == "Convective"


def test_advisory_detail_without_catalog_entry_omits_name():
    out = views.advisory_detail({"advisory_id": "cloud_top", "per_model": []}, None)
    assert out["cross_check_note"] == views.CROSS_CHECK_NOTE
    assert "name" not in out


def test_advisory_detail_surfaces_mitigations_and_note():
    adv = {
        "advisory_id": "vfr_feasibility",
        "aggregate_status": "red",
        "aggregate_detail": "VFR not feasible",
        "per_model": [
            {"model": "gfs", "status": "red", "detail": "deck", "affected_pct": 25,
             "mitigations": [_mitigation(kind="route_position", addresses="climb_deck",
                                         mitigated_status="amber")]},
        ],
        "parameters_used": {},
        "aggregate_mitigations": [_mitigation()],
    }
    out = views.advisory_detail(adv, None)
    # Aggregate + per-model mitigations surface verbatim; the guardrail is present.
    assert out["aggregate_mitigations"][0]["addresses"] == "cruise_imc"
    assert out["per_model"][0]["mitigations"][0]["addresses"] == "climb_deck"
    assert out["mitigation_note"] == views.MITIGATION_NOTE


def test_advisory_detail_omits_mitigation_note_when_none():
    # No mitigations anywhere → the guardrail note is omitted, keeping the
    # drill-down quiet for mitigation-free advisories (backward-compat).
    adv = {
        "advisory_id": "cloud_top",
        "aggregate_status": "amber",
        "per_model": [{"model": "gfs", "status": "amber", "detail": "d"}],
    }
    out = views.advisory_detail(adv, None)
    assert "mitigation_note" not in out
    assert "aggregate_mitigations" not in out


# --------------------------------------------------------------------------
# convective_detail — thermo vs nwp split + blue-sky signal
# --------------------------------------------------------------------------

def test_convective_detail_splits_thermo_and_nwp():
    route_analyses = {
        "analyses": [
            {
                "distance_from_origin_nm": 12,
                "waypoint_icao": "LFBO",
                "forecast_hour": 15,
                "interpolated_time": "2026-06-21T15:00:00Z",
                "sounding": {
                    "gfs": {
                        "convective": {"method": "thermo"},
                        "convective_thermo": {
                            "cape_jkg": 1800, "top_ft": 27000, "risk_level": "high",
                        },
                        "convective_nwp": {"cover_pct": 0, "top_ft": None},
                    }
                },
            }
        ]
    }
    out = views.convective_detail(route_analyses, ["gfs"])
    gfs = out["gfs"]
    assert gfs["assessment_method"] == "thermo"
    assert gfs["thermo"]["peak"]["el_top_ft"] == 27000
    assert gfs["thermo"]["peak"]["cape_jkg"] == 1800
    # cover ~0 = the machine-readable "blue sky" signal next to high CAPE.
    assert gfs["nwp"]["max_cover_pct"] == 0
