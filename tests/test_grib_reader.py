"""Direct eccodes decoders, pinned against committed fixtures (#674 phase 1).

Each test writes a synthetic GRIB file shaped like the real product (ECMWF
a1/a2 multi-grid files mixing GRIB1 and GRIB2, an ICON model-level blob) and
decodes it. The cfgrib decoders these replaced were the original reference;
they were deleted in #678 after a full-run parity replay on live data, so the
expected output now lives in ``tests/fixtures/grib_reader/*.json``.

The fixtures were generated **through the cfgrib decoders**, in the commit
before they were deleted, so they are the original oracle's values rather than
a snapshot of the replacement's own output. They also carry two earlier
verifications forward: #677's parity replay against prod data (ECMWF oper
20261007T06z, all 109 a1 + 109 a2 steps; ICON-EU 2026100706 f003, all 9
model-level variables), and the #441 check that the vectorised ICON
interpolation matches a per-level ``xarray.interp`` reference — the deleted
cfgrib path that produced the ICON fixtures here was the one that test pinned.

Regenerate after a *deliberate* decode change — and read the diff, because a
changed value here is exactly what this file exists to catch:

    WB_FREEZE_GRIB_FIXTURES=1 pytest tests/test_grib_reader.py

Values are compared with a relative tolerance, not exactly: GRIB packing
quantises values and the packing an eccodes sample picks can shift between
eccodes versions. A real decode regression moves a value by orders of
magnitude or changes which keys exist at all, and the key set IS compared
exactly — that is what pins the series rules, the edge rule and the seam fill.
"""

from __future__ import annotations

import json
import math
import os
from pathlib import Path

import numpy as np
import pytest

eccodes = pytest.importorskip("eccodes")

from weatherbrief.fetch.grib import decode as dec  # noqa: E402
from weatherbrief.fetch.grib.grib_reader import iter_messages  # noqa: E402

from grib_synth import (  # noqa: E402
    EUROPE,
    EUROPE_G2,
    NORDIC,
    US_G1,
    US_G2,
    write as _write,
)

# ---------------------------------------------------------------------------
# Frozen-fixture plumbing
# ---------------------------------------------------------------------------

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "grib_reader"
FREEZE = os.environ.get("WB_FREEZE_GRIB_FIXTURES", "").strip() not in ("", "0")

# Packing quantisation is eccodes-version dependent; a genuine regression is
# never this small. See the module docstring.
_REL_TOL = 1e-6
_ABS_TOL = 1e-9


def _flatten(obj, prefix: str = "") -> dict:
    """Decoded output → {"a|b|c": value}, so a mismatch names its own field."""
    out: dict = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            out.update(_flatten(v, f"{prefix}|{k}" if prefix else str(k)))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            out.update(_flatten(v, f"{prefix}|{i}" if prefix else str(i)))
    elif obj is None:
        out[prefix] = None
    elif isinstance(obj, bool):
        out[prefix] = 1.0 if obj else 0.0
    else:
        out[prefix] = float(obj)
    return out


def _round(v):
    """9 significant digits: well past float32, keeps the fixtures readable."""
    if v is None:
        return None
    return float(f"{v:.9g}")


def check_frozen(name: str, actual) -> None:
    """Compare decoded output against its committed fixture (or rewrite it)."""
    flat = {k: _round(v) for k, v in _flatten(actual).items()}
    path = FIXTURE_DIR / f"{name}.json"
    if FREEZE:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(flat, indent=1, sort_keys=True) + "\n")
        return
    assert path.exists(), (
        f"missing fixture {path} — regenerate with WB_FREEZE_GRIB_FIXTURES=1"
    )
    expected = json.loads(path.read_text())
    missing = sorted(set(expected) - set(flat))[:8]
    added = sorted(set(flat) - set(expected))[:8]
    assert not missing and not added, (
        f"{name}: decoded key set changed. no longer decoded: {missing}; "
        f"newly decoded: {added}"
    )
    for k, exp in expected.items():
        got = flat[k]
        if exp is None or got is None:
            assert exp == got, (k, exp, got)
        else:
            assert math.isclose(got, exp, rel_tol=_REL_TOL, abs_tol=_ABS_TOL), (k, exp, got)


# Route-like targets: inside each grid, near edges, exactly on grid rows and
# columns, in the 59.5–60°N seam (#672), outside every grid, and US points
# (GRIB1 ±180 and GRIB2 0–360 US fields both decode — #673).
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


def test_a2_matches_frozen_output(tmp_path):
    path = tmp_path / "a2.grib"
    _write_a2(path)
    lats, lons = _targets()

    got, covered = dec.decode_ecmwf_pressure_per_point(path, lats, lons)

    check_frozen("a2", {"covered": covered, "points": got})
    assert sum(covered) > 50
    # z at 1 hPa never reaches the sounding (a one-level series is dropped).
    assert all(1 not in pt for pt in got)
    assert all("raw_geopotential_m2_s2" not in f for pt in got for f in pt.values())
    # cc arrives as a fraction and leaves as %.
    ccs = [f["cloud_area_fraction_pct"] for pt in got for f in pt.values()
           if "cloud_area_fraction_pct" in f]
    assert ccs and max(ccs) > 1.5


def test_a2_seam_points_are_bridged_outside_points_are_not(tmp_path):
    """#672: the 59.5–60°N strip is filled from the facing edge rows (ESSA-like
    two-sided, EGPB-like held); points outside every grid stay uncovered."""
    path = tmp_path / "a2.grib"
    _write_a2(path)
    got, covered = dec.decode_ecmwf_pressure_per_point(
        path, [59.65, 59.88, 40.0, 48.0], [17.92, -1.30, -30.0, 11.5],
    )
    assert covered == [True, True, False, True]
    assert got[0][850].keys() == got[3][850].keys()


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


def test_a1_matches_frozen_output(tmp_path):
    path = tmp_path / "a1.grib"
    _write_a1(path)
    lats, lons = _targets()
    # Exactly-on-a-row targets next to the masked band are the pinned edge rule
    # (tested on their own below), so keep them out of the frozen set.
    keep = [i for i, la in enumerate(lats) if not (51.5 <= la <= 53.1)]
    lats = [lats[i] for i in keep]
    lons = [lons[i] for i in keep]

    got, covered = dec.decode_ecmwf_surface_per_point(path, lats, lons)

    check_frozen("a1", {"covered": covered, "points": got})
    assert sum(covered) > 50
    # The Europe GRIB2 grid crosses the meridian: GRIB2 fields decode west of 0.
    west = lats.index(45.25)
    assert "ceiling_m" in got[west] and "ml_cape_jkg" in got[west]


def test_a1_seam_point_gets_grib1_and_grib2_fields(tmp_path):
    """GRIB1 and GRIB2 messages on one ECMWF area hash differently but share
    axes: the seam plan must group them, or a GRIB2-only field (ceil, kx)
    would be dropped at the seam (#672)."""
    path = tmp_path / "a1.grib"
    _write_a1(path)
    got, covered = dec.decode_ecmwf_surface_per_point(path, [59.65], [17.92])
    assert covered == [True]
    assert {"temperature_2m_k", "ceiling_m", "ml_cape_jkg"} <= got[0].keys()


def test_seam_only_route_does_not_unpack_unrelated_grids(tmp_path, monkeypatch):
    """A target next to Europe/Nordic edges unpacks those grids only, not US."""
    from weatherbrief.fetch.grib import grib_reader

    unpacked: list[tuple[int, int]] = []
    orig = grib_reader.GribMessage.values

    def spy(self, grid):
        unpacked.append(grid.shape)
        return orig(self, grid)

    monkeypatch.setattr(grib_reader.GribMessage, "values", spy)
    path = tmp_path / "a1.grib"
    _write_a1(path)
    _, covered = dec.decode_ecmwf_surface_per_point(path, [59.65], [17.92])
    assert covered == [True]
    us_shape = (int(round((US_G1["lat_first"] - US_G1["lat_last"]) / 0.25)) + 1,
                int(round((US_G1["lon_last"] - US_G1["lon_first"]) / 0.25)) + 1)
    assert unpacked and us_shape not in unpacked


def test_a1_us_grib1_and_grib2_fields_decode(tmp_path):
    """#673: the US GRIB2 grid is 0–360 and the GRIB1 one ±180, in one file;
    a ±180 route target gets both."""
    path = tmp_path / "a1.grib"
    _write_a1(path)
    got, covered = dec.decode_ecmwf_surface_per_point(path, [40.64], [-73.78])
    assert covered == [True]
    assert {"temperature_2m_k", "ceiling_m", "ml_cape_jkg", "k_index_c"} <= got[0].keys()


def test_a1_masked_cells_become_missing_not_9999(tmp_path):
    path = tmp_path / "a1.grib"
    _write_a1(path)
    got, _ = dec.decode_ecmwf_surface_per_point(path, [52.3, 48.0], [5.1, 5.1])
    assert "ceiling_m" not in got[0] and "cloud_base_height_m" not in got[0]
    assert "temperature_2m_k" in got[0]
    assert "ceiling_m" in got[1] and got[1]["ceiling_m"] < 9000


def test_zero_weight_masked_corner_does_not_blank(tmp_path):
    """The pinned edge rule (#674): a target exactly on the 51.5°N row, just
    south of the masked band, takes its row's value. xarray's ``.interp``
    (the pre-#674 path) picked the cell on the masked side and returned
    nothing; the gather keeps the value. A target strictly inside a
    masked-neighbour cell stays blank."""
    path = tmp_path / "a1.grib"
    _write_a1(path)
    got, _ = dec.decode_ecmwf_surface_per_point(path, [51.5, 51.55], [5.1, 5.1])
    assert "ceiling_m" in got[0]          # the rule we picked
    assert "ceiling_m" not in got[1]      # strictly inside a masked cell


# ---------------------------------------------------------------------------
# ICON model levels
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


def test_icon_single_var_matches_frozen_output():
    blob = _icon_blob("t", list(range(30, 41)), base=270.0, amp=10.0)
    lats, lons = _targets()

    got = dec._decode_icon_eu_single_var(blob, lats, lons)

    check_frozen("icon_single_var", got)
    assert sorted(got) == list(range(30, 41))
    assert sum(v is not None for v in got[35]) > 50


def test_icon_d2_grid_in_0_360_matches_frozen_output():
    """ICON-D2 model levels go through the same chunked decoder."""
    blob = _icon_blob("t", list(range(40, 46)), base=270.0, amp=10.0,
                      area=ICON_D2, step=0.1)
    lats = [50.0, 48.35, 52.5, 47.0, 55.1]
    lons = [8.5, -3.5, 13.4, -0.13, 20.0]  # incl. west of Greenwich

    got = dec._decode_icon_eu_single_var(blob, lats, lons)

    check_frozen("icon_d2", got)
    assert all(v is not None for v in got[42])


def test_icon_single_level_blob_is_dropped():
    """A one-level series is dropped, as cfgrib's dataset layout dropped it."""
    blob = _icon_blob("t", [40], base=270.0, amp=10.0)
    assert dec._decode_icon_eu_single_var(blob, [48.0], [11.5]) == {}


def test_icon_chunked_end_to_end_matches_frozen_output():
    levels = list(range(30, 41))
    # Model-level pressure decreasing with height (level number increases
    # downward in ICON), plus two sounding variables.
    var_bytes = {
        "p": _icon_blob("pres", levels, base=60000.0, amp=500.0),
        "t": _icon_blob("t", levels, base=270.0, amp=10.0),
        "u": _icon_blob("u", levels, base=5.0, amp=10.0),
    }
    lats, lons = [48.0, 50.3, 75.0], [11.5, 4.4, 10.0]

    got = dec.decode_icon_eu_per_point_chunked(var_bytes, lats, lons)

    check_frozen("icon_chunked", got)


# ---------------------------------------------------------------------------
# Reader details
# ---------------------------------------------------------------------------

def test_reader_axes_match_cfgrib(tmp_path):
    """Axes come from eccodes as cfgrib reads them: scan order, meridian-
    crossing GRIB2 presented as −17.5…39.5, US GRIB2 left in 0–360.

    Still a live cross-check rather than a fixture: cfgrib remains a project
    dependency for the GFS/HRRR/cloud-diag paths, and these axis conventions
    are the contract the whole direct path is built on.
    """
    cfgrib = pytest.importorskip("cfgrib")

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


def test_unreadable_file_returns_empty(tmp_path):
    path = tmp_path / "junk.grib"
    path.write_bytes(b"not a grib file at all")
    data, covered = dec.decode_ecmwf_pressure_per_point(path, [48.0], [11.5])
    assert data == [{}] and covered == [False]
    data, covered = dec.decode_ecmwf_surface_per_point(path, [48.0], [11.5])
    assert data == [{}] and covered == [False]
    assert dec._decode_icon_eu_single_var(b"junk", [48.0], [11.5]) == {}
