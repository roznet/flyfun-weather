"""#1013: icing over-called below a thin deck.

Four changes, one test group each: trimmed zone edges, the freezing-level cap,
the warm cruise, and one icing source for digest and card. The fifth, scaling by
the level's own cloud fraction, was dropped before landing; group 1 pins that.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories.aircraft_icing import (
    grade_aircraft_icing_model,
    icing_picture,
    resolve_aircraft_icing_params,
)
from weatherbrief.analysis.advisories._helpers import temperature_at_altitude
from weatherbrief.analysis.sounding.icing import (
    _INDEX_LIGHT,
    _level_nwp_cover_pct,
    assess_icing_zones_ogimet_nwp,
)
from weatherbrief.analysis.sounding.icing_common import (
    cap_zone_at_freezing,
    group_icing_levels,
    trim_zone_edges,
)
from weatherbrief.digest.prompt_builder import build_digest_context
from weatherbrief.models import (
    AdvisoryStatus,
    AircraftEquipage,
    CloudCoverage,
    DerivedLevel,
    EnhancedCloudLayer,
    ForecastSnapshot,
    HourlyForecast,
    IcingRisk,
    IcingType,
    IcingZone,
    ModelSource,
    NWPCloudDiagnostics,
    NWPCloudLayerDiag,
    PressureLevelData,
    RouteAdvisoriesManifest,
    RouteCrossSection,
    RoutePoint,
    SoundingAnalysis,
    WaypointAnalysis,
    WaypointForecast,
)

from .analysis.advisories.conftest import _make_elevation, _make_rpa, _make_sounding


def _lv(p, alt, t, td=None, caf=None):
    td = t - 0.2 if td is None else td
    return DerivedLevel(
        pressure_hpa=p, altitude_ft=alt, temperature_c=t, dewpoint_c=td,
        dewpoint_depression_c=t - td, relative_humidity_pct=95.0,
        cloud_area_fraction_pct=caf,
    )


# ── 1. cover: kept as #844 (local cover deferred) ──────────────────


class TestCoverKept:
    """Scaling by the level's own fraction was dropped before landing (#1014).

    Scored on US PIREPs it removed real icing: a 20 % cloud fraction still has
    cloud, and the pilot in it gets the full icing. The cover stays
    ``max(band cover, deck cover)`` (§54) until a gate-not-scale rule is scored.
    """

    DIAG = NWPCloudDiagnostics(
        low=NWPCloudLayerDiag(cover_pct=92.0),
        mid=NWPCloudLayerDiag(cover_pct=0.0),
        high=NWPCloudLayerDiag(cover_pct=0.0),
    )
    DECK = EnhancedCloudLayer(
        base_ft=2400, top_ft=2500, coverage=CloudCoverage.OVC,
        mean_cloud_cover_pct=95.0, source="nwp_3d",
    )
    FEW_LEVEL = dict(p=920, alt=2950, t=-4.1)

    def test_level_fraction_does_not_lower_the_cover(self):
        lv = _lv(**self.FEW_LEVEL, caf=15.0)
        assert _level_nwp_cover_pct(lv, [self.DECK], 92.0, 0.0, 0.0, self.DIAG) == 95.0

    def test_band_cover_when_above_the_deck_cover(self):
        deck = self.DECK.model_copy(update={"mean_cloud_cover_pct": 40.0})
        lv = _lv(**self.FEW_LEVEL, caf=15.0)
        assert _level_nwp_cover_pct(lv, [deck], 92.0, 0.0, 0.0, self.DIAG) == 92.0


# ── 2. trimmed zone edges ──────────────────────────────────────────


def _items(levels, indices):
    return [(lv, IcingType.MIXED, IcingRisk.NONE, idx) for lv, idx in zip(levels, indices)]


class TestTrimEdges:
    LEVELS = [_lv(1000 - 25 * i, 500 + 700 * i, -1.0 - i) for i in range(5)]

    def test_edges_below_light_trimmed_interior_kept(self):
        items = _items(self.LEVELS, [3.0, 15.0, 5.0, 20.0, 6.0])
        kept = trim_zone_edges(items, _INDEX_LIGHT)
        assert [it[0].pressure_hpa for it in kept] == [975, 950, 925]

    def test_group_with_no_light_level_left_whole(self):
        items = _items(self.LEVELS, [3.0, 4.0])
        assert trim_zone_edges(items, _INDEX_LIGHT) == items

    def test_grouping_trims_before_building(self):
        items = _items(self.LEVELS, [3.0, 15.0, 5.0, 20.0, 6.0])
        zones = group_icing_levels(
            items, lambda g: [it[0].pressure_hpa for it in g],
            all_levels=self.LEVELS, min_edge_index=_INDEX_LIGHT,
        )
        assert zones == [[975, 950, 925]]  # not split by the interior 5.0


# ── 3. freezing-level cap ──────────────────────────────────────────


class TestFreezingCap:
    def test_single_level_zone_capped_at_zero_crossing(self):
        warm = _lv(950, 1500, 1.0)
        cold = _lv(925, 2000, -1.0)
        zone = IcingZone(base_ft=1500, top_ft=2500, risk=IcingRisk.LIGHT)  # widened ±500
        capped = cap_zone_at_freezing(zone, cold, cold, [warm, cold])
        assert capped.base_ft == 1750  # 0 °C halfway between +1 and -1
        assert capped.top_ft == 2500

    def test_warm_nose_aloft_caps_the_top(self):
        cold = _lv(925, 2000, -1.0)
        nose = _lv(900, 3000, 2.0)
        zone = IcingZone(base_ft=1500, top_ft=2500, risk=IcingRisk.LIGHT)
        capped = cap_zone_at_freezing(zone, cold, cold, [cold, nose])
        assert capped.top_ft == round(2000 + 1000 * 1 / 3)
        assert capped.base_ft == 1500  # column cold below: no floor

    def test_ogimet_nwp_zone_never_below_freezing_level(self):
        deck = EnhancedCloudLayer(
            base_ft=1800, top_ft=2600, coverage=CloudCoverage.OVC,
            mean_cloud_cover_pct=95.0, source="nwp_3d",
        )
        levels = [_lv(975, 1800, 1.0, caf=0.0), _lv(950, 2200, -1.0, caf=95.0)]
        zones = assess_icing_zones_ogimet_nwp(levels, [deck])
        assert len(zones) == 1
        # Widened to 1,700–2,700 ft, then floored at the 0 °C crossing (2,000 ft).
        assert (zones[0].base_ft, zones[0].top_ft) == (2000, 2700)


# ── 4. warm cruise ─────────────────────────────────────────────────

CRUISE = 1500
NON_FIKI = AircraftEquipage(known=True, fiki=False, ceiling_ft=10000)


def _cross_section(temps_by_point: dict[int, list[tuple[float, float]]], n: int) -> RouteCrossSection:
    """GFS cross-section whose hourly levels are (altitude ft, °C) pairs."""
    t = datetime(2026, 3, 1, 9, 0)
    forecasts = []
    for i in range(n):
        levels = [
            PressureLevelData(
                pressure_hpa=1000 - 25 * k, temperature_c=temp,
                geopotential_height_m=alt / 3.28084,
            )
            for k, (alt, temp) in enumerate(temps_by_point.get(i, [(0, 5.0), (3000, 0.5)]))
        ]
        forecasts.append(WaypointForecast(
            waypoint={"icao": f"P{i}", "name": f"P{i}", "lat": 48.0, "lon": 2.0},
            model=ModelSource.GFS,
            fetched_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
            hourly=[HourlyForecast(time=t, pressure_levels=levels)],
        ))
    return RouteCrossSection(
        model=ModelSource.GFS,
        route_points=[RoutePoint(lat=48.0, lon=2.0, distance_from_origin_nm=i * 10.0) for i in range(n)],
        fetched_at=datetime(2026, 3, 1, tzinfo=timezone.utc),
        point_forecasts=forecasts,
    )


def _warm_ctx(cruise_temp_c: float, n: int = 11) -> RouteContext:
    """Every point: a moderate zone 700 ft above cruise, freezing level 1,450 ft
    (just below cruise), and the profile at cruise at *cruise_temp_c*."""
    zone = IcingZone(base_ft=2200, top_ft=3200, risk=IcingRisk.MODERATE, icing_type=IcingType.MIXED)
    profile = [(0, cruise_temp_c + 1.0), (CRUISE, cruise_temp_c), (4000, -5.0)]
    analyses = [
        _make_rpa(i, i * 10.0, {"gfs": _make_sounding(freezing_level_ft=1450, icing_zones=[zone])})
        for i in range(n)
    ]
    return RouteContext(
        analyses=analyses, cross_sections=[_cross_section({i: profile for i in range(n)}, n)],
        elevation=_make_elevation(100, n, (n - 1) * 10.0), models=["gfs"],
        cruise_altitude_ft=CRUISE, flight_ceiling_ft=8000,
        total_distance_nm=(n - 1) * 10.0, cruise_speed_ias_kt=80, equipage=NON_FIKI,
    )


class TestWarmCruise:
    def test_temperature_at_altitude_reads_geopotential_heights(self):
        ctx = _warm_ctx(0.4)
        rpa = ctx.analyses[0]
        t = temperature_at_altitude(ctx.cross_sections, "gfs", 0, 750, rpa.forecast_hour)
        assert t == pytest.approx(0.9)
        assert temperature_at_altitude(ctx.cross_sections, "gfs", 0, 9000, rpa.forecast_hour) is None

    def test_zone_in_band_above_warm_cruise_counts_no_minutes(self):
        ctx = _warm_ctx(0.4)
        params = resolve_aircraft_icing_params(ctx)
        picture = icing_picture(ctx, "gfs", params)
        assert all(p.warm_at_cruise and not p.cruise_zones for p in picture)
        assert grade_aircraft_icing_model(ctx, "gfs", params).status == AdvisoryStatus.GREEN

    def test_sub_zero_cruise_keeps_the_band(self):
        ctx = _warm_ctx(-0.5)
        params = resolve_aircraft_icing_params(ctx)
        picture = icing_picture(ctx, "gfs", params)
        assert all(p.cruise_zones and not p.warm_at_cruise for p in picture)
        assert grade_aircraft_icing_model(ctx, "gfs", params).status == AdvisoryStatus.RED


# ── 5. one icing source ────────────────────────────────────────────


def test_digest_prints_the_zones_the_card_graded(sample_route):
    target_time = datetime(2026, 2, 17, 9, 0, 0)
    dd = IcingZone(base_ft=1609, top_ft=2609, risk=IcingRisk.LIGHT,
                   icing_type=IcingType.CLEAR, mean_wet_bulb_c=-1.0)
    nwp = IcingZone(base_ft=2467, top_ft=3949, risk=IcingRisk.MODERATE,
                    icing_type=IcingType.MIXED, mean_wet_bulb_c=-4.0)
    sounding = SoundingAnalysis(
        icing_zones=[dd], icing_ogimet_dd_zones=[dd], icing_ogimet_nwp_zones=[nwp],
        nwp_cloud_layers=[],
    )
    snapshot = ForecastSnapshot(
        route=sample_route, target_date="2026-02-17", fetch_date="2026-02-15",
        days_out=2,
        analyses=[WaypointAnalysis(
            waypoint=sample_route.waypoints[0], target_time=target_time,
            sounding={"ecmwf": sounding},
        )],
    )

    graded = RouteAdvisoriesManifest(engine_methods={
        "icing_method": "ogimet_nwp", "cloud_source": "nwp", "convective_method": "nwp",
    })
    ctx = build_digest_context(snapshot, target_time, route_advisories=graded)
    assert "Icing zone [ecmwf]: moderate mixed 2467-3949ft" in ctx
    assert "1609-2609ft" not in ctx

    # A manifest from before #1013 keeps the stored zones (old prompt exactly).
    old = build_digest_context(snapshot, target_time, route_advisories=RouteAdvisoriesManifest())
    assert "1609-2609ft" in old


# ── follow-ups after landing (#1014 review) ────────────────────────


def test_freezing_cap_matches_copied_levels_by_pressure():
    """A zone built from copies of the levels is still capped (was skipped)."""
    warm = _lv(950, 1500, 1.0)
    cold = _lv(925, 2000, -1.0)
    zone = IcingZone(base_ft=1500, top_ft=2500, risk=IcingRisk.LIGHT)
    copy = cold.model_copy()
    capped = cap_zone_at_freezing(zone, copy, copy, [warm, cold])
    assert capped.base_ft == 1750


def test_warm_lookup_reads_the_lowest_level_just_below_it():
    """A low cruise just under the lowest delivered level reads its temperature."""
    cs = _cross_section({0: [(2000, 1.5), (4000, -2.0)]}, 1)
    t = datetime(2026, 3, 1, 9, 0)
    assert temperature_at_altitude([cs], "gfs", 0, 1500, t) is None
    assert temperature_at_altitude([cs], "gfs", 0, 1500, t, extend_below_ft=1000) == 1.5
    assert temperature_at_altitude([cs], "gfs", 0, 500, t, extend_below_ft=1000) is None


def test_text_digest_prints_the_zones_the_card_graded(sample_route):
    from weatherbrief.digest.text import format_digest

    target_time = datetime(2026, 2, 17, 9, 0, 0)
    dd = IcingZone(base_ft=1609, top_ft=2609, risk=IcingRisk.LIGHT,
                   icing_type=IcingType.CLEAR, mean_wet_bulb_c=-1.0)
    nwp = IcingZone(base_ft=2467, top_ft=3949, risk=IcingRisk.MODERATE,
                    icing_type=IcingType.MIXED, mean_wet_bulb_c=-4.0)
    sounding = SoundingAnalysis(
        icing_zones=[dd], icing_ogimet_dd_zones=[dd], icing_ogimet_nwp_zones=[nwp],
        nwp_cloud_layers=[],
    )
    snapshot = ForecastSnapshot(
        route=sample_route, target_date="2026-02-17", fetch_date="2026-02-15",
        days_out=2,
        # The text digest prints a waypoint's analysis only under its forecast.
        forecasts=[WaypointForecast(
            waypoint=sample_route.waypoints[0], model=ModelSource.ECMWF,
            fetched_at=datetime(2026, 2, 15, tzinfo=timezone.utc),
            hourly=[HourlyForecast(time=target_time)],
        )],
        analyses=[WaypointAnalysis(
            waypoint=sample_route.waypoints[0], target_time=target_time,
            sounding={"ecmwf": sounding},
        )],
    )
    graded = RouteAdvisoriesManifest(engine_methods={
        "icing_method": "ogimet_nwp", "cloud_source": "nwp", "convective_method": "nwp",
    })
    text = format_digest(snapshot, target_time, route_advisories=graded)
    assert "2467" in text and "1609" not in text
    assert "1609" in format_digest(snapshot, target_time)
