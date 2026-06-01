"""Tests for the experimental front advisory evaluator (#196)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories.fronts import FRONTS_ADVISORY_ID, FrontsEvaluator
from weatherbrief.models import (
    AdvisoryStatus,
    FrontCrossingModel,
    FrontProximityModel,
    RouteFrontAnalysisModel,
    RouteFrontsManifest,
)

# Default parameter set (matches the catalog default).
_PARAMS = {"closing_within_km": 300}


def _crossing(
    *,
    distance_km: float = 200.0,
    kind: str = "cold",
    intensity: str = "classical",
    advection: float = 0.0,
    gradient: float = 9.0,
) -> FrontCrossingModel:
    return FrontCrossingModel(
        lat=48.0, lon=2.0, distance_km=distance_km,
        gradient=gradient, neg_laplacian=1.0, advection=advection,
        tfp_before=0.5, tfp_after=-0.5, delta_theta_e=8.0,
        kind=kind, intensity=intensity,
    )


def _manifest(
    *,
    crossings: list[FrontCrossingModel] | None = None,
    nearest: FrontProximityModel | None = None,
    model: str = "gfs",
    primary_level: int = 850,
) -> RouteFrontsManifest:
    analysis = RouteFrontAnalysisModel(
        model=model, level_hPa=primary_level, hour=12.0,
        crossings=crossings or [], nearest=nearest,
    )
    return RouteFrontsManifest(
        generated_at=datetime(2026, 5, 31, tzinfo=timezone.utc),
        primary_level_hPa=primary_level,
        levels=[primary_level],
        models=[model],
        per_model={model: [analysis]},
    )


def _ctx(manifest: RouteFrontsManifest | None) -> RouteContext:
    return RouteContext(
        analyses=[], cross_sections=[], elevation=None,
        models=["gfs"], cruise_altitude_ft=8000, flight_ceiling_ft=18000,
        total_distance_nm=200.0,  # ~370 km route
        route_fronts=manifest,
    )


def test_no_artifact_is_unavailable():
    """No route_fronts → experimental feature off → UNAVAILABLE."""
    result = FrontsEvaluator.evaluate(_ctx(None), _PARAMS)
    assert result.advisory_id == FRONTS_ADVISORY_ID
    assert result.aggregate_status == AdvisoryStatus.UNAVAILABLE


def test_empty_per_model_is_unavailable():
    manifest = RouteFrontsManifest(
        generated_at=datetime(2026, 5, 31, tzinfo=timezone.utc),
        primary_level_hPa=850, levels=[850], models=[], per_model={},
    )
    result = FrontsEvaluator.evaluate(_ctx(manifest), _PARAMS)
    assert result.aggregate_status == AdvisoryStatus.UNAVAILABLE


def test_sharp_crossing_is_red():
    manifest = _manifest(crossings=[_crossing(intensity="sharp", gradient=14.0)])
    result = FrontsEvaluator.evaluate(_ctx(manifest), _PARAMS)
    assert result.aggregate_status == AdvisoryStatus.RED
    assert "sharp" in result.aggregate_detail.lower()


def test_classical_crossing_is_amber():
    manifest = _manifest(crossings=[_crossing(intensity="classical")])
    result = FrontsEvaluator.evaluate(_ctx(manifest), _PARAMS)
    assert result.aggregate_status == AdvisoryStatus.AMBER


def test_significant_crossing_is_amber():
    manifest = _manifest(crossings=[_crossing(intensity="significant", gradient=6.5)])
    result = FrontsEvaluator.evaluate(_ctx(manifest), _PARAMS)
    assert result.aggregate_status == AdvisoryStatus.AMBER


def test_warm_advection_adds_deteriorating_tail():
    manifest = _manifest(crossings=[_crossing(kind="warm", advection=2.0)])
    result = FrontsEvaluator.evaluate(_ctx(manifest), _PARAMS)
    assert "deteriorating" in result.aggregate_detail.lower()


def test_cold_advection_adds_improving_tail():
    manifest = _manifest(crossings=[_crossing(kind="cold", advection=-2.0)])
    result = FrontsEvaluator.evaluate(_ctx(manifest), _PARAMS)
    assert "improving" in result.aggregate_detail.lower()


def test_closing_offtrack_front_is_amber():
    nearest = FrontProximityModel(
        distance_km=120.0, lat=49.0, lon=3.0, gradient=7.0, delta_theta_e=6.0,
        on_track=False, trend="closing", closing_km_per_h=8.0,
    )
    manifest = _manifest(crossings=[], nearest=nearest)
    result = FrontsEvaluator.evaluate(_ctx(manifest), _PARAMS)
    assert result.aggregate_status == AdvisoryStatus.AMBER


def test_receding_offtrack_front_is_green():
    nearest = FrontProximityModel(
        distance_km=120.0, lat=49.0, lon=3.0, gradient=7.0, delta_theta_e=6.0,
        on_track=False, trend="receding", closing_km_per_h=-5.0,
    )
    manifest = _manifest(crossings=[], nearest=nearest)
    result = FrontsEvaluator.evaluate(_ctx(manifest), _PARAMS)
    assert result.aggregate_status == AdvisoryStatus.GREEN


def test_distant_closing_front_beyond_threshold_is_green():
    nearest = FrontProximityModel(
        distance_km=500.0, lat=49.0, lon=3.0, gradient=7.0, delta_theta_e=6.0,
        on_track=False, trend="closing", closing_km_per_h=8.0,
    )
    manifest = _manifest(crossings=[], nearest=nearest)
    result = FrontsEvaluator.evaluate(_ctx(manifest), _PARAMS)
    assert result.aggregate_status == AdvisoryStatus.GREEN


def test_multiple_crossings_picks_worst_and_counts():
    manifest = _manifest(crossings=[
        _crossing(distance_km=50.0, intensity="significant", gradient=6.5),
        _crossing(distance_km=300.0, intensity="sharp", gradient=13.0),
    ])
    result = FrontsEvaluator.evaluate(_ctx(manifest), _PARAMS)
    assert result.aggregate_status == AdvisoryStatus.RED  # sharp wins
    assert "2" in result.aggregate_detail  # count surfaced


def test_matches_primary_level_not_position():
    """Analyses are matched by level_hPa, not list order."""
    a700 = RouteFrontAnalysisModel(
        model="gfs", level_hPa=700, hour=12.0,
        crossings=[_crossing(intensity="sharp", gradient=14.0)],
    )
    a850 = RouteFrontAnalysisModel(
        model="gfs", level_hPa=850, hour=12.0, crossings=[],  # primary, no fronts
    )
    manifest = RouteFrontsManifest(
        generated_at=datetime(2026, 5, 31, tzinfo=timezone.utc),
        primary_level_hPa=850, levels=[700, 850], models=["gfs"],
        per_model={"gfs": [a700, a850]},  # 700 first on purpose
    )
    result = FrontsEvaluator.evaluate(_ctx(manifest), _PARAMS)
    # Must grade the 850 (primary) analysis → GREEN, ignoring the sharp 700.
    assert result.aggregate_status == AdvisoryStatus.GREEN


def test_default_disabled_in_catalog():
    """Front advisory must not run by default — gated by artifact presence."""
    assert FrontsEvaluator.catalog_entry().default_enabled is False
