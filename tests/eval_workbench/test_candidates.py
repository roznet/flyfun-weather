"""Unit tests for coverage-aware candidate selection."""

from __future__ import annotations

from weatherbrief.eval_workbench.candidates import (
    base_score,
    candidate_reasons,
    select_candidates,
)
from weatherbrief.eval_workbench.corpus import CorpusMeta


def _meta(cid, situations, assessment="AMBER") -> CorpusMeta:
    return CorpusMeta(
        corpus_id=cid,
        route="EGTF -> LFAT",
        target_date="2026-03-14",
        fetch_date="2026-03-07",
        departure_time="2026-03-14T09:00:00+00:00",
        fetch_timestamp="2026-03-07T00:00:00+00:00",
        days_out=3,
        assessment=assessment,
        situations=situations,
    )


def test_reasons_and_score():
    amber_bias = _meta("a", ["all_green"], "AMBER")
    assert candidate_reasons(amber_bias) == ["amber_all_green"]
    assert base_score(amber_bias) == 3

    multi = _meta("b", ["multi_red", "single_red", "icing"], "RED")
    # multi_red wins over single_red (elif), no amber_all_green for RED.
    assert "multi_red" in candidate_reasons(multi)
    assert "single_red" not in candidate_reasons(multi)

    benign = _meta("c", ["all_green"], "GREEN")
    assert candidate_reasons(benign) == []
    assert base_score(benign) == 0


def test_selection_maximizes_cell_coverage():
    # With a tight limit, selection prefers packs that cover the most *new*
    # matrix cells. dup_icing covers two cells (icing + all_green) so it beats
    # the single-cell icing pack; convective adds a third distinct cell.
    metas = [
        _meta("icing", ["icing"], "AMBER"),
        _meta("convective", ["convective"], "AMBER"),
        _meta("dup_icing", ["icing", "all_green"], "AMBER"),
    ]
    picked = select_candidates(metas, limit=2, min_per_cell=1)
    covered = set()
    for p in picked:
        covered.update(p["situations"])
    assert covered == {"icing", "all_green", "convective"}
    assert {p["corpus_id"] for p in picked} == {"dup_icing", "convective"}


def test_selection_respects_limit_and_orders_by_value():
    metas = [
        _meta("benign1", ["all_green"], "GREEN"),
        _meta("benign2", ["all_green"], "GREEN"),
        _meta("rich", ["icing", "convective", "icing_plus_convective"], "RED"),
    ]
    picked = select_candidates(metas, limit=3, min_per_cell=1)
    assert len(picked) == 3
    # The pack filling the most new cells is selected first.
    assert picked[0]["corpus_id"] == "rich"
