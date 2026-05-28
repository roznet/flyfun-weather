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


def test_import_rejects_bad_category(cli_db, tmp_path):
    path = _write(tmp_path, [
        {"date": "2026-05-20", "title": "A", "body": "x", "category": "bogus", "highlight": False},
    ])
    with pytest.raises(SystemExit):
        cli.main(["import", path])
