"""Observed cells (#662): velocity over the lineage and advection along the field."""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from weatherbrief.observed.cells import DEFAULT_POLICY
from weatherbrief.observed.cells.__main__ import main as cli_main
from weatherbrief.observed.cells.advect import (
    MotionField,
    displacement,
    flow_to_dict,
    project_centroid,
    project_footprint,
    smooth_lattice,
)
from weatherbrief.observed.cells.motion import FlowField
from weatherbrief.observed.cells.policy import MOTION_VARIANTS, CellPolicy
from weatherbrief.observed.cells.scoring import summarise
from weatherbrief.observed.cells.velocity import history_entry, smoothed, track, window

T = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)


def _entry(minutes_ago, vec=None, centroid=None, is_break=False, legacy=False):
    t = T - timedelta(minutes=minutes_ago)
    if legacy:
        return [t.isoformat(), 45.0, 30.0, None]
    v = vec or (None, None)
    c = centroid or (None, None)
    return history_entry(t, 45.0, 30.0, None, v[0], v[1], c[0], c[1], is_break)


# --- Smoothing over the lineage -------------------------------------------------


def test_smoothed_is_an_exponentially_weighted_mean_inside_the_window():
    history = [_entry(25, (9.0, 9.0)), _entry(10, (0.0, 2.0)), _entry(5, (0.0, 1.0))]
    now = _entry(0, (0.0, 0.0))
    entries = window(history, now, T, DEFAULT_POLICY)
    assert [round(a) for a, _ in entries] == [10, 5, 0]  # 25 min is past the 20-min window
    est = smoothed(entries, (0.0, 0.0), DEFAULT_POLICY)
    w = [np.exp(-10 / 10), np.exp(-5 / 10), 1.0]
    assert est.n == 3
    assert est.dcol_per_min == pytest.approx((2 * w[0] + 1 * w[1]) / sum(w))
    assert est.drow_per_min == pytest.approx(0.0)


def test_unsupported_frames_add_no_vector_and_legacy_entries_still_read():
    history = [_entry(15, legacy=True), _entry(10), _entry(5, (1.0, 1.0))]
    entries = window(history, _entry(0, (3.0, 3.0)), T, DEFAULT_POLICY)
    est = smoothed(entries, (3.0, 3.0), DEFAULT_POLICY)
    assert est.n == 2
    assert 1.0 < est.drow_per_min < 3.0


def test_a_split_or_merge_starts_a_fresh_window():
    history = [_entry(15, (5.0, 5.0), (0.0, 0.0)), _entry(10, (5.0, 5.0), (1.0, 1.0)),
               _entry(5, None, (40.0, 40.0), is_break=True)]
    now = _entry(0, (1.0, 0.0), (41.0, 40.0))
    entries = window(history, now, T, DEFAULT_POLICY)
    assert len(entries) == 2  # the split frame (its centroid) and now
    est = smoothed(entries, (1.0, 0.0), DEFAULT_POLICY)
    assert (est.drow_per_min, est.dcol_per_min, est.n) == (1.0, 0.0, 1)  # below min_vectors: raw
    tr = track(entries, (1.0, 0.0), DEFAULT_POLICY)
    assert tr.n == 1  # two centroids < track_min_centroids: raw, not a fit across the jump


def test_track_is_a_least_squares_line_through_the_centroids():
    # Centroids every 5 min moving 1 row / 2 cols per 5 min, with ±0.5 px noise.
    noise = [0.5, -0.5, 0.5, -0.5]
    history = [_entry(5 * k, (9.0, 9.0), (-(k / 5) * 5 + noise[k - 1], -(2 * k / 5) * 5))
               for k in (4, 3, 2, 1)]
    now = _entry(0, (9.0, 9.0), (0.0, 0.0))
    est = track(window(history, now, T, DEFAULT_POLICY), (9.0, 9.0), DEFAULT_POLICY)
    assert est.n == 5
    assert est.drow_per_min == pytest.approx(0.2, abs=0.05)
    assert est.dcol_per_min == pytest.approx(0.4, abs=1e-9)


def test_policy_rejects_an_unknown_display_motion_and_versions_the_new_knobs():
    with pytest.raises(ValueError):
        CellPolicy(display_motion="curvy")
    assert CellPolicy(smooth_tau_minutes=5.0).policy_version != DEFAULT_POLICY.policy_version
    assert DEFAULT_POLICY.display_motion == "raw"


# --- The motion field -------------------------------------------------------------


def _flow(dr, dc, pair=10.0, tile=64, stride=32):
    dr = np.asarray(dr, dtype=float)
    dc = np.asarray(dc, dtype=float)
    return FlowField(pair, tile, stride, dr, dc, np.ones_like(dr), dr.size, int(np.isfinite(dr).sum()))


def _field(dr, dc, policy=DEFAULT_POLICY, **kw):
    return MotionField.from_dict(json.loads(json.dumps(flow_to_dict(_flow(dr, dc, **kw), 0, 0))), policy)


def test_field_reaches_one_tile_past_matched_tiles_and_no_further():
    dr = np.full((7, 7), np.nan)
    dc = np.full((7, 7), np.nan)
    dr[3, 3], dc[3, 3] = 4.0, -2.0
    vr, vc = smooth_lattice(dr, dc, DEFAULT_POLICY)
    reach = np.isfinite(vr)
    assert reach[2:5, 2:5].all() and reach.sum() == 9
    assert np.allclose(vr[reach], 4.0) and np.allclose(vc[reach], -2.0)  # one tile: its value


def test_field_velocity_is_bilinear_per_minute_and_nan_where_unsupported():
    dr = np.full((3, 6), np.nan)
    dc = np.zeros((3, 6))
    dr[:, 0:3] = [[0.0, 10.0, 20.0]] * 3  # px per 10-min pair: 0, 1, 2 px/min across columns
    policy = dataclasses.replace(DEFAULT_POLICY, field_sigma_tiles=0.0, field_fill_tiles=0)
    field = _field(dr, np.where(np.isfinite(dr), dc, np.nan), policy)
    half = (64 - 1) / 2
    vr, _ = field.velocity([half + 32, half + 32], [half + 16, half + 32 * 5])
    assert vr[0] == pytest.approx(0.5)  # halfway between columns 0 and 1
    assert np.isnan(vr[1])


def test_field_advection_curves_where_the_flow_turns():
    # Westerly flow everywhere, plus a southward component that grows eastward:
    # a straight line from the west keeps its initial heading, a trajectory
    # turns south as it moves east.
    n = 12
    cols = np.arange(n)
    dc = np.full((n, n), 10.0)  # 1 px/min east
    dr = np.tile(cols * 1.0, (n, 1))  # 0.1 px/min more southward per lattice step east
    policy = dataclasses.replace(DEFAULT_POLICY, field_sigma_tiles=0.0)
    field = _field(dr, dc, policy)
    half = (64 - 1) / 2
    cell = {"row": half + 5 * 32, "col": half + 2 * 32,
            "motion": {"status": "available", "drow_per_min": 0.2, "dcol_per_min": 1.0,
                       "smoothed": {"drow_per_min": 0.2, "dcol_per_min": 1.0, "n": 3}}}
    straight = project_centroid(cell, "raw", 60, field, policy)
    curved = project_centroid(cell, "field", 60, field, policy)
    anchored = project_centroid(cell, "field_anchored", 60, field, policy)
    assert straight == pytest.approx((cell["row"] + 12, cell["col"] + 60))
    assert curved[1] == pytest.approx(cell["col"] + 60)
    # Moving 60 px east crosses ~1.9 lattice steps: the southward speed grows
    # from 0.2 to ~0.39 px/min along the path, so it ends further south.
    assert curved[0] > straight[0] + 4
    # The anchored field equals the field at the cell here (offset 0).
    assert anchored == pytest.approx(curved)


def test_anchored_field_moves_at_the_cells_own_velocity_in_a_uniform_field():
    field = _field(np.full((6, 6), 10.0), np.zeros((6, 6)))  # 1 px/min south
    cell = {"row": 100.0, "col": 100.0,
            "motion": {"status": "available", "drow_per_min": 0.0, "dcol_per_min": 0.5,
                       "smoothed": {"drow_per_min": 0.0, "dcol_per_min": 0.6, "n": 2}}}
    assert project_centroid(cell, "field", 30, field, DEFAULT_POLICY) == pytest.approx((130.0, 100.0))
    assert project_centroid(cell, "field_anchored", 30, field, DEFAULT_POLICY) == pytest.approx((100.0, 118.0))


def test_unsupported_field_falls_back_to_the_cells_smoothed_vector():
    field = _field(np.full((4, 4), np.nan), np.full((4, 4), np.nan))
    cell = {"row": 50.0, "col": 50.0,
            "motion": {"status": "available", "drow_per_min": 1.0, "dcol_per_min": 0.0,
                       "smoothed": {"drow_per_min": 0.5, "dcol_per_min": 0.0, "n": 3}}}
    for variant in ("field", "field_anchored", "smoothed"):
        assert project_centroid(cell, variant, 10, field, DEFAULT_POLICY) == pytest.approx((55.0, 50.0))
    assert project_centroid(cell, "raw", 10, field, DEFAULT_POLICY) == pytest.approx((60.0, 50.0))


def test_no_variant_projects_a_cell_without_available_motion():
    cell = {"row": 1.0, "col": 1.0, "motion": {"status": "withheld", "drow_per_min": None, "dcol_per_min": None}}
    for variant in MOTION_VARIANTS:
        assert displacement(cell, variant, 30, [1.0], [1.0], None, DEFAULT_POLICY) is None


def test_straight_footprint_projection_is_the_whole_pixel_shift():
    cell = {"row": 10.0, "col": 10.0, "motion": {"status": "available", "drow_per_min": 0.23, "dcol_per_min": -0.11}}
    rows = np.array([8, 8, 9, 40])
    cols = np.array([3, 4, 5, 41])
    moved = project_footprint(cell, "raw", 30, rows, cols, 8, None, DEFAULT_POLICY)
    assert (moved[0] == rows + int(round(0.23 * 30))).all()
    assert (moved[1] == cols + int(round(-0.11 * 30))).all()


def test_field_footprint_projection_moves_each_block_as_one():
    field = _field(np.full((6, 6), 10.0), np.full((6, 6), 20.0))  # 1 south, 2 east px/min
    cell = {"row": 100.0, "col": 100.0, "motion": {"status": "available", "drow_per_min": 0.0, "dcol_per_min": 0.0}}
    rows = np.repeat(np.arange(96, 104), 8)
    cols = np.tile(np.arange(96, 104), 8)
    moved = project_footprint(cell, "field", 10, rows, cols, 8, field, DEFAULT_POLICY)
    assert (moved[0] - rows == 10).all() and (moved[1] - cols == 20).all()


# --- Wiring: catalogue, scores, display, CLI --------------------------------------


@pytest.fixture(scope="module")
def processed(tmp_path_factory):
    from weatherbrief.observed.cells.runner import FrameCache, Workspace, analyse_tick
    from weatherbrief.observed.frames import SOURCE_OPERA_DBZH, FrameStore

    from .cells_helpers import scene, write_dbzh

    root = tmp_path_factory.mktemp("cells662")
    store = FrameStore(root, retain_all=True)
    times = [T + timedelta(minutes=5 * i) for i in range(14)]
    for i, t in enumerate(times):
        write_dbzh(store, t, scene(220, [(90, 80, 50, 7), (140, 130, 44, 10)], shift=(1.0 * i, 2.0 * i)))
    ws = Workspace(root)
    analyse_tick(ws, times[-1] + timedelta(minutes=1), timedelta(hours=2), DEFAULT_POLICY,
                 FrameCache(ws.frames), (SOURCE_OPERA_DBZH,))
    return root, times


def test_catalogue_records_flow_lineage_velocities_and_extended_history(processed):
    from weatherbrief.observed.cells.catalogue import catalogue_path, read_catalogue

    root, times = processed
    cat = read_catalogue(catalogue_path(root, times[-1]))
    assert cat["flow"]["pair_minutes"] == 10 and cat["flow"]["origin"] == [0, 0]
    core = next(c for c in cat["cells"] if c["tier"] == "core35")
    m = core["motion"]
    assert m["smoothed"]["n"] == 5 and m["track"]["n"] == 5  # 20-min window at 5-min frames
    assert m["smoothed"]["drow_per_min"] == pytest.approx(0.2, abs=0.01)
    assert m["track"]["dcol_per_min"] == pytest.approx(0.4, abs=0.01)
    entry = core["history"][-1]
    assert len(entry) == 9 and entry[4] == m["drow_per_min"] and entry[6] == core["row"]
    first = read_catalogue(catalogue_path(root, times[0]))
    assert first["flow"] is None
    assert all(c["motion"]["smoothed"] is None for c in first["cells"])


def test_scores_carry_every_variant_and_raw_matches_extrapolation(processed):
    root, times = processed
    rows = [json.loads(line) for line in (root / "cells" / "scores" / "20261003.jsonl").read_text().splitlines()]
    assert rows
    for row in rows:
        assert set(row["variants"]) == set(MOTION_VARIANTS)
        raw = row["variants"]["raw"]
        assert {k: raw[k] for k in row["extrapolation"]} == row["extrapolation"]
        assert raw["centroid_err_km_median"] == row["centroid_err_km_median"]
        for v in MOTION_VARIANTS:  # uniform motion: every variant finds the cells
            assert row["variants"][v]["csi"] > row["persistence"]["csi"]
    table = summarise(rows)
    assert {(t["forecast"]) for t in table} == set(MOTION_VARIANTS) | {"persistence"}


def test_scores_cli_prints_the_side_by_side(processed, capsys):
    root, times = processed
    assert cli_main(["scores", "--from", times[0].isoformat(), "--to", times[-1].isoformat(),
                     "--root", str(root)]) == 0
    out = capsys.readouterr().out
    assert "field_anchored" in out and "persistence" in out


def test_display_arrow_follows_display_motion(processed):
    from weatherbrief.observed.cells.catalogue import catalogue_path, read_catalogue
    from weatherbrief.observed.cells.display import build_display
    from weatherbrief.observed.cells.runner import FrameCache, Workspace, grid_from_dict

    root, times = processed
    cat = read_catalogue(catalogue_path(root, times[-1]))
    grid = grid_from_dict(cat["grid"])
    dets = FrameCache(Workspace(root).frames).detections(times[-1], DEFAULT_POLICY)
    raw = build_display(cat, dets, grid, DEFAULT_POLICY)
    field = build_display(cat, dets, grid, dataclasses.replace(DEFAULT_POLICY, display_motion="field"))
    assert raw["motion_variant"] == "raw" and field["motion_variant"] == "field"
    for a, b in zip(raw["cells"], field["cells"]):
        assert a["id"] == b["id"]
        assert (a["arrow"] is None) == (b["arrow"] is None)
        if a["arrow"]:
            assert b["arrow"] == pytest.approx(a["arrow"], abs=0.01)  # uniform flow: same place
            assert b["motion"]["speed_kt"] == pytest.approx(a["motion"]["speed_kt"], abs=1.0)
