"""Two-area (staging | corpus) lookup + promotion."""

from __future__ import annotations

import pytest

from weatherbrief.eval_workbench import corpus
from weatherbrief.eval_workbench.corpus import CorpusLabel, CorpusMeta


@pytest.fixture
def areas(tmp_path, monkeypatch):
    """Point corpus + staging at sibling tmp dirs (the repo layout)."""
    root = tmp_path / "evalset"
    monkeypatch.setenv("EVAL_CORPUS_DIR", str(root / "corpus"))
    monkeypatch.setenv("EVAL_STAGING_DIR", str(root / "staging"))
    return root


def _meta(cid="egtf_lfat_2026-06-01_d2_2026-05-30") -> CorpusMeta:
    return CorpusMeta(
        corpus_id=cid,
        route="EGTF -> LFAT",
        target_date="2026-06-01",
        fetch_date="2026-05-30",
        departure_time="2026-06-01T09:00:00+00:00",
        fetch_timestamp="2026-05-30T00:00:00+00:00",
        days_out=2,
    )


def test_find_pack_searches_staging_first(areas):
    m = _meta()
    corpus.save_corpus_meta(m, "staging")
    found = corpus.find_pack(m.corpus_id)
    assert found is not None
    assert found.area == "staging"


def test_promote_requires_label(areas):
    m = _meta()
    corpus.save_corpus_meta(m, "staging")
    with pytest.raises(ValueError):
        corpus.promote(m.corpus_id)


def test_promote_moves_pack_and_label(areas):
    m = _meta()
    corpus.save_corpus_meta(m, "staging")
    corpus.save_label(m.corpus_id, CorpusLabel(assessments={"balanced": "AMBER"}), "staging")

    promoted = corpus.promote(m.corpus_id)

    assert promoted.area == "corpus"
    assert promoted.is_labeled
    # Moved, not copied: gone from staging, present in corpus, label travelled.
    assert not corpus.corpus_exists(m.corpus_id, "staging")
    assert corpus.corpus_exists(m.corpus_id, "corpus")
    assert corpus.load_label(m.corpus_id, "corpus").assessments == {"balanced": "AMBER"}


def test_promote_twice_conflicts(areas):
    m = _meta()
    corpus.save_corpus_meta(m, "staging")
    corpus.save_label(m.corpus_id, CorpusLabel(assessments={"balanced": "GREEN"}), "staging")
    corpus.promote(m.corpus_id)
    # A second staging copy with the same id can't clobber the corpus one.
    corpus.save_corpus_meta(m, "staging")
    corpus.save_label(m.corpus_id, CorpusLabel(assessments={"balanced": "RED"}), "staging")
    with pytest.raises(FileExistsError):
        corpus.promote(m.corpus_id)
