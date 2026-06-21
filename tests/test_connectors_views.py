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

def _adv_manifest(status: str, cross_check: str | None = None):
    per_model = [{"model": "gfs", "status": status, "detail": "d", "affected_pct": 40}]
    if cross_check:
        per_model[0]["cross_check"] = cross_check
    return {
        "advisories": [
            {
                "advisory_id": "convective",
                "aggregate_status": status,
                "aggregate_detail": "agg",
                "per_model": per_model,
                "parameters_used": {"cape_red": 1500},
            }
        ],
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


def test_summarize_advisories_green_with_cross_check_expands():
    # A cross-check on an otherwise-green advisory is still surfaced (the hook
    # that provokes the follow-up question) without priming a downgrade.
    out = views.summarize_advisories(_adv_manifest("green", cross_check="CAPE 1800 J/kg"))
    entry = out[0]
    assert entry["cross_check_present"] is True
    assert entry["detail_tool"] == "get_advisory_detail"
    assert entry["per_model"][0]["cross_check"] == "CAPE 1800 J/kg"


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
