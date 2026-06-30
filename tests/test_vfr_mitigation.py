"""Tests for VFR Feasibility mitigations (issue #328).

A mitigation surfaces a decision that would improve a flagged sub-issue
(fly lower to clear cruise IMC, reposition the climb/descent around a corridor
deck). Mitigations are **advice only** — they never change the advisory grade.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories.vfr_feasibility import VFRFeasibilityEvaluator
from weatherbrief.models import (
    AdvisoryStatus,
    CloudCoverage,
    ElevationPoint,
    ElevationProfile,
    EnhancedCloudLayer,
    Mitigation,
    MitigationKind,
    ModelAdvisoryResult,
    RouteAdvisoryResult,
    RoutePointAnalysis,
    SoundingAnalysis,
    ThermodynamicIndices,
)

# Default VFR params (mirror the catalog defaults).
_VFR_DEFAULTS = {
    "cloud_clearance_ft": 1000,
    "imc_pct_amber": 15,
    "imc_pct_red": 30,
    "terminal_corridor_nm": 5,
}


# ---------------------------------------------------------------------------
# Builders (self-contained — the advisory conftest fixtures live in a
# different test package and are not visible here).
# ---------------------------------------------------------------------------

def _sounding(layers: list[EnhancedCloudLayer] | None = None) -> SoundingAnalysis:
    return SoundingAnalysis(
        indices=ThermodynamicIndices(freezing_level_ft=5000),
        cloud_layers=layers or [],
    )


def _rpa(
    point_index: int,
    distance_nm: float,
    layers_by_model: dict[str, list[EnhancedCloudLayer]],
) -> RoutePointAnalysis:
    return RoutePointAnalysis(
        point_index=point_index,
        lat=48.0 + point_index * 0.1,
        lon=2.0 + point_index * 0.1,
        distance_from_origin_nm=distance_nm,
        interpolated_time=datetime(2026, 3, 1, 10, 0),
        forecast_hour=datetime(2026, 3, 1, 9, 0),
        track_deg=90.0,
        sounding={m: _sounding(layers) for m, layers in layers_by_model.items()},
    )


def _elevation(max_elev_ft: float = 500.0, n_points: int = 20, total_nm: float = 200.0) -> ElevationProfile:
    points = [
        ElevationPoint(
            distance_nm=i * total_nm / (n_points - 1),
            elevation_ft=max_elev_ft,
            lat=48.0 + i * 0.1,
            lon=2.0 + i * 0.1,
        )
        for i in range(n_points)
    ]
    return ElevationProfile(
        route_name="test",
        points=points,
        max_elevation_ft=max_elev_ft,
        total_distance_nm=total_nm,
    )


def _ctx(
    analyses: list[RoutePointAnalysis],
    *,
    elevation: ElevationProfile | None = None,
    models: tuple[str, ...] = ("gfs",),
    cruise_altitude_ft: int = 8000,
    total_distance_nm: float = 200.0,
) -> RouteContext:
    return RouteContext(
        analyses=analyses,
        cross_sections=[],
        elevation=elevation if elevation is not None else _elevation(total_nm=total_distance_nm),
        models=list(models),
        cruise_altitude_ft=cruise_altitude_ft,
        flight_ceiling_ft=18000,
        total_distance_nm=total_distance_nm,
    )


def _mitigations(result: RouteAdvisoryResult, model: str = "gfs") -> list[Mitigation]:
    per = next(m for m in result.per_model if m.model == model)
    return per.mitigations


# ---------------------------------------------------------------------------
# Vertical mitigation (addresses cruise_imc)
# ---------------------------------------------------------------------------

def test_vertical_happy_path():
    """Cruise inside an OVC deck, clear band below well above terrain.

    One ALTITUDE mitigation reporting the highest clear altitude, GREEN, while
    the advisory itself stays RED (the grade is independent of the mitigation).
    """
    # OVC 7000–12000 contains cruise (8000) at every point → IMC everywhere.
    # Highest clear altitude = base 7000 − 1000 clearance = 6000ft (terrain 500).
    deck = EnhancedCloudLayer(base_ft=7000, top_ft=12000, coverage=CloudCoverage.OVC)
    analyses = [_rpa(i, i * 20.0, {"gfs": [deck]}) for i in range(10)]
    result = VFRFeasibilityEvaluator.evaluate(_ctx(analyses), _VFR_DEFAULTS)

    assert result.aggregate_status == AdvisoryStatus.RED  # grade unchanged
    mits = _mitigations(result)
    alt = [m for m in mits if m.kind == MitigationKind.ALTITUDE]
    assert len(alt) == 1
    m = alt[0]
    assert m.addresses == "cruise_imc"
    assert m.altitude_ft == 6000
    assert m.mitigated_status == AdvisoryStatus.GREEN  # axis status, not overall
    assert m.detail  # localized phrasing populated


def test_vertical_blocked_by_terrain():
    """The only clear band sits below the terrain floor → no vertical mitigation."""
    # OVC 5000–12000 contains cruise; terrain 5500 → floor 6500, so every
    # candidate altitude down to the floor is still inside the deck.
    deck = EnhancedCloudLayer(base_ft=5000, top_ft=12000, coverage=CloudCoverage.OVC)
    analyses = [_rpa(i, i * 20.0, {"gfs": [deck]}) for i in range(10)]
    result = VFRFeasibilityEvaluator.evaluate(
        _ctx(analyses, elevation=_elevation(max_elev_ft=5500)), _VFR_DEFAULTS
    )

    assert result.aggregate_status == AdvisoryStatus.RED
    assert not any(m.kind == MitigationKind.ALTITUDE for m in _mitigations(result))


# ---------------------------------------------------------------------------
# Along-route mitigation (addresses climb_deck / descent_deck)
# ---------------------------------------------------------------------------

def test_along_route_climb_happy_path():
    """OVC deck below cruise over the corridor points nearest departure,
    clear beyond → one ROUTE_POSITION climb mitigation."""
    deck = EnhancedCloudLayer(base_ft=3000, top_ft=5000, coverage=CloudCoverage.OVC)
    # Wide corridor (60nm) so points 0,20,40,60 are in the climb corridor.
    # Blocked at 0 & 20nm, clear at 40 & 60nm → climb after ~40nm.
    analyses = [_rpa(i, i * 20.0, {"gfs": [deck] if i in (0, 1) else []}) for i in range(10)]
    result = VFRFeasibilityEvaluator.evaluate(
        _ctx(analyses), {**_VFR_DEFAULTS, "terminal_corridor_nm": 60}
    )

    assert result.aggregate_status == AdvisoryStatus.RED  # OVC corridor deck
    climb = [m for m in _mitigations(result) if m.addresses == "climb_deck"]
    assert len(climb) == 1
    m = climb[0]
    assert m.kind == MitigationKind.ROUTE_POSITION
    assert m.distance_nm == 40.0
    assert m.reference == "departure"
    assert m.mitigated_status == AdvisoryStatus.GREEN
    # Cruise is clear (deck top below cruise) → no vertical mitigation.
    assert not any(m.kind == MitigationKind.ALTITUDE for m in _mitigations(result))


def test_along_route_descent_happy_path():
    """Symmetric to climb: deck near arrival, clear before → descent mitigation."""
    deck = EnhancedCloudLayer(base_ft=3000, top_ft=5000, coverage=CloudCoverage.OVC)
    # Blocked at 160 & 180nm (near arrival), clear at 140nm → descend before
    # ~60nm from arrival (200 − 140).
    analyses = [_rpa(i, i * 20.0, {"gfs": [deck] if i in (8, 9) else []}) for i in range(10)]
    result = VFRFeasibilityEvaluator.evaluate(
        _ctx(analyses), {**_VFR_DEFAULTS, "terminal_corridor_nm": 60}
    )

    assert result.aggregate_status == AdvisoryStatus.RED
    descent = [m for m in _mitigations(result) if m.addresses == "descent_deck"]
    assert len(descent) == 1
    m = descent[0]
    assert m.kind == MitigationKind.ROUTE_POSITION
    assert m.distance_nm == 60.0
    assert m.reference == "arrival"
    assert m.mitigated_status == AdvisoryStatus.GREEN


def test_along_route_uniformly_blocked():
    """Deck over the whole climb corridor → no along-route mitigation
    (the deck can't be avoided by repositioning)."""
    deck = EnhancedCloudLayer(base_ft=3000, top_ft=5000, coverage=CloudCoverage.OVC)
    analyses = [_rpa(i, i * 20.0, {"gfs": [deck] if i in (0, 1, 2, 3) else []}) for i in range(10)]
    result = VFRFeasibilityEvaluator.evaluate(
        _ctx(analyses), {**_VFR_DEFAULTS, "terminal_corridor_nm": 60}
    )

    assert result.aggregate_status == AdvisoryStatus.RED
    assert not any(m.addresses == "climb_deck" for m in _mitigations(result))


# ---------------------------------------------------------------------------
# Co-occurrence
# ---------------------------------------------------------------------------

def test_cooccurrence_vertical_and_along_route():
    """Cruise IMC AND a corridor deck → two mitigations; grade still RED."""
    cruise_deck = EnhancedCloudLayer(base_ft=7000, top_ft=12000, coverage=CloudCoverage.OVC)
    low_deck = EnhancedCloudLayer(base_ft=3000, top_ft=5000, coverage=CloudCoverage.OVC)
    # Every point has the cruise-level deck (IMC); points 0,20 also carry a low
    # corridor deck. Points 40,60 are clear in the corridor → climb after ~40nm.
    analyses = [
        _rpa(i, i * 20.0, {"gfs": [cruise_deck, low_deck] if i in (0, 1) else [cruise_deck]})
        for i in range(10)
    ]
    result = VFRFeasibilityEvaluator.evaluate(
        _ctx(analyses), {**_VFR_DEFAULTS, "terminal_corridor_nm": 60}
    )

    assert result.aggregate_status == AdvisoryStatus.RED  # worst(...) unchanged
    mits = _mitigations(result)
    addresses = {m.addresses for m in mits}
    assert "cruise_imc" in addresses
    assert "climb_deck" in addresses
    assert len(mits) == 2


# ---------------------------------------------------------------------------
# Aggregation — representative-model policy
# ---------------------------------------------------------------------------

def test_aggregate_uses_representative_model_mitigations():
    """``aggregate_mitigations`` = the mitigations of the FIRST per-model result
    whose status equals the aggregate status (representative-model policy).

    This is an explicit assertion of the current policy: a future switch to a
    conservative all-or-nothing merge must change this test deliberately.
    """
    mit_a = Mitigation(
        kind=MitigationKind.ALTITUDE, addresses="cruise_imc", detail="A",
        mitigated_status=AdvisoryStatus.GREEN, altitude_ft=6000,
    )
    mit_b = Mitigation(
        kind=MitigationKind.ALTITUDE, addresses="cruise_imc", detail="B",
        mitigated_status=AdvisoryStatus.GREEN, altitude_ft=5000,
    )
    per_model = [
        ModelAdvisoryResult.build(
            model="gfs", status=AdvisoryStatus.RED, detail="", affected=5, total=10,
            total_distance_nm=200, mitigations=[mit_a],
        ),
        ModelAdvisoryResult.build(
            model="ecmwf", status=AdvisoryStatus.RED, detail="", affected=5, total=10,
            total_distance_nm=200, mitigations=[mit_b],
        ),
        ModelAdvisoryResult.build(
            model="icon", status=AdvisoryStatus.AMBER, detail="", affected=2, total=10,
            total_distance_nm=200,
        ),
    ]
    result = RouteAdvisoryResult.from_per_model("vfr_feasibility", per_model, {})

    assert result.aggregate_status == AdvisoryStatus.RED
    # Representative is the FIRST RED model (gfs) — NOT a merge of gfs+ecmwf.
    assert result.aggregate_mitigations == [mit_a]


# ---------------------------------------------------------------------------
# Backward compatibility
# ---------------------------------------------------------------------------

def test_backward_compat_model_result_without_mitigations():
    """An old ModelAdvisoryResult JSON without the field → empty mitigations."""
    old = ModelAdvisoryResult.model_validate_json('{"model": "gfs", "status": "green"}')
    assert old.mitigations == []


def test_backward_compat_route_result_without_mitigations():
    """An old RouteAdvisoryResult JSON without the field → empty aggregate."""
    old = RouteAdvisoryResult.model_validate_json(
        '{"advisory_id": "vfr_feasibility", "aggregate_status": "green"}'
    )
    assert old.aggregate_mitigations == []
