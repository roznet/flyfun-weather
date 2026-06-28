"""Tests for the weather-alternates connector shapers.

``summarize_alternates`` and ``alternates_hook`` are the shared dict->dict
transforms behind both agent front-doors — the Claude MCP ``get_alternates``
tool and the ChatGPT ``GET /agent/v1/.../alternates`` route — so the shaping
verified here applies identically to both. The raw input is built from the real
``RouteAlternates`` pydantic models and dumped (``mode="json"``) so the fixture
matches on-snapshot serialization exactly (enums -> values, datetimes -> ISO).
"""

from __future__ import annotations

from datetime import datetime, timezone

from weatherbrief.connectors import views
from weatherbrief.models.alternate_requirement import (
    AlternateQual,
    BandVerdict,
    CriterionAssessment,
    RegAlternateTrigger,
    TriggerVerdict,
)
from weatherbrief.models.alternates import (
    AlternateAirport,
    AlternateAxisPick,
    AlternateRequirement,
    RouteAlternates,
)

_ETA = datetime(2026, 6, 28, 12, 0, tzinfo=timezone.utc)


def _criterion(forecast: float | None, lo: float, hi: float, unit: str, verdict: str):
    return CriterionAssessment(
        label="ceiling" if unit == "ft" else "visibility",
        unit=unit, forecast=forecast, required_min=lo, required_max=hi, verdict=verdict,
    )


def _trigger(regime: str, status: TriggerVerdict, source: str = "nwp"):
    return RegAlternateTrigger(
        regime=regime, status=status, reason=f"{regime} reason", source=source,
        ceiling=_criterion(1500.0, 600.0, 800.0, "ft", status.value),
        visibility=_criterion(8000.0, 1600.0, 3200.0, "m", status.value),
    )


def _qual(regime: str, verdict: BandVerdict, source: str = "nwp"):
    return AlternateQual(
        regime=regime, verdict=verdict, reason=f"{regime} qual",
        ceiling=_criterion(2000.0, 600.0, 800.0, "ft", verdict.value),
        visibility=_criterion(9000.0, 1600.0, 3200.0, "m", verdict.value),
        source=source,
    )


def _candidate(icao: str, dist: float, *, major: bool = False, dominates: bool = False):
    return AlternateAirport(
        icao=icao, name=f"{icao} Airport", lat=46.0, lon=6.0,
        distance_from_dest_nm=dist, position="before",
        flight_category="VFR", wind_speed_kt=10.0, crosswind_kt=4.0,
        ceiling_ft=3000.0, visibility_m=9999.0, best_runway_id="27",
        has_instrument_approach=True, best_approach_type="ILS",
        longest_runway_ft=8000, has_hard_runway=True, point_of_entry=True,
        is_major=major, better_category=True, dominates_destination=dominates,
        faa=_qual("faa", BandVerdict.LIKELY), easa=_qual("easa", BandVerdict.LIKELY),
    )


def _raw_alternates(*, n_candidates: int = 2) -> dict:
    """A realistic serialized RouteAlternates block (as it sits in briefing.json)."""
    cands = [_candidate(f"LF{i:02d}", 20.0 + i, major=(i == 1), dominates=(i == 0))
             for i in range(n_candidates)]
    ra = RouteAlternates(
        destination_icao="LSGS", destination_category="MVFR",
        destination_crosswind_kt=12.0, destination_ceiling_ft=1200.0,
        destination_visibility_m=6000.0, eta=_ETA, corridor_nm=20.0, radius_nm=50.0,
        require_approach=True, approach_filter_relaxed=False, candidates_evaluated=40,
        alternates=cands,
        nearest_improving=[
            AlternateAxisPick(axis="category", icao="LF00", distance_from_dest_nm=20.0,
                              position="before"),
            AlternateAxisPick(axis="wind", icao=None),  # no qualifier -> filtered out
        ],
        alternate_requirement=AlternateRequirement(
            destination_icao="LSGS", eta=_ETA,
            faa=_trigger("faa", TriggerVerdict.NOT_REQUIRED),
            easa=_trigger("easa", TriggerVerdict.REQUIRED),
            caveats=["estimated minima"],
        ),
    )
    return ra.model_dump(mode="json")


# --------------------------------------------------------------------------
# summarize_alternates
# --------------------------------------------------------------------------

def test_summarize_alternates_core_shape():
    out = views.summarize_alternates(_raw_alternates())

    assert out["destination"]["icao"] == "LSGS"
    assert out["destination"]["flight_category"] == "MVFR"
    assert out["destination"]["crosswind_kt"] == 12.0
    assert out["corridor_nm"] == 20.0
    assert out["radius_nm"] == 50.0
    assert out["candidates_evaluated"] == 40
    assert out["approach_filter_relaxed"] is False
    # The weather-vs-operational caveat must always ride along.
    assert "operational" in out["note"].lower()
    assert "NOT" in out["note"]


def test_summarize_alternates_requirement_faa_easa():
    out = views.summarize_alternates(_raw_alternates())
    req = out["requirement"]
    assert req["faa"]["status"] == "not_required"
    assert req["easa"]["status"] == "required"
    # Forecast-vs-band transparency is preserved per criterion.
    assert req["faa"]["ceiling"]["forecast"] == 1500.0
    assert req["faa"]["ceiling"]["required_max"] == 800.0
    assert req["easa"]["visibility"]["unit"] == "m"
    assert req["caveats"] == ["estimated minima"]


def test_summarize_alternates_nearest_improving_filters_none():
    out = views.summarize_alternates(_raw_alternates())
    # The "wind" axis pick has no icao -> dropped; only "category" survives.
    axes = [p["axis"] for p in out["nearest_improving"]]
    assert axes == ["category"]
    assert out["nearest_improving"][0]["icao"] == "LF00"


def test_summarize_alternates_candidate_fields():
    out = views.summarize_alternates(_raw_alternates())
    c0 = out["candidates"][0]
    assert c0["icao"] == "LF00"
    assert c0["flight_category"] == "VFR"
    # Suitability fields for the operational cross-check are present.
    assert c0["best_approach_type"] == "ILS"
    assert c0["longest_runway_ft"] == 8000
    assert c0["has_hard_runway"] is True
    assert c0["point_of_entry"] is True
    assert c0["dominates_destination"] is True
    # Regulatory qualification compacted to verdict + provenance only.
    assert c0["faa"] == {"verdict": "likely", "source": "nwp"}
    assert c0["easa"] == {"verdict": "likely", "source": "nwp"}


def test_summarize_alternates_truncates_long_candidate_list():
    out = views.summarize_alternates(_raw_alternates(n_candidates=35), max_candidates=10)
    assert len(out["candidates"]) == 10
    assert out["candidates_truncated"] == 25


def test_summarize_alternates_handles_missing_requirement():
    raw = _raw_alternates()
    raw["alternate_requirement"] = None
    out = views.summarize_alternates(raw)
    assert "requirement" not in out
    assert out["candidates"]  # rest of the shape still renders


# --------------------------------------------------------------------------
# alternates_hook
# --------------------------------------------------------------------------

def test_alternates_hook_is_compact_signal():
    hook = views.alternates_hook(_raw_alternates())
    assert hook["destination"] == "LSGS"
    assert hook["candidate_count"] == 2
    assert hook["detail_tool"] == "get_alternates"
    assert hook["alternate_required"] == {"faa": "not_required", "easa": "required"}
    # No full candidate list on the hot-path hook.
    assert "candidates" not in hook
    assert [p["axis"] for p in hook["nearest_improving"]] == ["category"]


def test_alternates_hook_without_requirement_omits_required_flag():
    raw = _raw_alternates()
    raw["alternate_requirement"] = None
    hook = views.alternates_hook(raw)
    assert "alternate_required" not in hook
    assert hook["candidate_count"] == 2
