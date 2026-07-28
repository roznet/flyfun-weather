"""Two different briefings of one flight on one day must not overwrite.

The corpus key is (route, target, days-out, fetch *day*) on purpose — re-pulling
the same scenario has to rebuild corpus_meta.json while preserving label.json.
But a flight re-briefed later the same day produced the same key, and the second
ingest silently replaced the first (reporting it as "new"). Real case: the
2026-07-26 EGKB->EGHN flight had an 07:01 briefing (LIFR departure, RED) and an
09:38 re-brief (MVFR, AMBER) — materially different scenarios, one slot.
"""

from __future__ import annotations

import pytest

from weatherbrief.eval_workbench import corpus
from weatherbrief.eval_workbench.corpus import CorpusMeta
from weatherbrief.eval_workbench.ingest import (
    corpus_id_for,
    disambiguated_corpus_id,
)

BASE_ID = "egkb_morez_eghn_2026-07-26_d0_2026-07-26"


@pytest.fixture
def areas(tmp_path, monkeypatch):
    root = tmp_path / "evalset"
    monkeypatch.setenv("EVAL_CORPUS_DIR", str(root / "corpus"))
    monkeypatch.setenv("EVAL_STAGING_DIR", str(root / "staging"))
    return root


def _meta(source: str, hhmm: str = "0701", cid: str = BASE_ID) -> CorpusMeta:
    return CorpusMeta(
        corpus_id=cid,
        route="EGKB -> MOREZ -> EGHN",
        target_date="2026-07-26",
        fetch_date="2026-07-26",
        departure_time="2026-07-26T13:00:00+00:00",
        fetch_timestamp=f"2026-07-26T{hhmm[:2]}:{hhmm[2:]}:09+00:00",
        days_out=0,
        source=source,
    )


def test_free_slot_keeps_the_bare_id(areas):
    assert disambiguated_corpus_id(_meta("pack:aaa"), "staging") == BASE_ID


def test_same_pack_reingested_is_idempotent(areas):
    """Re-pulling one scenario must reuse its slot — that is what preserves the
    golden label, and is the reason the key is day-precision in the first place."""
    corpus.save_corpus_meta(_meta("pack:aaa"), "staging")
    assert disambiguated_corpus_id(_meta("pack:aaa"), "staging") == BASE_ID


def test_different_briefing_same_day_gets_its_own_slot(areas):
    """The 09:38 re-brief must not land on top of the 07:01 briefing."""
    corpus.save_corpus_meta(_meta("pack:morning", hhmm="0701"), "staging")

    placed = disambiguated_corpus_id(_meta("pack:midday", hhmm="0938"), "staging")

    assert placed == f"{BASE_ID}_t0938"
    # Incumbent untouched: existing ids, committed dirs and labels stay valid.
    assert corpus.find_pack(BASE_ID) is not None


def test_third_briefing_at_the_same_minute_falls_back_to_seconds(areas):
    corpus.save_corpus_meta(_meta("pack:a", hhmm="0701"), "staging")
    corpus.save_corpus_meta(_meta("pack:b", hhmm="0938", cid=f"{BASE_ID}_t0938"), "staging")

    placed = disambiguated_corpus_id(_meta("pack:c", hhmm="0938"), "staging")

    assert placed == f"{BASE_ID}_t093809"


def test_disambiguated_slot_is_itself_idempotent(areas):
    """A re-pull of the *suffixed* pack returns its own slot, not a new one."""
    corpus.save_corpus_meta(_meta("pack:morning", hhmm="0701"), "staging")
    corpus.save_corpus_meta(
        _meta("pack:midday", hhmm="0938", cid=f"{BASE_ID}_t0938"), "staging"
    )

    assert disambiguated_corpus_id(_meta("pack:midday", hhmm="0938"), "staging") == (
        f"{BASE_ID}_t0938"
    )


def test_collision_spans_areas(areas):
    """An id held in corpus/ must also block a *different* staging pack.

    find_pack searches staging before corpus, so a staging pack reusing a
    promoted pack's id silently shadows it in the workbench — the labelled pack
    becomes unreachable. Identity has to be global, not per-area.
    """
    corpus.save_corpus_meta(_meta("pack:morning", hhmm="0701"), "corpus")

    placed = disambiguated_corpus_id(_meta("pack:midday", hhmm="0938"), "staging")

    assert placed == f"{BASE_ID}_t0938"


def test_suffixed_candidate_also_checks_the_other_area(areas):
    """The fallback ladder must not land on an id the corpus already holds."""
    corpus.save_corpus_meta(_meta("pack:morning", hhmm="0701"), "staging")
    corpus.save_corpus_meta(
        _meta("pack:other", hhmm="0938", cid=f"{BASE_ID}_t0938"), "corpus"
    )

    placed = disambiguated_corpus_id(_meta("pack:midday", hhmm="0938"), "staging")

    assert placed == f"{BASE_ID}_t093809"


def test_missing_timestamp_falls_back_to_content_hash(areas):
    corpus.save_corpus_meta(_meta("pack:morning"), "staging")
    m = _meta("pack:deadbeef1234")
    m = m.model_copy(update={"fetch_timestamp": "not-a-timestamp"})

    assert disambiguated_corpus_id(m, "staging") == f"{BASE_ID}_tdeadbe"


def test_corpus_id_for_suffix_extends_the_base():
    base = corpus_id_for("EGKB -> EGHN", "2026-07-26", 0, "2026-07-26")
    suffixed = corpus_id_for("EGKB -> EGHN", "2026-07-26", 0, "2026-07-26", suffix="t0701")
    assert suffixed == f"{base}_t0701"
