"""Radar-DPC fill of the OPERA Italy gap (#923).

Synthetic throughout: a small ODIM composite over Rome that is ``nodata``
except for a strip OPERA does cover, and a full-size DPC GeoTIFF built with
the published grid.  The network is faked; the API itself is unreachable from
CI and is covered by ``test_collect_live.py`` only when enabled.
"""

from __future__ import annotations

import io
import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pytest

from weatherbrief.observed import collect, dpc, opera
from weatherbrief.observed.frames import SOURCE_OPERA_DBZH, SOURCE_SPECS, FrameStore
from weatherbrief.observed.grid import GridWindow

OPERA_PROJ4 = (
    "+proj=laea +lat_0=55.0 +lon_0=10.0 +x_0=1950000.0 +y_0=-2100000.0 "
    "+units=m +ellps=WGS84 +no_defs"
)
SCALE = 2000.0
SIZE = 100  # 200 km square around Rome

# LIRA (Roma Urbe): one of the 14 Italian fields OPERA leaves dark.
LIRA = (41.952, 12.499)

VALID = datetime(2026, 10, 24, 18, 35, tzinfo=timezone.utc)

# The strip of columns OPERA itself covers (Corsica / Adriatic stand-in).
OPERA_COLS = slice(SIZE - 10, SIZE)
OPERA_DBZ = 30.0


@pytest.fixture(autouse=True)
def _dpc_on(monkeypatch):
    monkeypatch.setenv("WB_OBSERVED_DPC", "1")
    monkeypatch.delenv("WB_OBSERVED_DPC_DBZH_PRODUCT", raising=False)


def _write_opera(path) -> None:
    import h5py
    from pyproj import CRS, Transformer

    crs = CRS.from_proj4(OPERA_PROJ4)
    fwd = Transformer.from_crs(CRS.from_epsg(4326), crs, always_xy=True)
    inv = Transformer.from_crs(crs, CRS.from_epsg(4326), always_xy=True)
    cx, cy = fwd.transform(LIRA[1], LIRA[0])
    half = SIZE * SCALE / 2.0
    ul_lon, ul_lat = inv.transform(cx - half, cy + half)

    gain, offset = 0.5, -32.0
    raw = np.full((SIZE, SIZE), 255, dtype=np.uint8)  # nodata
    raw[:, OPERA_COLS] = int(round((OPERA_DBZ - offset) / gain))
    raw[:5, OPERA_COLS] = 0  # a little OPERA undetect too

    with h5py.File(str(path), "w") as handle:
        what = handle.create_group("what")
        what.attrs["date"] = np.bytes_(b"20261024")
        what.attrs["time"] = np.bytes_(b"183500")
        what.attrs["source"] = np.bytes_(b"ORG:247")
        where = handle.create_group("where")
        where.attrs["projdef"] = np.bytes_(OPERA_PROJ4.encode())
        where.attrs["xsize"] = np.int64(SIZE)
        where.attrs["ysize"] = np.int64(SIZE)
        where.attrs["xscale"] = np.float64(SCALE)
        where.attrs["yscale"] = np.float64(SCALE)
        where.attrs["UL_lon"] = np.float64(ul_lon)
        where.attrs["UL_lat"] = np.float64(ul_lat)
        how = handle.create_group("how")
        how.attrs["nodes"] = np.bytes_(b"'frbor','sialb'")
        how.attrs["license"] = np.bytes_(b"CC BY 4.0")
        dataset = handle.create_group("dataset1")
        dwhat = dataset.create_group("what")
        dwhat.attrs["startdate"] = np.bytes_(b"20261024")
        dwhat.attrs["starttime"] = np.bytes_(b"182500")
        dwhat.attrs["enddate"] = np.bytes_(b"20261024")
        dwhat.attrs["endtime"] = np.bytes_(b"183500")
        data = dataset.create_group("data1")
        dgroup = data.create_group("what")
        dgroup.attrs["quantity"] = np.bytes_(b"DBZH")
        dgroup.attrs["gain"] = np.float64(gain)
        dgroup.attrs["offset"] = np.float64(offset)
        dgroup.attrs["nodata"] = np.float64(255.0)
        dgroup.attrs["undetect"] = np.float64(0.0)
        data.create_dataset("data", data=raw)


def _dpc_field() -> np.ndarray:
    """Undetect everywhere, a 40 dBZ cell on LIRA, no coverage west of 12.0°E."""
    grid = dpc.expected_grid()
    lon, lat = grid.mesh_lonlat(0, grid.ny, 0, grid.nx)
    field = np.full((grid.ny, grid.nx), dpc.DPC_UNDETECT, dtype=np.float32)
    near = (np.abs(lat - LIRA[0]) < 0.1) & (np.abs(lon - LIRA[1]) < 0.1)
    field[near] = 40.0
    field[lon < 12.0] = dpc.DPC_NODATA
    return field


def _tiff(values: np.ndarray, *, point: bool = False, ul=(dpc.DPC_UL_X, dpc.DPC_UL_Y),
          compression: str | None = "tiff_adobe_deflate") -> bytes:
    from PIL import Image, TiffImagePlugin

    ifd = TiffImagePlugin.ImageFileDirectory_v2()
    ifd[33550] = (dpc.DPC_PIXEL_M, dpc.DPC_PIXEL_M, 0.0)
    ifd.tagtype[33550] = 12
    x, y = ul
    if point:  # tie point on the centre of pixel (0, 0)
        x, y = x + dpc.DPC_PIXEL_M / 2, y - dpc.DPC_PIXEL_M / 2
    ifd[33922] = (0.0, 0.0, 0.0, x, y, 0.0)
    ifd.tagtype[33922] = 12
    ifd[34735] = (1, 1, 0, 1, 1025, 0, 1, 2 if point else 1)
    ifd.tagtype[34735] = 3
    buf = io.BytesIO()
    Image.fromarray(values.astype(np.float32), mode="F").save(
        buf, format="TIFF", tiffinfo=ifd, compression=compression
    )
    return buf.getvalue()


@pytest.fixture(scope="module")
def dpc_tiff() -> bytes:
    return _tiff(_dpc_field())


@pytest.fixture
def frame_path(tmp_path, dpc_tiff):
    path = tmp_path / "opera_dbzh" / "20261024T1835.h5"
    path.parent.mkdir()
    _write_opera(path)
    dpc.fill_path(path).write_bytes(dpc_tiff)
    return path


def _full(path):
    grid = opera.read_grid(path)
    return grid, opera.read_window(
        path, "DBZH", GridWindow(0, grid.ny, 0, grid.nx), source=SOURCE_OPERA_DBZH, units="dBZ"
    )


def _pixel(grid, lat, lon):
    col, row = grid.lonlat_to_colrow(lon, lat)
    return int(round(row)), int(round(col))


# --- GeoTIFF -----------------------------------------------------------------


@pytest.mark.parametrize("compression", [None, "tiff_adobe_deflate", "tiff_lzw"])
def test_decode_reads_values_and_the_published_grid(compression):
    values = np.full((dpc.DPC_NY, dpc.DPC_NX), dpc.DPC_UNDETECT, dtype=np.float32)
    values[700, 600] = 42.5
    frame = dpc.decode_tiff(_tiff(values, compression=compression))
    assert frame.values[700, 600] == pytest.approx(42.5)
    assert frame.grid == dpc.expected_grid()
    # Pixel centre of (0, 0) is half a pixel inside the corner.
    assert frame.grid.x0 == dpc.DPC_UL_X + 500.0
    assert frame.grid.y0 == dpc.DPC_UL_Y - 500.0


def test_pixel_is_point_tie_lands_on_the_same_grid():
    values = np.zeros((dpc.DPC_NY, dpc.DPC_NX), dtype=np.float32)
    assert dpc.decode_tiff(_tiff(values, point=True)).grid == dpc.expected_grid()


def test_a_recut_grid_is_refused_not_reprojected_on_a_guess():
    values = np.zeros((dpc.DPC_NY, dpc.DPC_NX), dtype=np.float32)
    with pytest.raises(ValueError, match="differs from the published grid"):
        dpc.decode_tiff(_tiff(values, ul=(dpc.DPC_UL_X - 2000.0, dpc.DPC_UL_Y)))
    with pytest.raises(ValueError, match="expected 1200x1400"):
        dpc.decode_tiff(_tiff(np.zeros((100, 100), dtype=np.float32)))


# --- Fill --------------------------------------------------------------------


def test_fill_writes_only_into_opera_nodata(frame_path):
    grid, frame = _full(frame_path)

    # LIRA was dark in OPERA; DPC's cell now shows there.
    r, c = _pixel(grid, *LIRA)
    assert not frame.nodata[r, c]
    assert frame.values[r, c] == pytest.approx(40.0)

    # OPERA stays authoritative where it has data, even where DPC disagrees.
    strip = frame.values[5:, OPERA_COLS]
    assert np.allclose(strip, OPERA_DBZ)
    assert frame.undetect[:5, OPERA_COLS].all()

    # DPC "undetect" becomes undetect, never a value or a gap.
    east = _pixel(grid, LIRA[0] - 0.5, 12.8)
    assert frame.undetect[east] and not frame.nodata[east]

    # Outside DPC coverage (west of 12°E here) it is still nodata, not clear.
    west = _pixel(grid, LIRA[0], 11.6)
    assert frame.nodata[west] and not frame.undetect[west]

    assert frame.aux["dpc_filled"] > 0
    assert "Radar-DPC" in frame.attribution.text


def test_fill_is_off_without_the_gate(frame_path, monkeypatch):
    monkeypatch.setenv("WB_OBSERVED_DPC", "0")
    grid, frame = _full(frame_path)
    r, c = _pixel(grid, *LIRA)
    assert frame.nodata[r, c]
    assert "dpc_filled" not in frame.aux
    assert "Radar-DPC" not in frame.attribution.text


def test_no_paired_file_means_no_fill(frame_path):
    dpc.fill_path(frame_path).unlink()
    grid, frame = _full(frame_path)
    assert frame.nodata[_pixel(grid, *LIRA)]


def test_a_corrupt_fill_costs_the_fill_not_the_frame(frame_path):
    dpc.fill_path(frame_path).write_bytes(b"not a tiff")
    grid, frame = _full(frame_path)
    assert frame.nodata[_pixel(grid, *LIRA)]
    assert np.allclose(frame.values[5:, OPERA_COLS], OPERA_DBZ)


def test_windowed_read_and_row_blocks_agree_with_the_full_read(frame_path):
    grid, full = _full(frame_path)
    window = GridWindow(20, 70, 15, 95)
    part = opera.read_window(frame_path, "DBZH", window, source=SOURCE_OPERA_DBZH, units="dBZ")
    np.testing.assert_array_equal(part.nodata, full.nodata[20:70, 15:95])
    np.testing.assert_array_equal(part.undetect, full.undetect[20:70, 15:95])
    np.testing.assert_array_equal(part.values, full.values[20:70, 15:95])

    rows = []
    for _row0, values, nodata, undetect in opera.iter_row_blocks(frame_path, "DBZH", block_rows=33):
        rows.append((values, nodata, undetect))
    np.testing.assert_array_equal(np.vstack([r[1] for r in rows]), full.nodata)
    np.testing.assert_array_equal(np.vstack([r[2] for r in rows]), full.undetect)
    np.testing.assert_array_equal(np.vstack([r[0] for r in rows]), full.values)


# --- Slot pairing --------------------------------------------------------------


def test_pairs_the_newest_dpc_slot_inside_the_opera_window():
    window = timedelta(minutes=10)
    # DPC 18:35 not out yet (newest 18:30): take 18:30, inside DBZH's 10 min.
    assert dpc.pick_slot(VALID, VALID - timedelta(minutes=5), window) == VALID - timedelta(minutes=5)
    # Backfill: DPC already has later frames, take the OPERA slot itself.
    assert dpc.pick_slot(VALID, VALID + timedelta(minutes=20), window) == VALID
    # DPC stalled 15 min back: outside the window, no fill.
    assert dpc.pick_slot(VALID, VALID - timedelta(minutes=15), window) is None


def test_product_defaults_and_override(monkeypatch):
    assert dpc.product_for("DBZH") == "VMI"
    assert dpc.product_for("RATE") == "SRI"
    monkeypatch.setenv("WB_OBSERVED_DPC_DBZH_PRODUCT", "cappi2")
    assert dpc.product_for("DBZH") == "CAPPI2"


# --- Collector -------------------------------------------------------------------


class _Resp:
    def __init__(self, status=200, content=b"", payload=None):
        self.status_code = status
        self.content = content
        self._payload = payload
        self.text = json.dumps(payload) if payload is not None else ""

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class _Session:
    """OPERA S3 + the three DPC calls, all faked."""

    def __init__(self, opera_bytes, dpc_bytes, *, dpc_latest, dpc_fail=False):
        self.opera_bytes = opera_bytes
        self.dpc_bytes = dpc_bytes
        self.latest_ms = int(dpc_latest.timestamp() * 1000)
        self.dpc_fail = dpc_fail
        self.posted = []

    def get(self, url, params=None, headers=None, timeout=None):
        if "findLastProductByType" in url:
            if self.dpc_fail:
                return _Resp(status=503)
            assert headers and headers.get("origin")
            return _Resp(payload={"lastProducts": [{"time": self.latest_ms, "period": "PT5M"}]})
        if url.startswith("https://signed.example/"):
            return _Resp(content=self.dpc_bytes)
        if "OPERA@20261024T1835@0@DBZH" in url:
            return _Resp(content=self.opera_bytes)
        return _Resp(status=404)

    def post(self, url, json=None, headers=None, timeout=None):
        self.posted.append(json)
        return _Resp(payload={"url": "https://signed.example/VMI/24-10-2026-18-30.tif"})


def _collect(tmp_path, session):
    store = FrameStore(tmp_path / "observed")
    collect.collect_opera(
        SOURCE_OPERA_DBZH, store, now=VALID + timedelta(minutes=5),
        lookback=timedelta(0), session=session,
    )
    return store


@pytest.fixture
def opera_bytes(tmp_path):
    path = tmp_path / "src.h5"
    _write_opera(path)
    return path.read_bytes()


def test_collector_pairs_and_publishes_the_fill(tmp_path, opera_bytes, dpc_tiff, monkeypatch):
    monkeypatch.setattr("weatherbrief.observed.tiles.tiles_enabled", lambda: False)
    session = _Session(opera_bytes, dpc_tiff, dpc_latest=VALID - timedelta(minutes=5))
    store = _collect(tmp_path, session)

    frame = store.latest(SOURCE_OPERA_DBZH)
    assert frame is not None
    assert store.fill_path(SOURCE_OPERA_DBZH, VALID).exists()
    assert frame.meta["fill"]["product"] == "VMI"
    assert frame.meta["fill"]["offset_minutes"] == 5.0
    assert "Radar-DPC" in frame.meta["attribution"]["text"]
    assert session.posted == [
        {"productType": "VMI", "productDate": int((VALID - timedelta(minutes=5)).timestamp() * 1000)}
    ]


def test_dpc_down_publishes_the_opera_frame_unfilled(tmp_path, opera_bytes, dpc_tiff, monkeypatch):
    monkeypatch.setattr("weatherbrief.observed.tiles.tiles_enabled", lambda: False)
    session = _Session(opera_bytes, dpc_tiff, dpc_latest=VALID, dpc_fail=True)
    store = _collect(tmp_path, session)

    frame = store.latest(SOURCE_OPERA_DBZH)
    assert frame is not None
    assert "fill" not in frame.meta
    assert not store.fill_path(SOURCE_OPERA_DBZH, VALID).exists()


def test_collector_leaves_dpc_alone_when_gated_off(tmp_path, opera_bytes, dpc_tiff, monkeypatch):
    monkeypatch.setenv("WB_OBSERVED_DPC", "0")
    monkeypatch.setattr("weatherbrief.observed.tiles.tiles_enabled", lambda: False)
    session = _Session(opera_bytes, dpc_tiff, dpc_latest=VALID)
    store = _collect(tmp_path, session)
    assert store.latest(SOURCE_OPERA_DBZH) is not None
    assert session.posted == []


def test_purge_takes_the_fill_with_its_frame(tmp_path, frame_path):
    store = FrameStore(tmp_path)
    store.write_sidecar(SOURCE_OPERA_DBZH, VALID, {"attribution": {}})
    fill = store.fill_path(SOURCE_OPERA_DBZH, VALID)
    assert fill.exists()
    store.purge(SOURCE_OPERA_DBZH, now=VALID + SOURCE_SPECS[SOURCE_OPERA_DBZH].retention + timedelta(minutes=5))
    assert not fill.exists()
    assert not frame_path.exists()
