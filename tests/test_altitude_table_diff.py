"""Tests for the altitude-diff primitive and the digest ALTITUDE OPTIONS block (#259)."""

from __future__ import annotations

from weatherbrief.analysis.advisories.altitude_table import (
    diff_altitude_rows,
    row_for_altitude,
)
from weatherbrief.digest.prompt_builder import _format_altitude_options_context
from weatherbrief.models import (
    AdvisoryStatus,
    AltitudeAdvisoryRow,
    AltitudeTableResult,
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
    assert "ALTITUDE OPTIONS" in block
    assert "Planned 8,000 ft" in block
    assert "improves Icing Escape (AMBER→GREEN)" in block
    assert "worsens Headwind (GREEN→AMBER)" in block


def test_altitude_options_block_none_without_planned_row():
    # cruise altitude absent from rows → no usable planned row → omit the block.
    t = _table()
    t.cruise_altitude_ft = 9999
    assert _format_altitude_options_context(t) is None
