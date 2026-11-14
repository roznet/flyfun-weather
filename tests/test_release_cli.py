"""Tests for the `python -m weatherbrief.release` CLI — the programmatic path
used by the deploy skill and the initial backfill import.
"""

from __future__ import annotations

import json

import pytest
from sqlalchemy.orm import sessionmaker

import weatherbrief.release.__main__ as cli
from weatherbrief.db.models import SystemMessageRow


@pytest.fixture
def cli_db(monkeypatch):
    """Point the CLI's SessionLocal/get_engine at a throwaway in-memory DB."""
    from conftest import make_app_engine
    engine = make_app_engine()
    TestSession = sessionmaker(bind=engine)
    monkeypatch.setattr(cli, "get_engine", lambda: engine)
    monkeypatch.setattr(cli, "SessionLocal", TestSession)
    yield TestSession
    engine.dispose()


def _write(tmp_path, entries):
    p = tmp_path / "seed.json"
    p.write_text(json.dumps(entries), encoding="utf-8")
    return str(p)


def _all(session_factory):
    s = session_factory()
    rows = s.query(SystemMessageRow).all()
    out = [(r.date, r.title, r.highlight) for r in rows]
    s.close()
    return out


def test_import_creates_and_dedupes(cli_db, tmp_path):
    path = _write(tmp_path, [
        {"date": "2026-05-20", "title": "A", "body": "x", "category": "feature", "highlight": True},
        {"date": "2026-05-20", "title": "B", "body": "y", "category": "change", "highlight": False},
        {"date": "2026-05-19", "title": "C", "body": "z", "category": "fix", "highlight": False},
    ])

    cli.main(["import", path])
    assert len(_all(cli_db)) == 3

    # Re-running is idempotent: same (date, title) pairs are skipped.
    cli.main(["import", path])
    assert len(_all(cli_db)) == 3


def test_import_force_no_highlight(cli_db, tmp_path):
    path = _write(tmp_path, [
        {"date": "2026-05-20", "title": "A", "body": "x", "category": "feature", "highlight": True},
    ])
    cli.main(["import", path, "--force-no-highlight"])
    rows = _all(cli_db)
    assert rows == [("2026-05-20", "A", False)]


def test_import_preserves_highlight_without_flag(cli_db, tmp_path):
    path = _write(tmp_path, [
        {"date": "2026-05-20", "title": "A", "body": "x", "category": "feature", "highlight": True},
    ])
    cli.main(["import", path])
    assert _all(cli_db) == [("2026-05-20", "A", True)]


def test_import_dry_run_writes_nothing(cli_db, tmp_path):
    path = _write(tmp_path, [
        {"date": "2026-05-20", "title": "A", "body": "x", "category": "feature", "highlight": True},
    ])
    cli.main(["import", path, "--dry-run"])
    assert _all(cli_db) == []


def test_import_reads_stdin(cli_db, monkeypatch):
    """`import -` reads the JSON from stdin.

    This is the path that publishes an iOS release entry on prod: `release-notes/`
    isn't in the container image, so the drafted file is piped in over SSH rather
    than named as a path the container can't see.
    """
    import io

    entries = [{"date": "2026-08-20", "title": "iOS 1.5 — Something",
                "body": "- A bullet", "category": "app_release", "highlight": True}]
    monkeypatch.setattr(cli.sys, "stdin", io.StringIO(json.dumps(entries)))

    cli.main(["import", "-"])
    assert _all(cli_db) == [("2026-08-20", "iOS 1.5 — Something", True)]


def test_import_rejects_bad_category(cli_db, tmp_path):
    path = _write(tmp_path, [
        {"date": "2026-05-20", "title": "A", "body": "x", "category": "bogus", "highlight": False},
    ])
    with pytest.raises(SystemExit):
        cli.main(["import", path])


def test_import_accepts_android_release(cli_db, tmp_path):
    """An Android version is its own category, so the web can point its
    install button at Google Play rather than the App Store (#985)."""
    path = _write(tmp_path, [
        {"date": "2026-11-12", "title": "Android 1.0: Something", "body": "- A bullet",
         "category": "android_release", "highlight": False},
    ])
    cli.main(["import", path])
    s = cli_db()
    assert [r.category for r in s.query(SystemMessageRow).all()] == ["android_release"]
    s.close()


def test_drafted_release_notes_are_importable():
    """Every drafted `release-notes/*.json` passes the CLI's validation, so a
    publish over SSH can't fail on a category or a missing field."""
    from pathlib import Path

    root = Path(__file__).resolve().parent.parent / "release-notes"
    files = sorted(root.glob("*.json"))
    assert files, "no release-notes/*.json found"
    for f in files:
        entries = json.loads(f.read_text(encoding="utf-8"))
        assert isinstance(entries, list), f.name
        for e in entries:
            cli._validate_entry(e.get("date", ""), e.get("title", ""), e.get("body", ""),
                                e.get("category", "feature"))
