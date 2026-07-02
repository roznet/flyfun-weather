"""Unit tests for the timing-scenario scan pure logic.

Covers the parts that don't need ECMWF GRIB on disk: the timing_class registry
mapping, the full-picture diff, the daylight window, the honesty coverage
guardrail, and model round-trips. The full enrichment→grade integration is
exercised against a real synced pack, not here.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from weatherbrief.analysis.advisories import (
    get_scan_class_ids,
    get_timing_class_ids,
)
from weatherbrief.models import (
    RouteAdvisoriesManifest,
    RouteAdvisoryResult,
    TimeWindowScan,
    TimeScanBaseline,
    TimeScanWindow,
)
from weatherbrief.tasks.time_scan import (
    EcmwfExtendResult,
    _diff_manifests,
)


def _adv(advisory_id: str, status: str) -> RouteAdvisoryResult:
    return RouteAdvisoryResult(advisory_id=advisory_id, aggregate_status=status)


def _manifest(statuses: dict[str, str]) -> RouteAdvisoriesManifest:
    return RouteAdvisoriesManifest(
        advisories=[_adv(k, v) for k, v in statuses.items()],
        models=["ecmwf"],
    )


# --- timing_class registry ---------------------------------------------------


def test_timing_class_mapping_partitions_all_evaluators():
    scan = get_timing_class_ids("scan")
    cheap = get_timing_class_ids("cheap")
    none = get_timing_class_ids("none")
    # No overlaps, and every evaluator lands in exactly one bucket.
    assert scan & cheap == set()
    assert scan & none == set()
    assert cheap & none == set()
    assert len(scan) + len(cheap) + len(none) == 21


def test_scan_class_matches_plan():
    assert get_scan_class_ids() == {
        "convective", "convective_character", "icing_escape", "fiki_icing",
        "cloud_top", "vmc_cruise", "vfr_feasibility", "ifr_feasibility",
        "freezing_precip",
    }


def test_fronts_is_none_experimental():
    # fronts is experimental/default-off → must not trigger the scan.
    assert "fronts" not in get_scan_class_ids()


# --- full-picture diff -------------------------------------------------------


def test_diff_improves_worsens_and_trigger_margin():
    scan_ids = {"convective", "icing_escape"}
    baseline = _manifest({
        "convective": "red",       # scan-class, will improve
        "icing_escape": "amber",   # scan-class, unchanged
        "airport_wind": "green",   # non-scan, will worsen
    })
    candidate = _manifest({
        "convective": "amber",     # red→amber : improved (scan)
        "icing_escape": "amber",   # unchanged
        "airport_wind": "amber",   # green→amber : worsened (non-scan)
    })
    improves, worsens, margin = _diff_manifests(baseline, candidate, scan_ids)
    assert improves == ["convective"]
    assert worsens == ["airport_wind"]
    # Only the scan-class delta counts toward the ranking margin (+1).
    assert margin == 1


def test_diff_ignores_unavailable_either_side():
    scan_ids = {"convective"}
    baseline = _manifest({"convective": "unavailable"})
    candidate = _manifest({"convective": "green"})
    improves, worsens, margin = _diff_manifests(baseline, candidate, scan_ids)
    assert improves == [] and worsens == [] and margin == 0


def test_diff_non_scan_improvement_does_not_earn_margin():
    scan_ids = {"convective"}
    baseline = _manifest({"airport_wind": "red"})
    candidate = _manifest({"airport_wind": "green"})
    improves, worsens, margin = _diff_manifests(baseline, candidate, scan_ids)
    # Surfaced in the full-picture improves list, but earns no trigger margin.
    assert improves == ["airport_wind"]
    assert margin == 0


# --- honesty coverage guardrail ---------------------------------------------


def _dt(h: int) -> datetime:
    return datetime(2026, 7, 2, h, tzinfo=timezone.utc)


def test_coverage_and_covers_guardrail():
    ext = EcmwfExtendResult(
        cross_sections=[],
        decoded_valid_times=[_dt(6), _dt(9), _dt(12)],
    )
    assert ext.coverage == (_dt(6), _dt(12))
    # Entirely inside decoded coverage → honest.
    assert ext.covers([_dt(7), _dt(8), _dt(11)]) is True
    # Straddles the late edge → refuse (would clamp to OM past 12z).
    assert ext.covers([_dt(11), _dt(13)]) is False
    # Before the early edge → refuse.
    assert ext.covers([_dt(5), _dt(7)]) is False


def test_coverage_none_when_no_anchors():
    ext = EcmwfExtendResult(cross_sections=[])
    assert ext.coverage is None
    assert ext.covers([_dt(9)]) is False


# --- model round-trip --------------------------------------------------------


def _catalog():
    from weatherbrief.models import AdvisoryCatalogEntry
    return [
        AdvisoryCatalogEntry(
            id="convective", name="Convection", short_description="x",
            description="y", category="convective", timing_class="scan",
        ),
        AdvisoryCatalogEntry(
            id="headwind", name="Headwind", short_description="x",
            description="y", category="wind",  # timing_class defaults to "none"
        ),
    ]


# --- digest synchronous timing hint -----------------------------------------


def test_digest_timing_hint_fires_on_flagged_scan_class():
    from weatherbrief.digest.prompt_builder import _format_timing_hint_context

    m = RouteAdvisoriesManifest(
        advisories=[
            RouteAdvisoryResult(advisory_id="convective", aggregate_status="red"),
            RouteAdvisoryResult(advisory_id="headwind", aggregate_status="amber"),
        ],
        catalog=_catalog(),
    )
    hint = _format_timing_hint_context(m)
    assert hint is not None
    assert "Convection" in hint
    assert "Headwind" not in hint  # non-scan-class must not appear
    assert "Timing options" in hint


def test_digest_timing_hint_silent_when_no_scan_class_flagged():
    from weatherbrief.digest.prompt_builder import _format_timing_hint_context

    m = RouteAdvisoriesManifest(
        advisories=[RouteAdvisoryResult(advisory_id="headwind", aggregate_status="red")],
        catalog=_catalog(),
    )
    assert _format_timing_hint_context(m) is None


# --- MCP / connector referral ------------------------------------------------


def test_connector_timing_referral_on_flagged_scan_class_only():
    from weatherbrief.connectors.views import summarize_advisories

    adv = {
        "catalog": [c.model_dump() for c in _catalog()],
        "advisories": [
            {"advisory_id": "convective", "aggregate_status": "red", "per_model": []},
            {"advisory_id": "headwind", "aggregate_status": "red", "per_model": []},
        ],
    }
    out = {a["id"]: a for a in summarize_advisories(adv)}
    assert out["convective"].get("timing_referral") is True
    assert "timing_referral" not in out["headwind"]  # not scan-class


def test_connector_no_referral_when_scan_class_green():
    from weatherbrief.connectors.views import summarize_advisories

    adv = {
        "catalog": [c.model_dump() for c in _catalog()],
        "advisories": [
            {"advisory_id": "convective", "aggregate_status": "green", "per_model": []},
        ],
    }
    out = {a["id"]: a for a in summarize_advisories(adv)}
    assert "timing_referral" not in out["convective"]


def test_time_window_scan_round_trip_and_candidate_at():
    scan = TimeWindowScan(
        baseline=TimeScanBaseline(departure_time=_dt(9), ecmwf_assessment="AMBER"),
        window=TimeScanWindow(start=_dt(6), end=_dt(16)),
    )
    dumped = scan.model_dump_json()
    loaded = TimeWindowScan.model_validate_json(dumped)
    assert loaded.baseline.ecmwf_assessment == "AMBER"
    assert loaded.candidate_at(_dt(9)) is None  # no candidates yet
