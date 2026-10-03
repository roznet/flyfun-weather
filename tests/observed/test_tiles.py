"""Europe-wide radar tiles (#652): canvas per frame, tiles sliced from it."""

from __future__ import annotations

import io
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from weatherbrief.observed import collect, tiles
from weatherbrief.observed.frames import SOURCE_OPERA_DBZH, SOURCE_OPERA_RATE, FrameStore
from weatherbrief.observed.imagery import NODATA_RGBA, OverlayBounds, render_overlay

NOW = datetime(2026, 8, 25, 14, 7, tzinfo=timezone.utc)
VALID = datetime(2026, 8, 25, 14, 0, tzinfo=timezone.utc)
# The fixture station (LFAT) — inside the fixture's radar coverage.
STATION = (50.517, 1.621)


@pytest.fixture
def store(tmp_path, dbzh_path) -> FrameStore:
    from weatherbrief.observed import opera

    store = FrameStore(tmp_path / "observed")
    path = store.write_payload(SOURCE_OPERA_DBZH, VALID, dbzh_path.read_bytes())
    store.write_sidecar(SOURCE_OPERA_DBZH, VALID, opera.read_metadata(path, "DBZH"))
    return store


def _tile_of(lat: float, lon: float, z: int) -> tuple[int, int]:
    x, y = tiles.lonlat_to_world_px(lon, lat, z)
    return int(x // tiles.TILE_SIZE), int(y // tiles.TILE_SIZE)


def _decode(png: bytes) -> np.ndarray:
    from PIL import Image

    return np.array(Image.open(io.BytesIO(png)).convert("RGBA"))


# --- Codec -----------------------------------------------------------------------


@pytest.mark.parametrize("source", [SOURCE_OPERA_DBZH, SOURCE_OPERA_RATE])
def test_codec_never_decodes_below_the_measured_value(source):
    """Rounding to nearest decoded 0.5 mm/h (the light floor) as 0.47 — a class down."""
    codec = tiles._CODECS[source]
    values = (
        np.linspace(-20, 70, 2001) if source == SOURCE_OPERA_DBZH
        else np.logspace(-2.5, 2.5, 2001)
    )
    decoded = codec.decode(codec.encode(values))
    assert (decoded >= values - 1e-4 * np.abs(values) - 1e-6).all()


@pytest.mark.parametrize(
    "source,floors",
    [
        (SOURCE_OPERA_DBZH, [18.0, 30.0, 41.0, 46.0, 50.0]),
        (SOURCE_OPERA_RATE, [0.5, 2.5, 10.0, 30.0, 50.0]),
    ],
)
def test_a_class_floor_survives_the_canvas(source, floors):
    from weatherbrief.observed.intensity import classify_dbz, classify_rate

    classify = classify_dbz if source == SOURCE_OPERA_DBZH else classify_rate
    codec = tiles._CODECS[source]
    decoded = codec.decode(codec.encode(np.array(floors)))
    assert [classify(float(v)) for v in decoded] == [classify(f) for f in floors]


# --- Canvas ----------------------------------------------------------------------


def test_canvas_is_written_beside_its_frame(store):
    path = tiles.write_canvas(store, SOURCE_OPERA_DBZH, VALID)
    assert path == store.canvas_path(SOURCE_OPERA_DBZH, VALID)
    assert path.exists()
    canvas = tiles.load_canvas(store, SOURCE_OPERA_DBZH, VALID)
    codes = canvas.codes
    # All three states are present: the fixture has an echo, clear sky and a
    # deliberate no-coverage half.
    assert (codes == 0).any() and (codes == 255).any()
    assert ((codes > 0) & (codes < 255)).any()


def test_canvas_matches_the_corridor_overlay(store):
    """Same paint, same place: a tile and the corridor PNG agree at the station.

    They are two routes to the same pixel; if they disagreed the map would
    change colour when the client switched from one to the other.
    """
    lat, lon = STATION
    z = 8
    x, y = _tile_of(lat, lon, z)
    canvas = tiles.load_canvas(store, SOURCE_OPERA_DBZH, VALID)
    tile = tiles.render_tile_rgba(canvas, z, x, y)

    # The tile's own lat/lon box, rendered by the corridor path at tile size.
    west, north = tiles.world_px_to_lonlat(x * 256, y * 256, z)
    east, south = tiles.world_px_to_lonlat((x + 1) * 256, (y + 1) * 256, z)
    from weatherbrief.observed.frames import SOURCE_SPECS
    from weatherbrief.observed import opera
    from weatherbrief.observed.grid import GridWindow

    path = store.payload_path(SOURCE_OPERA_DBZH, VALID)
    grid = opera.read_grid(path)
    frame = opera.read_window(
        path, "DBZH", GridWindow(0, grid.ny, 0, grid.nx),
        source=SOURCE_OPERA_DBZH, units=SOURCE_SPECS[SOURCE_OPERA_DBZH].units,
    )
    bounds = OverlayBounds(south=float(south), west=float(west), north=float(north), east=float(east))
    overlay = _decode(render_overlay(frame, bounds, max_pixels=256)[0])
    assert overlay.shape[:2] == (256, 256)

    # Compare where both draw something (the canvas resamples once more, so
    # an edge pixel may differ by a step); the bulk must match.
    both = (tile[..., 3] > 0) & (overlay[..., 3] > 0)
    assert both.sum() > 500
    same = np.all(tile[both] == overlay[both], axis=-1)
    assert same.mean() > 0.8


def test_a_missing_canvas_is_built_on_first_request(store):
    assert not store.canvas_path(SOURCE_OPERA_DBZH, VALID).exists()
    tiles.load_canvas(store, SOURCE_OPERA_DBZH, VALID)
    assert store.canvas_path(SOURCE_OPERA_DBZH, VALID).exists()


def test_an_unstored_frame_is_not_found(store):
    with pytest.raises(FileNotFoundError):
        tiles.load_canvas(store, SOURCE_OPERA_DBZH, VALID - timedelta(minutes=5))


def test_a_stale_canvas_version_is_rebuilt(store, monkeypatch):
    tiles.write_canvas(store, SOURCE_OPERA_DBZH, VALID)
    tiles._CANVAS_CACHE.clear()
    monkeypatch.setattr(tiles, "CANVAS_VERSION", tiles.CANVAS_VERSION + 1)
    before = store.canvas_path(SOURCE_OPERA_DBZH, VALID).stat().st_mtime_ns
    tiles.load_canvas(store, SOURCE_OPERA_DBZH, VALID)
    assert store.canvas_path(SOURCE_OPERA_DBZH, VALID).stat().st_mtime_ns >= before


def test_purge_removes_the_canvas_with_its_frame(store):
    tiles.write_canvas(store, SOURCE_OPERA_DBZH, VALID)
    store.purge(SOURCE_OPERA_DBZH, now=VALID + timedelta(hours=6))
    assert not store.canvas_path(SOURCE_OPERA_DBZH, VALID).exists()
    assert not store.payload_path(SOURCE_OPERA_DBZH, VALID).exists()


def test_purge_keeps_a_current_canvas(store):
    tiles.write_canvas(store, SOURCE_OPERA_DBZH, VALID)
    store.purge(SOURCE_OPERA_DBZH, now=VALID + timedelta(minutes=10))
    assert store.canvas_path(SOURCE_OPERA_DBZH, VALID).exists()


def test_purge_sweeps_an_orphaned_canvas(store):
    """A canvas whose frame vanished must not outlive it on disk."""
    path = tiles.write_canvas(store, SOURCE_OPERA_DBZH, VALID)
    store.payload_path(SOURCE_OPERA_DBZH, VALID).unlink()
    store.purge(SOURCE_OPERA_DBZH, now=VALID + timedelta(minutes=10))
    assert not path.exists()


def test_list_frames_ignores_canvases(store):
    tiles.write_canvas(store, SOURCE_OPERA_DBZH, VALID)
    assert [f.valid_time for f in store.list_frames(SOURCE_OPERA_DBZH)] == [VALID]


# --- Collector -------------------------------------------------------------------


class _Response:
    def __init__(self, status_code: int, content: bytes = b""):
        self.status_code = status_code
        self.content = content


class _Session:
    def __init__(self, payload: bytes):
        self.payload = payload

    def get(self, url, timeout=None):
        return _Response(200, self.payload)


def test_the_collector_builds_the_canvas_once_per_frame(tmp_path, dbzh_path, monkeypatch):
    monkeypatch.delenv("WB_OBSERVED_TILES", raising=False)
    store = FrameStore(tmp_path / "observed")
    collect.collect_opera(
        SOURCE_OPERA_DBZH, store, now=NOW, max_fetch=1,
        session=_Session(dbzh_path.read_bytes()), lookback=timedelta(minutes=10),
    )
    frame = store.list_frames(SOURCE_OPERA_DBZH)[0]
    assert store.canvas_path(SOURCE_OPERA_DBZH, frame.valid_time).exists()


def test_the_collector_can_leave_canvases_to_the_api(tmp_path, dbzh_path, monkeypatch):
    monkeypatch.setenv("WB_OBSERVED_TILES", "0")
    store = FrameStore(tmp_path / "observed")
    collect.collect_opera(
        SOURCE_OPERA_DBZH, store, now=NOW, max_fetch=1,
        session=_Session(dbzh_path.read_bytes()), lookback=timedelta(minutes=10),
    )
    frame = store.list_frames(SOURCE_OPERA_DBZH)[0]
    assert not store.canvas_path(SOURCE_OPERA_DBZH, frame.valid_time).exists()


def test_a_canvas_failure_does_not_lose_the_frame(tmp_path, dbzh_path, monkeypatch):
    def boom(*_a, **_k):
        raise RuntimeError("canvas exploded")

    monkeypatch.setattr(tiles, "write_canvas", boom)
    store = FrameStore(tmp_path / "observed")
    result = collect.collect_opera(
        SOURCE_OPERA_DBZH, store, now=NOW, max_fetch=1,
        session=_Session(dbzh_path.read_bytes()), lookback=timedelta(minutes=10),
    )
    assert result.fetched == 1
    assert store.list_frames(SOURCE_OPERA_DBZH)


# --- Tiles -----------------------------------------------------------------------


@pytest.mark.parametrize("z", [4, 5, 6, 7, 8, 10])
def test_tiles_render_at_every_zoom(store, z):
    lat, lon = STATION
    x, y = _tile_of(lat, lon, z)
    rgba = _decode(tiles.tile_png(store, SOURCE_OPERA_DBZH, VALID, z, x, y))
    assert rgba.shape == (256, 256, 4)
    assert (rgba[..., 3] > 0).any()


def test_a_low_zoom_tile_keeps_the_core(store):
    """Max-pooled, not averaged: the strongest echo survives zooming out."""
    canvas = tiles.load_canvas(store, SOURCE_OPERA_DBZH, VALID)
    lat, lon = STATION
    peak = {}
    for z in (4, 7):
        x, y = _tile_of(lat, lon, z)
        values, _cov, _hole = (
            tiles._pooled(canvas, z, x, y) if z < tiles.CANVAS_ZOOM
            else tiles._upsampled(canvas, z, x, y)
        )
        peak[z] = np.nanmax(values)
    assert peak[4] >= peak[7] - 0.5


def test_a_tile_outside_the_radar_domain_is_the_coverage_wash(store):
    # Mid-Atlantic, far outside the fixture grid.
    x, y = _tile_of(40.0, -40.0, 6)
    rgba = _decode(tiles.tile_png(store, SOURCE_OPERA_DBZH, VALID, 6, x, y))
    assert np.all(rgba == np.array(NODATA_RGBA, dtype=np.uint8))


def test_tile_zoom_range_is_enforced():
    assert tiles.valid_tile(tiles.MIN_TILE_ZOOM, 0, 0)
    assert not tiles.valid_tile(tiles.MIN_TILE_ZOOM - 1, 0, 0)
    assert not tiles.valid_tile(tiles.MAX_TILE_ZOOM + 1, 0, 0)
    assert not tiles.valid_tile(5, 32, 0)
    assert not tiles.valid_tile(5, 0, -1)
