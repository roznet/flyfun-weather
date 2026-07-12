"""Tests for the remaining advisory highlight emitters (#375).

The mechanism (helpers + vmc_cruise/convective) is covered by
``test_highlights.py``; this file proves the per-evaluator geometry added in
issue #375: icing_escape, fiki_icing, turbulence, cloud_top, the two
composites (multi-kind cutouts + airport endpoint colouring), mountain_wind,
freezing_precip, and enroute_precip. Shared invariants per evaluator: the
ribbon tiles ``[0, total_nm]``, regions appear only on flagged points, and the
severity mapping honours relevance-filter greens and UNAVAILABLE gaps.
"""

from __future__ import annotations

from datetime import datetime

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories.cloud_top import CloudTopEvaluator
from weatherbrief.analysis.advisories.enroute_precip import EnroutePrecipEvaluator
from weatherbrief.analysis.advisories.fiki_icing import FIKIIcingEvaluator
from weatherbrief.analysis.advisories.freezing_precip import FreezingPrecipEvaluator
from weatherbrief.analysis.advisories.icing_escape import IcingEscapeEvaluator
from weatherbrief.analysis.advisories.ifr_feasibility import IFRFeasibilityEvaluator
from weatherbrief.analysis.advisories.mountain_wind import MountainWindEvaluator
from weatherbrief.analysis.advisories.turbulence import TurbulenceEvaluator
from weatherbrief.analysis.advisories.vfr_feasibility import VFRFeasibilityEvaluator
from weatherbrief.models import (
    AdvisoryStatus,
    CATRiskLayer,
    CATRiskLevel,
    CloudCoverage,
    ConvectiveAssessment,
    ConvectiveRisk,
    DerivedLevel,
    ElevationPoint,
    ElevationProfile,
    EnhancedCloudLayer,
    HighlightSeverity,
    HourlyForecast,
    IcingRisk,
    IcingType,
    IcingZone,
    InversionLayer,
    ModelSource,
    PrecipIntensity,
    PrecipPhase,
    PrecipitationAssessment,
    PressureLevelData,
    RouteCrossSection,
    RoutePoint,
    RoutePointAnalysis,
    SoundingAnalysis,
    ThermodynamicIndices,
    VerticalMotionAssessment,
    VerticalMotionClass,
    Waypoint,
    WaypointForecast,
)
from weatherbrief.models.airport_conditions import (
    AirportConditions,
    AirportConditionsSummary,
    AirportModelCondition,
    FlightCategory,
)

S = HighlightSeverity
_T0 = datetime(2026, 3, 1, 12, 0)


def _params(evaluator) -> dict[str, float]:
    return {p.key: p.default for p in evaluator.catalog_entry().parameters}


def _rpa(i: int, dist: float, sounding: dict[str, SoundingAnalysis]) -> RoutePointAnalysis:
    return RoutePointAnalysis(
        point_index=i,
        lat=48.0 + i * 0.5,
        lon=2.0 + i * 0.5,
        distance_from_origin_nm=dist,
        interpolated_time=_T0,
        forecast_hour=_T0,
        track_deg=90.0,
        sounding=sounding,
    )


def _ctx(
    soundings: list[SoundingAnalysis | None],
    *,
    cruise_ft: int = 8000,
    ceiling_ft: int = 18000,
    elevation: ElevationProfile | None = None,
    airport_conditions: AirportConditions | None = None,
    total_nm: float | None = None,
) -> RouteContext:
    """One point every 20 nm; ``None`` in ``soundings`` = no sounding (gap)."""
    analyses = [
        _rpa(i, i * 20.0, {} if s is None else {"gfs": s})
        for i, s in enumerate(soundings)
    ]
    return RouteContext(
        analyses=analyses,
        cross_sections=[],
        elevation=elevation,
        airport_conditions=airport_conditions,
        models=["gfs"],
        cruise_altitude_ft=cruise_ft,
        flight_ceiling_ft=ceiling_ft,
        total_distance_nm=total_nm if total_nm is not None else 20.0 * (len(soundings) - 1),
    )


def _flat_elevation(n: int, elev_ft: float = 500.0) -> ElevationProfile:
    total = 20.0 * (n - 1)
    return ElevationProfile(
        route_name="test",
        points=[
            ElevationPoint(distance_nm=i * 20.0, elevation_ft=elev_ft, lat=48.0, lon=2.0 + i * 0.5)
            for i in range(n)
        ],
        max_elevation_ft=elev_ft,
        total_distance_nm=total,
    )


def _airports(
    dep_cat: FlightCategory,
    arr_cat: FlightCategory,
    dep_ceiling_ft: int | None = None,
    arr_ceiling_ft: int | None = None,
) -> AirportConditions:
    return AirportConditions(
        departure=AirportConditionsSummary(
            icao="LFPG", name="Dep", conditions=[AirportModelCondition(
                model="gfs", flight_category=dep_cat, ceiling_ft=dep_ceiling_ft,
            )],
        ),
        arrival=AirportConditionsSummary(
            icao="EGTF", name="Arr", conditions=[AirportModelCondition(
                model="gfs", flight_category=arr_cat, ceiling_ft=arr_ceiling_ft,
            )],
        ),
    )


def _assert_tiles(ribbon, total_nm: float) -> None:
    assert ribbon, "ribbon must not be empty"
    assert ribbon[0].dist_from_nm == 0.0
    assert abs(ribbon[-1].dist_to_nm - total_nm) < 1e-9
    for a, b in zip(ribbon, ribbon[1:]):
        assert abs(a.dist_to_nm - b.dist_from_nm) < 1e-9
        assert a.dist_from_nm < a.dist_to_nm
        assert a.severity != b.severity


# ---------------------------------------------------------------------------
# icing_escape
# ---------------------------------------------------------------------------

def _icing_sounding(base: float, top: float, fz: float | None) -> SoundingAnalysis:
    return SoundingAnalysis(
        icing_zones=[IcingZone(
            base_ft=base, top_ft=top,
            risk=IcingRisk.LIGHT, icing_type=IcingType.RIME,
        )],
        indices=ThermodynamicIndices(freezing_level_ft=fz),
    )


class TestIcingEscapeHighlights:
    def test_escapable_icing_amber_band(self):
        n = 10
        ctx = _ctx(
            [_icing_sounding(4000, 6000, 3000)] * n,
            elevation=_flat_elevation(n),
        )
        res = IcingEscapeEvaluator.evaluate(ctx, _params(IcingEscapeEvaluator))
        h = res.per_model[0].highlights
        assert h is not None
        _assert_tiles(h.ribbon, ctx.total_distance_nm)
        assert all(seg.severity == S.AMBER for seg in h.ribbon)
        assert len(h.regions) == 1
        assert h.regions[0].kind == "icing_band"
        assert h.regions[0].severity == S.AMBER
        assert h.regions[0].base_ft == 4000 and h.regions[0].top_ft == 6000
        assert h.peak_dist_nm is not None

    def test_no_escape_is_red(self):
        # Freezing level 1200 < terrain 500 + margin 1000 → no escape.
        n = 10
        ctx = _ctx(
            [_icing_sounding(2000, 6000, 1200)] * n,
            elevation=_flat_elevation(n),
        )
        res = IcingEscapeEvaluator.evaluate(ctx, _params(IcingEscapeEvaluator))
        h = res.per_model[0].highlights
        assert all(seg.severity == S.RED for seg in h.ribbon)
        assert all(r.severity == S.RED for r in h.regions)

    def test_unknown_fz_or_terrain_is_red(self):
        # elevation=None → terrain unknown at an affected point → no-escape red
        # (matches the grade's no-escape count).
        ctx = _ctx([_icing_sounding(4000, 6000, 3000)] * 5)
        res = IcingEscapeEvaluator.evaluate(ctx, _params(IcingEscapeEvaluator))
        h = res.per_model[0].highlights
        assert all(seg.severity == S.RED for seg in h.ribbon)

    def test_icing_above_buffer_is_green_no_region(self):
        # Zone 11000-13000 with cruise 8000 + buffer 2000 → irrelevant → green.
        n = 5
        ctx = _ctx(
            [_icing_sounding(11000, 13000, 3000)] * n,
            elevation=_flat_elevation(n),
        )
        res = IcingEscapeEvaluator.evaluate(ctx, _params(IcingEscapeEvaluator))
        h = res.per_model[0].highlights
        assert all(seg.severity == S.GREEN for seg in h.ribbon)
        assert h.regions == []
        assert h.peak_dist_nm is None

    def test_unavailable_gap(self):
        n = 6
        soundings: list[SoundingAnalysis | None] = [
            _icing_sounding(4000, 6000, 3000)
        ] * n
        soundings[3] = None
        ctx = _ctx(soundings, elevation=_flat_elevation(n))
        res = IcingEscapeEvaluator.evaluate(ctx, _params(IcingEscapeEvaluator))
        h = res.per_model[0].highlights
        _assert_tiles(h.ribbon, ctx.total_distance_nm)
        assert any(seg.severity == S.UNAVAILABLE for seg in h.ribbon)


# ---------------------------------------------------------------------------
# fiki_icing
# ---------------------------------------------------------------------------

class TestFIKIIcingHighlights:
    def test_thick_transit_red_in_corridors_only(self):
        # 6000 ft transit column (>= 5000 red) everywhere; cruise itself is
        # clear (top 6000, cruise 8000, clearance 2000 >= buffer). Red in the
        # dep/arr corridors (default proximity 50 nm), green mid-route.
        n = 10  # total 180 nm; corridors cover d<=50 and d>=130
        sounding = _icing_sounding(0, 6000, None)
        ctx = _ctx([sounding] * n)
        res = FIKIIcingEvaluator.evaluate(ctx, _params(FIKIIcingEvaluator))
        h = res.per_model[0].highlights
        assert h is not None
        _assert_tiles(h.ribbon, ctx.total_distance_nm)
        assert [seg.severity for seg in h.ribbon] == [S.RED, S.GREEN, S.RED]
        assert all(r.kind == "icing_band" for r in h.regions)
        assert {r.severity for r in h.regions} == {S.RED}

    def test_cruise_icing_amber(self):
        # Zone hugging cruise (7500-8500): thin (500 ft transit) but cruise is
        # not clear → amber everywhere.
        ctx = _ctx([_icing_sounding(7500, 8500, None)] * 6)
        res = FIKIIcingEvaluator.evaluate(ctx, _params(FIKIIcingEvaluator))
        h = res.per_model[0].highlights
        assert all(seg.severity == S.AMBER for seg in h.ribbon)
        assert all(r.severity == S.AMBER and r.kind == "icing_band" for r in h.regions)

    def test_sld_in_corridor_red(self):
        sld = SoundingAnalysis(icing_zones=[IcingZone(
            base_ft=1000, top_ft=3000, risk=IcingRisk.LIGHT,
            icing_type=IcingType.CLEAR, sld_risk=True,
        )])
        ctx = _ctx([sld] * 5)  # 80 nm — everything is in a corridor
        res = FIKIIcingEvaluator.evaluate(ctx, _params(FIKIIcingEvaluator))
        h = res.per_model[0].highlights
        assert all(seg.severity == S.RED for seg in h.ribbon)

    def test_no_icing_green(self):
        ctx = _ctx([SoundingAnalysis()] * 5)
        res = FIKIIcingEvaluator.evaluate(ctx, _params(FIKIIcingEvaluator))
        h = res.per_model[0].highlights
        assert len(h.ribbon) == 1 and h.ribbon[0].severity == S.GREEN
        assert h.regions == []


# ---------------------------------------------------------------------------
# turbulence
# ---------------------------------------------------------------------------

def _cat_sounding(base: float, top: float, risk: CATRiskLevel) -> SoundingAnalysis:
    return SoundingAnalysis(vertical_motion=VerticalMotionAssessment(
        classification=VerticalMotionClass.QUIESCENT,
        cat_risk_layers=[CATRiskLayer(base_ft=base, top_ft=top, risk=risk)],
    ))


class TestTurbulenceHighlights:
    def test_moderate_at_cruise_amber_with_layer_band(self):
        ctx = _ctx([_cat_sounding(7000, 9000, CATRiskLevel.MODERATE)] * 6)
        res = TurbulenceEvaluator.evaluate(ctx, _params(TurbulenceEvaluator))
        h = res.per_model[0].highlights
        assert all(seg.severity == S.AMBER for seg in h.ribbon)
        assert len(h.regions) == 1
        assert h.regions[0].kind == "cat_layer"
        assert h.regions[0].base_ft == 7000 and h.regions[0].top_ft == 9000

    def test_severe_anywhere_in_column_is_red(self):
        # SEVERE CAT at FL200-250, cruise 8000: the ribbon locates it (red +
        # cutout where it sits) even though the layer is far above cruise —
        # per the #375 spec, this is exactly the ambiguity the cutout resolves.
        ctx = _ctx([_cat_sounding(20000, 25000, CATRiskLevel.SEVERE)] * 6)
        res = TurbulenceEvaluator.evaluate(ctx, _params(TurbulenceEvaluator))
        h = res.per_model[0].highlights
        assert all(seg.severity == S.RED for seg in h.ribbon)
        assert h.regions[0].kind == "cat_layer"
        assert h.regions[0].severity == S.RED
        assert h.regions[0].base_ft == 20000 and h.regions[0].top_ft == 25000

    def test_light_at_cruise_is_green_ribbon(self):
        # LIGHT counts toward the grade's extent but is not a per-point flag.
        ctx = _ctx([_cat_sounding(7000, 9000, CATRiskLevel.LIGHT)] * 6)
        res = TurbulenceEvaluator.evaluate(ctx, _params(TurbulenceEvaluator))
        h = res.per_model[0].highlights
        assert all(seg.severity == S.GREEN for seg in h.ribbon)
        assert h.regions == []

    def test_strong_updraft_amber_no_region(self):
        # Strong w near cruise flags the point (amber ribbon) but is a single
        # resolved level, not a band — no cutout is invented.
        sounding = SoundingAnalysis(vertical_motion=VerticalMotionAssessment(
            classification=VerticalMotionClass.CONVECTIVE,
            max_w_fpm=400.0, max_w_level_ft=9000.0,
        ))
        ctx = _ctx([sounding] * 6)
        res = TurbulenceEvaluator.evaluate(ctx, _params(TurbulenceEvaluator))
        h = res.per_model[0].highlights
        assert all(seg.severity == S.AMBER for seg in h.ribbon)
        assert h.regions == []


# ---------------------------------------------------------------------------
# cloud_top
# ---------------------------------------------------------------------------

class TestCloudTopHighlights:
    def test_blocking_deck_amber(self):
        deck = EnhancedCloudLayer(base_ft=7000, top_ft=17500, coverage=CloudCoverage.OVC)
        ctx = _ctx([SoundingAnalysis(cloud_layers=[deck])] * 6)
        res = CloudTopEvaluator.evaluate(ctx, _params(CloudTopEvaluator))
        h = res.per_model[0].highlights
        assert all(seg.severity == S.AMBER for seg in h.ribbon)
        assert len(h.regions) == 1
        assert h.regions[0].kind == "blocking_deck"
        assert h.regions[0].base_ft == 7000 and h.regions[0].top_ft == 17500

    def test_toppable_green_no_region(self):
        deck = EnhancedCloudLayer(base_ft=7000, top_ft=12000, coverage=CloudCoverage.OVC)
        ctx = _ctx([SoundingAnalysis(cloud_layers=[deck])] * 6)
        res = CloudTopEvaluator.evaluate(ctx, _params(CloudTopEvaluator))
        h = res.per_model[0].highlights
        assert all(seg.severity == S.GREEN for seg in h.ribbon)
        assert h.regions == []
        assert h.peak_dist_nm is None


# ---------------------------------------------------------------------------
# vfr_feasibility (composite: multi-kind cutouts + endpoint colouring)
# ---------------------------------------------------------------------------

class TestVFRFeasibilityHighlights:
    def test_multi_kind_cutouts_at_overlapping_x(self):
        # Point 0 carries BOTH a low deck (climb_deck) and cruise in cloud
        # (cruise_imc): two different-kind cutouts at the same x that must NOT
        # merge into one.
        both = SoundingAnalysis(cloud_layers=[
            EnhancedCloudLayer(base_ft=1000, top_ft=3000, coverage=CloudCoverage.OVC),
            EnhancedCloudLayer(base_ft=7000, top_ft=9000, coverage=CloudCoverage.OVC),
        ])
        soundings: list[SoundingAnalysis | None] = [both] + [SoundingAnalysis()] * 9
        ctx = _ctx(soundings)
        res = VFRFeasibilityEvaluator.evaluate(ctx, _params(VFRFeasibilityEvaluator))
        h = res.per_model[0].highlights
        assert h is not None
        _assert_tiles(h.ribbon, ctx.total_distance_nm)
        kinds = {r.kind for r in h.regions}
        assert kinds == {"cruise_imc", "climb_deck"}
        imc = next(r for r in h.regions if r.kind == "cruise_imc")
        deck = next(r for r in h.regions if r.kind == "climb_deck")
        # Same x-extent (point 0's cell), different bands — not fused.
        assert imc.dist_from_nm == deck.dist_from_nm == 0.0
        assert imc.base_ft == 7000 and imc.top_ft == 9000
        assert deck.base_ft == 1000 and deck.top_ft == 3000
        # Worst axis at point 0 is red (OVC deck + IMC).
        assert h.ribbon[0].severity == S.RED

    def test_airport_endpoint_colouring(self):
        # Clear en route; dep IFR → first ribbon segment red, rest green.
        ctx = _ctx(
            [SoundingAnalysis()] * 10,
            airport_conditions=_airports(FlightCategory.IFR, FlightCategory.VFR),
        )
        res = VFRFeasibilityEvaluator.evaluate(ctx, _params(VFRFeasibilityEvaluator))
        h = res.per_model[0].highlights
        assert [seg.severity for seg in h.ribbon] == [S.RED, S.GREEN]
        assert h.ribbon[0].dist_to_nm == 10.0  # first point's cell only

    def test_precip_axis_capped_amber(self):
        # Moderate snow everywhere: the standalone advisory ribbons it red,
        # the composite caps it at amber.
        snow = SoundingAnalysis(precipitation=PrecipitationAssessment(
            surface_phase=PrecipPhase.SNOW,
            surface_intensity=PrecipIntensity.MODERATE,
        ))
        ctx = _ctx([snow] * 6)
        res = VFRFeasibilityEvaluator.evaluate(ctx, _params(VFRFeasibilityEvaluator))
        h = res.per_model[0].highlights
        assert all(seg.severity == S.AMBER for seg in h.ribbon)
        # Precip contributes to the ribbon only — no vfr cutout kind for it.
        assert h.regions == []

    def test_marginal_clearance_amber_no_cutout(self):
        near = SoundingAnalysis(cloud_layers=[
            EnhancedCloudLayer(base_ft=8500, top_ft=10000, coverage=CloudCoverage.BKN),
        ])
        ctx = _ctx([near] * 6)
        res = VFRFeasibilityEvaluator.evaluate(ctx, _params(VFRFeasibilityEvaluator))
        h = res.per_model[0].highlights
        assert all(seg.severity == S.AMBER for seg in h.ribbon)
        assert h.regions == []


# ---------------------------------------------------------------------------
# ifr_feasibility (composite: icing + convective kinds, endpoint colouring)
# ---------------------------------------------------------------------------

class TestIFRFeasibilityHighlights:
    def test_icing_and_tower_kinds_at_overlapping_x(self):
        hazard = SoundingAnalysis(
            icing_zones=[IcingZone(
                base_ft=7000, top_ft=9000,
                risk=IcingRisk.LIGHT, icing_type=IcingType.RIME,
            )],
            convective=ConvectiveAssessment(
                risk_level=ConvectiveRisk.HIGH, cape_jkg=2000,
                base_ft=4000, top_ft=35000,
            ),
        )
        ctx = _ctx([hazard] * 8)
        res = IFRFeasibilityEvaluator.evaluate(ctx, _params(IFRFeasibilityEvaluator))
        h = res.per_model[0].highlights
        assert h is not None
        _assert_tiles(h.ribbon, ctx.total_distance_nm)
        # Convective HIGH → red ribbon (worst of the two axes).
        assert all(seg.severity == S.RED for seg in h.ribbon)
        kinds = {r.kind for r in h.regions}
        assert kinds == {"icing_band", "tower"}
        icing = next(r for r in h.regions if r.kind == "icing_band")
        tower = next(r for r in h.regions if r.kind == "tower")
        assert icing.severity == S.AMBER
        assert icing.base_ft == 7000 and icing.top_ft == 9000
        assert tower.severity == S.RED
        assert tower.base_ft == 4000 and tower.top_ft == 35000

    def test_unresolved_tower_is_ghost(self):
        hazard = SoundingAnalysis(convective=ConvectiveAssessment(
            risk_level=ConvectiveRisk.MODERATE, base_ft=None, top_ft=None,
            method="nwp_precip",
        ))
        ctx = _ctx([hazard] * 6)
        res = IFRFeasibilityEvaluator.evaluate(ctx, _params(IFRFeasibilityEvaluator))
        h = res.per_model[0].highlights
        assert len(h.regions) == 1
        assert h.regions[0].kind == "tower_unresolved"
        assert h.regions[0].base_ft is None and h.regions[0].top_ft is None
        assert all(seg.severity == S.AMBER for seg in h.ribbon)

    def test_lifr_endpoint_amber(self):
        ctx = _ctx(
            [SoundingAnalysis()] * 8,
            airport_conditions=_airports(
                FlightCategory.LIFR, FlightCategory.VFR, dep_ceiling_ft=300,
            ),
        )
        res = IFRFeasibilityEvaluator.evaluate(ctx, _params(IFRFeasibilityEvaluator))
        h = res.per_model[0].highlights
        assert [seg.severity for seg in h.ribbon] == [S.AMBER, S.GREEN]

    def test_lifr_below_minimum_endpoint_red(self):
        ctx = _ctx(
            [SoundingAnalysis()] * 8,
            airport_conditions=_airports(
                FlightCategory.VFR, FlightCategory.LIFR, arr_ceiling_ft=100,
            ),
        )
        res = IFRFeasibilityEvaluator.evaluate(ctx, _params(IFRFeasibilityEvaluator))
        h = res.per_model[0].highlights
        assert [seg.severity for seg in h.ribbon] == [S.GREEN, S.RED]


# ---------------------------------------------------------------------------
# mountain_wind (ridge_wind + wave_signature kinds)
# ---------------------------------------------------------------------------

_MW_N = 10
_MW_TOTAL = 200.0


def _mw_elevation(mountain: bool = True) -> ElevationProfile:
    points = []
    for i in range(_MW_N):
        d = i * _MW_TOTAL / (_MW_N - 1)
        elev = 5000.0 if (mountain and 4 <= i <= 6) else 500.0
        points.append(ElevationPoint(distance_nm=d, elevation_ft=elev, lat=46.0, lon=6.0 + i * 0.2))
    return ElevationProfile(
        route_name="test", points=points,
        max_elevation_ft=5000 if mountain else 500,
        total_distance_nm=_MW_TOTAL,
    )


def _mw_cross_section(wind_speed_kt: float) -> RouteCrossSection:
    route_points = [
        RoutePoint(lat=46.0, lon=6.0 + i * 0.2, distance_from_origin_nm=i * _MW_TOTAL / (_MW_N - 1))
        for i in range(_MW_N)
    ]
    forecasts = [
        WaypointForecast(
            waypoint=Waypoint(icao=f"P{i}", name=f"P{i}", lat=46.0, lon=6.0 + i * 0.2),
            model=ModelSource.GFS,
            fetched_at=_T0,
            hourly=[HourlyForecast(
                time=_T0,
                pressure_levels=[PressureLevelData(
                    pressure_hpa=800,
                    wind_speed_kt=wind_speed_kt,
                    wind_direction_deg=270.0,
                )],
            )],
        )
        for i in range(_MW_N)
    ]
    return RouteCrossSection(
        model=ModelSource.GFS, route_points=route_points,
        fetched_at=_T0, point_forecasts=forecasts,
    )


def _mw_ctx(wind_speed_kt: float, *, ridge_inversion: bool = False, mountain: bool = True) -> RouteContext:
    sounding = SoundingAnalysis(
        indices=ThermodynamicIndices(),
        inversion_layers=(
            [InversionLayer(base_ft=5500, top_ft=6500, strength_c=3.0)]
            if ridge_inversion else []
        ),
        vertical_motion=VerticalMotionAssessment(
            classification=VerticalMotionClass.QUIESCENT,
        ),
    )
    analyses = [
        RoutePointAnalysis(
            point_index=i, lat=46.0, lon=6.0 + i * 0.2,
            distance_from_origin_nm=i * _MW_TOTAL / (_MW_N - 1),
            interpolated_time=_T0, forecast_hour=_T0, track_deg=90.0,
            sounding={"gfs": sounding},
        )
        for i in range(_MW_N)
    ]
    return RouteContext(
        analyses=analyses,
        cross_sections=[_mw_cross_section(wind_speed_kt)],
        elevation=_mw_elevation(mountain),
        models=["gfs"],
        cruise_altitude_ft=8000,
        flight_ceiling_ft=18000,
        total_distance_nm=_MW_TOTAL,
    )


class TestMountainWindHighlights:
    def test_corroborated_red_with_wave_signature_band(self):
        # 32 kt + ridge inversion → rotor-day red at the mountain points, with
        # both the terrain-hugging ridge_wind band and the corroborating
        # inversion band as separate kinds.
        ctx = _mw_ctx(32.0, ridge_inversion=True)
        res = MountainWindEvaluator.evaluate(ctx, _params(MountainWindEvaluator))
        h = res.per_model[0].highlights
        assert h is not None
        _assert_tiles(h.ribbon, _MW_TOTAL)
        assert [seg.severity for seg in h.ribbon] == [S.GREEN, S.RED, S.GREEN]
        kinds = {r.kind for r in h.regions}
        assert kinds == {"ridge_wind", "wave_signature"}
        ridge = next(r for r in h.regions if r.kind == "ridge_wind")
        wave = next(r for r in h.regions if r.kind == "wave_signature")
        assert ridge.base_ft == 5000 and ridge.top_ft == 7000  # terrain → +margin
        assert wave.base_ft == 5500 and wave.top_ft == 6500    # the inversion
        assert h.peak_dist_nm is not None

    def test_windy_ridge_amber_no_wave_band(self):
        ctx = _mw_ctx(25.0)
        res = MountainWindEvaluator.evaluate(ctx, _params(MountainWindEvaluator))
        h = res.per_model[0].highlights
        assert [seg.severity for seg in h.ribbon] == [S.GREEN, S.AMBER, S.GREEN]
        assert {r.kind for r in h.regions} == {"ridge_wind"}
        assert all(r.severity == S.AMBER for r in h.regions)

    def test_no_mountains_solid_green_ribbon(self):
        # GREEN-not-UNAVAILABLE choice: a flat route still gets an explicit
        # all-green ribbon, not highlights=None.
        ctx = _mw_ctx(45.0, mountain=False)
        res = MountainWindEvaluator.evaluate(ctx, _params(MountainWindEvaluator))
        h = res.per_model[0].highlights
        assert h is not None
        assert len(h.ribbon) == 1 and h.ribbon[0].severity == S.GREEN
        assert h.regions == []

    def test_mountain_points_without_wind_are_unavailable(self):
        ctx = _mw_ctx(32.0)
        from dataclasses import replace
        res = MountainWindEvaluator.evaluate(
            replace(ctx, cross_sections=[]), _params(MountainWindEvaluator),
        )
        h = res.per_model[0].highlights
        assert [seg.severity for seg in h.ribbon] == [S.GREEN, S.UNAVAILABLE, S.GREEN]


# ---------------------------------------------------------------------------
# freezing_precip
# ---------------------------------------------------------------------------

def _fp_dry() -> SoundingAnalysis:
    return SoundingAnalysis(
        precipitation=PrecipitationAssessment(),
        derived_levels=[
            DerivedLevel(pressure_hpa=1000, altitude_ft=400, wet_bulb_c=4.0),
            DerivedLevel(pressure_hpa=900, altitude_ft=3200, wet_bulb_c=0.5),
            DerivedLevel(pressure_hpa=800, altitude_ft=6400, wet_bulb_c=-4.0),
        ],
    )


def _fp_primed() -> SoundingAnalysis:
    return SoundingAnalysis(
        precipitation=PrecipitationAssessment(),
        derived_levels=[
            DerivedLevel(pressure_hpa=1000, altitude_ft=500, wet_bulb_c=-2.0),
            DerivedLevel(pressure_hpa=925, altitude_ft=3000, wet_bulb_c=1.0),
            DerivedLevel(pressure_hpa=900, altitude_ft=4000, wet_bulb_c=1.5),
            DerivedLevel(pressure_hpa=800, altitude_ft=6500, wet_bulb_c=-5.0),
        ],
    )


def _fp_active() -> SoundingAnalysis:
    return SoundingAnalysis(precipitation=PrecipitationAssessment(
        surface_phase=PrecipPhase.FREEZING_RAIN,
        freezing_rain_risk=True, total_mm=1.2,
    ))


class TestFreezingPrecipHighlights:
    def test_active_red_column_with_fallback_top(self):
        # Active point has no derived levels → the cutout falls back to the
        # fixed shallow AGL band (terrain unknown → 0 + 3000).
        ctx = _ctx([_fp_dry()] * 4 + [_fp_active()])
        res = FreezingPrecipEvaluator.evaluate(ctx, _params(FreezingPrecipEvaluator))
        h = res.per_model[0].highlights
        assert h is not None
        _assert_tiles(h.ribbon, ctx.total_distance_nm)
        assert h.ribbon[-1].severity == S.RED
        assert len(h.regions) == 1
        assert h.regions[0].kind == "freezing_precip_column"
        assert h.regions[0].severity == S.RED
        assert h.regions[0].base_ft is None      # down to the surface
        assert h.regions[0].top_ft == 3000       # fallback AGL band

    def test_primed_amber_column_topped_by_warm_nose(self):
        ctx = _ctx([_fp_primed()] * 6)
        res = FreezingPrecipEvaluator.evaluate(ctx, _params(FreezingPrecipEvaluator))
        h = res.per_model[0].highlights
        assert all(seg.severity == S.AMBER for seg in h.ribbon)
        assert len(h.regions) == 1
        assert h.regions[0].base_ft is None
        assert h.regions[0].top_ft == 4000       # warm-nose top from the profile

    def test_dry_green_no_regions(self):
        ctx = _ctx([_fp_dry()] * 5)
        res = FreezingPrecipEvaluator.evaluate(ctx, _params(FreezingPrecipEvaluator))
        h = res.per_model[0].highlights
        assert len(h.ribbon) == 1 and h.ribbon[0].severity == S.GREEN
        assert h.regions == []

    def test_no_signal_model_has_no_highlights(self):
        # Soundings without precip data anywhere → UNAVAILABLE, no highlights.
        ctx = _ctx([SoundingAnalysis()] * 5)
        res = FreezingPrecipEvaluator.evaluate(ctx, _params(FreezingPrecipEvaluator))
        assert res.per_model[0].status == AdvisoryStatus.UNAVAILABLE
        assert res.per_model[0].highlights is None


# ---------------------------------------------------------------------------
# enroute_precip
# ---------------------------------------------------------------------------

def _precip(phase: PrecipPhase, intensity: PrecipIntensity) -> SoundingAnalysis:
    return SoundingAnalysis(precipitation=PrecipitationAssessment(
        surface_phase=phase, surface_intensity=intensity,
    ))


class TestEnroutePrecipHighlights:
    def test_moderate_snow_red_full_column(self):
        ctx = _ctx([_precip(PrecipPhase.SNOW, PrecipIntensity.MODERATE)] * 6)
        res = EnroutePrecipEvaluator.evaluate(ctx, _params(EnroutePrecipEvaluator))
        h = res.per_model[0].highlights
        assert h is not None
        _assert_tiles(h.ribbon, ctx.total_distance_nm)
        assert all(seg.severity == S.RED for seg in h.ribbon)
        assert len(h.regions) == 1
        assert h.regions[0].kind == "precip_column"
        assert h.regions[0].base_ft is None and h.regions[0].top_ft is None

    def test_light_snow_amber(self):
        ctx = _ctx([_precip(PrecipPhase.SNOW, PrecipIntensity.LIGHT)] * 6)
        res = EnroutePrecipEvaluator.evaluate(ctx, _params(EnroutePrecipEvaluator))
        h = res.per_model[0].highlights
        assert all(seg.severity == S.AMBER for seg in h.ribbon)

    def test_moderate_rain_amber(self):
        ctx = _ctx([_precip(PrecipPhase.RAIN, PrecipIntensity.MODERATE)] * 6)
        res = EnroutePrecipEvaluator.evaluate(ctx, _params(EnroutePrecipEvaluator))
        h = res.per_model[0].highlights
        assert all(seg.severity == S.AMBER for seg in h.ribbon)

    def test_light_rain_green_no_region(self):
        ctx = _ctx([_precip(PrecipPhase.RAIN, PrecipIntensity.LIGHT)] * 6)
        res = EnroutePrecipEvaluator.evaluate(ctx, _params(EnroutePrecipEvaluator))
        h = res.per_model[0].highlights
        assert all(seg.severity == S.GREEN for seg in h.ribbon)
        assert h.regions == []

    def test_no_signal_no_highlights(self):
        ctx = _ctx([SoundingAnalysis()] * 5)
        res = EnroutePrecipEvaluator.evaluate(ctx, _params(EnroutePrecipEvaluator))
        assert res.per_model[0].status == AdvisoryStatus.UNAVAILABLE
        assert res.per_model[0].highlights is None
