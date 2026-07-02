"""Tests for the altitude-diff primitive and the digest OPTIONS TO IMPROVE block (#259, #330)."""

from __future__ import annotations

from weatherbrief.analysis.advisories.altitude_table import (
    diff_altitude_rows,
    row_for_altitude,
)
from weatherbrief.digest.prompt_builder import (
    _format_altitude_options_context,
    _format_options_to_improve_context,
    _format_tactical_mitigations_context,
)
from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AdvisoryStatus,
    AltitudeAdvisoryRow,
    AltitudeTableResult,
    Mitigation,
    MitigationKind,
    ModelAdvisoryResult,
    RouteAdvisoriesManifest,
    RouteAdvisoryResult,
)

_NAMES = {"icing_escape": "Icing Escape", "headwind": "Headwind", "vmc_cruise": "VMC Cruise"}


def _row(alt: int, **statuses: AdvisoryStatus) -> AltitudeAdvisoryRow:
    return AltitudeAdvisoryRow(altitude_ft=alt, statuses=statuses)


def _table() -> AltitudeTableResult:
    return AltitudeTableResult(
        rows=[
            _row(8000, icing_escape=AdvisoryStatus.AMBER, headwind=AdvisoryStatus.GREEN,
                 vmc_cruise=AdvisoryStatus.GREEN),
            _row(4000, icing_escape=AdvisoryStatus.GREEN, headwind=AdvisoryStatus.AMBER,
                 vmc_cruise=AdvisoryStatus.GREEN),
        ],
        advisory_ids=["icing_escape", "headwind", "vmc_cruise"],
        advisory_names=_NAMES,
        cruise_altitude_ft=8000,
        flight_ceiling_ft=10000,
        step_ft=2000,
        best_below_cruise=4000,
        best_above_cruise=None,
    )


def test_diff_classifies_by_severity():
    t = _table()
    delta = diff_altitude_rows(t.rows[0], t.rows[1], _NAMES)
    assert [c.advisory_id for c in delta.improved] == ["icing_escape"]
    assert delta.improved[0].from_status == AdvisoryStatus.AMBER
    assert delta.improved[0].to_status == AdvisoryStatus.GREEN
    assert [c.advisory_id for c in delta.worsened] == ["headwind"]
    assert delta.unchanged == ["vmc_cruise"]
    assert not delta.is_empty


def test_diff_ignores_unavailable_either_side():
    a = _row(8000, icing_escape=AdvisoryStatus.UNAVAILABLE, headwind=AdvisoryStatus.GREEN)
    b = _row(4000, icing_escape=AdvisoryStatus.RED, headwind=AdvisoryStatus.UNAVAILABLE)
    delta = diff_altitude_rows(a, b)
    assert delta.improved == []
    assert delta.worsened == []
    assert delta.unchanged == []
    assert delta.is_empty


def test_row_for_altitude_exact_match():
    t = _table()
    assert row_for_altitude(t, 4000).altitude_ft == 4000
    assert row_for_altitude(t, 5000) is None
    assert row_for_altitude(t, None) is None


def test_altitude_options_block_names_tradeoff():
    block = _format_altitude_options_context(_table())
    assert block is not None
    assert "Altitude (one choice, affects all altitude-dependent advisories):" in block
    assert "Planned 8,000 ft" in block
    assert "improves Icing Escape (AMBER→GREEN)" in block
    assert "worsens Headwind (GREEN→AMBER)" in block


def test_altitude_options_block_none_without_planned_row():
    # cruise altitude absent from rows → no usable planned row → omit the block.
    t = _table().model_copy(update={"cruise_altitude_ft": 9999})
    assert _format_altitude_options_context(t) is None


def test_altitude_options_block_same_picture_when_statuses_match():
    # A lower option with identical statuses → "same advisory picture as planned".
    t = AltitudeTableResult(
        rows=[
            _row(8000, icing_escape=AdvisoryStatus.AMBER, headwind=AdvisoryStatus.GREEN),
            _row(4000, icing_escape=AdvisoryStatus.AMBER, headwind=AdvisoryStatus.GREEN),
        ],
        advisory_ids=["icing_escape", "headwind"],
        advisory_names=_NAMES,
        cruise_altitude_ft=8000,
        flight_ceiling_ft=10000,
        step_ft=2000,
        best_below_cruise=4000,
        best_above_cruise=None,
    )
    block = _format_altitude_options_context(t)
    assert block is not None
    assert "Lower option 4,000 ft: same advisory picture as planned." in block
    assert "Higher option: planned altitude is already best at/above cruise." in block


# --- OPTIONS TO IMPROVE: tactical mitigations + consolidation (#330) ---


def _manifest(*advisories: RouteAdvisoryResult) -> RouteAdvisoriesManifest:
    catalog = [
        AdvisoryCatalogEntry(
            id="vfr_feasibility", name="VFR Feasibility",
            short_description="", description="", category="feasibility",
        ),
        AdvisoryCatalogEntry(
            id="cloud_top", name="Cloud Top",
            short_description="", description="", category="cloud",
        ),
    ]
    return RouteAdvisoriesManifest(advisories=list(advisories), catalog=catalog)


def _advisory(advisory_id: str, *mitigations: Mitigation) -> RouteAdvisoryResult:
    return RouteAdvisoryResult(
        advisory_id=advisory_id,
        aggregate_status=AdvisoryStatus.RED,
        aggregate_detail="",
        per_model=[ModelAdvisoryResult(model="gfs", status=AdvisoryStatus.RED)],
        aggregate_mitigations=list(mitigations),
    )


def test_tactical_block_groups_nonaltitude_mitigations_by_advisory():
    tactical = Mitigation(
        kind=MitigationKind.ROUTE_POSITION, addresses="climb_deck",
        detail="climb to cruise after ~40 nm from departure",
        mitigated_status=AdvisoryStatus.GREEN, distance_nm=40.0, reference="departure",
    )
    block = _format_tactical_mitigations_context(_manifest(_advisory("vfr_feasibility", tactical)))
    assert block is not None
    assert "Tactical (per-advisory, no altitude change):" in block
    assert "VFR Feasibility: climb to cruise after ~40 nm from departure" in block


def test_tactical_block_drops_altitude_mitigations():
    # An ALTITUDE mitigation must NOT appear in the tactical block — the altitude
    # sub-block owns that axis (and shows the worsens-Y trade-off it would hide).
    altitude = Mitigation(
        kind=MitigationKind.ALTITUDE, addresses="cruise_imc",
        detail="fly 6,000 ft to stay below the deck",
        mitigated_status=AdvisoryStatus.GREEN, altitude_ft=6000,
    )
    assert _format_tactical_mitigations_context(_manifest(_advisory("cloud_top", altitude))) is None


def test_tactical_block_none_when_no_mitigations():
    assert _format_tactical_mitigations_context(_manifest(_advisory("vfr_feasibility"))) is None


def test_options_to_improve_consolidates_altitude_and_tactical():
    tactical = Mitigation(
        kind=MitigationKind.ROUTE_POSITION, addresses="climb_deck",
        detail="climb to cruise after ~40 nm from departure",
        mitigated_status=AdvisoryStatus.AMBER, distance_nm=40.0,
    )
    block = _format_options_to_improve_context(
        _table(), _manifest(_advisory("vfr_feasibility", tactical))
    )
    assert block is not None
    assert "=== OPTIONS TO IMPROVE (advice only — do NOT change the assessment) ===" in block
    # Both sub-parts present under the one fence.
    assert "Altitude (one choice" in block
    assert "Tactical (per-advisory, no altitude change):" in block


def test_options_to_improve_none_when_both_empty():
    # No table, no tactical mitigations, and no flagged scan-class advisory
    # (empty manifest → no timing hint either) → whole section omitted.
    assert _format_options_to_improve_context(None, _manifest()) is None


def test_options_to_improve_timing_hint_when_scan_class_flagged():
    # A flagged scan-class advisory (vfr_feasibility RED) adds the timing
    # pointer sub-block even with no altitude table / tactical mitigations.
    block = _format_options_to_improve_context(None, _manifest(_advisory("vfr_feasibility")))
    assert block is not None
    assert "Timing" in block
    assert "Timing options in the app" in block


def test_options_to_improve_altitude_only_when_no_tactical():
    block = _format_options_to_improve_context(_table(), _manifest(_advisory("vfr_feasibility")))
    assert block is not None
    assert "Altitude (one choice" in block
    assert "Tactical" not in block
