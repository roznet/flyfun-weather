"""Test fixtures for route advisory evaluators."""

from __future__ import annotations

from datetime import datetime

import pytest

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.models import (
    AgreementLevel,
    CATRiskLayer,
    CATRiskLevel,
    CloudCoverage,
    ConvectiveAssessment,
    ConvectiveRisk,
    ElevationPoint,
    ElevationProfile,
    EnhancedCloudLayer,
    IcingRisk,
    IcingType,
    IcingZone,
    ModelDivergence,
    RoutePointAnalysis,
    SoundingAnalysis,
    ThermodynamicIndices,
    VerticalMotionAssessment,
    VerticalMotionClass,
)


def _make_rpa(
    point_index: int,
    distance_nm: float,
    sounding: dict[str, SoundingAnalysis] | None = None,
    model_divergence: list[ModelDivergence] | None = None,
) -> RoutePointAnalysis:
    """Create a RoutePointAnalysis with minimal required fields."""
    return RoutePointAnalysis(
        point_index=point_index,
        lat=48.0 + point_index * 0.5,
        lon=2.0 + point_index * 0.5,
        distance_from_origin_nm=distance_nm,
        interpolated_time=datetime(2026, 3, 1, 10, 0),
        forecast_hour=datetime(2026, 3, 1, 9, 0),
        track_deg=135.0,
        sounding=sounding or {},
        model_divergence=model_divergence or [],
    )


def _make_sounding(
    freezing_level_ft: float | None = 5000,
    icing_zones: list[IcingZone] | None = None,
    cloud_layers: list[EnhancedCloudLayer] | None = None,
    convective: ConvectiveAssessment | None = None,
    vertical_motion: VerticalMotionAssessment | None = None,
) -> SoundingAnalysis:
    """Create a SoundingAnalysis with common defaults."""
    return SoundingAnalysis(
        indices=ThermodynamicIndices(freezing_level_ft=freezing_level_ft),
        icing_zones=icing_zones or [],
        cloud_layers=cloud_layers or [],
        convective=convective,
        vertical_motion=vertical_motion,
    )


def _make_elevation(max_elev_ft: float = 500, n_points: int = 20, total_nm: float = 200) -> ElevationProfile:
    """Create a flat terrain elevation profile."""
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


def _make_mountain_elevation(n_points: int = 20, total_nm: float = 200) -> ElevationProfile:
    """Create terrain with mountains in the middle."""
    points = []
    for i in range(n_points):
        d = i * total_nm / (n_points - 1)
        # Mountain in the middle: peaks at 5000ft
        frac = abs(i - n_points // 2) / (n_points // 2)
        elev = 5000 * (1 - frac) + 500 * frac
        points.append(ElevationPoint(
            distance_nm=d, elevation_ft=elev, lat=48.0 + i * 0.1, lon=2.0 + i * 0.1,
        ))
    return ElevationProfile(
        route_name="test",
        points=points,
        max_elevation_ft=5000,
        total_distance_nm=total_nm,
    )


@pytest.fixture
def clear_context() -> RouteContext:
    """Context with clear skies — all green."""
    n_points = 10
    analyses = [
        _make_rpa(i, i * 20.0, sounding={"gfs": _make_sounding(), "ecmwf": _make_sounding()})
        for i in range(n_points)
    ]
    return RouteContext(
        analyses=analyses,
        cross_sections=[],
        elevation=_make_elevation(),
        models=["gfs", "ecmwf"],
        cruise_altitude_ft=8000,
        flight_ceiling_ft=18000,
        total_distance_nm=200,
    )


@pytest.fixture
def icing_context() -> RouteContext:
    """Context with icing along most of the route."""
    n_points = 10
    icing_zone = IcingZone(
        base_ft=4000, top_ft=10000, risk=IcingRisk.MODERATE,
        icing_type=IcingType.MIXED,
    )
    analyses = [
        _make_rpa(i, i * 20.0, sounding={
            "gfs": _make_sounding(freezing_level_ft=5000, icing_zones=[icing_zone]),
            "ecmwf": _make_sounding(freezing_level_ft=4500, icing_zones=[icing_zone]),
        })
        for i in range(n_points)
    ]
    return RouteContext(
        analyses=analyses,
        cross_sections=[],
        elevation=_make_elevation(max_elev_ft=500),
        models=["gfs", "ecmwf"],
        cruise_altitude_ft=8000,
        flight_ceiling_ft=18000,
        total_distance_nm=200,
    )


@pytest.fixture
def icing_no_escape_context() -> RouteContext:
    """Context with icing and freezing level at terrain — no warm escape."""
    n_points = 10
    icing_zone = IcingZone(
        base_ft=3000, top_ft=10000, risk=IcingRisk.MODERATE,
        icing_type=IcingType.MIXED,
    )
    analyses = [
        _make_rpa(i, i * 20.0, sounding={
            "gfs": _make_sounding(freezing_level_ft=3500, icing_zones=[icing_zone]),
        })
        for i in range(n_points)
    ]
    return RouteContext(
        analyses=analyses,
        cross_sections=[],
        elevation=_make_mountain_elevation(),
        models=["gfs"],
        cruise_altitude_ft=8000,
        flight_ceiling_ft=18000,
        total_distance_nm=200,
    )


@pytest.fixture
def cloudy_context() -> RouteContext:
    """Context with BKN/OVC cloud at cruise altitude."""
    n_points = 10
    ovc_cloud = EnhancedCloudLayer(
        base_ft=6000, top_ft=12000, coverage=CloudCoverage.OVC,
    )
    bkn_cloud = EnhancedCloudLayer(
        base_ft=6000, top_ft=10000, coverage=CloudCoverage.BKN,
    )
    analyses = []
    for i in range(n_points):
        if i < 6:
            cloud = ovc_cloud
        else:
            cloud = bkn_cloud
        analyses.append(_make_rpa(i, i * 20.0, sounding={
            "gfs": _make_sounding(cloud_layers=[cloud]),
            "ecmwf": _make_sounding(cloud_layers=[cloud]),
        }))
    return RouteContext(
        analyses=analyses,
        cross_sections=[],
        elevation=_make_elevation(),
        models=["gfs", "ecmwf"],
        cruise_altitude_ft=8000,
        flight_ceiling_ft=18000,
        total_distance_nm=200,
    )


@pytest.fixture
def turbulent_context() -> RouteContext:
    """Context with CAT turbulence at cruise altitude."""
    n_points = 10
    cat_layer = CATRiskLayer(
        base_ft=7000, top_ft=10000, risk=CATRiskLevel.MODERATE,
    )
    vm = VerticalMotionAssessment(
        classification=VerticalMotionClass.SYNOPTIC_ASCENT,
        max_omega_pa_s=-2.0,
        max_w_fpm=300,
        max_w_level_ft=8000,
        cat_risk_layers=[cat_layer],
    )
    analyses = [
        _make_rpa(i, i * 20.0, sounding={
            "gfs": _make_sounding(vertical_motion=vm),
        })
        for i in range(n_points)
    ]
    return RouteContext(
        analyses=analyses,
        cross_sections=[],
        elevation=_make_elevation(),
        models=["gfs"],
        cruise_altitude_ft=8000,
        flight_ceiling_ft=18000,
        total_distance_nm=200,
    )


@pytest.fixture
def convective_context() -> RouteContext:
    """Context with moderate convective risk."""
    n_points = 10
    conv = ConvectiveAssessment(
        risk_level=ConvectiveRisk.MODERATE,
        cape_jkg=1000,
    )
    analyses = [
        _make_rpa(i, i * 20.0, sounding={
            "gfs": _make_sounding(convective=conv),
        })
        for i in range(n_points)
    ]
    return RouteContext(
        analyses=analyses,
        cross_sections=[],
        elevation=_make_elevation(),
        models=["gfs"],
        cruise_altitude_ft=8000,
        flight_ceiling_ft=18000,
        total_distance_nm=200,
    )


@pytest.fixture
def high_cirrus_context() -> RouteContext:
    """Context with high cirrus above ceiling + lower cloud below ceiling."""
    n_points = 10
    # High cirrus entirely above ceiling — should be ignored
    cirrus = EnhancedCloudLayer(base_ft=35000, top_ft=39000, coverage=CloudCoverage.SCT)
    # Lower cloud within reachable altitude
    lower = EnhancedCloudLayer(base_ft=6000, top_ft=10000, coverage=CloudCoverage.BKN)
    analyses = [
        _make_rpa(i, i * 20.0, sounding={
            "gfs": _make_sounding(cloud_layers=[cirrus, lower]),
        })
        for i in range(n_points)
    ]
    return RouteContext(
        analyses=analyses,
        cross_sections=[],
        elevation=_make_elevation(),
        models=["gfs"],
        cruise_altitude_ft=8000,
        flight_ceiling_ft=18000,
        total_distance_nm=200,
    )


@pytest.fixture
def only_cirrus_context() -> RouteContext:
    """Context with ONLY high cirrus above ceiling — all layers should be ignored."""
    n_points = 10
    cirrus = EnhancedCloudLayer(base_ft=35000, top_ft=39000, coverage=CloudCoverage.SCT)
    analyses = [
        _make_rpa(i, i * 20.0, sounding={
            "gfs": _make_sounding(cloud_layers=[cirrus]),
        })
        for i in range(n_points)
    ]
    return RouteContext(
        analyses=analyses,
        cross_sections=[],
        elevation=_make_elevation(),
        models=["gfs"],
        cruise_altitude_ft=8000,
        flight_ceiling_ft=18000,
        total_distance_nm=200,
    )


@pytest.fixture
def poor_agreement_context() -> RouteContext:
    """Context with poor model agreement."""
    n_points = 10
    analyses = [
        _make_rpa(i, i * 20.0,
            sounding={"gfs": _make_sounding(), "ecmwf": _make_sounding()},
            model_divergence=[
                ModelDivergence(
                    variable="temperature_c",
                    model_values={"gfs": 5.0, "ecmwf": 15.0},
                    mean=10.0,
                    spread=10.0,
                    agreement=AgreementLevel.POOR,
                ),
            ],
        )
        for i in range(n_points)
    ]
    return RouteContext(
        analyses=analyses,
        cross_sections=[],
        elevation=_make_elevation(),
        models=["gfs", "ecmwf"],
        cruise_altitude_ft=8000,
        flight_ceiling_ft=18000,
        total_distance_nm=200,
    )
