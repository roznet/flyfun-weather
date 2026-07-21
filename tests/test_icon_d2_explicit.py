"""ICON-D2 explicit-convection meteorology and decode semantics (#462)."""

from __future__ import annotations

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
from weatherbrief.models import (
    ConvectiveAssessment,
    ConvectiveRisk,
    NWPCloudDiagnostics,
    NWPExplicitConvectiveDiagnostics,
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
        # Below firing band
        (34.9, None, None, None, None, ConvectiveRisk.NONE),
        # 35–44 needs storm-process corroboration (not CAPE alone)
        (40.0, None, None, None, None, ConvectiveRisk.NONE),
        (40.0, None, None, None, 500.0, ConvectiveRisk.NONE),
        (40.0, 1.0, None, None, None, ConvectiveRisk.MARGINAL),
        # LPI present → w/graupel are narrative only, not additive |C|
        (40.0, 1.0, 10.0, 0.5, None, ConvectiveRisk.MARGINAL),
        # No LPI → w + graupel may substitute as two counts
        (40.0, None, 10.0, 0.5, None, ConvectiveRisk.MODERATE),
        # Strong LPI alone (≥5) counts as 2
        (40.0, 5.0, None, None, None, ConvectiveRisk.MODERATE),
        # 45–49
        (47.0, None, None, None, None, ConvectiveRisk.MODERATE),
        (47.0, 5.0, None, None, None, ConvectiveRisk.HIGH),
        # ≥50 always HIGH on dbz alone
        (50.0, None, None, None, None, ConvectiveRisk.HIGH),
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
    assert result.risk_level is expected
    assert result.method == "nwp_explicit"
    assert result.native_data_complete is True
    assert result.top_ft is None


def test_incomplete_detection_is_unavailable_not_quiet():
    result = assess_convective_explicit(
        ThermodynamicIndices(cape_surface_jkg=1500.0),
        NWPExplicitConvectiveDiagnostics(
            reflectivity_hour_max_dbz=55.0,
            detection_complete=False,
        ),
    )
    assert result.method == "nwp_explicit"
    assert result.native_data_complete is False
    assert result.risk_level is ConvectiveRisk.NONE
    # Must not look like a quiet scheme to the DD cross-check.
    thermo_loaded = ConvectiveAssessment(
        risk_level=ConvectiveRisk.MODERATE, cape_jkg=800.0, method="thermo",
    )
    assert convective_cross_check(thermo_loaded, result) is None


def test_echo_top_is_detail_never_clearance_geometry():
    result = assess_convective_explicit(
        ThermodynamicIndices(),
        _diag(50.0, echo_top_18dbz_ft=28000.0, echo_top_complete=True),
    )
    assert result.echo_top_18dbz_ft == 28000.0
    assert result.top_ft is None
    assert result.base_ft is None
    assert any("not a cloud top" in d for d in result.drivers)


def test_cape_is_narrative_not_firing_corroborator():
    result = assess_convective_explicit(
        ThermodynamicIndices(),
        _diag(40.0),
        NWPCloudDiagnostics(ml_cape_jkg=1200.0),
    )
    assert result.risk_level is ConvectiveRisk.NONE
    assert any("ML-CAPE" in d for d in result.drivers) or not result.drivers
    # Environment CAPE still surfaces as detail when a storm does fire.
    firing = assess_convective_explicit(
        ThermodynamicIndices(),
        _diag(50.0),
        NWPCloudDiagnostics(ml_cape_jkg=1200.0),
    )
    assert firing.risk_level is ConvectiveRisk.HIGH
    assert any("ML-CAPE" in d for d in firing.drivers)


def test_cross_check_understands_explicit_active_and_quiet():
    thermo_quiet = ConvectiveAssessment(
        risk_level=ConvectiveRisk.NONE, method="thermo",
    )
    explicit_active = assess_convective_explicit(
        ThermodynamicIndices(), _diag(50.0),
    )
    xc = convective_cross_check(thermo_quiet, explicit_active)
    assert xc is not None
    assert xc.direction == "model_active_dd_quiet"

    thermo_loaded = ConvectiveAssessment(
        risk_level=ConvectiveRisk.MODERATE, cape_jkg=800.0, method="thermo",
    )
    explicit_quiet = assess_convective_explicit(
        ThermodynamicIndices(), _diag(None),
    )
    xc = convective_cross_check(thermo_loaded, explicit_quiet)
    assert xc is not None
    assert xc.direction == "dd_not_corroborated"


def test_corridor_max_catches_off_centre_cell_and_retains_uh_sign():
    lats = np.array([49.98, 50.00, 50.02])
    lons = np.array([7.98, 8.00, 8.02])
    field = np.zeros((3, 3))
    field[2, 2] = 48.0
    values, complete = _corridor_extrema(
        field, lats, lons, [50.0], [8.0], radius_nm=2.0, mode="max",
    )
    assert complete == [True]
    assert values == [48.0]

    uh = np.array([[10.0, -35.0], [20.0, 30.0]])
    values, _ = _corridor_extrema(
        uh, lats[:2], lons[:2], [49.99], [7.99],
        radius_nm=2.0, mode="abs_signed_max",
    )
    assert values == [-35.0]


def test_masked_corridor_is_incomplete_not_a_partial_maximum():
    grid = np.array([[20.0, np.nan], [45.0, 30.0]])
    values, complete = _corridor_extrema(
        grid, np.array([50.0, 50.02]), np.array([8.0, 8.02]),
        [50.01], [8.01], radius_nm=2.0, mode="max",
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
