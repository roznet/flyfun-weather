"""scripts/ops/deploy.py + sync_ecmwf.py: the decisions that must never read a false `ok`."""

from __future__ import annotations

import os
import time
from datetime import datetime, timedelta, timezone

import pytest

from scripts.ops import deploy, sync_ecmwf

NOW = datetime(2026, 10, 10, 18, 30, tzinfo=timezone.utc)


def _ts(minutes_ago: float) -> str:
    return (NOW - timedelta(minutes=minutes_ago)).isoformat().replace("+00:00", "Z")


# --- standalone cycle state (deploy-notes §D7) ------------------------------------------

def test_cycle_running_when_last_line_is_a_launch():
    lines = [f"{_ts(30)} Standalone verification: sleeping 600s until next verification hour",
             f"{_ts(12)} Standalone full cycle: launching subprocess (weatherbrief.verify standalone)"]
    st, ev = deploy.cycle_state(lines, now=NOW)
    assert st == "warn" and "12 min ago" in ev


def test_other_loops_sleeping_lines_never_count_as_idle():
    # Review finding #2: "METAR ingest: sleeping" once read as "idle" mid-cycle.
    lines = [f"{_ts(1)} METAR ingest: sleeping 590s until next ingest tick",
             f"{_ts(2)} Forecast fetch: sleeping 3000s until next fetch hour"]
    assert deploy.cycle_state(lines, now=NOW)[0] == "unknown"


def test_no_lines_is_could_not_tell_not_idle():
    assert deploy.cycle_state([], now=NOW)[0] == "unknown"


def test_sleep_is_measured_from_when_it_was_logged():
    # 400 s sleep logged 6 min ago -> due in ~40 s, not in 6+ minutes.
    lines = [f"{_ts(6)} Standalone verification: sleeping 400s until next verification hour"]
    st, ev = deploy.cycle_state(lines, now=NOW)
    assert st == "warn" and "due" in ev
    lines = [f"{_ts(1)} Standalone verification: sleeping 42000s until next verification hour"]
    assert deploy.cycle_state(lines, now=NOW)[0] == "ok"


def test_completed_cycle_is_ok():
    lines = [f"{_ts(20)} Standalone light cycle: launching subprocess (x)",
             f"{_ts(19)} Standalone verification cycle complete: 3 models"]
    assert deploy.cycle_state(lines, now=NOW)[0] == "ok"


# --- parsing helpers ----------------------------------------------------------------------

def test_parse_ts_handles_docker_nanoseconds_and_naive_as_utc():
    assert deploy.parse_ts("2026-10-10T16:07:39.203971618Z") == datetime(
        2026, 10, 10, 16, 7, 39, 203971, tzinfo=timezone.utc)
    assert deploy.parse_ts("2026-10-10 20:00").tzinfo is not None
    assert deploy.parse_ts("garbage") is None


def test_sections_missing_end_marker_is_detectable():
    s = deploy.sections("@@head\nabc\n@@df\n/dev/sda 100 50 50 50% /\n")
    assert s["head"] == "abc" and "end" not in s
    assert deploy.sections("@@head\nabc\n@@end\n")["end"] == ""


def test_heads_at_reads_git_not_the_working_tree():
    heads = deploy.heads_at("HEAD")
    assert len(heads) == 1 and next(iter(heads)).isdigit()


def test_test_plan_by_changed_area():
    names = lambda changed: [n for n, _ in deploy.test_plan(changed)]  # noqa: E731
    assert names(["src/weatherbrief/x.py"]) == ["pytest"]
    assert names(["web/ts/a.ts"]) == ["pytest", "vitest (hard gate)", "playwright (warn only)"]
    assert names(["configs/x.yaml"]) == ["pytest", "playwright (warn only)"]


# --- sync_ecmwf ------------------------------------------------------------------------------

def test_run_ts_and_filter_order():
    assert sync_ecmwf.run_ts("20260426_00z") == "20260426T000000Z"
    assert sync_ecmwf.run_ts("20260425_18z") == "20260425T180000Z"
    with pytest.raises(ValueError):
        sync_ecmwf.run_ts("2026-04-26")
    f = sync_ecmwf.rsync_filters("20260426_00z")
    # first match wins in rsync: the .idx exclude must precede the brg_* include
    assert f[0] == "--exclude=*.idx" and f[-1] == "--exclude=*"
    assert "--include=brg_*_fc_20260426T000000Z_*" in f


def test_sentinel_file_count():
    assert sync_ecmwf.sentinel_files("complete\nfiles=226/226\n") == (226, 226)
    assert sync_ecmwf.sentinel_files("complete\n") is None


def test_run_files_anchor_on_init_time_not_valid_time(tmp_path):
    for n in ("brg_a_fc_20261010T120000Z_20261010T120000Z_0h",   # this run
              "brg_a_fc_20261010T060000Z_20261010T120000Z_6h",   # older run, same VALID time
              "brg_a_fc_20261010T120000Z_20261010T130000Z_1h.idx"):
        (tmp_path / n).touch()
    got = [p.name for p in sync_ecmwf.run_files(tmp_path, "20261010_12z")]
    assert got == ["brg_a_fc_20261010T120000Z_20261010T120000Z_0h"]


def test_stale_runs_never_lists_the_kept_run(tmp_path):
    old = time.time() - 30 * 3600
    for tag in ("20261008_00z", "20261010_12z"):
        s = tmp_path / f".ready_{tag}"
        s.touch()
        os.utime(s, (old, old))
    tags = [t for t, *_ in sync_ecmwf.stale_runs(tmp_path, keep="20261010_12z")]
    assert tags == ["20261008_00z"]
