"""Direct eccodes decoders vs the cfgrib decoders they replace (#674 phase 1).

Each test writes a synthetic GRIB file shaped like the real product (ECMWF
a1/a2 multi-grid files mixing GRIB1 and GRIB2, an ICON-EU model-level blob),
decodes it through both paths and compares. The cfgrib path is the reference:
the acceptance bar of #674 is the same output, apart from the one pinned edge
rule (a zero-weight corner never blanks a value).
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pytest

eccodes = pytest.importorskip("eccodes")
pytest.importorskip("cfgrib")

from weatherbrief.fetch.grib import decode as dec  # noqa: E402
from weatherbrief.fetch.grib.grib_reader import (  # noqa: E402
    DECODER_ENV,
    iter_messages,
    use_cfgrib_decoder,
)

# ---------------------------------------------------------------------------
# Synthetic GRIB writer
# ---------------------------------------------------------------------------

# ECMWF delivery areas (0.25°), as found in real a1/a2 files.
EUROPE = dict(lat_first=59.5, lat_last=35.0, lon_first=-17.5, lon_last=39.5)
# GRIB2 encodes the same Europe area as 342.5 → 39.5 (crosses the meridian).
EUROPE_G2 = dict(lat_first=59.5, lat_last=35.0, lon_first=342.5, lon_last=39.5)
NORDIC = dict(lat_first=71.5, lat_last=60.0, lon_first=2.5, lon_last=40.0)
US_G1 = dict(lat_first=50.0, lat_last=20.0, lon_first=-128.0, lon_last=-71.0)
US_G2 = dict(lat_first=50.0, lat_last=20.0, lon_first=232.0, lon_last=289.0)


def _field(lats: np.ndarray, lons: np.ndarray, *, base: float, amp: float, seed: int) -> np.ndarray:
    """Smooth, non-linear field plus noise, so bilinear results are non-trivial."""
    rng = np.random.default_rng(seed)
    la, lo = np.meshgrid(lats, lons, indexing="ij")
    smooth = np.sin(np.radians(la) * 7.0) * np.cos(np.radians(lo) * 5.0)
    return base + amp * (smooth + 0.2 * rng.standard_normal(la.shape))


def _write(
    f,
    *,
    sample: str,
    short: str,
    type_of_level: str,
    level: int,
    area: dict,
    step: float = 0.25,
    j_positive: bool = False,
    base: float = 0.0,
    amp: float = 1.0,
    seed: int = 0,
    mask=None,
) -> None:
    """Append one regular_ll message to the open file ``f``.

    ``mask(lats_2d, lons_2d) -> bool array`` marks cells to bitmap out.
    """
    gid = eccodes.codes_grib_new_from_samples(sample)
    try:
        lat_first, lat_last = area["lat_first"], area["lat_last"]
        if j_positive:
            lat_first, lat_last = min(lat_first, lat_last), max(lat_first, lat_last)
        lon_first, lon_last = area["lon_first"], area["lon_last"]
        nj = int(round(abs(lat_last - lat_first) / step)) + 1
        ni = int(round(((lon_last - lon_first) % 360.0) / step)) + 1
        eccodes.codes_set(gid, "typeOfLevel", type_of_level)
        eccodes.codes_set(gid, "level", level)
        eccodes.codes_set(gid, "shortName", short)
        eccodes.codes_set(gid, "Ni", ni)
        eccodes.codes_set(gid, "Nj", nj)
        eccodes.codes_set(gid, "latitudeOfFirstGridPointInDegrees", lat_first)
        eccodes.codes_set(gid, "latitudeOfLastGridPointInDegrees", lat_last)
        eccodes.codes_set(gid, "longitudeOfFirstGridPointInDegrees", lon_first)
        eccodes.codes_set(gid, "longitudeOfLastGridPointInDegrees", lon_last)
        eccodes.codes_set(gid, "iDirectionIncrementInDegrees", step)
        eccodes.codes_set(gid, "jDirectionIncrementInDegrees", step)
        eccodes.codes_set(gid, "jScansPositively", 1 if j_positive else 0)
        lats = np.linspace(lat_first, lat_last, nj)
        lons = lon_first + step * np.arange(ni)
        lons = np.where(lons > 180.0, lons - 360.0, lons)
        values = _field(lats, lons, base=base, amp=amp, seed=seed)
        if mask is not None:
            la, lo = np.meshgrid(lats, lons, indexing="ij")
            m = mask(la, lo)
            eccodes.codes_set(gid, "bitmapPresent", 1)
            eccodes.codes_set(gid, "missingValue", 9999.0)
            values = np.where(m, 9999.0, values)
        eccodes.codes_set_values(gid, values.ravel())
        eccodes.codes_write(gid, f)
    finally:
        eccodes.codes_release(gid)


# Route-like targets: inside each grid, near edges, exactly on grid rows and
# columns, in the 59.5–60°N seam (#672, still uncovered), outside every grid,
# and US points (GRIB2 US fields stay undecoded — #673, out of scope here).
def _targets() -> tuple[list[float], list[float]]:
    rng = np.random.default_rng(42)
    lats = list(rng.uniform(35.2, 59.3, 40)) + list(rng.uniform(60.2, 71.3, 15))
    lons = list(rng.uniform(-17.3, 39.3, 40)) + list(rng.uniform(2.7, 39.8, 15))
    fixed = [
        (48.0, 11.5), (51.75, -0.25), (50.0, 0.0), (45.25, -1.1),  # rows/columns
        (59.5, 10.0), (35.0, 5.0), (48.0, -17.5), (48.0, 39.5),   # grid edges
        (59.65, 17.92), (59.88, -1.30),                            # seam strip
        (40.0, -30.0), (75.0, 10.0),                               # outside
        (40.64, -73.78), (33.94, -118.41),                         # US
        (60.0, 2.5), (71.5, 40.0),                                 # Nordic corners
    ]
    for la, lo in fixed:
        lats.append(la)
        lons.append(lo)
    return lats, lons


def _approx_equal_dicts(a: dict, b: dict, rel: float = 1e-9) -> None:
    assert a.keys() == b.keys()
    for k in a:
        va, vb = a[k], b[k]
        if isinstance(va, dict):
            _approx_equal_dicts(va, vb, rel)
        else:
            assert math.isclose(va, vb, rel_tol=rel, abs_tol=1e-9), (k, va, vb)


@pytest.fixture
def cfgrib_mode(monkeypatch):
    def _set(on: bool) -> None:
        if on:
            monkeypatch.setenv(DECODER_ENV, "cfgrib")
        else:
            monkeypatch.delenv(DECODER_ENV, raising=False)
    return _set


# ---------------------------------------------------------------------------
# ECMWF a2 (pressure levels)
# ---------------------------------------------------------------------------

_A2_LEVELS = [1000, 925, 850, 700, 500, 300]
_A2_VARS = [  # (shortName, base, amp)
    ("t", 260.0, 15.0), ("u", 5.0, 12.0), ("v", -3.0, 10.0), ("r", 60.0, 30.0),
    ("q", 0.004, 0.002), ("w", 0.0, 0.5), ("gh", 3000.0, 200.0),
    ("clwc", 1e-4, 5e-5), ("ciwc", 1e-5, 5e-6), ("cc", 0.4, 0.3),
]


def _write_a2(path: Path) -> None:
    with open(path, "wb") as f:
        for gi, (sample, area) in enumerate((
            ("regular_ll_pl_grib1", EUROPE),
            ("regular_ll_pl_grib1", NORDIC),
            ("regular_ll_pl_grib1", US_G1),
        )):
            for vi, (short, base, amp) in enumerate(_A2_VARS):
                if short == "cc":
                    sample_v = sample.replace("grib1", "grib2")
                    area_v = EUROPE_G2 if area is EUROPE else area
                else:
                    sample_v, area_v = sample, area
                for li, lev in enumerate(_A2_LEVELS):
                    _write(
                        f, sample=sample_v, short=short, type_of_level="isobaricInhPa",
                        level=lev, area=area_v, base=base, amp=amp,
                        seed=1000 * gi + 10 * vi + li,
                    )
            # ECMWF z: the catalogue only offers it at 1 hPa.
            _write(
                f, sample=sample, short="z", type_of_level="isobaricInhPa",
                level=1, area=area, base=470000.0, amp=1000.0, seed=999 + gi,
            )


def test_a2_matches_cfgrib(tmp_path, cfgrib_mode):
    path = tmp_path / "a2.grib"
    _write_a2(path)
    lats, lons = _targets()

    cfgrib_mode(True)
    ref, ref_cov = dec.decode_ecmwf_pressure_per_point(path, lats, lons)
    cfgrib_mode(False)
    got, got_cov = dec.decode_ecmwf_pressure_per_point(path, lats, lons)

    assert got_cov == ref_cov
    assert got == ref  # bit-identical: same float32 values, same gather order
    assert sum(ref_cov) > 50
    # z at 1 hPa never reaches the sounding (dropped by both paths).
    assert all(1 not in pt for pt in got)
    assert all("raw_geopotential_m2_s2" not in f for pt in got for f in pt.values())
    # cc arrives as a fraction and leaves as %.
    ccs = [f["cloud_area_fraction_pct"] for pt in got for f in pt.values()
           if "cloud_area_fraction_pct" in f]
    assert ccs and max(ccs) > 1.5


def test_a2_seam_and_outside_points_stay_uncovered(tmp_path):
    """#672's 59.5–60°N strip is not fixed here: still no data, like today."""
    path = tmp_path / "a2.grib"
    _write_a2(path)
    _, covered = dec.decode_ecmwf_pressure_per_point(
        path, [59.65, 59.88, 40.0, 48.0], [17.92, -1.30, -30.0, 11.5],
    )
    assert covered == [False, False, False, True]


# ---------------------------------------------------------------------------
# ECMWF a1 (surface)
# ---------------------------------------------------------------------------

def _no_cloud_band(la, lo):
    """Bitmap out a band of cells, like ceil's 'no ceiling' cells."""
    return (la > 51.6) & (la < 53.0)


def _write_a1(path: Path) -> None:
    g1 = [("2t", 285.0, 8.0), ("2d", 278.0, 6.0), ("10u", 2.0, 6.0), ("10v", -1.0, 6.0),
          ("tcc", 0.5, 0.4), ("lcc", 0.3, 0.3), ("sp", 98000.0, 1500.0),
          ("vis", 20000.0, 8000.0), ("cbh", 1200.0, 600.0), ("deg0l", 2500.0, 600.0)]
    g2 = [("ceil", 900.0, 400.0), ("mlcape100", 200.0, 300.0), ("kx", 285.0, 10.0)]
    with open(path, "wb") as f:
        for gi, (g1_area, g2_area) in enumerate(((EUROPE, EUROPE_G2), (NORDIC, NORDIC), (US_G1, US_G2))):
            for vi, (short, base, amp) in enumerate(g1):
                _write(
                    f, sample="regular_ll_sfc_grib1", short=short, type_of_level="surface",
                    level=0, area=g1_area, base=base, amp=amp, seed=100 * gi + vi,
                    mask=_no_cloud_band if short == "cbh" else None,
                )
            for vi, (short, base, amp) in enumerate(g2):
                _write(
                    f, sample="regular_ll_sfc_grib2", short=short, type_of_level="surface",
                    level=0, area=g2_area, base=base, amp=amp, seed=100 * gi + 50 + vi,
                    mask=_no_cloud_band if short == "ceil" else None,
                )


def test_a1_matches_cfgrib(tmp_path, cfgrib_mode):
    path = tmp_path / "a1.grib"
    _write_a1(path)
    lats, lons = _targets()
    # Exactly-on-a-row targets next to the masked band are the pinned edge rule
    # (tested on their own below), so keep them out of the parity set.
    keep = [i for i, la in enumerate(lats) if not (51.5 <= la <= 53.1)]
    lats = [lats[i] for i in keep]
    lons = [lons[i] for i in keep]

    cfgrib_mode(True)
    ref, ref_cov = dec.decode_ecmwf_surface_per_point(path, lats, lons)
    cfgrib_mode(False)
    got, got_cov = dec.decode_ecmwf_surface_per_point(path, lats, lons)

    assert got_cov == ref_cov
    for g, r in zip(got, ref):
        _approx_equal_dicts(g, r)
    assert sum(ref_cov) > 50
    # The Europe GRIB2 grid crosses the meridian: GRIB2 fields decode west of 0.
    west = lats.index(45.25)
    assert "ceiling_m" in got[west] and "ml_cape_jkg" in got[west]


def test_a1_us_grib2_fields_still_missing(tmp_path):
    """#673 is not fixed by phase 1: the US GRIB2 grid stays 0–360, as before."""
    path = tmp_path / "a1.grib"
    _write_a1(path)
    got, covered = dec.decode_ecmwf_surface_per_point(path, [40.64], [-73.78])
    assert covered == [True]
    assert "temperature_2m_k" in got[0]
    assert "ceiling_m" not in got[0] and "k_index_c" not in got[0]


def test_a1_masked_cells_become_missing_not_9999(tmp_path):
    path = tmp_path / "a1.grib"
    _write_a1(path)
    got, _ = dec.decode_ecmwf_surface_per_point(path, [52.3, 48.0], [5.1, 5.1])
    assert "ceiling_m" not in got[0] and "cloud_base_height_m" not in got[0]
    assert "temperature_2m_k" in got[0]
    assert "ceiling_m" in got[1] and got[1]["ceiling_m"] < 9000


def test_zero_weight_masked_corner_does_not_blank(tmp_path, cfgrib_mode):
    """The pinned edge rule (#674): a target exactly on the 51.5°N row, just
    south of the masked band, takes its row's value. xarray's ``.interp``
    picked the cell on the masked side and returned nothing; the gather keeps
    the value. A target strictly inside a masked-neighbour cell stays blank."""
    path = tmp_path / "a1.grib"
    _write_a1(path)
    lats, lons = [51.5, 51.55], [5.1, 5.1]

    cfgrib_mode(True)
    ref, _ = dec.decode_ecmwf_surface_per_point(path, lats, lons)
    cfgrib_mode(False)
    got, _ = dec.decode_ecmwf_surface_per_point(path, lats, lons)

    assert "ceiling_m" not in ref[0]          # the old behaviour this changes
    assert "ceiling_m" in got[0]              # the rule we picked
    assert "ceiling_m" not in got[1] and "ceiling_m" not in ref[1]


# ---------------------------------------------------------------------------
# ICON-EU model levels
# ---------------------------------------------------------------------------

ICON_EU = dict(lat_first=29.5, lat_last=70.5, lon_first=-23.5, lon_last=62.5)
# ICON-D2's regular-ll product: DWD writes its western edge in 0–360 (356.06),
# so the axis only reads −3.94…20.34 because eccodes normalises it. Coarsened
# here (0.1° instead of 0.02°) to keep the file small; the edge is what matters.
ICON_D2 = dict(lat_first=43.2, lat_last=58.0, lon_first=356.0, lon_last=20.3)


def _icon_blob(
    short: str, levels: list[int], *, base: float, amp: float,
    area: dict = ICON_EU, step: float = 0.5,
) -> bytes:
    import io
    import tempfile

    with tempfile.NamedTemporaryFile(suffix=".grib2") as tmp:
        with open(tmp.name, "wb") as f:
            for li, lev in enumerate(levels):
                _write(
                    f, sample="regular_ll_sfc_grib2", short=short,
                    type_of_level="generalVerticalLayer", level=lev, area=area,
                    step=step, j_positive=True, base=base, amp=amp, seed=li,
                )
        return io.FileIO(tmp.name).readall()


def test_icon_single_var_matches_cfgrib(cfgrib_mode):
    blob = _icon_blob("t", list(range(30, 41)), base=270.0, amp=10.0)
    lats, lons = _targets()

    cfgrib_mode(True)
    ref = dec._decode_icon_eu_single_var(blob, lats, lons)
    cfgrib_mode(False)
    got = dec._decode_icon_eu_single_var(blob, lats, lons)

    assert got == ref
    assert sorted(got) == list(range(30, 41))
    assert sum(v is not None for v in got[35]) > 50


def test_icon_d2_grid_in_0_360_matches_cfgrib(cfgrib_mode):
    """ICON-D2 model levels go through the same chunked decoder."""
    blob = _icon_blob("t", list(range(40, 46)), base=270.0, amp=10.0,
                      area=ICON_D2, step=0.1)
    lats = [50.0, 48.35, 52.5, 47.0, 55.1]
    lons = [8.5, -3.5, 13.4, -0.13, 20.0]  # incl. west of Greenwich

    cfgrib_mode(True)
    ref = dec._decode_icon_eu_single_var(blob, lats, lons)
    cfgrib_mode(False)
    got = dec._decode_icon_eu_single_var(blob, lats, lons)

    assert got == ref
    assert all(v is not None for v in got[42])


def test_icon_single_level_blob_is_dropped_like_cfgrib(cfgrib_mode):
    blob = _icon_blob("t", [40], base=270.0, amp=10.0)
    cfgrib_mode(True)
    assert dec._decode_icon_eu_single_var(blob, [48.0], [11.5]) == {}
    cfgrib_mode(False)
    assert dec._decode_icon_eu_single_var(blob, [48.0], [11.5]) == {}


def test_icon_chunked_end_to_end_matches_cfgrib(cfgrib_mode):
    levels = list(range(30, 41))
    # Model-level pressure decreasing with height (level number increases
    # downward in ICON), plus two sounding variables.
    def var_bytes():
        return {
            "p": _icon_blob("pres", levels, base=60000.0, amp=500.0),
            "t": _icon_blob("t", levels, base=270.0, amp=10.0),
            "u": _icon_blob("u", levels, base=5.0, amp=10.0),
        }
    lats, lons = [48.0, 50.3, 75.0], [11.5, 4.4, 10.0]

    cfgrib_mode(True)
    ref = dec.decode_icon_eu_per_point_chunked(var_bytes(), lats, lons)
    cfgrib_mode(False)
    got = dec.decode_icon_eu_per_point_chunked(var_bytes(), lats, lons)
    assert got == ref


# ---------------------------------------------------------------------------
# Reader details
# ---------------------------------------------------------------------------

def test_reader_axes_match_cfgrib(tmp_path):
    """Axes come from eccodes as cfgrib reads them: scan order, meridian-
    crossing GRIB2 presented as −17.5…39.5, US GRIB2 left in 0–360."""
    import cfgrib

    path = tmp_path / "axes.grib"
    with open(path, "wb") as f:
        _write(f, sample="regular_ll_sfc_grib2", short="ceil", type_of_level="surface",
               level=0, area=EUROPE_G2)
        _write(f, sample="regular_ll_sfc_grib2", short="kx", type_of_level="surface",
               level=0, area=US_G2, j_positive=True)
    ours = []
    for msg in iter_messages(path):
        grid = msg.grid()
        ours.append((grid.lats, grid.lons, msg.values(grid)))
    theirs = cfgrib.open_datasets(str(path), backend_kwargs={"indexpath": ""})
    assert len(theirs) == len(ours)
    for (la, lo, vals), ds in zip(ours, theirs):
        (da,) = ds.data_vars.values()
        np.testing.assert_array_equal(la, ds.latitude.values)
        np.testing.assert_array_equal(lo, ds.longitude.values)
        np.testing.assert_array_equal(vals, da.values)
    assert ours[0][1][0] == -17.5 and ours[1][1][0] == 232.0
    assert ours[1][0][0] < ours[1][0][-1]  # jScansPositively kept as scanned


def test_decoder_switch(monkeypatch):
    monkeypatch.delenv(DECODER_ENV, raising=False)
    assert not use_cfgrib_decoder()
    monkeypatch.setenv(DECODER_ENV, "eccodes")
    assert not use_cfgrib_decoder()
    monkeypatch.setenv(DECODER_ENV, " CFGRIB ")
    assert use_cfgrib_decoder()


def test_unreadable_file_returns_empty(tmp_path):
    path = tmp_path / "junk.grib"
    path.write_bytes(b"not a grib file at all")
    data, covered = dec.decode_ecmwf_pressure_per_point(path, [48.0], [11.5])
    assert data == [{}] and covered == [False]
    data, covered = dec.decode_ecmwf_surface_per_point(path, [48.0], [11.5])
    assert data == [{}] and covered == [False]
    assert dec._decode_icon_eu_single_var(b"junk", [48.0], [11.5]) == {}
