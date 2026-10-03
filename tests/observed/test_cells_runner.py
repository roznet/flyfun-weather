"""Observed cells (#650): the loop, the catalogue, replay and configuration.

End-to-end over real ODIM files in a temporary archive store: a cell moving
at a known speed for an hour of 5-minute frames.  Pins idempotence, id
continuity, measured speed, self-scoring rows and the byte-for-byte replay the
acceptance criteria ask for.
"""

from __future__ import annotations

import gzip
import json
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pytest

from weatherbrief.observed import collect
from weatherbrief.observed.cells import DEFAULT_POLICY
from weatherbrief.observed.cells.catalogue import catalogue_path, read_catalogue
from weatherbrief.observed.cells.runner import (
    DEFAULT_CELLS_SOURCES,
    FrameCache,
    Workspace,
    analyse_tick,
    cells_root,
    cells_sources,
    coverage_report,
    peak_rss_mb,
    process_frame,
    record_downtime,
    replay,
)
from weatherbrief.observed.frames import SOURCE_OPERA_DBZH, FrameStore

from .cells_helpers import scene, write_dbzh

T0 = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
SIZE = 220
FRAMES = 16  # 75 minutes of 5-minute frames: a 60-minute score needs a
# catalogue issued an hour earlier that already had motion (frame 0 cannot).
# Whole pixels per frame (1 south, 2 east), so the fixture's texture moves with
# its blobs exactly: 4.47 km per 5 min ≈ 29 kt.
STEP = (1.0, 2.0)
SOURCES = (SOURCE_OPERA_DBZH,)


def _times():
    return [T0 + timedelta(minutes=5 * i) for i in range(FRAMES)]


@pytest.fixture(scope="module")
def archive(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("cells")
    store = FrameStore(root / "frames", retain_all=True)
    blobs = [(90, 80, 50, 7), (140, 130, 44, 10)]
    for i, t in enumerate(_times()):
        write_dbzh(store, t, scene(SIZE, blobs, shift=(STEP[0] * i, STEP[1] * i)))
    return root


@pytest.fixture(scope="module")
def processed(archive) -> Path:
    ws = Workspace(archive)
    now = _times()[-1] + timedelta(minutes=1)
    done = analyse_tick(ws, now, timedelta(hours=2), DEFAULT_POLICY, FrameCache(ws.frames), SOURCES)
    assert done == FRAMES
    return archive


def _cat(root, t):
    return read_catalogue(catalogue_path(root, t))


def test_every_frame_gets_one_catalogue_and_a_second_tick_does_nothing(processed):
    ws = Workspace(processed)
    paths = [catalogue_path(processed, t) for t in _times()]
    stamps = [(p.stat().st_mtime_ns, p.read_bytes()) for p in paths]
    now = _times()[-1] + timedelta(minutes=2)
    assert analyse_tick(ws, now, timedelta(hours=2), DEFAULT_POLICY, FrameCache(ws.frames), SOURCES) == 0
    assert [(p.stat().st_mtime_ns, p.read_bytes()) for p in paths] == stamps


def test_ids_persist_and_speed_is_measured(processed):
    last = _cat(processed, _times()[-1])
    cores = [c for c in last["cells"] if c["tier"] == "core35"]
    assert cores
    for cell in cores:
        assert cell["lineage"]["event"] == "continued"
        assert cell["lineage"]["age_min"] == pytest.approx(5.0 * (FRAMES - 1))
        assert cell["motion"]["status"] == "available"
        assert cell["motion"]["speed_kt"] == pytest.approx(29.0, abs=1.5)
        # South-east-by-east: bearing of (+1 row south, +2 cols east) ≈ 117°,
        # give or take the Lambert grid's rotation from true north here.
        assert 100 < cell["motion"]["toward_deg"] < 135
        assert cell["trend"]["state"] == "steady"
        assert len(cell["history"]) >= 7


def test_first_frame_reports_why_motion_is_missing(processed):
    first = _cat(processed, _times()[0])
    assert first["inputs"]["flow_pair"] is None
    assert {"what": "motion", "reason": "no earlier DBZH frame to pair with"} in first["unavailable"]
    assert all(c["motion"]["status"] == "no_pair" for c in first["cells"])


def test_catalogue_carries_schema_policy_and_inputs(processed):
    cat = _cat(processed, _times()[6])
    assert cat["schema"] == "observed-cells/1"
    assert cat["policy_version"] == DEFAULT_POLICY.policy_version
    assert cat["inputs"]["flow_pair"]["minutes"] == 10
    assert cat["inputs"]["lineage_from"] == _times()[5].isoformat()
    assert "processed_at" not in json.dumps(cat)  # no wall clock on the wire


def test_self_scoring_rows_written_with_persistence(processed):
    rows = [json.loads(line) for line in (processed / "scores" / "20261003.jsonl").read_text().splitlines()]
    leads = {r["lead_min"] for r in rows}
    assert leads == {30, 60}
    core = [r for r in rows if r["tier"] == "core35" and r["lead_min"] == 30]
    assert core
    for r in core:
        # A steadily moving cell: extrapolation beats persistence.
        assert r["extrapolation"]["csi"] > r["persistence"]["csi"]
        assert r["centroid_err_km_median"] < r["persistence_centroid_err_km_median"]


def test_run_log_records_timing_and_memory(processed):
    rows = [json.loads(line) for line in (processed / "runs" / "20261003.jsonl").read_text().splitlines()]
    frames = [r for r in rows if r["type"] == "frame"]
    assert len(frames) == FRAMES
    assert all(r["seconds"] > 0 and r["peak_rss_mb"] > 0 and r["catalogue_bytes"] > 0 for r in frames)


def test_replay_is_byte_for_byte(processed, tmp_path):
    out = tmp_path / "replay"
    assert replay(processed, out, _times()[0], _times()[-1], DEFAULT_POLICY, sources=SOURCES) == FRAMES
    for t in _times():
        assert catalogue_path(out, t).read_bytes() == catalogue_path(processed, t).read_bytes()
    # gzip header carries no timestamp, or the bytes would differ run to run.
    assert gzip.decompress(catalogue_path(out, _times()[0]).read_bytes())


def test_replay_refuses_the_live_root_and_existing_output(processed, tmp_path):
    with pytest.raises(ValueError):
        replay(processed, processed, _times()[0], _times()[1], DEFAULT_POLICY, sources=SOURCES)
    out = tmp_path / "again"
    replay(processed, out, _times()[0], _times()[1], DEFAULT_POLICY, sources=SOURCES)
    with pytest.raises(FileExistsError):
        replay(processed, out, _times()[0], _times()[1], DEFAULT_POLICY, sources=SOURCES)
    assert replay(processed, out, _times()[0], _times()[1], DEFAULT_POLICY, force=True, sources=SOURCES) == 2


def test_missing_frame_returns_none(tmp_path):
    assert process_frame(Workspace(tmp_path), T0, DEFAULT_POLICY, sources=SOURCES) is None


def test_coverage_report_lists_gaps(processed):
    report = coverage_report(Workspace(processed), _times()[0] - timedelta(minutes=10),
                             _times()[-1], SOURCES)
    dbzh = report[SOURCE_OPERA_DBZH]
    assert dbzh["stored"] == FRAMES and dbzh["expected"] == FRAMES + 2
    assert dbzh["gaps"] == [[(T0 - timedelta(minutes=10)).isoformat(), (T0 - timedelta(minutes=5)).isoformat()]]
    assert report["catalogues"] == FRAMES


def test_downtime_longer_than_the_lookback_is_logged(tmp_path):
    ws = Workspace(tmp_path)
    now = T0 + timedelta(hours=10)
    record_downtime(ws, {"last_tick": T0.isoformat()}, now, timedelta(hours=6))
    (row,) = [json.loads(x) for x in (tmp_path / "runs" / f"{now:%Y%m%d}.jsonl").read_text().splitlines()]
    assert row["type"] == "gap" and row["from"] == T0.isoformat()
    record_downtime(ws, {"last_tick": (now - timedelta(hours=1)).isoformat()}, now, timedelta(hours=6))
    assert len((tmp_path / "runs" / f"{now:%Y%m%d}.jsonl").read_text().splitlines()) == 1


# --- Configuration -----------------------------------------------------------


def test_root_must_be_explicit(monkeypatch):
    monkeypatch.delenv("WB_CELLS_ROOT", raising=False)
    with pytest.raises(RuntimeError):
        cells_root()
    monkeypatch.setenv("WB_CELLS_ROOT", "~/cells")
    assert cells_root() == Path(os.path.expanduser("~/cells"))


def test_sources_default_excludes_ctth_and_must_include_dbzh(monkeypatch):
    monkeypatch.delenv("WB_CELLS_SOURCES", raising=False)
    assert cells_sources() == DEFAULT_CELLS_SOURCES
    assert "eumetsat_ctth" not in DEFAULT_CELLS_SOURCES
    monkeypatch.setenv("WB_CELLS_SOURCES", "opera_rate")
    with pytest.raises(ValueError):
        cells_sources()
    monkeypatch.setenv("WB_CELLS_SOURCES", "opera_dbzh,eumetsat_ctth")
    assert cells_sources() == ("opera_dbzh", "eumetsat_ctth")


def test_peak_rss_is_megabytes_not_bytes_or_kib():
    assert 10 < peak_rss_mb() < 64_000


def test_policy_version_moves_with_any_number():
    from dataclasses import replace

    assert replace(DEFAULT_POLICY, min_ncc=0.6).policy_version != DEFAULT_POLICY.policy_version
    assert DEFAULT_POLICY.policy_version.startswith("cells-1+")


# --- Library options the loop relies on --------------------------------------


def test_retain_all_store_never_purges(tmp_path):
    store = FrameStore(tmp_path, retain_all=True)
    write_dbzh(store, T0 - timedelta(days=30), np.zeros((40, 40)))
    assert store.purge(SOURCE_OPERA_DBZH, now=T0) == 0
    assert store.has(SOURCE_OPERA_DBZH, T0 - timedelta(days=30))
    assert FrameStore(tmp_path).purge(SOURCE_OPERA_DBZH, now=T0) == 1


def test_collect_once_passes_lookback_and_budget(monkeypatch, tmp_path):
    seen = {}

    def fake(source, store, **kwargs):
        seen[source] = kwargs
        return collect.CollectResult(source=source)

    monkeypatch.setattr(collect, "collect_opera", fake)
    monkeypatch.setattr(collect, "collect_eumetsat", fake)
    store = FrameStore(tmp_path)
    collect.collect_once(store, now=T0, sources=("opera_dbzh", "eumetsat_li"),
                         lookback=timedelta(hours=6), max_fetch=72)
    assert seen["opera_dbzh"]["lookback"] == timedelta(hours=6)
    assert seen["eumetsat_li"]["max_fetch"] == 72
    collect.collect_once(store, now=T0, sources=("opera_dbzh",))
    assert seen["opera_dbzh"]["lookback"] == collect.DEFAULT_LOOKBACK
    assert "max_fetch" not in seen["opera_dbzh"]  # each source keeps its own default


# --- Tick ordering -------------------------------------------------------------


def test_new_frame_waits_for_its_lightning_and_stops_the_queue(tmp_path):
    from weatherbrief.observed.frames import SOURCE_EUMETSAT_LI

    ws = Workspace(tmp_path)
    blobs = [(60, 60, 50, 7)]
    for i in range(3):
        write_dbzh(ws.frames, T0 + timedelta(minutes=5 * i), scene(120, blobs, shift=(0, i)))
    sources = (SOURCE_OPERA_DBZH, SOURCE_EUMETSAT_LI)
    now = T0 + timedelta(minutes=12)
    # T0 is 12 minutes old with no LI frame: it waits, and so does everything after it.
    assert analyse_tick(ws, now, timedelta(hours=1), DEFAULT_POLICY, FrameCache(ws.frames), sources) == 0
    # Past the wait, frames are analysed without lightning and say so.
    later = T0 + timedelta(minutes=40)
    assert analyse_tick(ws, later, timedelta(hours=1), DEFAULT_POLICY, FrameCache(ws.frames), sources) == 3
    cat = _cat(tmp_path, T0)
    assert {"what": "lightning", "reason": "no lightning frame for this slot"} in cat["unavailable"]
    assert all(c["flashes"] is None for c in cat["cells"])


def test_sweep_budget_is_per_family(monkeypatch, tmp_path):
    from weatherbrief.observed.cells import runner

    seen = []
    monkeypatch.setattr(runner, "collect_once",
                        lambda store, **kw: seen.append((kw["sources"], kw.get("max_fetch"))) or [])
    ws = Workspace(tmp_path)
    sources = ("opera_dbzh", "opera_rate", "eumetsat_li")
    runner.collect_tick(ws, sources, T0, sweep=True, lookback=timedelta(hours=6), family=runner._OPERA)
    runner.collect_tick(ws, sources, T0, sweep=True, lookback=timedelta(hours=6), family=runner._EUMETSAT)
    assert seen == [(("opera_dbzh", "opera_rate"), runner.SWEEP_MAX_FETCH_OPERA),
                    (("eumetsat_li",), runner.SWEEP_MAX_FETCH_EUMETSAT)]
