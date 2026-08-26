"""Migration 093's per-row isolation, driven through the migration itself.

:mod:`tests.test_extent_param_rename` covers the transform. What it cannot cover
is the *wiring*: the `try/except` in ``093``'s own loop, added specifically so
one unparseable row cannot roll back the rewrite for every other profile. That
matters more here than in a normal migration — env.py runs the whole
``alembic upgrade`` in a single transaction, and this PR removes the evaluators'
legacy old-key read path, so a migration that raises behind already-deployed
code leaves every pilot's tuning silently ignored fleet-wide (#571 review
round 8).

These drive the real ``upgrade()`` / ``downgrade()`` functions against a seeded
SQLite database, with ``alembic.op`` bound to a live connection, so a regression
in the loop — not just in the transform — fails here.
"""

from __future__ import annotations

import importlib.util
import json
import pathlib

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations

_MIGRATION = (
    pathlib.Path(__file__).resolve().parents[1]
    / "alembic" / "versions" / "093_consolidate_extent_params.py"
)


def _load_migration():
    spec = importlib.util.spec_from_file_location("_mig093", _MIGRATION)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _run(conn, fn):
    """Invoke a migration function with ``alembic.op`` bound to ``conn``."""
    operations = Operations(MigrationContext.configure(conn))
    with Operations.context(operations):
        fn()


@pytest.fixture
def seeded(tmp_path):
    """Three profiles: a good one, an unparseable one, another good one.

    Row 2's ``settings_json`` is not JSON at all — the one failure the
    transform's own guards cannot absorb, because it happens in ``json.loads``
    before the transform is reached.
    """
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'p.db'}")
    with engine.begin() as conn:
        conn.execute(sa.text(
            "CREATE TABLE flight_profiles (id INTEGER PRIMARY KEY, settings_json TEXT)"
        ))
        good_a = {"advisories": {"params": {"vmc_cruise": {"bkn_pct_amber": 12}}}}
        good_b = {"advisories": {"params": {"fiki_icing": {
            "clear_cruise_amber_pct": 70,
        }}}}
        for row_id, payload in ((1, json.dumps(good_a)), (2, "{not json"),
                                (3, json.dumps(good_b))):
            conn.execute(
                sa.text("INSERT INTO flight_profiles VALUES (:i, :s)"),
                {"i": row_id, "s": payload},
            )
    return engine


def _settings(engine, row_id):
    with engine.connect() as conn:
        raw = conn.execute(
            sa.text("SELECT settings_json FROM flight_profiles WHERE id = :i"),
            {"i": row_id},
        ).scalar_one()
    return raw


class TestPerRowIsolation:
    def test_a_bad_row_does_not_cost_the_others_their_rewrite(self, seeded, capsys):
        module = _load_migration()
        with seeded.begin() as conn:
            _run(conn, module.upgrade)

        assert json.loads(_settings(seeded, 1))["advisories"]["params"] == {
            "vmc_cruise": {"extent_pct_amber": 12}
        }
        assert json.loads(_settings(seeded, 3))["advisories"]["params"] == {
            "fiki_icing": {"extent_pct_amber": 30.0}  # polarity flipped with the name
        }

    def test_the_bad_row_is_left_untouched_and_named_in_the_log(self, seeded, capsys):
        module = _load_migration()
        with seeded.begin() as conn:
            _run(conn, module.upgrade)

        assert _settings(seeded, 2) == "{not json", "a row it cannot read, it must not write"
        out = capsys.readouterr().out
        assert "SKIPPED profile 2" in out
        assert "still hold old keys: [2]" in out, (
            "a skipped row must be enumerated, not merely counted"
        )

    def test_downgrade_has_the_same_isolation(self, seeded, capsys):
        module = _load_migration()
        with seeded.begin() as conn:
            _run(conn, module.upgrade)
        with seeded.begin() as conn:
            _run(conn, module.downgrade)

        assert json.loads(_settings(seeded, 1))["advisories"]["params"] == {
            "vmc_cruise": {"bkn_pct_amber": 12}
        }
        assert json.loads(_settings(seeded, 3))["advisories"]["params"] == {
            "fiki_icing": {"clear_cruise_amber_pct": 70.0}
        }
        assert _settings(seeded, 2) == "{not json"
        assert "SKIPPED profile 2" in capsys.readouterr().out


class TestDryRun:
    def test_dry_run_raises_before_writing_anything(self, seeded, monkeypatch):
        """The abort is the mechanism — it rolls the invocation back — so the
        raise and the untouched rows are one assertion, not two."""
        monkeypatch.setenv("EXTENT_RENAME_DRY_RUN", "1")
        module = _load_migration()
        with pytest.raises(RuntimeError, match="EXTENT_RENAME_DRY_RUN"):
            with seeded.begin() as conn:
                _run(conn, module.upgrade)

        assert json.loads(_settings(seeded, 1))["advisories"]["params"] == {
            "vmc_cruise": {"bkn_pct_amber": 12}
        }

    def test_dry_run_reports_the_blast_radius_it_would_have_written(
        self, seeded, monkeypatch, capsys,
    ):
        monkeypatch.setenv("EXTENT_RENAME_DRY_RUN", "1")
        module = _load_migration()
        with pytest.raises(RuntimeError):
            with seeded.begin() as conn:
                _run(conn, module.upgrade)
        out = capsys.readouterr().out
        assert "[093][dry-run] profile 1" in out
        assert "[093][dry-run] profile 3" in out
        assert "on 2 profiles" in out
