"""Regression test: re-ingest must never clobber a committed golden label."""

from __future__ import annotations

from weatherbrief.eval_workbench import ingest


def test_copy_artifacts_preserves_committed_files(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "briefing.json").write_text('{"x": 1}')
    # A source pack should never carry these, but if it somehow does they must
    # not overwrite the corpus' committed copies.
    (src / "label.json").write_text('{"from": "src"}')
    (src / "corpus_meta.json").write_text('{"from": "src"}')

    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "label.json").write_text('{"golden": "keep me"}')

    ingest._copy_artifacts(src, dest)

    # Payload copied through...
    assert (dest / "briefing.json").read_text() == '{"x": 1}'
    # ...but the committed golden label is untouched...
    assert (dest / "label.json").read_text() == '{"golden": "keep me"}'
    # ...and a stray source corpus_meta.json was not copied in.
    assert not (dest / "corpus_meta.json").exists()
