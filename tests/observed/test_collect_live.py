"""Live-provider smoke tests for the observed collectors.

Skipped unless ``WB_OBSERVED_LIVE_TESTS=1``.  They reach the real OPERA S3
cache and the real EUMETSAT Data Store, so they are unsuitable for CI: they
are slow, they need credentials, and they fail for reasons that have nothing
to do with this repository.

What they are *for* is the class of bug a fixture cannot catch — the provider
renaming a variable, re-cutting the OPERA domain, or changing the LI product
baseline.  The committed fixtures encode our understanding of each format;
these check that understanding against what the providers actually publish
today.  Run them when picking this work back up, and after any provider
announcement:

    WB_OBSERVED_LIVE_TESTS=1 pytest tests/observed/test_collect_live.py -v
"""

from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone

import pytest

from weatherbrief.observed import collect
from weatherbrief.observed.frames import (
    SOURCE_EUMETSAT_CTTH,
    SOURCE_EUMETSAT_LI,
    SOURCE_OPERA_DBZH,
    FrameStore,
)
from weatherbrief.observed.grid import compute_window
from weatherbrief.observed.sampler import SampleStation, sample

pytestmark = pytest.mark.skipif(
    os.environ.get("WB_OBSERVED_LIVE_TESTS", "") not in ("1", "true", "yes"),
    reason="live-provider tests: set WB_OBSERVED_LIVE_TESTS=1 to run",
)

# Le Touquet — inside the OPERA footprint and well within the MTG disc.
STATION = SampleStation("LFAT", 50.517, 1.627)


@pytest.fixture
def store(tmp_path) -> FrameStore:
    return FrameStore(tmp_path / "observed")


@pytest.mark.slow
def test_opera_publishes_a_frame_we_can_decode(store):
    """The OPERA composite still has the structure the reader assumes."""
    from weatherbrief.observed import opera

    result = collect.collect_opera(SOURCE_OPERA_DBZH, store, max_fetch=1)
    if result.fetched == 0:
        pytest.skip(f"no OPERA frame available right now: {result.errors or 'not published'}")

    stored = store.list_frames(SOURCE_OPERA_DBZH)[0]
    grid = opera.read_grid(stored.path)
    # The domain has been re-cut before, which is exactly why the reader takes
    # its geolocation from the file. Assert only that it is a plausible
    # European LAEA grid, not a specific cut.
    assert "+proj=laea" in grid.proj4
    assert grid.nx > 1000 and grid.ny > 1000

    window = compute_window(grid, [STATION.lat], [STATION.lon], radius_km=40.0, pad_km=5.0)
    frame = opera.read_window(
        stored.path, "DBZH", window, source=SOURCE_OPERA_DBZH, units="dBZ"
    )
    assert frame.valid_time.tzinfo is not None
    # The three-state split must be real, not a formality: a live composite
    # always has coverage holes somewhere on the continent.
    assert frame.nodata.size > 0

    annuli = sample(frame, window, [STATION])[STATION.id]
    assert [a.radius_nm for a in annuli] == [5.0, 10.0, 20.0]
    for annulus in annuli:
        assert annulus.total_px == annulus.valid_px + annulus.nodata_px


@pytest.mark.slow
def test_opera_nodata_fraction_is_still_about_half(store):
    """The 49.4%-nodata figure the whole three-state design rests on.

    If this drifts a long way, the coverage floor and the "no coverage" UI
    states were calibrated against a grid that no longer exists.
    """
    from weatherbrief.observed import opera

    result = collect.collect_opera(SOURCE_OPERA_DBZH, store, max_fetch=1)
    if result.fetched == 0:
        pytest.skip("no OPERA frame available right now")

    stored = store.list_frames(SOURCE_OPERA_DBZH)[0]
    grid = opera.read_grid(stored.path)
    frame = opera.read_window(
        stored.path,
        "DBZH",
        compute_window(grid, [50.0], [5.0], radius_km=0.0, pad_km=0.0).__class__(
            0, grid.ny, 0, grid.nx
        ),
        source=SOURCE_OPERA_DBZH,
        units="dBZ",
    )
    nodata_fraction = float(frame.nodata.mean())
    assert 0.3 < nodata_fraction < 0.7, (
        f"OPERA nodata fraction is {nodata_fraction:.1%}; the three-state "
        f"design and the coverage floor were calibrated against ~49%"
    )


@pytest.mark.slow
def test_eumetsat_ctth_still_ships_the_parallax_fields(store):
    """``delta_latitude``/``delta_longitude`` are load-bearing, not optional.

    Without them the sampler describes a location up to 52 km from the one it
    claims to. If a product baseline ever drops them, this must fail loudly
    rather than the corridor silently sampling the wrong place.
    """
    import netCDF4

    from weatherbrief.observed import ctth

    if collect.eumetsat_credentials() is None:
        pytest.skip("EUMETSAT_CONSUMER_KEY / _SECRET not set")

    result = collect.collect_eumetsat(SOURCE_EUMETSAT_CTTH, store, max_fetch=1)
    if result.fetched == 0:
        pytest.skip(f"no CTTH granule available: {result.errors or 'none in window'}")

    stored = store.list_frames(SOURCE_EUMETSAT_CTTH)[0]
    with netCDF4.Dataset(str(stored.path)) as dataset:
        for name in ("delta_latitude", "delta_longitude", "quality_method", "cloud_top_height"):
            assert name in dataset.variables, f"CTTH granule no longer has {name}"
        grid = ctth.read_grid(dataset)
    assert "+proj=geos" in grid.proj4

    window = compute_window(
        grid,
        [STATION.lat],
        [STATION.lon],
        radius_km=40.0,
        pad_km=ctth.PARALLAX_PAD_KM,
        full_width=True,
    )
    frame = ctth.read_window(stored.path, window, source=SOURCE_EUMETSAT_CTTH)
    dlat = frame.aux["delta_latitude"]
    # Northern-hemisphere displacement is southward; a non-negative field
    # everywhere would mean the sign convention changed.
    assert float(dlat.min()) < 0


@pytest.mark.slow
def test_eumetsat_li_flash_variables_are_still_recognised(store):
    """LI variable naming has moved between baselines — check it still parses."""
    from weatherbrief.observed import lightning

    if collect.eumetsat_credentials() is None:
        pytest.skip("EUMETSAT_CONSUMER_KEY / _SECRET not set")

    result = collect.collect_eumetsat(SOURCE_EUMETSAT_LI, store, max_fetch=1)
    if result.fetched == 0:
        pytest.skip(f"no LI granule available: {result.errors or 'none in window'}")

    stored = store.list_frames(SOURCE_EUMETSAT_LI)[0]
    # Raises rather than reporting a quiet zero if the names moved again.
    frame = lightning.read_flashes(
        stored.path, source=SOURCE_EUMETSAT_LI, window_minutes=10.0
    )
    assert frame.lats.size == frame.lons.size == frame.times.size
    assert frame.valid_time.tzinfo is not None


@pytest.mark.slow
def test_frame_delivery_lag_matches_the_registry(store):
    """The lag the collector waits out should still be roughly right.

    Too short and every tick spends a 404; too long and the briefing shows an
    older frame than the provider has.
    """
    result = collect.collect_opera(SOURCE_OPERA_DBZH, store, max_fetch=1)
    if result.fetched == 0 or result.latest_valid_time is None:
        pytest.skip("no OPERA frame available right now")
    age = datetime.now(timezone.utc) - result.latest_valid_time
    assert age < timedelta(minutes=20), (
        f"newest OPERA frame is {age}; either delivery has slowed a long way "
        f"or the collector's lag/lookback needs recalibrating"
    )
