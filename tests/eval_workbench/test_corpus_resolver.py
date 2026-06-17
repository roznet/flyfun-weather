"""Unit tests for the dev-only eval workbench corpus + virtual-flight resolver.

Pure-logic tests: they write a corpus descriptor to a temp dir and check the
resolver rebuilds a Flight + BriefingPackMeta from it, plus corpus label I/O
and coverage. No DB, no server.
"""

from __future__ import annotations

import json

import pytest

from weatherbrief.eval_workbench import config, corpus, resolver


@pytest.fixture
def corpus_env(tmp_path, monkeypatch):
    """Point the corpus at a temp dir and enable the workbench."""
    monkeypatch.setenv("EVAL_CORPUS_DIR", str(tmp_path))
    monkeypatch.setenv("WEATHERBRIEF_EVAL_WORKBENCH", "1")
    return tmp_path


def _make_meta(corpus_id="egtf_lfat_2026-03-14_d7") -> corpus.CorpusMeta:
    return corpus.CorpusMeta(
        corpus_id=corpus_id,
        route="EGTF -> LFAT",
        waypoints=["EGTF", "LFAT"],
        target_date="2026-03-14",
        fetch_date="2026-03-07",
        departure_time="2026-03-14T09:00:00+00:00",
        fetch_timestamp="2026-03-07T06:30:00+00:00",
        days_out=7,
        cruise_altitude_ft=9000,
        assessment="RED",
        assessment_reason="Multiple red icing advisories.",
        situations=["multi_red", "icing", "channel_crossing"],
        faithful=True,
        source="prod pack <hash>",
    )


# --- config -----------------------------------------------------------------

def test_flight_id_namespace_roundtrip():
    fid = config.eval_flight_id("abc")
    assert fid == "eval-abc"
    assert config.is_eval_flight_id(fid)
    assert config.corpus_id_from_flight_id(fid) == "abc"
    assert not config.is_eval_flight_id("egtf-lfat-2026-1234")
    assert not config.is_eval_flight_id(None)


def test_enabled_gate(monkeypatch):
    monkeypatch.delenv("WEATHERBRIEF_EVAL_WORKBENCH", raising=False)
    assert config.eval_workbench_enabled() is False
    monkeypatch.setenv("WEATHERBRIEF_EVAL_WORKBENCH", "true")
    assert config.eval_workbench_enabled() is True
    monkeypatch.setenv("WEATHERBRIEF_EVAL_WORKBENCH", "0")
    assert config.eval_workbench_enabled() is False


# --- corpus I/O -------------------------------------------------------------

def test_save_and_load_meta_and_label(corpus_env):
    meta = _make_meta()
    corpus.save_corpus_meta(meta)
    assert corpus.corpus_exists(meta.corpus_id)

    loaded = corpus.load_corpus_meta(meta.corpus_id)
    assert loaded.route == "EGTF -> LFAT"
    assert loaded.assessment == "RED"

    # Unlabelled initially.
    assert corpus.load_label(meta.corpus_id) is None

    label = corpus.CorpusLabel(
        assessments={"conservative": "RED", "balanced": "RED", "tolerant": "AMBER"},
        rationale="Icing unmanageable without FIKI.",
        labeled_by="sme",
    )
    corpus.save_label(meta.corpus_id, label)

    reloaded = corpus.load_label(meta.corpus_id)
    assert reloaded is not None
    assert reloaded.assessments["tolerant"] == "AMBER"
    assert reloaded.is_complete is True

    # Label is written as a sibling file (committed artifact).
    label_file = corpus_env / meta.corpus_id / corpus.LABEL_FILE
    assert json.loads(label_file.read_text())["rationale"].startswith("Icing")


def test_save_label_requires_existing_pack(corpus_env):
    with pytest.raises(FileNotFoundError):
        corpus.save_label("does-not-exist", corpus.CorpusLabel())


def test_corpus_label_normalizes_and_validates_assessments():
    # Lower-case is upper-cased; empty values are dropped.
    lab = corpus.CorpusLabel(assessments={"balanced": "amber", "tolerant": ""})
    assert lab.assessments == {"balanced": "AMBER"}
    assert lab.is_complete is False

    # A typo'd assessment is rejected (would silently never match in scoring).
    with pytest.raises(ValueError):
        corpus.CorpusLabel(assessments={"balanced": "AMBR"})


def test_list_and_coverage(corpus_env):
    corpus.save_corpus_meta(_make_meta("a"))
    corpus.save_corpus_meta(
        _make_meta("b").model_copy(update={"corpus_id": "b", "situations": ["icing"]})
    )
    corpus.save_label(
        "a", corpus.CorpusLabel(assessments={"balanced": "RED"})
    )

    packs = corpus.list_corpus()
    assert {p.corpus_id for p in packs} == {"a", "b"}
    assert next(p for p in packs if p.corpus_id == "a").is_labeled
    assert not next(p for p in packs if p.corpus_id == "b").is_labeled

    rows = {r["situation"]: r for r in corpus.coverage_report(packs)}
    # "icing" tagged on both; only "a" is labelled.
    assert rows["icing"]["total"] == 2
    assert rows["icing"]["labeled"] == 1
    # "channel_crossing" only on "a" (labelled).
    assert rows["channel_crossing"]["total"] == 1
    assert rows["channel_crossing"]["labeled"] == 1
    # a cell no pack carries
    assert rows["convective"]["total"] == 0


# --- resolver ---------------------------------------------------------------

def test_synthesize_flight_and_pack_meta(corpus_env):
    meta = _make_meta()
    flight = resolver.synthesize_flight(meta)
    assert flight.id == "eval-egtf_lfat_2026-03-14_d7"
    assert flight.user_id == resolver.EVAL_USER_ID
    assert flight.waypoints == ["EGTF", "LFAT"]
    assert flight.cruise_altitude_ft == 9000
    assert flight.target_date == "2026-03-14"  # computed from departure_time
    assert flight.private is False

    pm = resolver.synthesize_pack_meta(meta)
    assert pm.flight_id == "eval-egtf_lfat_2026-03-14_d7"
    assert pm.days_out == 7
    assert pm.assessment == "RED"
    assert pm.has_digest is True
    # artifact_path points at the on-disk corpus pack dir so _get_pack_dir works.
    assert pm.artifact_path.endswith("egtf_lfat_2026-03-14_d7")


def test_resolve_from_disk(corpus_env):
    meta = _make_meta()
    corpus.save_corpus_meta(meta)
    fid = config.eval_flight_id(meta.corpus_id)

    flight = resolver.resolve_eval_flight(fid)
    assert flight.route_name == "EGTF -> LFAT"

    pm = resolver.resolve_eval_pack_meta(fid, "2026-03-07T06:30:00+00:00")
    assert pm.assessment == "RED"

    listed = resolver.resolve_eval_pack_list(fid)
    assert len(listed) == 1 and listed[0].flight_id == fid


def test_resolve_unknown_raises(corpus_env):
    with pytest.raises(FileNotFoundError):
        resolver.resolve_eval_flight("eval-nope")
    assert resolver.resolve_eval_pack_list("eval-nope") == []
