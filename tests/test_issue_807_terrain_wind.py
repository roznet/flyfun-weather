"""Issue #807 (meteorology review 2026-08 §2.7, §2.9, §3.5, §3.8).

- unknown terrain elevation stays None (never 0 ft) and every terrain guard
  reads it as unknown;
- the altitude table never recommends an altitude below terrain + clearance and
  ranks UNAVAILABLE below GREEN;
- the alt-departure RouteContext carries a sun analysis recomputed for the
  shifted departure;
- E-Shear runs for every model present anywhere on the route;
- negative Ri is excluded from the EDR remap / calibration moments;
- cruise wind is interpolated between bracketing levels, with a snap guard.
"""

from __future__ import annotations

import math
from datetime import datetime
from types import SimpleNamespace

import pytest

from weatherbrief.models import (
    AdvisoryStatus,
    AltitudeAdvisoryRow,
    ElevationPoint,
    ElevationProfile,
    HourlyForecast,
    PressureLevelData,
)


def _profile(elevs: list[float | None], spacing_nm: float = 1.0) -> ElevationProfile:
    pts = [
        ElevationPoint(distance_nm=i * spacing_nm, elevation_ft=e, lat=46.0, lon=7.0 + i * 0.01)
        for i, e in enumerate(elevs)
    ]
    known = [e for e in elevs if e is not None]
    return ElevationProfile(
        route_name="t",
        points=pts,
        max_elevation_ft=max(known) if known else None,
        total_distance_nm=pts[-1].distance_nm if pts else 0,
        missing_point_count=len(elevs) - len(known),
    )


# ---------------------------------------------------------------------------
# 1. Terrain elevation: unknown is None, not sea level
# ---------------------------------------------------------------------------


class TestElevationProfileVoids:
    def test_void_stays_none_and_max_ignores_it(self, monkeypatch):
        import weatherbrief.fetch.elevation as el

        coords = [(46.0, 7.0, 0.0), (46.0, 7.1, 0.5), (46.0, 7.2, 1.0)]
        monkeypatch.setattr(
            el, "walk_route",
            lambda route, spacing: [(la, lo, d, None, None) for la, lo, d in coords],
        )
        srtm_vals = {7.0: 1000.0, 7.1: None, 7.2: 500.0}  # metres
        monkeypatch.setattr(
            el.srtm, "get_data",
            lambda **kw: SimpleNamespace(get_elevation=lambda la, lo: srtm_vals[lo]),
        )
        # Open-Meteo fallback fails for the void.
        monkeypatch.setattr(el, "_fetch_elevations_open_meteo", lambda c: [None] * len(c))

        prof = el.get_elevation_profile(SimpleNamespace(name="r"), spacing_nm=0.5)
        assert [p.elevation_ft for p in prof.points] == [3281, None, 1640]
        assert prof.missing_point_count == 1
        assert prof.max_elevation_ft == 3281
        assert prof.terrain_complete is False

        diag = el.elevation_diagnostic(prof)
        assert diag is not None
        assert diag.level == "warn"
        assert diag.code == "terrain_elevation_incomplete"
        assert "1/3" in diag.message

    def test_complete_profile_has_no_diagnostic(self):
        from weatherbrief.fetch.elevation import elevation_diagnostic

        assert elevation_diagnostic(_profile([100.0, 200.0])) is None
        assert elevation_diagnostic(None) is None

    def test_legacy_pack_json_still_loads(self):
        legacy = (
            '{"route_name":"t","points":[{"distance_nm":0,"elevation_ft":0,'
            '"lat":1,"lon":2}],"max_elevation_ft":0,"total_distance_nm":0}'
        )
        prof = ElevationProfile.model_validate_json(legacy)
        assert prof.missing_point_count == 0
        assert prof.terrain_complete is True

    def test_display_projection_fills_voids_for_clients(self):
        from weatherbrief.fetch.elevation import display_elevation_profile

        data = display_elevation_profile(_profile([None, 1000.0, None, 3000.0, None]))
        elevs = [p["elevation_ft"] for p in data["points"]]
        assert elevs == [1000.0, 1000.0, 2000, 3000.0, 3000.0]
        assert [p["elevation_known"] for p in data["points"]] == [
            False, True, False, True, False,
        ]
        assert data["missing_point_count"] == 3

        all_void = display_elevation_profile(_profile([None, None]))
        assert [p["elevation_ft"] for p in all_void["points"]] == [0, 0]
        assert all_void["max_elevation_ft"] == 0

    def test_api_projection_only_for_incomplete_profiles(self, tmp_path):
        from weatherbrief.api.packs import _elevation_wire_projection

        path = tmp_path / "elevation_profile.json"
        path.write_text(_profile([100.0, 200.0]).model_dump_json())
        assert _elevation_wire_projection(path) is None  # served raw

        path.write_text(_profile([100.0, None, 300.0]).model_dump_json())
        out = _elevation_wire_projection(path)
        assert out is not None
        assert all(p["elevation_ft"] is not None for p in out["points"])


class TestTerrainHelpersUnknown:
    def test_terrain_at_distance_none_on_void(self):
        from weatherbrief.analysis.advisories._helpers import terrain_at_distance

        prof = _profile([1000.0, None, 3000.0, 4000.0])
        assert terrain_at_distance(prof, 0.5) is None
        assert terrain_at_distance(prof, 1.0) is None
        assert terrain_at_distance(prof, 2.5) == pytest.approx(3500.0)

    def test_max_terrain_between_none_when_span_touches_void(self):
        from weatherbrief.analysis.advisories._helpers import max_terrain_between

        prof = _profile([1000.0, None, 3000.0, 4000.0])
        assert max_terrain_between(prof, 0.0, 2.0) is None
        assert max_terrain_between(prof, 0.2, 0.4) is None  # endpoint interpolation
        assert max_terrain_between(prof, 2.0, 3.0) == 4000.0

    def test_max_terrain_near_point_none_when_radius_has_void(self):
        from weatherbrief.analysis.advisories._helpers import max_terrain_near_point

        prof = _profile([1000.0, None, 3000.0, 4000.0, 500.0, 500.0, 500.0, 500.0])
        assert max_terrain_near_point(prof, 1.0, radius_nm=2.0) is None
        assert max_terrain_near_point(prof, 6.0, radius_nm=1.5) == 500.0

    def test_descent_escape_not_approved_over_unknown_terrain(self):
        from tests.test_altitude_advisories import _descend, _sounding
        from weatherbrief.analysis.sounding.advisories import compute_altitude_advisories

        adv = compute_altitude_advisories(
            {"gfs": _sounding(freezing_level_ft=9000)}, 8000, 18000,
            terrain_elevation_ft=None, terrain_unknown=True,
        )
        descend = _descend(adv)
        assert descend.altitude_ft == 8500  # meteorological answer unchanged
        assert descend.feasible is False
        assert "unknown" in descend.reason

    def test_icing_escape_void_counts_as_no_escape(self, icing_context_with_void):
        from weatherbrief.analysis.advisories.icing_escape import IcingEscapeEvaluator

        params = {p.key: p.default for p in IcingEscapeEvaluator.catalog_entry().parameters}
        result = IcingEscapeEvaluator.evaluate(icing_context_with_void, params)
        assert result.aggregate_status == AdvisoryStatus.RED


@pytest.fixture
def icing_context_with_void():
    """Icing everywhere with a warm-air escape at 7,000 ft over 500 ft terrain —
    viable — except the whole profile is void, so no point can verify it."""
    from weatherbrief.analysis.advisories import RouteContext
    from weatherbrief.models import IcingRisk, IcingType, IcingZone
    from tests.analysis.advisories.conftest import _make_rpa, _make_sounding

    zone = IcingZone(
        base_ft=7000, top_ft=10000, risk=IcingRisk.MODERATE, icing_type=IcingType.MIXED,
    )
    analyses = [
        _make_rpa(i, i * 20.0, sounding={"gfs": _make_sounding(
            freezing_level_ft=7000, icing_zones=[zone],
        )})
        for i in range(10)
    ]
    return RouteContext(
        analyses=analyses,
        cross_sections=[],
        elevation=_profile([None] * 41, spacing_nm=5.0),
        models=["gfs"],
        cruise_altitude_ft=8000,
        flight_ceiling_ft=18000,
        total_distance_nm=200,
    )


# ---------------------------------------------------------------------------
# 2. Altitude table: terrain floor, UNAVAILABLE ranking, alt-departure sun
# ---------------------------------------------------------------------------


def _clear_analyses():
    from tests.analysis.advisories.conftest import _make_rpa, _make_sounding

    return [
        _make_rpa(i, i * 20.0, sounding={"gfs": _make_sounding()})
        for i in range(10)
    ]


class TestAltitudeTableTerrainFloor:
    def test_rows_below_terrain_floor_not_swept_or_recommended(self):
        from weatherbrief.analysis.advisories.altitude_table import compute_altitude_table

        elev = _profile([500.0] * 10 + [5200.0] + [500.0] * 10, spacing_nm=10.0)
        table = compute_altitude_table(
            analyses=_clear_analyses(), cross_sections=[], elevation=elev,
            models=["gfs"], cruise_altitude_ft=8000, flight_ceiling_ft=14000,
            total_distance_nm=200,
        )
        assert table.terrain_floor_ft == 6200  # 5,200 ft + 1,000 ft clearance
        alts = {r.altitude_ft for r in table.rows}
        assert min(alts) >= 6200
        assert 8000 in alts
        assert table.best_below_cruise is None or table.best_below_cruise >= 6200

    def test_planned_cruise_below_floor_kept_but_never_best(self):
        from weatherbrief.analysis.advisories.altitude_table import compute_altitude_table

        elev = _profile([9500.0] * 5, spacing_nm=50.0)
        table = compute_altitude_table(
            analyses=_clear_analyses(), cross_sections=[], elevation=elev,
            models=["gfs"], cruise_altitude_ft=6000, flight_ceiling_ft=14000,
            total_distance_nm=200,
        )
        alts = {r.altitude_ft for r in table.rows}
        assert 6000 in alts  # the plan is shown
        assert alts - {6000} and min(alts - {6000}) >= 10500
        assert table.best_above_cruise is not None and table.best_above_cruise >= 10500
        assert table.best_below_cruise is None

    def test_incomplete_terrain_withholds_lower_recommendation(self):
        from weatherbrief.analysis.advisories.altitude_table import compute_altitude_table

        elev = _profile([500.0, None, 500.0, 500.0], spacing_nm=60.0)
        table = compute_altitude_table(
            analyses=_clear_analyses(), cross_sections=[], elevation=elev,
            models=["gfs"], cruise_altitude_ft=8000, flight_ceiling_ft=14000,
            total_distance_nm=180,
        )
        assert table.best_below_cruise is None
        assert table.best_above_cruise is not None

    def test_no_profile_keeps_legacy_unfloored_sweep(self):
        from weatherbrief.analysis.advisories.altitude_table import compute_altitude_table

        table = compute_altitude_table(
            analyses=_clear_analyses(), cross_sections=[], elevation=None,
            models=["gfs"], cruise_altitude_ft=8000, flight_ceiling_ft=14000,
            total_distance_nm=200,
        )
        assert table.terrain_floor_ft is None
        assert min(r.altitude_ft for r in table.rows) == 2000


def _row(alt: int, *statuses: AdvisoryStatus) -> AltitudeAdvisoryRow:
    st = {f"a{i}": s for i, s in enumerate(statuses)}
    return AltitudeAdvisoryRow(
        altitude_ft=alt, statuses=st,
        red_count=sum(s == AdvisoryStatus.RED for s in statuses),
        amber_count=sum(s == AdvisoryStatus.AMBER for s in statuses),
        green_count=sum(s == AdvisoryStatus.GREEN for s in statuses),
    )


class TestFindBestUnavailable:
    G, A, R, U = (AdvisoryStatus.GREEN, AdvisoryStatus.AMBER,
                  AdvisoryStatus.RED, AdvisoryStatus.UNAVAILABLE)

    def _best(self, rows, cruise=8000):
        from weatherbrief.analysis.advisories.altitude_table import _find_best

        return _find_best(rows, lambda r: True, cruise)

    def test_green_beats_unavailable(self):
        # The unavailable row is closer to cruise: before #807 it won the tie.
        rows = [_row(8000, self.G, self.U), _row(4000, self.G, self.G)]
        assert self._best(rows) == 4000

    def test_amber_beats_unavailable(self):
        rows = [_row(8000, self.G, self.U), _row(4000, self.G, self.A)]
        assert self._best(rows) == 4000

    def test_unavailable_beats_red(self):
        rows = [_row(8000, self.G, self.U), _row(10000, self.G, self.R)]
        assert self._best(rows) == 8000


def test_alt_departure_context_carries_recomputed_sun(monkeypatch, tmp_path):
    import weatherbrief.analysis.advisories as adv_pkg
    import weatherbrief.tasks.advise as advise
    import weatherbrief.tasks.analyze as analyze
    import weatherbrief.tasks.artifacts as artifacts
    from tests.analysis.advisories.conftest import _make_rpa, _make_sounding

    alt_rpas = [
        _make_rpa(0, 0.0, sounding={"gfs": _make_sounding()}),
        _make_rpa(1, 20.0, sounding={"gfs": _make_sounding(), "ecmwf": _make_sounding()}),
    ]
    monkeypatch.setattr(artifacts, "load_cross_sections", lambda p: [object()])
    monkeypatch.setattr(
        artifacts, "load_route_points",
        lambda p: [SimpleNamespace(distance_from_origin_nm=0.0),
                   SimpleNamespace(distance_from_origin_nm=20.0)],
    )
    monkeypatch.setattr(artifacts, "load_elevation_profile", lambda p: None)
    monkeypatch.setattr(analyze, "analyze_all_route_points", lambda **kw: alt_rpas)
    monkeypatch.setattr(advise, "_resolve_analyses", lambda a, *rest: a)
    sentinel = object()
    seen_sun_inputs = []

    def fake_sun(rpas, conds):
        seen_sun_inputs.append(rpas)
        return sentinel

    monkeypatch.setattr(advise, "_compute_route_sun", fake_sun)
    captured = {}

    def fake_eval(ctx, *a, **k):
        captured["ctx"] = ctx
        return []

    monkeypatch.setattr(adv_pkg, "evaluate_all", fake_eval)

    route = SimpleNamespace(
        name="t", flight_duration_hours=1.0, cruise_altitude_ft=8000,
        flight_ceiling_ft=14000, destination=SimpleNamespace(icao="BBBB"),
    )
    advise.run_alt_from_pack(
        tmp_path, datetime(2026, 3, 1, 15, 0), route, persist=False, detect_fronts=False,
    )
    ctx = captured["ctx"]
    assert ctx.sun is sentinel
    assert seen_sun_inputs == [alt_rpas]  # computed from the shifted analyses
    # Model set is the union across points, not point 0 only.
    assert ctx.models == ["gfs", "ecmwf"]


# ---------------------------------------------------------------------------
# 3. E-Shear: union of models across all points
# ---------------------------------------------------------------------------


def test_e_shear_runs_for_model_missing_at_origin(monkeypatch):
    import weatherbrief.analysis.sounding.e_shear as es
    from tests.analysis.advisories.conftest import _make_rpa, _make_sounding
    from weatherbrief.models import CATRiskLayer, CATRiskLevel, VerticalMotionAssessment
    from weatherbrief.tasks.analyze import _enrich_e_shear

    marker = [CATRiskLayer(base_ft=9000, top_ft=10000, risk=CATRiskLevel.MODERATE)]
    monkeypatch.setattr(es, "compute_e_shear_per_sounding", lambda levels, hws: list(marker))
    monkeypatch.setattr(es, "compute_hws_between_points", lambda a, b, d: {})

    def snd():
        return _make_sounding(vertical_motion=VerticalMotionAssessment())

    analyses = [
        _make_rpa(0, 0.0, sounding={"gfs": snd()}),  # ECMWF failed at the origin
        _make_rpa(1, 20.0, sounding={"gfs": snd(), "ecmwf": snd()}),
        _make_rpa(2, 40.0, sounding={"gfs": snd(), "ecmwf": snd()}),
    ]
    _enrich_e_shear(analyses)
    assert analyses[1].sounding["ecmwf"].vertical_motion.e_shear_layers == marker
    assert analyses[2].sounding["ecmwf"].vertical_motion.e_shear_layers == marker
    assert analyses[0].sounding["gfs"].vertical_motion.e_shear_layers == marker


# ---------------------------------------------------------------------------
# 4. EDR: negative Ri excluded
# ---------------------------------------------------------------------------


class TestEdrNegativeRi:
    def test_negative_ri_has_no_diagnostic(self):
        from weatherbrief.analysis.sounding.edr import RI_FLOOR, richardson_to_d

        assert richardson_to_d(-0.5) is None
        assert richardson_to_d(-1e-6) is None
        assert richardson_to_d(0.0) == pytest.approx(1.0 / RI_FLOOR)
        assert richardson_to_d(0.5) == pytest.approx(2.0)

    def test_accumulator_skips_negative_ri_levels(self):
        from weatherbrief.analysis.sounding.edr import EdrAccumulator

        acc = EdrAccumulator()
        levels = [
            SimpleNamespace(richardson_number=-2.0, altitude_ft=12000),
            SimpleNamespace(richardson_number=0.5, altitude_ft=12000),
            SimpleNamespace(richardson_number=2.0, altitude_ft=12000),
        ]
        acc.observe_richardson_levels("gfs", levels)
        rows = {r[2]: r for r in acc.rows()}
        _, _, _, n, s1, _ = rows["10_20kft"]
        assert n == 2
        assert s1 == pytest.approx(math.log(2.0) + math.log(0.5))


# ---------------------------------------------------------------------------
# 5. Cruise wind: interpolation + snap guard
# ---------------------------------------------------------------------------


def _hourly(levels: list[tuple[int, float | None, float | None]]) -> HourlyForecast:
    return HourlyForecast(
        time=datetime(2026, 3, 1, 12, 0),
        pressure_levels=[
            PressureLevelData(pressure_hpa=p, wind_speed_kt=s, wind_direction_deg=d)
            for p, s, d in levels
        ],
    )


class TestCruiseWindInterpolation:
    def test_bracketed_interpolates_vector_in_altitude(self):
        from weatherbrief.analysis.wind import _isa_alt_ft, pick_wind_at_pressure

        h = _hourly([(850, 20.0, 270.0), (700, 40.0, 270.0)])
        w = pick_wind_at_pressure(h, 750)
        frac = (_isa_alt_ft(750) - _isa_alt_ft(850)) / (_isa_alt_ft(700) - _isa_alt_ft(850))
        assert w.wind_speed_kt == pytest.approx(20.0 + frac * 20.0)
        assert w.wind_direction_deg == pytest.approx(270.0)
        assert w.pressure_hpa == 750

    def test_veer_across_north_does_not_average_to_south(self):
        from weatherbrief.analysis.wind import pick_wind_at_pressure

        h = _hourly([(800, 20.0, 350.0), (700, 20.0, 10.0)])
        w = pick_wind_at_pressure(h, 750)
        assert min(w.wind_direction_deg, 360 - w.wind_direction_deg) < 15
        assert w.wind_speed_kt == pytest.approx(20.0, abs=0.5)

    def test_exact_level_returned_unchanged(self):
        from weatherbrief.analysis.wind import pick_wind_at_pressure

        h = _hourly([(850, 20.0, 250.0), (700, 40.0, 270.0)])
        w = pick_wind_at_pressure(h, 850)
        assert (w.wind_speed_kt, w.wind_direction_deg) == (20.0, 250.0)

    def test_unbracketed_nearest_within_guard(self):
        from weatherbrief.analysis.wind import pick_wind_at_pressure

        # Target ~1,000 ft above the 700 hPa top level.
        h = _hourly([(850, 20.0, 250.0), (700, 40.0, 270.0)])
        w = pick_wind_at_pressure(h, 670)
        assert (w.wind_speed_kt, w.wind_direction_deg) == (40.0, 270.0)

    def test_unbracketed_beyond_guard_is_unavailable(self):
        from weatherbrief.analysis.wind import pick_wind_at_pressure

        # 500 hPa target is ~8,000 ft above the 700 hPa top level.
        h = _hourly([(850, 20.0, 250.0), (700, 40.0, 270.0)])
        assert pick_wind_at_pressure(h, 500) is None

    def test_missing_wind_levels_skipped_for_bracketing(self):
        from weatherbrief.analysis.wind import pick_wind_at_pressure

        h = _hourly([(850, 20.0, 270.0), (800, None, None), (700, 40.0, 270.0)])
        w = pick_wind_at_pressure(h, 800)
        assert 20.0 < w.wind_speed_kt < 40.0

    def test_no_valid_levels(self):
        from weatherbrief.analysis.wind import pick_wind_at_pressure

        assert pick_wind_at_pressure(_hourly([(850, None, None)]), 850) is None
