"""Test fixtures for route advisory evaluators."""

from __future__ import annotations

from datetime import datetime

import pytest

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.models import (
    AgreementLevel,
    AirportConditions,
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
from weatherbrief.models.airport_conditions import (
    AirportConditionsSummary,
    AirportModelCondition,
    FlightCategory,
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
def fiki_departure_icing_context() -> RouteContext:
    """Icing only near departure — first 3 points (0, 20, 40nm) have icing.

    Icing 2000–7000ft, cruise at 8000ft, total 200nm.
    - Departure transit: 5000ft (2000→7000 clipped to cruise)
    - Cruise clear: 7 of 10 clear (icing top at 7000, cruise 8000, buffer 2000 → 1000ft < buffer)
    - Arrival: no icing
    """
    n_points = 10
    icing_zone = IcingZone(
        base_ft=2000, top_ft=7000, risk=IcingRisk.MODERATE,
        icing_type=IcingType.MIXED,
    )
    analyses = []
    for i in range(n_points):
        dist = i * 20.0
        if dist <= 40:  # first 3 points
            sounding = {"gfs": _make_sounding(icing_zones=[icing_zone])}
        else:
            sounding = {"gfs": _make_sounding()}
        analyses.append(_make_rpa(i, dist, sounding=sounding))
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
def fiki_icing_above_cruise_context() -> RouteContext:
    """Icing well above cruise — should be GREEN.

    Icing 11000–14000ft, cruise at 8000ft.
    - Transit thickness: 0 (icing starts above cruise)
    - Cruise clearance: 11000 - 8000 = 3000ft > 2000ft buffer → clear
    """
    n_points = 10
    icing_zone = IcingZone(
        base_ft=11000, top_ft=14000, risk=IcingRisk.MODERATE,
        icing_type=IcingType.RIME,
    )
    analyses = [
        _make_rpa(i, i * 20.0, sounding={
            "gfs": _make_sounding(icing_zones=[icing_zone]),
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
def fiki_icing_close_above_cruise_context() -> RouteContext:
    """Icing just above cruise within buffer — cruise NOT clear.

    Icing 9000–12000ft, cruise at 8000ft.
    - Transit thickness: 0 (icing base 9000 > cruise 8000)
    - Cruise clearance: 9000 - 8000 = 1000ft < 2000ft buffer → NOT clear
    """
    n_points = 10
    icing_zone = IcingZone(
        base_ft=9000, top_ft=12000, risk=IcingRisk.LIGHT,
        icing_type=IcingType.RIME,
    )
    analyses = [
        _make_rpa(i, i * 20.0, sounding={
            "gfs": _make_sounding(icing_zones=[icing_zone]),
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
def fiki_sld_context() -> RouteContext:
    """SLD risk near departure — always RED."""
    n_points = 10
    sld_zone = IcingZone(
        base_ft=3000, top_ft=7000, risk=IcingRisk.MODERATE,
        icing_type=IcingType.CLEAR, sld_risk=True,
    )
    analyses = []
    for i in range(n_points):
        dist = i * 20.0
        if dist <= 40:
            sounding = {"gfs": _make_sounding(icing_zones=[sld_zone])}
        else:
            sounding = {"gfs": _make_sounding()}
        analyses.append(_make_rpa(i, dist, sounding=sounding))
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
                    mean=10.0, spread=10.0,
                    agreement=AgreementLevel.POOR,
                ),
                ModelDivergence(
                    variable="wind_speed_kt",
                    model_values={"gfs": 5.0, "ecmwf": 25.0},
                    mean=15.0, spread=20.0,
                    agreement=AgreementLevel.POOR,
                ),
                ModelDivergence(
                    variable="cloud_cover_pct",
                    model_values={"gfs": 10.0, "ecmwf": 80.0},
                    mean=45.0, spread=70.0,
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


# ---------------------------------------------------------------------------
# Airport condition helpers
# ---------------------------------------------------------------------------

def _make_airport_conditions(
    dep_category: FlightCategory = FlightCategory.VFR,
    arr_category: FlightCategory = FlightCategory.VFR,
    dep_ceiling_ft: int | None = 5000,
    arr_ceiling_ft: int | None = 5000,
    models: list[str] | None = None,
) -> AirportConditions:
    """Create AirportConditions with given flight categories for all models."""
    models = models or ["gfs", "ecmwf"]
    dep_conditions = [
        AirportModelCondition(
            model=m, flight_category=dep_category, ceiling_ft=dep_ceiling_ft,
        )
        for m in models
    ]
    arr_conditions = [
        AirportModelCondition(
            model=m, flight_category=arr_category, ceiling_ft=arr_ceiling_ft,
        )
        for m in models
    ]
    return AirportConditions(
        departure=AirportConditionsSummary(
            icao="EGTK", name="Oxford", conditions=dep_conditions,
        ),
        arrival=AirportConditionsSummary(
            icao="LSGS", name="Sion", conditions=arr_conditions,
        ),
    )


# ---------------------------------------------------------------------------
# VFR / IFR feasibility fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def vfr_clear_context() -> RouteContext:
    """VFR-ideal: VFR at airports, clear en-route."""
    n_points = 10
    analyses = [
        _make_rpa(i, i * 20.0, sounding={
            "gfs": _make_sounding(), "ecmwf": _make_sounding(),
        })
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
        airport_conditions=_make_airport_conditions(),
    )


@pytest.fixture
def vfr_ifr_airport_context() -> RouteContext:
    """VFR flight with IFR conditions at arrival — should be RED for VFR."""
    n_points = 10
    analyses = [
        _make_rpa(i, i * 20.0, sounding={
            "gfs": _make_sounding(), "ecmwf": _make_sounding(),
        })
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
        airport_conditions=_make_airport_conditions(
            arr_category=FlightCategory.IFR, arr_ceiling_ft=800,
        ),
    )


@pytest.fixture
def vfr_mvfr_airport_context() -> RouteContext:
    """VFR flight with MVFR at departure — should be AMBER for VFR."""
    n_points = 10
    analyses = [
        _make_rpa(i, i * 20.0, sounding={
            "gfs": _make_sounding(), "ecmwf": _make_sounding(),
        })
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
        airport_conditions=_make_airport_conditions(
            dep_category=FlightCategory.MVFR, dep_ceiling_ft=2500,
        ),
    )


@pytest.fixture
def vfr_marginal_clearance_context() -> RouteContext:
    """VFR with cloud layers near cruise — marginal cloud clearance."""
    n_points = 10
    # BKN cloud with base at 8800ft — only 800ft above cruise (8000ft)
    near_cloud = EnhancedCloudLayer(
        base_ft=8800, top_ft=12000, coverage=CloudCoverage.BKN,
    )
    analyses = [
        _make_rpa(i, i * 20.0, sounding={
            "gfs": _make_sounding(cloud_layers=[near_cloud]),
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
        airport_conditions=_make_airport_conditions(models=["gfs"]),
    )


@pytest.fixture
def vfr_imc_enroute_context() -> RouteContext:
    """VFR with OVC cloud at cruise — in IMC en-route."""
    n_points = 10
    ovc_cloud = EnhancedCloudLayer(
        base_ft=6000, top_ft=12000, coverage=CloudCoverage.OVC,
    )
    analyses = [
        _make_rpa(i, i * 20.0, sounding={
            "gfs": _make_sounding(cloud_layers=[ovc_cloud]),
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
        airport_conditions=_make_airport_conditions(models=["gfs"]),
    )


@pytest.fixture
def ifr_normal_context() -> RouteContext:
    """IFR-normal: IFR at airports, no icing or convective issues."""
    n_points = 10
    analyses = [
        _make_rpa(i, i * 20.0, sounding={
            "gfs": _make_sounding(), "ecmwf": _make_sounding(),
        })
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
        airport_conditions=_make_airport_conditions(
            dep_category=FlightCategory.IFR, dep_ceiling_ft=800,
            arr_category=FlightCategory.IFR, arr_ceiling_ft=900,
        ),
    )


@pytest.fixture
def ifr_lifr_context() -> RouteContext:
    """IFR with LIFR at arrival — should be AMBER."""
    n_points = 10
    analyses = [
        _make_rpa(i, i * 20.0, sounding={
            "gfs": _make_sounding(),
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
        airport_conditions=_make_airport_conditions(
            dep_category=FlightCategory.IFR, dep_ceiling_ft=800,
            arr_category=FlightCategory.LIFR, arr_ceiling_ft=450,
            models=["gfs"],
        ),
    )


@pytest.fixture
def ifr_lifr_below_mins_context() -> RouteContext:
    """IFR with LIFR below minimums at arrival — should be RED."""
    n_points = 10
    analyses = [
        _make_rpa(i, i * 20.0, sounding={
            "gfs": _make_sounding(),
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
        airport_conditions=_make_airport_conditions(
            dep_category=FlightCategory.IFR, dep_ceiling_ft=800,
            arr_category=FlightCategory.LIFR, arr_ceiling_ft=150,
            models=["gfs"],
        ),
    )


@pytest.fixture
def ifr_heavy_icing_context() -> RouteContext:
    """IFR with icing along most of the route — should be RED."""
    n_points = 10
    icing_zone = IcingZone(
        base_ft=4000, top_ft=10000, risk=IcingRisk.MODERATE,
        icing_type=IcingType.MIXED,
    )
    analyses = [
        _make_rpa(i, i * 20.0, sounding={
            "gfs": _make_sounding(icing_zones=[icing_zone]),
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
        airport_conditions=_make_airport_conditions(
            dep_category=FlightCategory.IFR, dep_ceiling_ft=800,
            arr_category=FlightCategory.IFR, arr_ceiling_ft=900,
            models=["gfs"],
        ),
    )


@pytest.fixture
def ifr_convective_context() -> RouteContext:
    """IFR with HIGH convective risk en-route — should be RED."""
    n_points = 10
    conv = ConvectiveAssessment(
        risk_level=ConvectiveRisk.HIGH,
        cape_jkg=2500,
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
        airport_conditions=_make_airport_conditions(models=["gfs"]),
    )
