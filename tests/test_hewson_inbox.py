"""Hewson compute offload: inbox ingest (droplet side) and the wait/fallback.

The node rsyncs ``hewson/<model>/<init>.npz`` into a read-only inbox; the
serving box copies valid files into its own ``DATA_DIR/hewson`` and computes
locally only what never arrived.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from weatherbrief.frontal.grid import build_grid_coords
from weatherbrief.hewson.inbox import (
    REQUIRED_METRICS,
    SnapshotValidationError,
    ingest_inbox,
    newest_local_init,
    validate_snapshot,
)
from weatherbrief.hewson.precompute import snapshot_path, write_snapshot

NOW = datetime(2026, 10, 16, 6, 5, tzinfo=timezone.utc)
INIT = int(datetime(2026, 10, 16, 0, tzinfo=timezone.utc).timestamp())
LEVELS = [925, 850, 700]


def _write(root: Path, model: str, init: int = INIT, *, lat=None, lon=None,
           drop: str | None = None, n_time: int = 2) -> Path:
    """Write a schema-complete (small) snapshot the way the node would."""
    g_lat, g_lon = build_grid_coords()
    lat = g_lat if lat is None else lat
    lon = g_lon if lon is None else lon
    shape = (n_time, len(lat), len(lon))
    per_level = {
        L: {m: np.zeros(shape, dtype=np.float32) for m in REQUIRED_METRICS
            if m != drop}
        for L in LEVELS
    }
    valid = np.array(
        [np.datetime64(datetime.fromtimestamp(init + 10800 * i, tz=timezone.utc)
                       .replace(tzinfo=None)) for i in range(n_time)],
        dtype="datetime64[ns]",
    )
    path = snapshot_path(model, init, root)
    write_snapshot(path, init, valid, lat, lon, LEVELS, 3, per_level)
    return path


class TestValidateSnapshot:
    def test_valid_snapshot_returns_init(self, tmp_path):
        lat, lon = build_grid_coords()
        assert validate_snapshot(_write(tmp_path, "gfs"), lat, lon) == INIT

    def test_truncated_file_rejected(self, tmp_path):
        lat, lon = build_grid_coords()
        path = _write(tmp_path, "gfs")
        data = path.read_bytes()
        path.write_bytes(data[: len(data) // 2])
        with pytest.raises(SnapshotValidationError):
            validate_snapshot(path, lat, lon)

    def test_grid_mismatch_rejected(self, tmp_path):
        lat, lon = build_grid_coords()
        path = _write(tmp_path, "gfs", lat=lat[:-1])
        with pytest.raises(SnapshotValidationError, match="grid"):
            validate_snapshot(path, lat, lon)

    def test_missing_metric_rejected(self, tmp_path):
        lat, lon = build_grid_coords()
        path = _write(tmp_path, "gfs", drop="tendency")
        with pytest.raises(SnapshotValidationError, match="tendency"):
            validate_snapshot(path, lat, lon)

    def test_filename_must_match_init(self, tmp_path):
        lat, lon = build_grid_coords()
        path = _write(tmp_path, "gfs")
        renamed = path.with_name("2026-10-15T12:00:00Z.npz")
        path.rename(renamed)
        with pytest.raises(SnapshotValidationError, match="filename"):
            validate_snapshot(renamed, lat, lon)


class TestIngestInbox:
    def test_copies_new_snapshots(self, tmp_path):
        inbox, out = tmp_path / "inbox", tmp_path / "out"
        for m in ("ecmwf", "gfs", "icon"):
            _write(inbox, m)
        result = ingest_inbox(inbox, out, now=NOW)
        assert result.ingested == {m: [INIT] for m in ("ecmwf", "gfs", "icon")}
        for m in ("ecmwf", "gfs", "icon"):
            assert snapshot_path(m, INIT, out).exists()
            assert newest_local_init(m, out) == INIT
        # No temp file left behind for a reader's *.npz glob to trip on.
        assert not list(out.rglob(".*"))

    def test_existing_local_snapshot_not_recopied(self, tmp_path):
        inbox, out = tmp_path / "inbox", tmp_path / "out"
        _write(inbox, "gfs")
        local = _write(out, "gfs")
        before = local.stat().st_mtime_ns
        assert ingest_inbox(inbox, out, now=NOW).ingested == {}
        assert local.stat().st_mtime_ns == before

    def test_skips_rsync_temp_files(self, tmp_path):
        inbox, out = tmp_path / "inbox", tmp_path / "out"
        path = _write(inbox, "gfs")
        path.rename(path.with_name(f".{path.name}"))
        assert ingest_inbox(inbox, out, now=NOW).ingested == {}

    def test_skips_inits_past_retention(self, tmp_path):
        """A file the local purge just removed must not be copied straight back."""
        inbox, out = tmp_path / "inbox", tmp_path / "out"
        old = int((NOW - timedelta(hours=60)).timestamp()) // 3600 * 3600
        _write(inbox, "gfs", old)
        assert ingest_inbox(inbox, out, now=NOW).ingested == {}

    def test_corrupt_file_rejected_then_skipped(self, tmp_path):
        inbox, out = tmp_path / "inbox", tmp_path / "out"
        bad = _write(inbox, "gfs")
        bad.write_bytes(b"not a zip")
        first = ingest_inbox(inbox, out, now=NOW)
        assert first.rejected == [bad]
        assert not snapshot_path("gfs", INIT, out).exists()
        # The caller's skip set keeps it from being re-read every poll.
        assert ingest_inbox(inbox, out, now=NOW, skip={bad}).rejected == []


# ---------------------------------------------------------------------------
# Scheduler: wait for the node, fall back per model
# ---------------------------------------------------------------------------


@pytest.fixture
def sched_env(tmp_path, monkeypatch):
    """Inbox + DATA_DIR in tmp, fake clock, fixed expected inits."""
    from weatherbrief import scheduler as sched

    inbox = tmp_path / "inbox"
    inbox.mkdir()
    monkeypatch.setenv("HEWSON_INBOX_DIR", str(inbox))
    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("HEWSON_INGEST_HEALTHCHECK_URL", raising=False)

    clock = {"t": 0.0}
    monkeypatch.setattr(sched.time, "monotonic", lambda: clock["t"])

    async def fake_sleep(secs):
        clock["t"] += secs

    monkeypatch.setattr(sched.asyncio, "sleep", fake_sleep)
    # Real clock here (ingest_inbox's retention cutoff), so a recent init.
    init = int(datetime.now(timezone.utc).timestamp()) // 21600 * 21600 - 21600
    expected = {"ecmwf": init, "gfs": init, "icon": init}
    monkeypatch.setattr(sched, "_hewson_expected_inits", lambda models: dict(expected))

    pings: list[str] = []

    details: list[str] = []

    async def fake_ping(suffix="", detail="", env="INGEST_HEALTHCHECK_URL"):
        pings.append(f"{env}{suffix}")
        details.append(detail)

    monkeypatch.setattr(sched, "_ping_ingest_healthcheck", fake_ping)
    return SimpleNamespace(sched=sched, inbox=inbox, clock=clock, init=init,
                           expected=expected, pings=pings, details=details)


class TestTryIngestHewsonSnapshots:
    def test_no_inbox_returns_none_without_waiting(self, sched_env, monkeypatch):
        monkeypatch.setenv("HEWSON_INBOX_DIR", str(sched_env.inbox / "absent"))
        assert asyncio.run(sched_env.sched._try_ingest_hewson_snapshots()) is None
        assert sched_env.clock["t"] == 0

    def test_empty_inbox_is_not_in_use(self, sched_env):
        """The compose default mounts an empty dir; that must not cost a wait."""
        assert asyncio.run(sched_env.sched._try_ingest_hewson_snapshots()) is None
        assert sched_env.clock["t"] == 0

    def test_all_delivered_skips_local(self, sched_env):
        for m in ("ecmwf", "gfs", "icon"):
            _write(sched_env.inbox, m, sched_env.init, n_time=1)
        missing = asyncio.run(sched_env.sched._try_ingest_hewson_snapshots())
        assert missing == set()
        assert sched_env.clock["t"] == 0  # first poll was enough
        assert sched_env.pings == ["HEWSON_INGEST_HEALTHCHECK_URL"]

    def test_partial_delivery_falls_back_for_missing_only(self, sched_env):
        for m in ("gfs", "icon"):
            _write(sched_env.inbox, m, sched_env.init, n_time=1)
        missing = asyncio.run(sched_env.sched._try_ingest_hewson_snapshots())
        assert missing == {"ecmwf"}
        assert sched_env.clock["t"] >= sched_env.sched._HEWSON_INBOX_WAIT_MAX_SECONDS
        assert sched_env.pings == ["HEWSON_INGEST_HEALTHCHECK_URL/fail"]

    def test_stale_node_snapshot_does_not_count(self, sched_env):
        """Last cycle's file in the mirror is not this cycle's delivery."""
        older = sched_env.init - 12 * 3600
        for m in ("ecmwf", "gfs", "icon"):
            _write(sched_env.inbox, m, older, n_time=1)
        missing = asyncio.run(sched_env.sched._try_ingest_hewson_snapshots())
        assert missing == {"ecmwf", "gfs", "icon"}

    def test_unknown_init_needs_an_ingest_this_cycle(self, sched_env):
        sched_env.expected["ecmwf"] = 0
        for m in ("ecmwf", "gfs", "icon"):
            _write(sched_env.inbox, m, sched_env.init, n_time=1)
        assert asyncio.run(sched_env.sched._try_ingest_hewson_snapshots()) == set()


    def test_unknown_init_accepts_recent_local_snapshot(self, sched_env):
        """Metadata outage + snapshot already on disk is not a non-delivery."""
        import os

        sched_env.expected["ecmwf"] = 0
        _write(sched_env.inbox, "gfs", sched_env.init, n_time=1)
        _write(sched_env.inbox, "icon", sched_env.init, n_time=1)
        _write(Path(os.environ["DATA_DIR"]) / "hewson", "ecmwf", sched_env.init,
               n_time=1)
        assert asyncio.run(sched_env.sched._try_ingest_hewson_snapshots()) == set()
        assert sched_env.clock["t"] == 0

    def test_unknown_init_old_local_snapshot_still_missing(self, sched_env):
        import os

        sched_env.expected["ecmwf"] = 0
        for m in ("gfs", "icon"):
            _write(sched_env.inbox, m, sched_env.init, n_time=1)
        _write(Path(os.environ["DATA_DIR"]) / "hewson", "ecmwf",
               sched_env.init - 24 * 3600, n_time=1)
        assert asyncio.run(sched_env.sched._try_ingest_hewson_snapshots()) == {"ecmwf"}

    def test_local_only_models_are_not_waited_for(self, sched_env, monkeypatch):
        """HEWSON_INBOX_MODELS=gfs,icon: ECMWF is computed here, no 30-min wait."""
        monkeypatch.setenv("HEWSON_INBOX_MODELS", "gfs,icon")
        for m in ("gfs", "icon"):
            _write(sched_env.inbox, m, sched_env.init, n_time=1)
        missing = asyncio.run(sched_env.sched._try_ingest_hewson_snapshots())
        assert missing == {"ecmwf"}
        assert sched_env.clock["t"] == 0
        assert sched_env.pings == ["HEWSON_INGEST_HEALTHCHECK_URL"]

    def test_local_only_models_added_to_fallback(self, sched_env, monkeypatch):
        monkeypatch.setenv("HEWSON_INBOX_MODELS", "gfs,icon")
        _write(sched_env.inbox, "gfs", sched_env.init, n_time=1)
        missing = asyncio.run(sched_env.sched._try_ingest_hewson_snapshots())
        assert missing == {"ecmwf", "icon"}
        assert sched_env.pings == ["HEWSON_INGEST_HEALTHCHECK_URL/fail"]

    def test_inbox_models_unknown_names_ignored(self, monkeypatch):
        from weatherbrief import scheduler as sched

        monkeypatch.setenv("HEWSON_INBOX_MODELS", "gfs, bogus")
        assert sched._hewson_inbox_models() == ["gfs"]
        monkeypatch.setenv("HEWSON_INBOX_MODELS", "gsf")  # typo: keep the offload
        assert sched._hewson_inbox_models() == ["ecmwf", "gfs", "icon"]
        monkeypatch.delenv("HEWSON_INBOX_MODELS")
        assert sched._hewson_inbox_models() == ["ecmwf", "gfs", "icon"]

    def test_fail_ping_names_rejected_files(self, sched_env):
        for m in ("gfs", "icon"):
            _write(sched_env.inbox, m, sched_env.init, n_time=1)
        bad = _write(sched_env.inbox, "ecmwf", sched_env.init, n_time=1)
        bad.write_bytes(b"not a zip")
        assert asyncio.run(sched_env.sched._try_ingest_hewson_snapshots()) == {"ecmwf"}
        assert "rejected 1" in sched_env.details[-1]
        assert bad.name in sched_env.details[-1]


@pytest.mark.parametrize("skipped, rc", [
    ({}, 0), ({"gfs": "fresh"}, 0), ({"gfs": "error"}, 1), ({"ecmwf": "no-init-time"}, 1),
])
def test_cli_exit_code_flags_failed_models(monkeypatch, skipped, rc):
    """Non-zero only for real failures, so the node withholds its deadman."""
    import argparse

    from weatherbrief.hewson import cli

    monkeypatch.setattr(cli, "run_once", lambda **kw: SimpleNamespace(
        snapshots={}, skipped=skipped, sources={}, elapsed_seconds=0.0, purged=0,
        api_calls_total=0))
    args = argparse.Namespace(
        models=None, levels=None, output_dir=None, forecast_days=5,
        stride_hours=3, retention_hours=48, dry_run=False, force=False, date=None,
        source="grib")
    assert cli._cmd_precompute(args) == rc


def test_fallback_runs_only_missing_models(monkeypatch):
    from weatherbrief import scheduler as sched

    seen = {}

    def fake_run_once(models=None):
        seen["models"] = models
        return SimpleNamespace(snapshots={}, skipped={}, purged=0,
                               api_calls_total=0, elapsed_seconds=0.0)

    monkeypatch.setattr("weatherbrief.hewson.run_once", fake_run_once)
    sched._run_hewson_precompute_once(["ecmwf"])
    assert seen["models"] == ["ecmwf"]


def test_expected_inits_wait_for_the_cycle_when_local_run_is_stale(monkeypatch):
    """This box's ECMWF is a cycle behind: expect the cycle's init from the
    node, so an old snapshot on disk does not count as delivered."""
    import weatherbrief.scheduler as sched
    from weatherbrief.hewson import grib_source

    cycle = datetime(2026, 10, 24, 0, tzinfo=timezone.utc)
    stale = SimpleNamespace(init_unix=int((cycle - timedelta(hours=12)).timestamp()))
    current = SimpleNamespace(init_unix=int(cycle.timestamp()))
    monkeypatch.setattr(grib_source, "resolve_run",
                        lambda m, h, L: stale if m == "ecmwf" else current)
    monkeypatch.setattr(grib_source, "expected_init", lambda m: cycle)
    assert sched._hewson_expected_inits(["ecmwf", "gfs"]) == {
        "ecmwf": int(cycle.timestamp()), "gfs": int(cycle.timestamp()),
    }
