"""Observed cells (#650): detection, motion, lineage, trend and attributes.

Pure-function tests on synthetic fields.  The properties pinned here are the
ones the design rests on: nodata is never an echo, a coverage edge cannot
pull the motion estimate, a split or merge withholds velocity, and lightning
is counted where it happened rather than advected.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from weatherbrief.observed.cells import DEFAULT_POLICY, detect, estimate_flow, masked_ncc
from weatherbrief.observed.cells.attributes import cloud_tops, flash_counts, rate_peaks
from weatherbrief.observed.cells.detect import footprint_runs, runs_to_pixels
from weatherbrief.observed.cells.lineage import PreviousCell, link, trend
from weatherbrief.observed.cells.motion import cell_motion
from weatherbrief.observed.cells.policy import TierPolicy
from weatherbrief.observed.frames import FlashFrame, GridFrame

from .cells_helpers import grid_frame, grid_spec, scene

T0 = datetime(2026, 10, 3, 12, 0, tzinfo=timezone.utc)
CORE = DEFAULT_POLICY.tier("core35")
RAIN = DEFAULT_POLICY.tier("rain20")


# --- Detection ---------------------------------------------------------------


def test_nodata_never_joins_a_cell_and_marks_it_truncated():
    values = scene(120, [(60, 60, 50, 6)])
    nodata = np.zeros(values.shape, dtype=bool)
    nodata[:, 62:] = True  # radar network ends through the middle of the cell
    det = detect(grid_frame(values, T0, nodata), CORE)
    assert len(det.cells) == 1
    cell = det.cells[0]
    assert cell.truncated
    assert not (det.labels[:, 62:] > 0).any()


def test_window_edge_marks_a_cell_truncated_and_interior_cell_is_not():
    values = scene(120, [(2, 60, 50, 5), (70, 70, 50, 5)])
    det = detect(grid_frame(values, T0), CORE)
    by_row = sorted(det.cells, key=lambda c: c.centroid_row)
    assert by_row[0].truncated and not by_row[1].truncated


def test_min_area_drops_specks_and_labels_are_deterministic():
    values = scene(120, [(30, 30, 50, 6), (90, 90, 50, 0.6)])  # second is a speck
    a = detect(grid_frame(values, T0), CORE)
    b = detect(grid_frame(values, T0), CORE)
    assert len(a.cells) == 1
    assert np.array_equal(a.labels, b.labels)


def test_peak_area_and_centroid():
    values = scene(120, [(40, 80, 52, 6)])
    cell = detect(grid_frame(values, T0), CORE).cells[0]
    assert cell.peak_dbz == pytest.approx(52, abs=6)
    assert cell.centroid_row == pytest.approx(40, abs=1)
    assert cell.centroid_col == pytest.approx(80, abs=1)
    assert cell.area_km2 == pytest.approx(cell.area_px * 4.0)


def test_elongated_band_has_an_orientation_and_round_cell_has_none():
    rows, cols = np.mgrid[0:120, 0:120]
    band = np.where((np.abs(cols - 60) < 4) & (np.abs(rows - 60) < 40), 45.0, 0.0)
    round_ = scene(120, [(60, 60, 50, 6)], seed=1) * 0 + np.where(
        np.hypot(rows - 60, cols - 60) < 8, 45.0, 0.0)
    band_cell = detect(grid_frame(band, T0), CORE).cells[0]
    round_cell = detect(grid_frame(round_, T0), CORE).cells[0]
    # Grid-north band: bearing near 0/180 (the Lambert grid is rotated a few
    # degrees from true north here).
    assert min(band_cell.orientation_deg, 180 - band_cell.orientation_deg) < 15
    assert band_cell.major_km > 3 * band_cell.minor_km
    assert round_cell.orientation_deg is None


@pytest.mark.parametrize("block", [1, 4])
def test_footprint_runs_cover_the_cell(block):
    values = scene(120, [(50, 50, 50, 9)])
    det = detect(grid_frame(values, T0), RAIN)
    cell = det.cells[0]
    runs = footprint_runs(det, cell, block)
    rows, cols = runs_to_pixels(runs, block)
    covered = np.zeros_like(det.labels, dtype=bool)
    covered[rows, cols] = True
    inside = det.labels == cell.label
    assert covered[inside].all()  # never smaller than the cell
    if block == 1:
        assert np.array_equal(covered, inside)  # exact at pixel resolution


# --- Motion ------------------------------------------------------------------


def test_masked_ncc_matches_brute_force():
    rng = np.random.default_rng(3)
    image = rng.normal(size=(20, 20))
    template = image[5:13, 6:14].copy()
    i_mask = rng.random((20, 20)) > 0.2
    t_mask = rng.random((8, 8)) > 0.2
    got = masked_ncc(image, i_mask, template, t_mask)
    i, j = 4, 3
    m = i_mask[i:i + 8, j:j + 8] & t_mask
    a = image[i:i + 8, j:j + 8][m]
    b = template[m]
    expected = np.corrcoef(a, b)[0, 1]
    assert got[i, j] == pytest.approx(expected, abs=1e-6)
    assert np.unravel_index(np.nanargmax(got), got.shape) == (5, 6)


def _masked_ncc_fftconvolve(image, image_mask, template, template_mask, min_overlap_fraction=0.5):
    """The pre-#666 formulation: six independent ``fftconvolve`` calls."""
    from scipy.signal import fftconvolve

    def xcorr(a, b):
        return fftconvolve(a, b[::-1, ::-1], mode="valid")

    f_m, t_m = image_mask.astype(float), template_mask.astype(float)
    f1, t1 = image * f_m, template * t_m
    overlap, sum_f, sum_f2 = xcorr(f_m, t_m), xcorr(f1, t_m), xcorr(f1 * image, t_m)
    sum_t, sum_t2, sum_ft = xcorr(f_m, t1), xcorr(f_m, t1 * template), xcorr(f1, t1)
    with np.errstate(invalid="ignore", divide="ignore"):
        ok = overlap >= max(min_overlap_fraction * template_mask.sum(), 1.0)
        n = np.where(ok, overlap, 1.0)
        num = sum_ft - sum_f * sum_t / n
        var_f = sum_f2 - sum_f * sum_f / n
        var_t = sum_t2 - sum_t * sum_t / n
        denom = np.sqrt(np.clip(var_f, 0, None) * np.clip(var_t, 0, None))
        eps = 1e-6 * max(float(np.abs(sum_f2).max(initial=0.0)), 1.0)
        ok &= (var_f > eps) & (var_t > eps)
        ncc = np.where(ok, num / np.where(denom > 0, denom, 1.0), np.nan)
    return np.clip(ncc, -1.0, 1.0)


def test_shared_transform_ncc_equals_the_fftconvolve_one():
    """#666 halved the FFT count; the answer must not move (float tolerance)."""
    rng = np.random.default_rng(11)
    image = np.clip(rng.normal(10, 8, size=(180, 180)), 0, 50)
    template = image[30:158, 22:150].copy()
    i_mask = rng.random((180, 180)) > 0.3
    t_mask = rng.random((128, 128)) > 0.3
    got = masked_ncc(image, i_mask, template, t_mask)
    ref = _masked_ncc_fftconvolve(image, i_mask, template, t_mask)
    assert np.array_equal(np.isnan(got), np.isnan(ref))
    np.testing.assert_allclose(got[np.isfinite(got)], ref[np.isfinite(ref)], atol=1e-9)
    assert np.unravel_index(np.nanargmax(got), got.shape) == (30, 22)


BLOBS = [(60, 60, 50, 7), (110, 140, 45, 10), (150, 70, 40, 5)]


def test_translated_scene_recovers_the_velocity():
    a = grid_frame(scene(200, BLOBS), T0)
    b = grid_frame(scene(200, BLOBS, shift=(6, -4)), T0 + timedelta(minutes=10))
    flow = estimate_flow(a, b, DEFAULT_POLICY, 10)
    assert flow.matched > 0
    assert np.nanmedian(flow.dr) == pytest.approx(6, abs=0.3)
    assert np.nanmedian(flow.dc) == pytest.approx(-4, abs=0.3)
    det = detect(b, CORE)
    for cell in det.cells:
        mo = cell_motion(flow, det.labels, cell.label, cell.slice_rows, cell.slice_cols)
        assert mo.support > 0.5
        assert mo.drow_per_min * 10 == pytest.approx(6, abs=0.3)
        assert mo.dcol_per_min * 10 == pytest.approx(-4, abs=0.3)


def test_flow_does_not_depend_on_the_thread_count():
    a = grid_frame(scene(200, BLOBS), T0)
    b = grid_frame(scene(200, BLOBS, shift=(6, -4)), T0 + timedelta(minutes=10))
    one = estimate_flow(a, b, DEFAULT_POLICY, 10, threads=1)
    many = estimate_flow(a, b, DEFAULT_POLICY, 10, threads=4)
    assert (one.tried, one.matched) == (many.tried, many.matched)
    for x, y in ((one.dr, many.dr), (one.dc, many.dc), (one.ncc, many.ncc)):
        assert np.array_equal(x, y, equal_nan=True)


def test_peaks_per_cell_are_the_labelled_maxima():
    values = scene(200, BLOBS)
    det = detect(grid_frame(values, T0), CORE)
    for cell in det.cells:
        mask = det.labels == cell.label
        assert cell.peak_dbz == float(np.float32(values[mask].max()))


def test_a_moving_nodata_edge_does_not_bias_the_shift():
    """The coverage edge moves one way, the echo the other: only the echo counts."""
    nodata_a = np.zeros((200, 200), dtype=bool)
    nodata_a[:, 170:] = True
    nodata_b = np.zeros((200, 200), dtype=bool)
    nodata_b[:, 150:] = True  # edge "moved" 20 px west
    blobs = [(100, 120, 50, 9), (60, 100, 45, 7)]
    a = grid_frame(scene(200, blobs), T0, nodata_a)
    b = grid_frame(scene(200, blobs, shift=(4, 5)), T0 + timedelta(minutes=10), nodata_b)
    flow = estimate_flow(a, b, DEFAULT_POLICY, 10)
    assert flow.matched > 0
    assert np.nanmedian(flow.dr) == pytest.approx(4, abs=0.5)
    assert np.nanmedian(flow.dc) == pytest.approx(5, abs=0.5)


def test_no_echo_means_no_tile_is_even_tried():
    empty = np.zeros((200, 200))
    nodata = np.zeros((200, 200), dtype=bool)
    nodata[:, 100:] = True
    flow = estimate_flow(grid_frame(empty, T0, nodata),
                         grid_frame(empty, T0 + timedelta(minutes=10), nodata), DEFAULT_POLICY, 10)
    assert flow.tried == 0 and flow.matched == 0


def test_cell_without_matched_tiles_has_no_velocity():
    det = detect(grid_frame(scene(200, BLOBS), T0), CORE)
    cell = det.cells[0]
    mo = cell_motion(None, det.labels, cell.label, cell.slice_rows, cell.slice_cols)
    assert mo.status == "no_pair" and mo.drow_per_min is None


# --- Lineage and trend -------------------------------------------------------


def _prev(det, ids, drow=0.0, dcol=0.0):
    return {c.label: PreviousCell(ids[i], c.label, T0.isoformat(), [], drow, dcol)
            for i, c in enumerate(det.cells)}


def test_a_moving_cell_keeps_its_id_through_advection():
    blob = [(60, 60, 50, 6)]
    prev = detect(grid_frame(scene(150, blob), T0), CORE)
    # Moves 8 px in 5 min: more than its own radius would tolerate without
    # advecting the previous footprint first.
    cur = detect(grid_frame(scene(150, blob, shift=(0, 12)), T0 + timedelta(minutes=5)), CORE)
    out = link(cur, prev, _prev(prev, ["core35-A"], dcol=12 / 5), 5, T0 + timedelta(minutes=5),
               DEFAULT_POLICY)
    (lin,) = out.values()
    assert lin.id == "core35-A" and lin.event == "continued"


def test_split_keeps_the_id_on_the_larger_child_and_records_parents():
    rows, cols = np.mgrid[0:150, 0:150]
    one = np.where((np.abs(rows - 75) < 6) & (np.abs(cols - 75) < 30), 45.0, 0.0)
    two = one.copy()
    two[:, 80:84] = 0.0  # cut into a large west part and a small east part
    prev = detect(grid_frame(one, T0), CORE)
    cur = detect(grid_frame(two, T0 + timedelta(minutes=5)), CORE)
    out = link(cur, prev, _prev(prev, ["core35-P"]), 5, T0 + timedelta(minutes=5), DEFAULT_POLICY)
    by_area = sorted(cur.cells, key=lambda c: -c.area_px)
    big, small = out[by_area[0].label], out[by_area[1].label]
    assert big.id == "core35-P" and big.event == "split"
    assert small.id != "core35-P" and small.event == "split" and small.parents == ["core35-P"]


def test_merge_keeps_the_larger_parent_and_lists_both():
    rows, cols = np.mgrid[0:150, 0:150]
    two = np.where((np.abs(rows - 75) < 6) & (np.abs(cols - 75) < 30), 45.0, 0.0)
    two[:, 80:84] = 0.0
    one = np.where((np.abs(rows - 75) < 6) & (np.abs(cols - 75) < 30), 45.0, 0.0)
    prev = detect(grid_frame(two, T0), CORE)
    cur = detect(grid_frame(one, T0 + timedelta(minutes=5)), CORE)
    by_area = sorted(prev.cells, key=lambda c: -c.area_px)
    ids = {by_area[0].label: "core35-BIG", by_area[1].label: "core35-SMALL"}
    prev_map = {c.label: PreviousCell(ids[c.label], c.label, T0.isoformat(), [], 0.0, 0.0)
                for c in prev.cells}
    (lin,) = link(cur, prev, prev_map, 5, T0 + timedelta(minutes=5), DEFAULT_POLICY).values()
    assert lin.id == "core35-BIG" and lin.event == "merge"
    assert sorted(lin.parents) == ["core35-BIG", "core35-SMALL"]


def test_new_cell_gets_a_deterministic_id():
    det = detect(grid_frame(scene(150, [(60, 60, 50, 6)]), T0), CORE)
    out = link(det, None, None, 0, T0, DEFAULT_POLICY)
    assert out[1].id == "core35-20261003T1200-0001" and out[1].event == "born"


def _hist(minutes_ago, peak, area, flashes=None):
    return [(T0 - timedelta(minutes=minutes_ago)).isoformat(), peak, area, flashes]


def test_trend_states():
    now = [T0.isoformat(), 50.0, 200.0, 12]
    assert trend([], now, T0, DEFAULT_POLICY)["state"] == "new"
    assert trend([_hist(10, 40.0, 100.0)], now, T0, DEFAULT_POLICY)["state"] == "new"  # too recent
    grow = trend([_hist(30, 42.0, 100.0, 2)], now, T0, DEFAULT_POLICY)
    assert grow["state"] == "developing" and grow["d_flashes"] == 10
    assert trend([_hist(30, 58.0, 400.0)], now, T0, DEFAULT_POLICY)["state"] == "decaying"
    assert trend([_hist(30, 49.0, 190.0)], now, T0, DEFAULT_POLICY)["state"] == "steady"
    # Area doubled while the peak fell 8 dB: both signals, not "steady".
    assert trend([_hist(30, 58.0, 100.0)], now, T0, DEFAULT_POLICY)["state"] == "mixed"


# --- Attributes --------------------------------------------------------------


def _flashes(lats, lons):
    times = np.full(len(lats), np.datetime64("2026-10-03T12:00:00", "s"))
    return FlashFrame("eumetsat_li", T0, 10.0, np.array(lats, float), np.array(lons, float), times)


def test_lightning_counts_where_it_happened_within_the_buffer():
    frame = grid_frame(scene(150, [(40, 40, 50, 5), (110, 110, 50, 5)]), T0)
    det = detect(frame, CORE)
    grid = frame.grid
    a, b = sorted(det.cells, key=lambda c: c.centroid_row)
    edge_lon, edge_lat = grid.colrow_to_lonlat(a.centroid_col + 6, a.centroid_row)  # just outside, < 5 km
    far_lon, far_lat = grid.colrow_to_lonlat(a.centroid_col + 30, a.centroid_row)  # 60 km away
    flashes = _flashes([a.lat, a.lat, float(edge_lat), float(far_lat), b.lat],
                       [a.lon, a.lon, float(edge_lon), float(far_lon), b.lon])
    counts = dict(zip([c.label for c in det.cells], flash_counts(det, grid, flashes, 5.0)))
    assert counts[a.label] == 3  # two inside + one within the buffer; the far one is nobody's
    assert counts[b.label] == 1


def test_rate_peak_is_none_where_rate_saw_nothing():
    frame = grid_frame(scene(150, [(40, 40, 50, 5), (110, 110, 50, 5)]), T0)
    det = detect(frame, CORE)
    rate_vals = np.full(frame.values.shape, np.nan, dtype=np.float32)
    first = sorted(det.cells, key=lambda c: c.centroid_row)[0]
    rate_vals[int(first.centroid_row), int(first.centroid_col)] = 25.0
    rate = GridFrame("opera_rate", "RATE", "mm/h", T0, 15.0, frame.grid, frame.window, rate_vals,
                     np.zeros(rate_vals.shape, bool), np.isnan(rate_vals))
    peaks = dict(zip([c.label for c in det.cells], rate_peaks(det, frame.grid, rate)))
    assert peaks[first.label] == 25.0
    assert [v for k, v in peaks.items() if k != first.label] == [None]


def test_rate_on_a_coarser_grid_maps_through_the_affines():
    """OPERA RATE is 2 km while DBZH is 1 km: a rate pixel serves 2 × 2 DBZH pixels."""
    from weatherbrief.observed.grid import GridSpec, GridWindow

    frame = grid_frame(scene(150, [(40, 40, 50, 5)]), T0)
    det = detect(frame, CORE)
    cell = det.cells[0]
    g = frame.grid
    coarse = GridSpec(g.proj4, 75, 75, g.x0 + g.dx / 2, g.y0 + g.dy / 2, g.dx * 2, g.dy * 2)
    vals = np.full((75, 75), np.nan, dtype=np.float32)
    vals[int(cell.centroid_row) // 2, int(cell.centroid_col) // 2] = 18.0
    rate = GridFrame("opera_rate", "RATE", "mm/h", T0, 15.0, coarse, GridWindow(0, 75, 0, 75), vals,
                     np.zeros((75, 75), bool), np.isnan(vals))
    assert rate_peaks(det, g, rate) == [18.0]


def test_cloud_top_is_placed_by_its_parallax_corrected_position():
    """A cloud top imaged 25 km north of the cell belongs to it once corrected."""
    frame = grid_frame(scene(150, [(75, 75, 50, 5)]), T0)
    det = detect(frame, CORE)
    cell = det.cells[0]
    ctth_vals = np.full((150, 150), np.nan, dtype=np.float32)
    nominal_row = int(round(cell.centroid_row)) - 12  # 24 km north in the imagery
    col = int(round(cell.centroid_col))
    ctth_vals[nominal_row, col] = 11000.0
    lon_nom, lat_nom = frame.grid.colrow_to_lonlat(col, nominal_row)
    dlat = np.zeros((150, 150), np.float32)
    dlon = np.zeros((150, 150), np.float32)
    dlat[nominal_row, col] = cell.lat - float(lat_nom)
    dlon[nominal_row, col] = cell.lon - float(lon_nom)
    ctth = GridFrame("eumetsat_ctth", "cloud_top_height", "m", T0, 0.0, frame.grid, frame.window,
                     ctth_vals, np.zeros((150, 150), bool), np.isnan(ctth_vals),
                     aux={"delta_latitude": dlat, "delta_longitude": dlon})
    (top,) = cloud_tops(det, frame.grid, ctth)
    assert top[0] == pytest.approx(361, abs=1)  # 11 000 m ≈ FL361
    assert top[1] == 1
    # Without the correction the same pixel lands outside the cell.
    ctth.aux["delta_latitude"][:] = 0
    ctth.aux["delta_longitude"][:] = 0
    assert cloud_tops(det, frame.grid, ctth)[0] == (None, 0)


def test_custom_tier_threshold_is_honoured():
    tier = TierPolicy("core50", 50.0, 4.0)
    values = scene(120, [(40, 40, 55, 5), (80, 80, 45, 5)])
    assert len(detect(grid_frame(values, T0), tier).cells) == 1
    assert grid_spec(10).pixel_km == 2.0
