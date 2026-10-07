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
    store = FrameStore(root, retain_all=True)
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
    rows = [json.loads(line) for line in (processed / "cells" / "scores" / "20261003.jsonl").read_text().splitlines()]
    leads = {r["lead_min"] for r in rows}
    assert leads == {30, 60}
    core = [r for r in rows if r["tier"] == "core35" and r["lead_min"] == 30]
    assert core
    for r in core:
        # A steadily moving cell: extrapolation beats persistence.
        assert r["extrapolation"]["csi"] > r["persistence"]["csi"]
        assert r["centroid_err_km_median"] < r["persistence_centroid_err_km_median"]


def test_run_log_records_timing_and_memory(processed):
    rows = [json.loads(line) for line in (processed / "cells" / "runs" / "20261003.jsonl").read_text().splitlines()]
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
    (row,) = [json.loads(x) for x in (tmp_path / "cells" / "runs" / f"{now:%Y%m%d}.jsonl").read_text().splitlines()]
    assert row["type"] == "gap" and row["from"] == T0.isoformat()
    record_downtime(ws, {"last_tick": (now - timedelta(hours=1)).isoformat()}, now, timedelta(hours=6))
    assert len((tmp_path / "cells" / "runs" / f"{now:%Y%m%d}.jsonl").read_text().splitlines()) == 1


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
    assert DEFAULT_POLICY.policy_version.startswith("cells-3+")


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


def test_a_new_frame_never_waits_for_its_lightning(tmp_path):
    """#666: radar frames are analysed at once; missing lightning is stated."""
    from weatherbrief.observed.frames import SOURCE_EUMETSAT_LI

    ws = Workspace(tmp_path)
    blobs = [(60, 60, 50, 7)]
    for i in range(3):
        write_dbzh(ws.frames, T0 + timedelta(minutes=5 * i), scene(120, blobs, shift=(0, i)))
    sources = (SOURCE_OPERA_DBZH, SOURCE_EUMETSAT_LI)
    now = T0 + timedelta(minutes=12)
    assert analyse_tick(ws, now, timedelta(hours=1), DEFAULT_POLICY, FrameCache(ws.frames), sources) == 3
    cat = _cat(tmp_path, T0)
    assert {"what": "lightning", "reason": "no lightning frame for this slot"} in cat["unavailable"]
    assert all(c["flashes"] is None for c in cat["cells"])


# --- #666: rain rate from the newest frame on disk, lightning amended later ----


def _write_rate(store, t):
    """A RATE frame on disk (contents unused: the read is patched)."""
    from weatherbrief.observed.frames import SOURCE_OPERA_RATE

    store.write(SOURCE_OPERA_RATE, t, b"rate", {})


def test_rate_takes_the_newest_frame_on_disk_within_the_bound(tmp_path, monkeypatch):
    from weatherbrief.observed import opera
    from weatherbrief.observed.cells import runner
    from weatherbrief.observed.frames import SOURCE_OPERA_RATE

    ws = Workspace(tmp_path)
    t = T0 + timedelta(minutes=5)  # slot T0, which has not landed
    write_dbzh(ws.frames, t, scene(120, [(60, 60, 50, 7)]))
    like = runner.read_dbzh(ws.frames, t)
    read = []
    monkeypatch.setattr(opera, "read_grid", lambda path: like.grid)
    monkeypatch.setattr(opera, "read_window", lambda path, *a, **k: read.append(path.name) or like)

    _write_rate(ws.frames, T0 - timedelta(minutes=30))  # 35 min old: past the bound
    frame, why = runner._rate_frame(ws.frames, t, like)
    assert frame is None and "30 min" in why

    _write_rate(ws.frames, T0 - timedelta(minutes=15))  # 20 min old: used
    frame, why = runner._rate_frame(ws.frames, t, like)
    assert why is None and read[-1] == ws.frames.payload_path(
        SOURCE_OPERA_RATE, T0 - timedelta(minutes=15)).name

    _write_rate(ws.frames, T0)  # the frame's own slot wins when present
    runner._rate_frame(ws.frames, t, like)
    assert read[-1] == ws.frames.payload_path(SOURCE_OPERA_RATE, T0).name


def test_catalogue_and_display_say_how_old_the_rain_rate_is(tmp_path, monkeypatch):
    from weatherbrief.observed.cells import runner
    from weatherbrief.observed.cells.catalogue import display_path
    from weatherbrief.observed.frames import SOURCE_OPERA_RATE

    ws = Workspace(tmp_path)
    t = T0 + timedelta(minutes=5)
    write_dbzh(ws.frames, t, scene(120, [(60, 60, 50, 7)]))

    def older_rate(store, when, like):
        return runner.GridFrame(**{**like.__dict__, "source": SOURCE_OPERA_RATE,
                                   "valid_time": T0 - timedelta(minutes=15)}), None

    monkeypatch.setattr(runner, "_rate_frame", older_rate)
    sources = (SOURCE_OPERA_DBZH, SOURCE_OPERA_RATE)
    analyse_tick(ws, t + timedelta(minutes=1), timedelta(hours=1), DEFAULT_POLICY,
                 FrameCache(ws.frames), sources)
    assert _cat(tmp_path, t)["inputs"]["rate_age_min"] == 20.0
    display = json.loads(gzip.decompress(display_path(tmp_path, t).read_bytes()))
    assert display["rate_age_min"] == 20.0
    assert display["times"]["rate"] == (T0 - timedelta(minutes=15)).isoformat()
    assert display["revision"] == 0


def _li_on_disk(store, t):
    from weatherbrief.observed.frames import SOURCE_EUMETSAT_LI

    store.write(SOURCE_EUMETSAT_LI, t, b"li", {})


def _flashes_at(monkeypatch, points_by_slot):
    """Patch the LI reader: ``points_by_slot[slot] = [(lat, lon), ...]``."""
    from weatherbrief.observed.cells import runner
    from weatherbrief.observed.frames import FlashFrame, SOURCE_EUMETSAT_LI

    def read(path, **kwargs):
        slot = datetime.strptime(path.name.split(".")[0], "%Y%m%dT%H%M").replace(tzinfo=timezone.utc)
        pts = points_by_slot.get(slot, [])
        lats = np.array([p[0] for p in pts], dtype=np.float64)
        lons = np.array([p[1] for p in pts], dtype=np.float64)
        return FlashFrame(SOURCE_EUMETSAT_LI, slot, 10.0, lats, lons,
                          np.array([np.datetime64(slot.replace(tzinfo=None), "s")] * len(pts)))

    monkeypatch.setattr(runner.li_reader, "read_flashes", read)


def _display_doc(root, t, revision=0):
    from weatherbrief.observed.cells.catalogue import display_path

    return json.loads(gzip.decompress(display_path(root, t, revision).read_bytes()))


def test_lightning_lands_later_and_the_frame_is_amended_as_a_new_revision(tmp_path, monkeypatch):
    from weatherbrief.observed.cells.runner import amend_lightning
    from weatherbrief.observed.cells.catalogue import display_path, latest_display
    from weatherbrief.observed.frames import SOURCE_EUMETSAT_LI

    ws = Workspace(tmp_path)
    sources = (SOURCE_OPERA_DBZH, SOURCE_EUMETSAT_LI)
    write_dbzh(ws.frames, T0, scene(120, [(60, 60, 50, 7)]))
    cache = FrameCache(ws.frames)
    analyse_tick(ws, T0 + timedelta(minutes=4), timedelta(hours=1), DEFAULT_POLICY, cache, sources)
    first = display_path(tmp_path, T0).read_bytes()
    cell = next(c for c in _cat(tmp_path, T0)["cells"] if c["tier"] == "core41")
    assert cell["flashes"] is None

    # Nothing to amend until the LI slot is on disk.
    assert amend_lightning(ws, T0 + timedelta(minutes=8), DEFAULT_POLICY, cache, sources) == 0
    _flashes_at(monkeypatch, {T0: [(cell["lat"], cell["lon"])] * 3})
    _li_on_disk(ws.frames, T0)
    pushed = []
    assert amend_lightning(ws, T0 + timedelta(minutes=11), DEFAULT_POLICY, cache, sources,
                           on_display=lambda t: pushed.append(t) or "x") == 1
    assert pushed == [T0]

    cat = _cat(tmp_path, T0)
    amended = next(c for c in cat["cells"] if c["id"] == cell["id"])
    assert amended["flashes"] == 3 and amended["history"][-1][3] == 3
    assert cat["inputs"][SOURCE_EUMETSAT_LI] == T0.isoformat()
    assert "lightning" not in {u["what"] for u in cat["unavailable"]}
    # Revision 0 is untouched (served as immutable); r1 carries the lightning.
    assert display_path(tmp_path, T0).read_bytes() == first
    assert latest_display(tmp_path, T0) == (display_path(tmp_path, T0, 1), 1)
    r1 = _display_doc(tmp_path, T0, 1)
    assert r1["revision"] == 1 and r1["times"]["lightning"] == T0.isoformat()
    assert next(c for c in r1["cells"] if c["id"] == cell["id"])["flashes"] == 3
    rows = [json.loads(x) for x in (tmp_path / "cells" / "runs" / "20261003.jsonl").read_text().splitlines()]
    assert rows[-1]["type"] == "amend" and rows[-1]["revision"] == 1 and rows[-1]["pushed_at"] == "x"
    # Done once: the next tick finds nothing left to amend.
    assert amend_lightning(ws, T0 + timedelta(minutes=12), DEFAULT_POLICY, cache, sources) == 0


def test_lightning_waiting_to_land_is_pending_never_none(tmp_path):
    """#666 review: r0 must not read as "no lightning" while LI is on its way."""
    from weatherbrief.observed.frames import SOURCE_EUMETSAT_LI

    ws = Workspace(tmp_path)
    write_dbzh(ws.frames, T0, scene(120, [(60, 60, 50, 7)]))
    analyse_tick(ws, T0 + timedelta(minutes=4), timedelta(hours=1), DEFAULT_POLICY, FrameCache(ws.frames),
                 (SOURCE_OPERA_DBZH, SOURCE_EUMETSAT_LI))
    r0 = _display_doc(tmp_path, T0)
    assert r0["pending"] == ["lightning"]
    assert "lightning" not in {u["what"] for u in r0["unavailable"]}
    assert r0["cells"] and all(c["flashes"] is None and c["flashes_pending"] for c in r0["cells"])
    # The catalogue keeps the plain fact (and the amend keys off it).
    assert {"what": "lightning", "reason": "no lightning frame for this slot"} in _cat(tmp_path, T0)["unavailable"]


def test_a_frame_with_its_lightning_has_no_pending_fields(tmp_path, monkeypatch):
    from weatherbrief.observed.frames import SOURCE_EUMETSAT_LI

    ws = Workspace(tmp_path)
    write_dbzh(ws.frames, T0, scene(120, [(60, 60, 50, 7)]))
    _flashes_at(monkeypatch, {})
    _li_on_disk(ws.frames, T0)
    analyse_tick(ws, T0 + timedelta(minutes=12), timedelta(hours=1), DEFAULT_POLICY, FrameCache(ws.frames),
                 (SOURCE_OPERA_DBZH, SOURCE_EUMETSAT_LI))
    r0 = _display_doc(tmp_path, T0)
    assert r0["pending"] == [] and all("flashes_pending" not in c for c in r0["cells"])
    assert all(c["flashes"] == 0 for c in r0["cells"])


def test_amend_refreshes_the_rain_rate_to_the_frames_own_slot(tmp_path, monkeypatch):
    from weatherbrief.observed import opera
    from weatherbrief.observed.cells import runner
    from weatherbrief.observed.frames import SOURCE_EUMETSAT_LI, SOURCE_OPERA_RATE

    ws = Workspace(tmp_path)
    t = T0 + timedelta(minutes=5)
    write_dbzh(ws.frames, t, scene(120, [(60, 60, 50, 7)]))
    like = runner.read_dbzh(ws.frames, t)
    level = {T0 - timedelta(minutes=15): 2.0, T0: 9.0}

    def read_window(path, quantity, *a, **k):
        if quantity != "RATE":
            return real_read_window(path, quantity, *a, **k)
        when = datetime.strptime(path.name.split(".")[0], "%Y%m%dT%H%M").replace(tzinfo=timezone.utc)
        return runner.GridFrame(**{**like.__dict__, "source": SOURCE_OPERA_RATE, "valid_time": when,
                                   "values": np.full(like.values.shape, level[when], dtype=np.float32)})

    real_read_window = opera.read_window
    real_read_grid = opera.read_grid
    monkeypatch.setattr(opera, "read_grid",
                        lambda path: like.grid if "rate" in str(path) else real_read_grid(path))
    monkeypatch.setattr(opera, "read_window", read_window)
    sources = (SOURCE_OPERA_DBZH, SOURCE_OPERA_RATE, SOURCE_EUMETSAT_LI)
    _write_rate(ws.frames, T0 - timedelta(minutes=15))
    cache = FrameCache(ws.frames)
    analyse_tick(ws, t + timedelta(minutes=4), timedelta(hours=1), DEFAULT_POLICY, cache, sources)
    r0 = _display_doc(tmp_path, t)
    assert r0["rate_age_min"] == 20.0
    assert all(c["rate_peak_mm_h"] == 2.0 and c["rate_as_of"] == (T0 - timedelta(minutes=15)).isoformat()
               for c in r0["cells"])

    _write_rate(ws.frames, T0)  # lands at ~+10.1, just before the LI
    _flashes_at(monkeypatch, {})
    _li_on_disk(ws.frames, T0)
    assert runner.amend_lightning(ws, t + timedelta(minutes=6), DEFAULT_POLICY, cache, sources) == 1
    cat = _cat(tmp_path, t)
    assert cat["inputs"][SOURCE_OPERA_RATE] == T0.isoformat() and cat["inputs"]["rate_age_min"] == 5.0
    r1 = _display_doc(tmp_path, t, 1)
    assert all(c["rate_peak_mm_h"] == 9.0 and c["rate_as_of"] == T0.isoformat() for c in r1["cells"])


def test_an_amended_catalogue_equals_its_replay(tmp_path, monkeypatch):
    """With the same inputs on disk, publish-then-amend ends where a replay
    (everything present at analysis) does — byte for byte."""
    from weatherbrief.observed.cells.runner import amend_lightning, replay
    from weatherbrief.observed.frames import SOURCE_EUMETSAT_LI

    ws = Workspace(tmp_path / "live")
    sources = (SOURCE_OPERA_DBZH, SOURCE_EUMETSAT_LI)
    cache = FrameCache(ws.frames)
    times = [T0 + timedelta(minutes=5 * i) for i in range(6)]
    lightning = {}
    for i, t in enumerate(times):
        write_dbzh(ws.frames, t, scene(120, [(60, 60, 50, 7)], shift=(0, i)))
        analyse_tick(ws, t + timedelta(minutes=4), timedelta(hours=1), DEFAULT_POLICY, cache, sources)
        if t.minute % 10 == 5:
            slot = t - timedelta(minutes=5)
            cell = next(c for c in _cat(ws.root, t)["cells"] if c["tier"] == "core41")
            lightning[slot] = [(cell["lat"], cell["lon"])] * (1 + i)
            _flashes_at(monkeypatch, lightning)
            _li_on_disk(ws.frames, slot)
            amend_lightning(ws, t + timedelta(minutes=6), DEFAULT_POLICY, cache, sources)
    replay(ws.root, tmp_path / "replay", times[0], times[-1], DEFAULT_POLICY, sources=sources)
    for t in times:
        live = catalogue_path(ws.root, t).read_bytes()
        again = catalogue_path(tmp_path / "replay", t).read_bytes()
        assert live == again, t


def test_a_crash_between_display_and_catalogue_reissues_the_revision(tmp_path, monkeypatch):
    from weatherbrief.observed.cells import runner
    from weatherbrief.observed.cells.catalogue import latest_display
    from weatherbrief.observed.frames import SOURCE_EUMETSAT_LI

    ws = Workspace(tmp_path)
    sources = (SOURCE_OPERA_DBZH, SOURCE_EUMETSAT_LI)
    write_dbzh(ws.frames, T0, scene(120, [(60, 60, 50, 7)]))
    cache = FrameCache(ws.frames)
    analyse_tick(ws, T0 + timedelta(minutes=4), timedelta(hours=1), DEFAULT_POLICY, cache, sources)
    _flashes_at(monkeypatch, {})
    _li_on_disk(ws.frames, T0)
    real = runner.write_catalogue
    monkeypatch.setattr(runner, "write_catalogue", lambda *a: (_ for _ in ()).throw(OSError("disk")))
    assert runner.amend_lightning(ws, T0 + timedelta(minutes=11), DEFAULT_POLICY, cache, sources) == 0
    assert latest_display(tmp_path, T0)[1] == 1  # written first
    monkeypatch.setattr(runner, "write_catalogue", real)
    assert runner.amend_lightning(ws, T0 + timedelta(minutes=12), DEFAULT_POLICY, cache, sources) == 1
    assert latest_display(tmp_path, T0)[1] == 2  # retried, not lost


def test_amend_is_bounded_to_recent_frames(tmp_path, monkeypatch):
    from weatherbrief.observed.cells.runner import LI_AMEND_WINDOW, amend_lightning
    from weatherbrief.observed.frames import SOURCE_EUMETSAT_LI

    ws = Workspace(tmp_path)
    sources = (SOURCE_OPERA_DBZH, SOURCE_EUMETSAT_LI)
    write_dbzh(ws.frames, T0, scene(120, [(60, 60, 50, 7)]))
    cache = FrameCache(ws.frames)
    analyse_tick(ws, T0 + timedelta(minutes=4), timedelta(hours=1), DEFAULT_POLICY, cache, sources)
    _flashes_at(monkeypatch, {})
    _li_on_disk(ws.frames, T0)
    late = T0 + LI_AMEND_WINDOW + timedelta(minutes=5)
    assert amend_lightning(ws, late, DEFAULT_POLICY, cache, sources) == 0


def test_an_unreadable_lightning_frame_is_recorded_and_not_retried(tmp_path, monkeypatch):
    from weatherbrief.observed.cells import runner
    from weatherbrief.observed.cells.catalogue import latest_display
    from weatherbrief.observed.frames import SOURCE_EUMETSAT_LI

    ws = Workspace(tmp_path)
    sources = (SOURCE_OPERA_DBZH, SOURCE_EUMETSAT_LI)
    write_dbzh(ws.frames, T0, scene(120, [(60, 60, 50, 7)]))
    cache = FrameCache(ws.frames)
    analyse_tick(ws, T0 + timedelta(minutes=4), timedelta(hours=1), DEFAULT_POLICY, cache, sources)
    _li_on_disk(ws.frames, T0)
    calls = []

    def broken(path, **kwargs):
        calls.append(path)
        raise OSError("truncated")

    monkeypatch.setattr(runner.li_reader, "read_flashes", broken)
    for minutes in (11, 12):
        assert runner.amend_lightning(ws, T0 + timedelta(minutes=minutes), DEFAULT_POLICY, cache,
                                      sources) == 0
    assert len(calls) == 1
    reasons = {u["what"]: u["reason"] for u in _cat(tmp_path, T0)["unavailable"]}
    assert "unreadable lightning frame" in reasons["lightning"]
    # A revision, so the maps stop saying "pending": now plainly unavailable.
    assert latest_display(tmp_path, T0)[1] == 1
    r1 = _display_doc(tmp_path, T0, 1)
    assert r1["pending"] == [] and "lightning" in {u["what"] for u in r1["unavailable"]}
    assert not any(c.get("flashes_pending") for c in r1["cells"])


def test_successor_history_picks_up_the_amended_flashes(tmp_path, monkeypatch):
    """A frame published before its predecessor's lightning landed still gets
    that predecessor's flashes into its history (and so its flash trend)."""
    from weatherbrief.observed.cells.runner import amend_lightning
    from weatherbrief.observed.frames import SOURCE_EUMETSAT_LI

    ws = Workspace(tmp_path)
    sources = (SOURCE_OPERA_DBZH, SOURCE_EUMETSAT_LI)
    cache = FrameCache(ws.frames)
    times = [T0 + timedelta(minutes=5 * i) for i in range(8)]
    lightning = {}
    for i, t in enumerate(times):
        write_dbzh(ws.frames, t, scene(120, [(60, 60, 50, 7)], shift=(0, i)))
        analyse_tick(ws, t + timedelta(minutes=4), timedelta(hours=1), DEFAULT_POLICY, cache, sources)
        slot = t - timedelta(minutes=t.minute % 10)
        if t.minute % 10 == 5:  # the slot's LI lands after its second radar frame
            cell = next(c for c in _cat(tmp_path, t)["cells"] if c["tier"] == "core41")
            lightning[slot] = [(cell["lat"], cell["lon"])] * (2 + i)
            _flashes_at(monkeypatch, lightning)
            _li_on_disk(ws.frames, slot)
            assert amend_lightning(ws, t + timedelta(minutes=6), DEFAULT_POLICY, cache, sources) == 2
    last = _cat(tmp_path, times[-1])
    cell = next(c for c in last["cells"] if c["tier"] == "core41")
    assert all(entry[3] is not None for entry in cell["history"]), cell["history"]
    assert cell["trend"]["d_flashes"] is not None


def test_a_live_frame_is_pushed_before_scoring_and_the_row_says_when(tmp_path, monkeypatch):
    from weatherbrief.observed.cells import runner

    ws = Workspace(tmp_path)
    write_dbzh(ws.frames, T0, scene(120, [(60, 60, 50, 7)]))
    order = []
    real_score = runner.score_frame
    monkeypatch.setattr(runner, "score_frame", lambda *a, **k: order.append("score") or real_score(*a, **k))
    summary = runner.process_frame(ws, T0, DEFAULT_POLICY, sources=SOURCES,
                                   on_display=lambda t: order.append("push") or "2026-10-03T12:04:30+00:00")
    assert order == ["push", "score"]
    assert summary["pushed_at"] == "2026-10-03T12:04:30+00:00"
    assert summary["analysis_seconds"] <= summary["seconds"]


def test_run_tick_pushes_a_live_frame_at_once_but_batches_a_catch_up(tmp_path, monkeypatch):
    from weatherbrief.observed.cells import runner

    ws = Workspace(tmp_path)
    old, new = T0 - timedelta(hours=1), T0
    write_dbzh(ws.frames, old, scene(120, [(60, 60, 50, 7)]))
    write_dbzh(ws.frames, new, scene(120, [(60, 60, 50, 7)]))
    calls = []
    monkeypatch.setattr(runner, "collect_tick", lambda *a, **k: [])

    def fake_push(root, state, slots, prune=True, **kw):
        calls.append((list(slots), prune))
        return 0

    monkeypatch.setattr(runner, "push_pending", fake_push)
    runner.run_tick(ws, DEFAULT_POLICY, FrameCache(ws.frames), SOURCES, timedelta(hours=2),
                    now=T0 + timedelta(minutes=4))
    immediate = [slots for slots, prune in calls if not prune]
    assert immediate == [[new]]
    assert calls[-1][1] is True and old in calls[-1][0] and new in calls[-1][0]


def test_radar_poll_window():
    from weatherbrief.observed.cells.runner import radar_poll_wait

    class Store:
        def __init__(self, present):
            self.present = present

        def has(self, source, t):
            return t in self.present

    empty = Store(set())
    # 3.5 min after T0: the poll opens at +4.0 → 30 s to wait.
    assert radar_poll_wait(empty, T0 + timedelta(minutes=3, seconds=30)) == 30.0
    # Inside the window and missing: poll now.
    assert radar_poll_wait(empty, T0 + timedelta(minutes=4, seconds=10)) == 0.0
    # Two minutes of misses: back to the tick until the next slot's window.
    assert radar_poll_wait(empty, T0 + timedelta(minutes=6, seconds=30)) == 150.0
    # Already on disk: nothing to poll for until the next slot.
    assert radar_poll_wait(Store({T0}), T0 + timedelta(minutes=4, seconds=20)) == 280.0


def test_wait_for_next_tick_returns_as_soon_as_the_frame_lands(tmp_path, monkeypatch):
    from weatherbrief.observed.cells import runner

    clock = [0.0]
    now = [T0 + timedelta(minutes=4)]
    probes = []

    def sleep(seconds):
        clock[0] += seconds
        now[0] += timedelta(seconds=seconds)

    def probe(store, when):
        probes.append(when)
        return len(probes) == 3  # lands on the third check

    monkeypatch.setattr(runner, "probe_radar", probe)
    store = FrameStore(tmp_path, retain_all=True)
    runner.wait_for_next_tick(store, 60.0, clock=lambda: clock[0], sleep=sleep, utcnow=lambda: now[0])
    assert len(probes) == 3 and clock[0] == 2 * runner.RADAR_POLL_SECONDS


def test_wait_for_next_tick_without_a_due_frame_just_sleeps(tmp_path, monkeypatch):
    from weatherbrief.observed.cells import runner

    clock = [0.0]
    monkeypatch.setattr(runner, "probe_radar", lambda *a: pytest.fail("no probe outside the window"))
    store = FrameStore(tmp_path, retain_all=True)
    runner.wait_for_next_tick(store, 60.0, clock=lambda: clock[0],
                              sleep=lambda s: clock.__setitem__(0, clock[0] + s),
                              utcnow=lambda: T0 + timedelta(minutes=1))
    assert clock[0] == 60.0


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


# --- Review round 1 (#651): failures, lineage breaks, sweep state ------------


def test_a_failing_frame_is_marked_once_and_not_retried(tmp_path, monkeypatch):
    from weatherbrief.observed.cells import runner
    from weatherbrief.observed.cells.catalogue import failure_path

    ws = Workspace(tmp_path)
    write_dbzh(ws.frames, T0, scene(120, [(60, 60, 50, 7)]))
    calls = []

    def boom(*args, **kwargs):
        calls.append(1)
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(runner, "process_frame", boom)
    later = T0 + timedelta(minutes=40)
    for _ in range(3):
        analyse_tick(ws, later, timedelta(hours=1), DEFAULT_POLICY, FrameCache(ws.frames), SOURCES)
    assert len(calls) == 1
    assert failure_path(tmp_path, T0).exists()
    rows = [json.loads(x) for x in (tmp_path / "cells" / "runs" / "20261003.jsonl").read_text().splitlines()]
    assert [r["type"] for r in rows] == ["error"]
    assert "synthetic failure" in rows[0]["error"]
    report = coverage_report(ws, T0, T0, SOURCES)
    (failed,) = report["failed"]
    assert failed["valid_time"] == T0.isoformat() and failed["attempts"] == 1


def test_an_unreadable_dbzh_frame_is_marked_once(tmp_path):
    from weatherbrief.observed.cells.catalogue import failure_path

    ws = Workspace(tmp_path)
    write_dbzh(ws.frames, T0, scene(120, [(60, 60, 50, 7)]))
    ws.frames.payload_path(SOURCE_OPERA_DBZH, T0).write_bytes(b"not an hdf5 file")
    later = T0 + timedelta(minutes=40)
    assert analyse_tick(ws, later, timedelta(hours=1), DEFAULT_POLICY, FrameCache(ws.frames), SOURCES) == 0
    assert "unreadable" in json.loads(failure_path(tmp_path, T0).read_text())["error"]
    analyse_tick(ws, later, timedelta(hours=1), DEFAULT_POLICY, FrameCache(ws.frames), SOURCES)
    assert len((tmp_path / "cells" / "runs" / "20261003.jsonl").read_text().splitlines()) == 1


def test_a_lineage_break_is_stated_not_silent(processed):
    first = _cat(processed, _times()[0])
    reasons = {u["what"] for u in first["unavailable"]}
    assert "lineage" in reasons
    later = _cat(processed, _times()[3])
    assert "lineage" not in {u["what"] for u in later["unavailable"]}


def test_catalogue_carries_the_code_revision(processed):
    from weatherbrief.observed.cells.runner import code_revision

    assert _cat(processed, _times()[2])["code_revision"] == code_revision()


def test_failed_sweep_is_retried_after_the_backoff(tmp_path, monkeypatch):
    from weatherbrief.observed.cells import runner

    ws = Workspace(tmp_path)
    monkeypatch.setattr(runner, "collect_tick",
                        lambda *a, **k: [collect.CollectResult(source="opera_dbzh", failed=1)])
    runner.run_tick(ws, DEFAULT_POLICY, FrameCache(ws.frames), SOURCES, timedelta(hours=6), now=T0)
    state = ws.load_state()
    assert state["last_tick"] == T0.isoformat() and "last_sweep" not in state
    monkeypatch.setattr(runner, "collect_tick", lambda *a, **k: [collect.CollectResult(source="opera_dbzh")])
    runner.run_tick(ws, DEFAULT_POLICY, FrameCache(ws.frames), SOURCES, timedelta(hours=6),
                    now=T0 + runner.SWEEP_RETRY)
    assert ws.load_state()["last_sweep"] == (T0 + runner.SWEEP_RETRY).isoformat()


# --- Review round 2 (#651) -----------------------------------------------------


def test_a_failing_sweep_backs_off_instead_of_retrying_every_tick(tmp_path, monkeypatch):
    from weatherbrief.observed.cells import runner

    ws = Workspace(tmp_path)
    sweeps = []

    def failing(ws_, sources, now, *, sweep, lookback, family):
        if sweep:
            sweeps.append(now)
        return [collect.CollectResult(source=family[0], failed=1, errors=["HTTP 500"])]

    monkeypatch.setattr(runner, "collect_tick", failing)
    for minute in range(12):
        runner.run_tick(ws, DEFAULT_POLICY, FrameCache(ws.frames), SOURCES, timedelta(hours=6),
                        now=T0 + timedelta(minutes=minute))
    # Attempts at 0, 5 and 10 minutes — not 12.
    assert sorted({t for t in sweeps}) == [T0, T0 + timedelta(minutes=5), T0 + timedelta(minutes=10)]


def test_a_scoring_error_does_not_fail_a_written_frame(tmp_path, monkeypatch):
    from weatherbrief.observed.cells import runner
    from weatherbrief.observed.cells.catalogue import failure_path

    ws = Workspace(tmp_path)
    write_dbzh(ws.frames, T0, scene(120, [(60, 60, 50, 7)]))
    monkeypatch.setattr(runner, "score_frame", lambda *a, **k: (_ for _ in ()).throw(ValueError("bad score")))
    analyse_tick(ws, T0 + timedelta(minutes=40), timedelta(hours=1), DEFAULT_POLICY,
                 FrameCache(ws.frames), SOURCES)
    assert catalogue_path(tmp_path, T0).exists()
    assert not failure_path(tmp_path, T0).exists()
    (row,) = [json.loads(x) for x in (tmp_path / "cells" / "runs" / "20261003.jsonl").read_text().splitlines()]
    assert row["type"] == "frame" and "bad score" in row["scoring_error"]


def test_retry_failed_clears_markers(tmp_path):
    from weatherbrief.observed.cells.catalogue import failure_path
    from weatherbrief.observed.cells.runner import mark_failed, retry_failed

    ws = Workspace(tmp_path)
    mark_failed(ws, T0, "boom")
    assert failure_path(tmp_path, T0).exists()
    assert retry_failed(ws) == 1
    assert not failure_path(tmp_path, T0).exists()


def test_archive_store_still_rejects_an_unknown_source(tmp_path):
    with pytest.raises(KeyError):
        FrameStore(tmp_path, retain_all=True).purge("opera_dbhz")


# --- Follow-up to #651 (review round 3) ----------------------------------------


def test_a_failed_frame_is_retried_once_then_final(tmp_path, monkeypatch):
    from weatherbrief.observed.cells import runner

    ws = Workspace(tmp_path)
    write_dbzh(ws.frames, T0, scene(120, [(60, 60, 50, 7)]))
    calls = []
    monkeypatch.setattr(runner, "process_frame",
                        lambda *a, **k: calls.append(1) or (_ for _ in ()).throw(MemoryError()))
    first = T0 + timedelta(minutes=40)
    for minutes in (0, 10, 29, 30, 31, 90, 200):
        analyse_tick(ws, first + timedelta(minutes=minutes), timedelta(hours=6), DEFAULT_POLICY,
                     FrameCache(ws.frames), SOURCES)
    assert len(calls) == 2  # first try, one retry 30 min later, then final
    marker = runner.read_failure(ws, T0)
    assert marker["attempts"] == 2 and marker["final"] is True


def test_a_transient_failure_recovers_on_the_retry(tmp_path, monkeypatch):
    from weatherbrief.observed.cells import runner

    ws = Workspace(tmp_path)
    write_dbzh(ws.frames, T0, scene(120, [(60, 60, 50, 7)]))
    real = runner.process_frame
    state = {"n": 0}

    def flaky(*a, **k):
        state["n"] += 1
        if state["n"] == 1:
            raise OSError("disk blip")
        return real(*a, **k)

    monkeypatch.setattr(runner, "process_frame", flaky)
    first = T0 + timedelta(minutes=40)
    analyse_tick(ws, first, timedelta(hours=6), DEFAULT_POLICY, FrameCache(ws.frames), SOURCES)
    assert not catalogue_path(tmp_path, T0).exists()
    analyse_tick(ws, first + timedelta(minutes=30), timedelta(hours=6), DEFAULT_POLICY,
                 FrameCache(ws.frames), SOURCES)
    assert catalogue_path(tmp_path, T0).exists()


def test_a_corrupt_catalogue_reads_as_missing(tmp_path):
    path = catalogue_path(tmp_path, T0)
    path.parent.mkdir(parents=True)
    path.write_bytes(b"\x1f\x8b truncated")
    assert read_catalogue(path) is None
    path.write_bytes(gzip.compress(b"{not json"))
    assert read_catalogue(path) is None


def test_replay_survives_a_failing_frame(processed, tmp_path, monkeypatch):
    from weatherbrief.observed.cells import runner

    real = runner.process_frame

    def sometimes(ws, t, *a, **k):
        if t == _times()[2]:
            raise ValueError("bad frame")
        return real(ws, t, *a, **k)

    monkeypatch.setattr(runner, "process_frame", sometimes)
    out = tmp_path / "replay"
    assert replay(processed, out, _times()[0], _times()[4], DEFAULT_POLICY, sources=SOURCES) == 4
    rows = [json.loads(x) for x in (out / "cells" / "runs" / "20261003.jsonl").read_text().splitlines()]
    assert [r for r in rows if r["type"] == "error"][0]["error"] == "ValueError('bad frame')"


def test_a_marker_write_error_does_not_escape(tmp_path, monkeypatch):
    from weatherbrief.observed.cells.runner import mark_failed

    ws = Workspace(tmp_path)

    def no_disk(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(Path, "write_text", no_disk)
    mark_failed(ws, T0, "boom")  # logs, does not raise


def test_healthcheck_pinged_only_after_analysis_and_throttled(tmp_path, monkeypatch):
    import requests

    from weatherbrief.observed.cells import runner

    pings = []
    monkeypatch.setattr(requests, "get", lambda url, timeout: pings.append(url))
    monkeypatch.setenv("WB_CELLS_HEALTHCHECK_URL", "https://hc.example/ping")
    monkeypatch.setattr(runner, "collect_tick", lambda *a, **k: [])
    counts = iter([1, 0, 1, 1])
    monkeypatch.setattr(runner, "analyse_tick", lambda *a, **k: next(counts, 0))
    ws = Workspace(tmp_path)
    for minute in (0, 1, 2, 6):  # OPERA pass per tick; EUMETSAT pass skipped (nothing fetched)
        runner.run_tick(ws, DEFAULT_POLICY, FrameCache(ws.frames), SOURCES, timedelta(hours=6),
                        now=T0 + timedelta(minutes=minute))
    # analysed at 0 (ping), 1 (nothing), 2 (throttled), 6 (ping)
    assert pings == ["https://hc.example/ping", "https://hc.example/ping"]


def test_no_healthcheck_url_means_no_ping(tmp_path, monkeypatch):
    import requests

    from weatherbrief.observed.cells import runner

    monkeypatch.delenv("WB_CELLS_HEALTHCHECK_URL", raising=False)
    monkeypatch.setattr(requests, "get", lambda *a, **k: (_ for _ in ()).throw(AssertionError("pinged")))
    runner.ping_healthcheck({}, T0)


def test_cache_serves_a_frame_older_than_everything_cached(tmp_path):
    """Catch-up asks for old frames after new ones: the cache must not evict
    the frame it just read (KeyError on every back-filled frame, 2026-10-03)."""
    ws = Workspace(tmp_path)
    times = [T0 + timedelta(minutes=5 * i) for i in range(6)]
    for i, t in enumerate(times):
        write_dbzh(ws.frames, t, scene(80, [(40, 40, 50, 6)], shift=(0, i)))
    cache = FrameCache(ws.frames, size=2, det_size=1)
    for t in times[3:]:
        assert cache.dbzh(t) is not None
        assert cache.detections(t, DEFAULT_POLICY) is not None
    old = times[0]
    assert cache.dbzh(old).valid_time == old
    assert cache.detections(old, DEFAULT_POLICY) is not None
    assert len(cache._frames) <= 2 and len(cache._dets) <= 1


def test_catch_up_of_older_frames_after_live_ones_succeeds(tmp_path):
    ws = Workspace(tmp_path)
    times = [T0 + timedelta(minutes=5 * i) for i in range(8)]
    for i, t in enumerate(times):
        write_dbzh(ws.frames, t, scene(80, [(40, 40, 50, 6)], shift=(0, i)))
    cache = FrameCache(ws.frames)
    later = times[-1] + timedelta(minutes=1)
    # Live loop saw only the newest three frames first ...
    assert analyse_tick(ws, later, timedelta(minutes=12), DEFAULT_POLICY, cache, SOURCES) == 3
    # ... then a sweep back-filled older ones into the same cache.
    assert analyse_tick(ws, later, timedelta(hours=2), DEFAULT_POLICY, cache, SOURCES) > 0
    for t in times:
        assert catalogue_path(tmp_path, t).exists(), t


# --- Layout (one tree on the mini, the MacBook, the NAS and the droplet) -------


def test_archive_layout_matches_the_droplet_observed_tree(processed):
    """Frames at <root>/<source>/ like DATA_DIR/observed; analysis under cells/."""
    from weatherbrief.observed.cells.catalogue import display_path

    t = _times()[3]
    assert (processed / "opera_dbzh" / "20261003T1215.h5").exists()
    assert catalogue_path(processed, t) == processed / "cells" / "catalogues" / "20261003" / "20261003T1215.json.gz"
    assert display_path(processed, t) == processed / "cells" / "display" / "20261003T1215.json.gz"
    assert not (processed / "frames").exists()


def test_frame_names_are_the_shared_key():
    """The mini and the droplet name frames identically; analysis links to the
    droplet's own frames by this stamp. Changing it breaks that link."""
    from weatherbrief.observed.collect import _snap_to_interval
    from weatherbrief.observed.frames import SOURCE_SPECS, frame_stamp

    assert frame_stamp(datetime(2026, 10, 4, 1, 5, tzinfo=timezone.utc)) == "20261004T0105"
    # EUMETSAT products are keyed by sensing time snapped down to the slot.
    sensed = datetime(2026, 10, 4, 0, 49, 58, tzinfo=timezone.utc)
    assert frame_stamp(_snap_to_interval(sensed, SOURCE_SPECS["eumetsat_li"].interval)) == "20261004T0040"
    assert SOURCE_SPECS["opera_dbzh"].extension == "h5" and SOURCE_SPECS["eumetsat_li"].extension == "nc"
