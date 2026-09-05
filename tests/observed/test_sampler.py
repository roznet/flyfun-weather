"""Corridor-sampler behaviour: parallax, three-state coverage, statistics."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from weatherbrief.models.observed import ObservedTopsAnnulus
from weatherbrief.observed import ctth, lightning, opera
from weatherbrief.observed.frames import GridFrame
from weatherbrief.observed.grid import GridSpec, GridWindow, compute_window
from weatherbrief.observed.sampler import sample, sample_flashes

from .conftest import STATION, STATION_NO_COVERAGE


def _radar_frame(path, quantity="DBZH", stations=(STATION,), radius_km=37.0):
    grid = opera.read_grid(path)
    window = compute_window(
        grid,
        [s.lat for s in stations],
        [s.lon for s in stations],
        radius_km=radius_km,
        pad_km=5.0,
    )
    frame = opera.read_window(
        path, quantity, window, source="opera_dbzh", units="dBZ"
    )
    return frame, window


def _tops_frame(path, stations=(STATION,)):
    import netCDF4

    with netCDF4.Dataset(str(path)) as dataset:
        grid = ctth.read_grid(dataset)
    window = compute_window(
        grid,
        [s.lat for s in stations],
        [s.lon for s in stations],
        radius_km=37.0,
        pad_km=ctth.PARALLAX_PAD_KM,
        full_width=True,
    )
    return ctth.read_window(path, window, source="eumetsat_ctth"), window


# --- Parallax --------------------------------------------------------------


def test_parallax_is_load_bearing_for_cloud_tops(ctth_path):
    """Removing the parallax correction must lose the cirrus entirely.

    The fixture places FL350 cirrus 0.5° north of the station with
    ``delta_latitude = -0.5``, which is how the real product behaves at
    50°N — measured displacement is 52 km against a 37 km corridor.  If this
    test ever passes with the correction removed, the sampler has started
    describing a different place than the one it claims.
    """
    frame, window = _tops_frame(ctth_path)

    with_parallax = sample(frame, window, [STATION])[STATION.id]
    assert all(isinstance(a, ObservedTopsAnnulus) for a in with_parallax)
    corrected = with_parallax[0]
    assert corrected.highest_fl == pytest.approx(350.0, abs=1.0)
    assert corrected.fl_bins["FL250-400"] > 0
    assert corrected.quality_method["6"] > 0  # radiance ratio IR10.5 / IR13.4

    frame.aux.pop("delta_latitude")
    frame.aux.pop("delta_longitude")
    uncorrected = sample(frame, window, [STATION])[STATION.id][0]

    assert uncorrected.fl_bins["FL250-400"] == 0
    assert uncorrected.highest_fl == pytest.approx(40.0, abs=1.0)


def test_low_cloud_survives_with_and_without_correction(ctth_path):
    """The barely-displaced stratus is found either way.

    Guards the parallax test above against the trivial explanation "the
    correction just moves everything out of range".
    """
    frame, window = _tops_frame(ctth_path)
    with_parallax = sample(frame, window, [STATION])[STATION.id][0]
    frame.aux.pop("delta_latitude")
    frame.aux.pop("delta_longitude")
    without = sample(frame, window, [STATION])[STATION.id][0]

    assert with_parallax.fl_bins["FL000-050"] > 0
    assert without.fl_bins["FL000-050"] > 0


def test_parallax_pad_exceeds_measured_displacement():
    """The window pad must outrun the displacement it exists to catch."""
    assert ctth.PARALLAX_PAD_KM >= 65.0


# --- Three-state coverage --------------------------------------------------


def test_no_radar_coverage_is_not_reported_as_clear(dbzh_path):
    """``nodata`` must never read as "the radar looked and saw nothing"."""
    frame, window = _radar_frame(dbzh_path, stations=(STATION_NO_COVERAGE,))
    annuli = sample(frame, window, [STATION_NO_COVERAGE])[STATION_NO_COVERAGE.id]

    for annulus in annuli:
        assert annulus.total_px > 0
        assert annulus.valid_px == 0
        assert annulus.undetect_px == 0
        assert annulus.nodata_px == annulus.total_px
        assert annulus.coverage_fraction == 0.0
        assert annulus.insufficient_coverage is True
        # No detections and no value to assert.
        assert annulus.max_value is None
        assert annulus.detected_fraction is None


def test_counts_partition_exactly(dbzh_path):
    frame, window = _radar_frame(dbzh_path)
    for annulus in sample(frame, window, [STATION])[STATION.id]:
        assert annulus.total_px == annulus.valid_px + annulus.nodata_px
        assert annulus.valid_px == annulus.detected_px + annulus.undetect_px


def test_partial_coverage_is_reported_as_a_fraction(dbzh_path):
    """A station on the fixture's coverage boundary sees exactly half."""
    frame, window = _radar_frame(dbzh_path)
    annulus = sample(frame, window, [STATION])[STATION.id][-1]
    assert 0.4 < annulus.coverage_fraction < 0.6
    assert annulus.nodata_px > 0
    assert annulus.detected_px > 0


def test_disc_outside_the_read_window_counts_as_nodata(dbzh_path):
    """Truncating the window must lower coverage, not shrink the denominator.

    A window sized for 5 NM but sampled at 20 NM has genuinely not looked at
    most of the larger disc; reporting full coverage there would assert a
    clear sky over pixels nobody read.
    """
    grid = opera.read_grid(dbzh_path)
    narrow = compute_window(
        grid, [STATION.lat], [STATION.lon], radius_km=5.0, pad_km=0.0
    )
    frame = opera.read_window(dbzh_path, "DBZH", narrow, source="opera_dbzh", units="dBZ")
    annuli = sample(frame, narrow, [STATION], radii_nm=(5.0, 20.0))[STATION.id]

    assert annuli[1].total_px > annuli[0].total_px
    assert annuli[1].coverage_fraction < annuli[0].coverage_fraction


def test_station_off_grid_yields_empty_but_honest_annuli(dbzh_path):
    """A station the grid does not reach reports no coverage, not zero echo."""
    from weatherbrief.observed.sampler import SampleStation

    frame, window = _radar_frame(dbzh_path)
    far = SampleStation("FAR", -33.9, 151.2)  # Sydney
    annuli = sample(frame, window, [far])[far.id]
    for annulus in annuli:
        assert annulus.valid_px == 0
        assert annulus.insufficient_coverage is True


# --- Statistics ------------------------------------------------------------


def test_statistics_ignore_undetect_pixels(dbzh_path):
    """Averaging ``undetect`` zeros into a rain rate would invent drizzle."""
    frame, window = _radar_frame(dbzh_path, quantity="DBZH")
    annuli = sample(frame, window, [STATION])[STATION.id]
    wide = annuli[-1]
    assert wide.undetect_px > 0
    assert wide.mean_value is not None
    # Every detection in the fixture is >= -31.5 dBZ and the echo core is
    # 45 dBZ; a mean dragged toward zero by empty pixels would sit far below
    # the p90.
    assert wide.mean_value > 0
    assert wide.max_value == pytest.approx(45.0, abs=0.5)
    assert wide.p90_value is not None and wide.p90_value <= wide.max_value


def test_radii_are_cumulative_discs(dbzh_path):
    frame, window = _radar_frame(dbzh_path)
    annuli = sample(frame, window, [STATION])[STATION.id]
    assert [a.radius_nm for a in annuli] == [5.0, 10.0, 20.0]
    assert annuli[0].total_px < annuli[1].total_px < annuli[2].total_px


# --- Windows and safety ----------------------------------------------------


def test_mismatched_window_is_rejected(dbzh_path):
    frame, window = _radar_frame(dbzh_path)
    wrong = GridWindow(window.row0 + 3, window.row1, window.col0, window.col1)
    with pytest.raises(ValueError, match="does not match"):
        sample(frame, wrong, [STATION])


def test_sampler_reads_no_files(dbzh_path, monkeypatch):
    """Sampling must not reopen the granule — not once, not per station."""
    import h5py

    frame, window = _radar_frame(dbzh_path)

    def _forbidden(*args, **kwargs):  # pragma: no cover - only runs on failure
        raise AssertionError("sampler opened a file")

    monkeypatch.setattr(h5py, "File", _forbidden)
    sample(frame, window, [STATION, STATION_NO_COVERAGE])


def test_tops_sampler_reads_no_files(ctth_path, monkeypatch):
    """Same guard for the CTTH path, which opens netCDF4 rather than h5py.

    The radar guard above says nothing about this path, and CTTH is the
    expensive one — full-width strips off a 5568-wide granule, ~90–130 ms even
    done right.  A per-station reopen here would be the costliest version of
    the regression the invariant exists to prevent.
    """
    import netCDF4

    frame, window = _tops_frame(ctth_path)

    def _forbidden(*args, **kwargs):  # pragma: no cover - only runs on failure
        raise AssertionError("sampler opened a netCDF file")

    monkeypatch.setattr(netCDF4, "Dataset", _forbidden)
    result = sample(frame, window, [STATION, STATION_NO_COVERAGE])
    assert len(result) == 2


# --- Lightning -------------------------------------------------------------


def test_flash_counts_grow_with_radius(li_path):
    frame = lightning.read_flashes(li_path, source="eumetsat_li", window_minutes=10.0)
    annuli = sample_flashes(frame, [STATION])[STATION.id]
    counts = [a.flash_count for a in annuli]
    assert counts == sorted(counts)
    assert counts[-1] > 0


def test_flash_rate_normalises_by_area_and_window(li_path):
    frame = lightning.read_flashes(li_path, source="eumetsat_li", window_minutes=10.0)
    annulus = sample_flashes(frame, [STATION])[STATION.id][-1]
    expected = annulus.flash_count / (annulus.area_km2 / 1000.0) / 10.0
    assert annulus.flashes_per_1000km2_per_min == pytest.approx(expected)


def test_zero_flashes_is_an_observation_not_a_gap(li_path):
    """Lightning has no coverage mask: the imager sees the whole disc."""
    from weatherbrief.observed.sampler import SampleStation

    frame = lightning.read_flashes(li_path, source="eumetsat_li", window_minutes=10.0)
    quiet = SampleStation("QUIET", 48.0, -4.0)
    annulus = sample_flashes(frame, [quiet])[quiet.id][0]
    assert annulus.flash_count == 0
    assert annulus.flashes_per_1000km2_per_min == 0.0


# --- Synthetic-grid unit checks -------------------------------------------


def _uniform_frame(values, nodata, undetect):
    grid = GridSpec(
        proj4="+proj=laea +lat_0=50 +lon_0=2 +units=m +ellps=WGS84 +no_defs",
        nx=values.shape[1],
        ny=values.shape[0],
        x0=-(values.shape[1] // 2) * 2000.0,
        y0=(values.shape[0] // 2) * 2000.0,
        dx=2000.0,
        dy=-2000.0,
    )
    window = GridWindow(0, values.shape[0], 0, values.shape[1])
    return GridFrame(
        source="test",
        quantity="DBZH",
        units="dBZ",
        valid_time=datetime(2026, 8, 25, 14, 0, tzinfo=timezone.utc),
        window_minutes=10.0,
        grid=grid,
        window=window,
        values=values,
        nodata=nodata,
        undetect=undetect,
    ), window


def test_all_undetect_reports_full_coverage_and_no_echo():
    shape = (61, 61)
    frame, window = _uniform_frame(
        np.full(shape, np.nan, dtype=np.float32),
        np.zeros(shape, dtype=bool),
        np.ones(shape, dtype=bool),
    )
    from weatherbrief.observed.sampler import SampleStation

    station = SampleStation("MID", 50.0, 2.0)
    annulus = sample(frame, window, [station], radii_nm=(5.0,))[station.id][0]
    assert annulus.nodata_px == 0
    assert annulus.undetect_px == annulus.total_px
    assert annulus.coverage_fraction == 1.0
    assert annulus.insufficient_coverage is False
    assert annulus.detected_fraction == 0.0
    assert annulus.max_value is None


# --- Cost ------------------------------------------------------------------


@pytest.mark.slow
def test_two_hundred_stations_sample_in_well_under_the_budget(dbzh_path):
    """Acceptance bound: a ~200-station route, well under 200 ms.

    Marked slow because it is a timing assertion, and the bound is loose
    (1 s) on purpose: the point is to catch an algorithmic regression — a
    reintroduced per-station file open, or a full-grid transform — not to
    police tens of milliseconds on whatever machine happens to run it. The
    measured figures are ~42 ms radar / ~90–130 ms CTTH on Apple silicon.
    """
    import time

    from weatherbrief.observed.sampler import SampleStation

    stations = [
        SampleStation(f"P{i:03d}", 50.2 + 0.004 * i, 1.3 + 0.004 * i)
        for i in range(200)
    ]
    grid = opera.read_grid(dbzh_path)
    window = compute_window(
        grid,
        [s.lat for s in stations],
        [s.lon for s in stations],
        radius_km=37.0,
        pad_km=5.0,
    )
    frame = opera.read_window(dbzh_path, "DBZH", window, source="opera_dbzh", units="dBZ")

    started = time.perf_counter()
    result = sample(frame, window, stations)
    elapsed = time.perf_counter() - started

    assert len(result) == 200
    assert elapsed < 1.0, f"200-station sample took {elapsed * 1000:.0f} ms"
