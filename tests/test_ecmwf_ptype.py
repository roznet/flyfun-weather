"""ECMWF precipitation type (ptype) and freezing rain (fzra), #411 step 2.

Decode (nearest cell, never blended), the briefing write (held over the step,
fzra de-accumulated), and the 0.01 mm/h gate on the model's own type.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import pytest

eccodes = pytest.importorskip("eccodes")

from grib_synth import EUROPE_G2, write  # noqa: E402

from weatherbrief.analysis.sounding.precipitation import (  # noqa: E402
    assess_precipitation,
    model_freezing_type,
)
from weatherbrief.fetch.grib import decode as dec  # noqa: E402
from weatherbrief.fetch.grib.grib_reader import gather_nearest  # noqa: E402
from weatherbrief.models import HourlyForecast  # noqa: E402


def _ptype_checkerboard(lats, lons):
    """Rain (1) and snow (5) on alternate cells: a blend would read 3."""
    la, lo = np.meshgrid(lats, lons, indexing="ij")
    i = np.round((la - 35.0) / 0.25).astype(int)
    j = np.round((lo % 360.0) / 0.25).astype(int)
    return np.where((i + j) % 2 == 0, 1.0, 5.0)


def _write_a1(path):
    with open(path, "wb") as f:
        write(f, sample="regular_ll_sfc_grib2", short="ptype", type_of_level="surface",
              level=0, area=EUROPE_G2, values=_ptype_checkerboard)
        write(f, sample="regular_ll_sfc_grib2", short="fzra", type_of_level="surface",
              level=0, area=EUROPE_G2, base=0.002, amp=0.001, seed=7)
        write(f, sample="regular_ll_sfc_grib2", short="tp", type_of_level="surface",
              level=0, area=EUROPE_G2, base=0.004, amp=0.001, seed=8)


def test_ptype_is_gathered_from_the_nearest_cell_never_blended(tmp_path):
    path = tmp_path / "a1.grib"
    _write_a1(path)
    # Between cells, where a bilinear gather of 1 and 5 lands on 3.
    lats = [48.125, 50.06, 45.2, 52.37]
    lons = [11.125, 2.19, -1.11, 7.01]
    got, covered = dec.decode_ecmwf_surface_per_point(path, lats, lons)
    assert all(covered)
    for pt in got:
        assert pt["precip_type_code"] in (1.0, 5.0)
        assert "freezing_rain_m" in pt
        assert "total_precip_m" in pt


def test_gather_nearest_picks_the_heaviest_corner():
    lat = np.array([50.0, 49.75])
    lon = np.array([10.0, 10.25])
    values = np.array([[1.0, 5.0], [5.0, 1.0]])
    bw = dec._bilinear_grid_weights(lat, lon, np.array([49.99, 49.80]), np.array([10.24, 10.01]))
    out = gather_nearest(values, bw)
    # 49.99/10.24 is nearest (50.0, 10.25) → 5; 49.80/10.01 nearest (49.75, 10.0) → 5.
    assert out.tolist() == [5.0, 5.0]


def _hourly(code, precip, fzra=None):
    return HourlyForecast(
        time=datetime(2026, 1, 10, 10, tzinfo=UTC),
        precip_type_code=code, precipitation_mm=precip, freezing_rain_mm=fzra,
    )


@pytest.mark.parametrize("code, precip, expected", [
    (3, 0.5, "freezing_rain"),
    (12, 0.01, "freezing_drizzle"),   # at the gate
    (8, 0.2, "ice_pellets"),
    (12, 0.009, None),                # below the gate
    (12, None, None),                 # no precip known
    (1, 2.0, None),                   # rain
    (5, 2.0, None),                   # snow
    (None, 2.0, None),                # no type (every non-ECMWF model)
])
def test_model_freezing_type_gate(code, precip, expected):
    assert model_freezing_type(_hourly(code, precip)) == expected


def test_assessment_carries_the_model_type_without_touching_the_phase():
    a = assess_precipitation([], [], hourly=_hourly(12, 0.05, fzra=0.0))
    assert a.model_freezing_type == "freezing_drizzle"
    assert a.model_precip_type_code == 12
    assert a.freezing_rain_mm == 0.0
    assert a.surface_phase.value != "freezing_rain"
