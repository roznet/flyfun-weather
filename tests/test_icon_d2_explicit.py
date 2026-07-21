"""ICON-D2 explicit-convection meteorology and decode semantics (#462)."""

from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
import pytest

from weatherbrief.analysis.sounding.convective import (
    assess_convective_explicit,
    convective_cross_check,
)
from weatherbrief.fetch.grib.decode import (
    _corridor_extrema,
    _deaccumulate_nonnegative_grid,
    _hourly_echo_min_pressure_grid,
)
from weatherbrief.fetch.grib import _pressure_pa_to_column_altitude_ft
from weatherbrief.models import (
    ConvectiveAssessment,
    ConvectiveRisk,
    HourlyForecast,
    NWPCloudDiagnostics,
    NWPExplicitConvectiveDiagnostics,
    PressureLevelData,
    ThermodynamicIndices,
)


def _diag(dbz: float | None, **updates) -> NWPExplicitConvectiveDiagnostics:
    values = {
        "reflectivity_hour_max_dbz": dbz,
        "detection_complete": True,
    }
    values.update(updates)
    return NWPExplicitConvectiveDiagnostics(**values)


@pytest.mark.parametrize(
    ("dbz", "lpi", "updraft", "graupel", "cape", "expected"),
    [
        (34.9, None, None, None, None, ConvectiveRisk.NONE),
        (40.0, None, None, None, None, ConvectiveRisk.NONE),
        (40.0, 1.0, None, None, None, ConvectiveRisk.MARGINAL),
        (40.0, 1.0, 10.0, None, None, ConvectiveRisk.MODERATE),
        (47.0, None, None, None, None, ConvectiveRisk.MODERATE),
        (47.0, 5.0, None, None, None, ConvectiveRisk.HIGH),
        (50.0, None, None, None, None, ConvectiveRisk.HIGH),
        (40.0, None, None, None, 500.0, ConvectiveRisk.MARGINAL),
    ],
)
def test_v1_decision_table(dbz, lpi, updraft, graupel, cape, expected):
    result = assess_convective_explicit(
        ThermodynamicIndices(),
        _diag(
            dbz,
            lightning_potential_hour_max_jkg=lpi,
            updraft_hour_max_ms=updraft,
            graupel_hour_mm=graupel,
        ),
        NWPCloudDiagnostics(ml_cape_jkg=cape) if cape is not None else None,
    )
    assert result is not None
    assert result.risk_level is expected
    assert result.method == "nwp_explicit"


def test_incomplete_detection_is_unavailable_not_quiet():
    result = assess_convective_explicit(
        ThermodynamicIndices(cape_surface_jkg=1500.0),
        NWPExplicitConvectiveDiagnostics(
            reflectivity_hour_max_dbz=55.0,
            detection_complete=False,
        ),
    )
    assert result is None


def test_echo_top_is_detail_never_clearance_geometry():
    result = assess_convective_explicit(
        ThermodynamicIndices(),
        _diag(50.0, echo_top_18dbz_ft=28000.0, echo_top_complete=True),
    )
    assert result is not None
    assert result.echo_top_18dbz_ft == 28000.0
    assert result.detection_complete is True
    assert result.echo_top_complete is True
    assert result.top_ft is None
    assert result.base_ft is None


def test_cross_check_understands_explicit_active_and_quiet():
    thermo_quiet = ConvectiveAssessment(
        risk_level=ConvectiveRisk.NONE, method="thermo",
    )
    explicit_active = assess_convective_explicit(
        ThermodynamicIndices(), _diag(50.0),
    )
    assert explicit_active is not None
    xc = convective_cross_check(thermo_quiet, explicit_active)
    assert xc is not None
    assert xc.direction == "model_active_dd_quiet"

    thermo_loaded = ConvectiveAssessment(
        risk_level=ConvectiveRisk.MODERATE, cape_jkg=800.0, method="thermo",
    )
    explicit_quiet = assess_convective_explicit(
        ThermodynamicIndices(), _diag(None),
    )
    assert explicit_quiet is not None
    xc = convective_cross_check(thermo_loaded, explicit_quiet)
    assert xc is not None
    assert xc.direction == "dd_not_corroborated"


def test_corridor_max_catches_off_centre_cell_and_retains_uh_sign():
    lats = np.array([49.94, 49.98, 50.00, 50.02, 50.06])
    lons = np.array([7.94, 7.98, 8.00, 8.02, 8.06])
    field = np.zeros((5, 5))
    field[3, 3] = 48.0
    values, complete = _corridor_extrema(
        field, lats, lons, [50.0], [8.0], radius_nm=2.0, mode="max",
    )
    assert complete == [True]
    assert values == [48.0]

    uh = np.zeros((5, 5))
    uh[1:3, 1:3] = np.array([[10.0, -35.0], [20.0, 30.0]])
    values, _ = _corridor_extrema(
        uh, lats, lons, [50.0], [8.0],
        radius_nm=2.0, mode="abs_signed_max",
    )
    assert values == [-35.0]


def test_masked_corridor_is_incomplete_not_a_partial_maximum():
    grid = np.ones((4, 4)) * 20.0
    grid[1, 1] = np.nan
    grid[2, 2] = 45.0
    values, complete = _corridor_extrema(
        grid,
        np.array([49.96, 50.0, 50.02, 50.06]),
        np.array([7.96, 8.0, 8.02, 8.06]),
        [50.01], [8.01], radius_nm=2.0, mode="max",
    )
    assert complete == [False]
    assert values == [None]


def test_corridor_clipped_by_outer_grid_edge_is_incomplete():
    values, complete = _corridor_extrema(
        np.ones((3, 3)),
        np.array([50.0, 50.1, 50.2]),
        np.array([8.0, 8.1, 8.2]),
        [50.0],
        [8.1],
        radius_nm=10.0,
        mode="max",
    )
    assert complete == [False]
    assert values == [None]


def test_graupel_is_deaccumulated_before_corridor_maximum():
    previous = np.array([[10.0, 0.0]])
    current = np.array([[10.0, 5.0]])
    increment = _deaccumulate_nonnegative_grid(current, previous)
    assert increment.tolist() == [[0.0, 5.0]]
    # Differencing the two corridor maxima would incorrectly yield zero.
    assert float(current.max() - previous.max()) == 0.0
    assert float(increment.max()) == 5.0


def test_hourly_echo_top_uses_four_quarters_and_ignores_no_echo_sentinel():
    quarters = [
        np.array([[-999.0, 70000.0]]),
        np.array([[65000.0, -999.0]]),
        np.array([[60000.0, 68000.0]]),
        np.array([[62000.0, 66000.0]]),
    ]
    hourly = _hourly_echo_min_pressure_grid(quarters)
    assert hourly is not None
    assert hourly.tolist() == [[60000.0, 66000.0]]
    assert _hourly_echo_min_pressure_grid(quarters[:3]) is None


def test_echo_pressure_uses_same_hour_column_height_not_isa():
    hourly = HourlyForecast(
        time=datetime(2026, 7, 21, tzinfo=timezone.utc),
        pressure_levels=[
            PressureLevelData(pressure_hpa=500, geopotential_height_m=6000.0),
            PressureLevelData(pressure_hpa=400, geopotential_height_m=7800.0),
            PressureLevelData(pressure_hpa=300, geopotential_height_m=10100.0),
        ],
    )
    height_ft = _pressure_pa_to_column_altitude_ft(45000.0, hourly)
    assert height_ft is not None
    assert 22000.0 < height_ft < 24000.0
    # Above the sounding slice still follows the top two model-column levels.
    extrapolated_ft = _pressure_pa_to_column_altitude_ft(25000.0, hourly)
    assert extrapolated_ft is not None
    assert extrapolated_ft > 10100.0 * 3.28084
