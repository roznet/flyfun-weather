"""Display files and the push (#656): built on the home node, rsynced out.

Synthetic frames as in the runner tests: the display file is checked through
the real loop (``analyse_tick``), not a hand-built catalogue.
"""

from __future__ import annotations

import gzip
import json
import shutil
import stat
import subprocess
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from weatherbrief.observed.cells import DEFAULT_POLICY
from weatherbrief.observed.cells import runner as runner_mod
from weatherbrief.observed.cells.catalogue import catalogue_path, display_path, read_catalogue
from weatherbrief.observed.cells.display import (
    DISPLAY_SCHEMA,
    RAIN_MIN_AREA_KM2,
    arrow_end,
    display_cell,
    outline_step,
)
from weatherbrief.observed.cells.push import push_pending
from weatherbrief.observed.cells.runner import FrameCache, Workspace, analyse_tick, dbzh_slots, replay
from weatherbrief.observed.frames import SOURCE_OPERA_DBZH, FrameStore, frame_stamp

from .cells_helpers import grid_spec, scene, write_dbzh

T0 = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
SIZE = 200
FRAMES = 6
STEP = (1.0, 2.0)
SOURCES = (SOURCE_OPERA_DBZH,)


def _times():
    return [T0 + timedelta(minutes=5 * i) for i in range(FRAMES)]


@pytest.fixture(scope="module")
def processed(tmp_path_factory) -> Path:
    root = tmp_path_factory.mktemp("cells-display")
    store = FrameStore(root, retain_all=True)
    blobs = [(80, 70, 50, 7), (130, 120, 44, 10)]
    for i, t in enumerate(_times()):
        write_dbzh(store, t, scene(SIZE, blobs, shift=(STEP[0] * i, STEP[1] * i)))
    ws = Workspace(root)
    now = _times()[-1] + timedelta(minutes=1)
    assert analyse_tick(ws, now, timedelta(hours=2), DEFAULT_POLICY, FrameCache(ws.frames), SOURCES) == FRAMES
    return root


def _display(root, t) -> dict:
    return json.loads(gzip.decompress(display_path(root, t).read_bytes()))


def test_every_catalogue_gets_a_display_file_beside_it(processed):
    for t in _times():
        assert catalogue_path(processed, t).exists()
        path = display_path(processed, t)
        assert path == processed / "cells" / "display" / f"{frame_stamp(t)}.json.gz"
        # World-readable: it travels to another machine and another user.
        assert stat.S_IMODE(path.stat().st_mode) == 0o644


def test_display_carries_schema_versions_and_the_frame_times(processed):
    t = _times()[-1]
    doc = _display(processed, t)
    cat = read_catalogue(catalogue_path(processed, t))
    assert doc["schema"] == DISPLAY_SCHEMA
    assert doc["policy_version"] == cat["policy_version"] == DEFAULT_POLICY.policy_version
    assert doc["code_revision"] == cat["code_revision"]
    assert doc["valid_time"] == t.isoformat()
    assert doc["times"]["radar"] == t.isoformat()
    assert set(doc["times"]) == {"radar", "rate", "lightning", "cloud_top"}
    assert doc["unavailable"] == cat["unavailable"]


def test_display_has_no_footprints_or_history(processed):
    doc = _display(processed, _times()[-1])
    raw = json.dumps(doc)
    for internal in ("footprint", "history", "runs", "drow_per_min", "ellipse", "grid"):
        assert internal not in raw


def test_display_cells_are_the_cores_with_the_issue_fields(processed):
    t = _times()[-1]
    doc = _display(processed, t)
    cat = read_catalogue(catalogue_path(processed, t))
    cores = {c["id"] for c in cat["cells"] if c["tier"] != "rain20"}
    assert cores and cores <= {c["id"] for c in doc["cells"]}
    cell = doc["cells"][0]
    # `within` (#688) only on a cell inside a lower-tier cell.
    assert set(cell) - {"within"} == {"id", "tier", "lat", "lon", "area_km2", "peak_dbz", "rate_peak_mm_h",
                                      "flashes", "top_fl", "truncated", "age_min", "event", "trend", "motion",
                                      "arrow"}
    assert set(cell["trend"]) == {"state", "window_min", "d_peak_db", "area_ratio", "d_flashes"}
    assert set(cell["motion"]) == {"status", "reason", "speed_kt", "toward_deg"}


def test_cores_name_the_cell_they_sit_in(processed):
    """#688: a core41 is `within` its core35, a core35 within its rain20, read
    off the lower tier's labels (the droplet groups a storm from this)."""
    t = _times()[-1]
    doc = _display(processed, t)
    cat = read_catalogue(catalogue_path(processed, t))
    tier_of = {c["id"]: c["tier"] for c in cat["cells"]}
    by_tier = {}
    for c in doc["cells"]:
        by_tier.setdefault(c["tier"], []).append(c)
    assert by_tier["core41"] and all(tier_of[c["within"]] == "core35" for c in by_tier["core41"])
    assert all(tier_of[c["within"]] == "rain20" for c in by_tier["core35"])
    assert all("within" not in c for c in by_tier.get("rain20", []))


def test_small_rain_areas_get_no_marker(processed):
    for t in _times():
        cat = read_catalogue(catalogue_path(processed, t))
        shown = {c["id"] for c in _display(processed, t)["cells"]}
        for c in cat["cells"]:
            if c["tier"] == "rain20":
                assert (c["id"] in shown) == (c["area_km2"] >= RAIN_MIN_AREA_KM2)


def test_arrow_only_when_motion_is_available(processed):
    seen_available = seen_other = False
    for t in _times():
        for c in _display(processed, t)["cells"]:
            if c["motion"]["status"] == "available":
                seen_available = True
                assert c["arrow"] is not None
            else:
                seen_other = True
                assert c["arrow"] is None
    assert seen_available and seen_other  # frame 0 has no pair → no motion


def test_arrow_points_downstream_by_thirty_minutes(processed):
    # The fixture moves 1 px south and 2 px east per 5 min on a 2 km grid:
    # 30 min ≈ 12 km south, 24 km east.
    doc = _display(processed, _times()[-1])
    moving = [c for c in doc["cells"] if c["arrow"]]
    assert moving
    for c in moving:
        assert c["arrow"][0] < c["lat"]
        assert c["arrow"][1] > c["lon"]


def test_arrow_end_refuses_withheld_and_unsupported_motion():
    grid = grid_spec(50)
    base = {"col": 10.0, "row": 10.0}
    for status in ("withheld", "unsupported", "no_pair"):
        cell = dict(base, motion={"status": status, "dcol_per_min": 0.5, "drow_per_min": 0.5})
        assert arrow_end(cell, grid) is None
    ok = dict(base, motion={"status": "available", "dcol_per_min": 0.5, "drow_per_min": 0.0})
    assert arrow_end(ok, grid) is not None


def test_outlines_are_latlon_polylines_near_the_cells(processed):
    doc = _display(processed, _times()[-1])
    assert doc["outlines"]["core35"]
    lats = [p[0] for line in doc["outlines"]["core35"] for p in line]
    lons = [p[1] for line in doc["outlines"]["core35"] for p in line]
    cells = [c for c in doc["cells"] if c["tier"] == "core35"]
    assert min(lats) <= min(c["lat"] for c in cells) <= max(lats)
    assert min(lons) <= min(c["lon"] for c in cells) <= max(lons)


def test_rain20_outlines_are_traced_coarser():
    assert outline_step("rain20", big=False) > outline_step("core35", big=False)
    assert outline_step("rain20", big=True) > outline_step("core41", big=True) > 1


def test_run_row_records_the_display_size(processed):
    rows = [json.loads(line) for p in (processed / "cells" / "runs").glob("*.jsonl")
            for line in p.read_text().splitlines()]
    frames = [r for r in rows if r["type"] == "frame"]
    assert frames and all(r["display_bytes"] > 0 and r["display_error"] is None for r in frames)


def test_replay_reproduces_display_files_byte_for_byte(processed, tmp_path):
    out = tmp_path / "replay"
    replay(processed, out, _times()[0], _times()[-1], DEFAULT_POLICY, sources=SOURCES)
    for t in _times():
        assert display_path(out, t).read_bytes() == display_path(processed, t).read_bytes()


def test_a_display_failure_does_not_fail_the_frame(tmp_path, monkeypatch):
    store = FrameStore(tmp_path, retain_all=True)
    write_dbzh(store, T0, scene(SIZE, [(80, 70, 50, 7)]))

    def boom(*_a, **_k):
        raise RuntimeError("contour blew up")

    monkeypatch.setattr(runner_mod, "build_display", boom)
    ws = Workspace(tmp_path)
    assert analyse_tick(ws, T0 + timedelta(minutes=1), timedelta(hours=1), DEFAULT_POLICY,
                        FrameCache(ws.frames), SOURCES) == 1
    assert catalogue_path(tmp_path, T0).exists()
    assert not display_path(tmp_path, T0).exists()
    assert not (tmp_path / "cells" / "catalogues" / "20261003" / "20261003T1200.failed.json").exists()


def test_display_cell_matches_the_catalogue_cell():
    cat_cell = {
        "id": "core41-x", "tier": "core41", "lat": 50.0, "lon": 2.0, "row": 5.0, "col": 5.0,
        "area_km2": 12.0, "peak_dbz": 48.0, "rate_peak_mm_h": None, "flashes": None, "top_fl": None,
        "truncated": True, "lineage": {"age_min": 0.0, "event": "born"},
        "trend": {"state": "new", "window_min": None},
        "motion": {"status": "withheld", "reason": "split on this frame", "speed_kt": None,
                   "toward_deg": None, "drow_per_min": None, "dcol_per_min": None, "support": 0.9},
    }
    out = display_cell(cat_cell, grid_spec(20))
    assert out["truncated"] is True and out["arrow"] is None
    assert out["trend"] == {"state": "new", "window_min": None, "d_peak_db": None,
                            "area_ratio": None, "d_flashes": None}
    assert out["motion"]["reason"] == "split on this frame"


# --- Push ----------------------------------------------------------------------


class _Run:
    def __init__(self, fail: Exception | None = None):
        self.calls: list[list[str]] = []
        self.fail = fail

    def __call__(self, cmd, **kwargs):
        self.calls.append(cmd)
        assert kwargs.get("timeout")  # a hung ssh must not wedge the loop
        if self.fail:
            raise self.fail
        return subprocess.CompletedProcess(cmd, 0, "", "")


def _slots():
    return dbzh_slots(_times()[0], _times()[-1])


def test_push_is_off_without_a_target(processed, monkeypatch):
    monkeypatch.delenv("WB_CELLS_PUSH_TARGET", raising=False)
    run = _Run()
    state: dict = {}
    assert push_pending(processed, state, _slots(), run=run) == 0
    assert run.calls == []


def test_push_sends_pending_files_once(processed):
    run = _Run()
    state: dict = {}
    assert push_pending(processed, state, _slots(), target="node@droplet:/inbox", run=run) == FRAMES
    cmd = run.calls[0]
    assert cmd[0] == "rsync" and cmd[-1] == "node@droplet:/inbox/"
    assert [Path(p).name for p in cmd[1:-1] if not p.startswith("-")] == \
        [f"{frame_stamp(t)}.json.gz" for t in _times()]
    assert state["pushed"] == sorted(frame_stamp(t) for t in _times())
    assert "push_error" not in state
    # Nothing new: no rsync at all.
    assert push_pending(processed, state, _slots(), target="node@droplet:/inbox", run=run) == 0
    assert len(run.calls) == 1


def test_a_failed_push_stays_pending_and_never_raises(processed):
    state: dict = {}
    failing = _Run(subprocess.CalledProcessError(12, ["rsync"], stderr="connection refused\n"))
    assert push_pending(processed, state, _slots(), target="/nowhere/", run=failing) == 0
    assert state["pushed"] == [] and "connection refused" in state["push_error"]
    for exc in (subprocess.TimeoutExpired(["rsync"], 1), FileNotFoundError("rsync")):
        assert push_pending(processed, state, _slots(), target="/nowhere/", run=_Run(exc)) == 0
    ok = _Run()
    assert push_pending(processed, state, _slots(), target="/nowhere/", run=ok) == FRAMES
    assert "push_error" not in state


def test_pushed_set_only_remembers_the_lookback(processed):
    state = {"pushed": ["20200101T0000"]}
    push_pending(processed, state, _slots(), target="/x/", run=_Run())
    assert "20200101T0000" not in state["pushed"]


def test_an_amended_frame_is_pushed_again_under_its_revision(processed, tmp_path):
    """#666: only the newest revision is a candidate, tracked by key."""
    root = tmp_path / "root"
    shutil.copytree(processed / "cells", root / "cells")
    state = {"pushed": [frame_stamp(t) for t in _times()]}  # a pre-#666 state file
    t = _times()[-2]
    display_path(root, t, 1).write_bytes(display_path(root, t).read_bytes())
    run = _Run()
    assert push_pending(root, state, _slots(), target="/x/", run=run) == 1
    assert [Path(p).name for p in run.calls[0][1:-1] if not p.startswith("-")] == \
        [f"{frame_stamp(t)}.r1.json.gz"]
    assert f"{frame_stamp(t)}.r1" in state["pushed"] and frame_stamp(t) in state["pushed"]


def test_a_single_frame_push_leaves_the_rest_of_the_sent_set(processed):
    state = {"pushed": ["20200101T0000"]}
    t = _times()[-1]
    assert push_pending(processed, state, [t], target="/x/", run=_Run(), prune=False) == 1
    assert state["pushed"] == sorted(["20200101T0000", frame_stamp(t)])


@pytest.mark.skipif(shutil.which("rsync") is None, reason="rsync not installed")
def test_push_with_real_rsync_into_a_local_inbox(processed, tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    state: dict = {}
    assert push_pending(processed, state, _slots(), target=str(inbox)) == FRAMES
    for t in _times():
        assert (inbox / f"{frame_stamp(t)}.json.gz").read_bytes() == display_path(processed, t).read_bytes()


def test_run_tick_pushes_after_analysis(tmp_path, monkeypatch):
    store = FrameStore(tmp_path, retain_all=True)
    write_dbzh(store, T0, scene(SIZE, [(80, 70, 50, 7)]))
    calls = []
    monkeypatch.setattr(runner_mod, "collect_tick", lambda *a, **k: [])
    monkeypatch.setattr(runner_mod, "push_pending",
                        lambda root, state, slots, **kw: calls.append(sorted(slots)) or 0)
    ws = Workspace(tmp_path)
    runner_mod.run_tick(ws, DEFAULT_POLICY, FrameCache(ws.frames), SOURCES, timedelta(hours=1),
                        now=T0 + timedelta(minutes=1))
    assert display_path(tmp_path, T0).exists()
    assert calls and T0 in calls[-1]


def test_review_map_uses_the_display_cell_shape(processed, tmp_path):
    """The prototype (webmap) reads the same reduced cells as the display file."""
    from weatherbrief.observed.cells.webmap import render_map

    out = render_map(Workspace(processed), _times()[-1], tmp_path / "m.html")
    html = out.read_text()
    data = json.loads(html.split("const D=", 1)[1].split(";\nconst map", 1)[0])
    shown = {c["id"] for c in _display(processed, _times()[-1])["cells"]}
    assert {c["id"] for c in data["cells"]} <= shown
    assert all("peak_dbz" in c and "age_min" in c for c in data["cells"])
    assert "c.peak_dbz" in html and "c.peak}" not in html


def test_an_outage_longer_than_the_lookback_is_logged_once(processed, caplog):
    state = {"last_push": (T0 - timedelta(hours=7)).isoformat()}
    with caplog.at_level("WARNING"):
        push_pending(processed, state, _slots(), target="/x/", run=_Run())
        state["last_push"] = (T0 - timedelta(hours=7)).isoformat()  # still the same outage
        push_pending(processed, state, _slots(), target="/x/", run=_Run())
    assert sum("longer than the lookback" in r.message for r in caplog.records) == 1
