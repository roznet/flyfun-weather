"""Tests for the approach-lookup wiring behind ``approach_feasibility`` (#509)
and its departure counterpart ``departure_feasibility`` (#829).

The advisory reads ``RouteContext.arrival_approaches``. Two wiring points build
that context — the briefing run and the recalculate/preview re-run — and the
recalculate one has no resolved ``RouteConfig``, so it resolves the destination
from the recomputed airport conditions instead. If either point drops the
lookup, the advisory silently degrades to a grey UNAVAILABLE card; these tests
pin both.
"""

from __future__ import annotations

from datetime import datetime
from types import SimpleNamespace

import pytest

from weatherbrief.models import AirportApproaches, RunwayApproach
from weatherbrief.models.airport_conditions import (
    AirportConditions,
    AirportConditionsSummary,
)
from weatherbrief.tasks import advise


class _Route:
    """Just enough of RouteConfig for the destination lookup."""

    class _Waypoint:
        def __init__(self, icao):
            self.icao = icao

    def __init__(self, icao):
        self.destination = self._Waypoint(icao)


def _conditions(arr_icao: str) -> AirportConditions:
    return AirportConditions(
        departure=AirportConditionsSummary(icao="EGTK", name="EGTK"),
        arrival=AirportConditionsSummary(icao=arr_icao, name=arr_icao),
    )


class TestArrivalIcaoResolution:
    def test_prefers_the_resolved_route(self):
        assert advise._arrival_icao(_Route("EGKA"), _conditions("EGBJ")) == "EGKA"

    def test_falls_back_to_airport_conditions_on_the_recalc_path(self):
        """Recalculate has no RouteConfig — the destination comes from the pack."""
        assert advise._arrival_icao(None, _conditions("EGBJ")) == "EGBJ"

    def test_no_route_and_no_conditions_is_none(self):
        assert advise._arrival_icao(None, None) is None


class TestCollectArrivalApproaches:
    def test_looks_up_the_destination(self, monkeypatch):
        expected = AirportApproaches(
            icao="EGKA", has_procedure_data=True,
            approaches=[RunwayApproach(name="RWY02 ILS", approach_type="ILS", runway_id="02")],
        )
        seen: dict = {}

        def _fake(icaos, db_path, declared=None):
            seen["icaos"] = icaos
            seen["db_path"] = db_path
            seen["declared"] = declared
            return {"EGKA": expected}

        monkeypatch.setattr("weatherbrief.airports.get_runway_approaches", _fake)
        assert advise._collect_airport_approaches("EGKA", "nav.db") is expected
        assert seen == {"icaos": ["EGKA"], "db_path": "nav.db", "declared": None}

    def test_passes_the_declared_list_through(self, monkeypatch):
        """The #510 declarations must reach the collection step from every path.

        Pinned because the helper swallows every exception to protect the
        briefing: a signature drift here would surface as a silently absent
        advisory, not as an error.
        """
        seen: dict = {}

        def _fake(icaos, db_path, declared=None):
            seen["declared"] = declared
            return {"EGTF": AirportApproaches(icao="EGTF")}

        monkeypatch.setattr("weatherbrief.airports.get_runway_approaches", _fake)
        advise._collect_airport_approaches("EGTF", "nav.db", ["EGTF", "EGSX"])
        assert seen["declared"] == ["EGTF", "EGSX"]

    @pytest.mark.parametrize("icao,db_path", [(None, "nav.db"), ("EGKA", None), ("EGKA", "")])
    def test_missing_inputs_degrade_to_none(self, icao, db_path):
        """None means "could not assess", which the evaluator reports as UNAVAILABLE."""
        assert advise._collect_airport_approaches(icao, db_path) is None

    def test_lookup_failure_never_breaks_the_briefing(self, monkeypatch):
        def _boom(icaos, db_path):
            raise RuntimeError("nav.db is corrupt")

        monkeypatch.setattr("weatherbrief.airports.get_runway_approaches", _boom)
        assert advise._collect_airport_approaches("EGKA", "nav.db") is None


# ---------------------------------------------------------------------------
# Departure side (#829): the same lookup at the ORIGIN, for departure_feasibility
# ---------------------------------------------------------------------------


class _FullRoute:
    """Just enough of RouteConfig for both ends."""

    name = "t"
    cruise_altitude_ft = 8000
    flight_ceiling_ft = 12000
    flight_duration_hours = 1.0

    def __init__(self, origin, destination):
        self.origin = _Route._Waypoint(origin)
        self.destination = _Route._Waypoint(destination)


class TestDepartureIcaoResolution:
    def test_prefers_the_resolved_route(self):
        assert advise._departure_icao(_FullRoute("LSZF", "EDNY"), _conditions("EDNY")) == "LSZF"

    def test_falls_back_to_airport_conditions_on_the_recalc_path(self):
        assert advise._departure_icao(None, _conditions("EDNY")) == "EGTK"

    def test_no_route_and_no_conditions_is_none(self):
        assert advise._departure_icao(None, None) is None


def _dep_conditions(category):
    from weatherbrief.models.airport_conditions import AirportModelCondition

    def _summary(icao, cat):
        return AirportConditionsSummary(icao=icao, name=icao, conditions=[
            AirportModelCondition(model=m, flight_category=cat) for m in ("ecmwf", "gfs")
        ])

    from weatherbrief.models.airport_conditions import FlightCategory
    return AirportConditions(
        departure=_summary("LSZF", category), arrival=_summary("EDNY", FlightCategory.VFR),
    )


def _fake_lookup(seen):
    def _fake(icaos, db_path, declared=None):
        seen.append((tuple(icaos), declared))
        return {i: AirportApproaches(icao=i, iso_country="CH") for i in icaos}
    return _fake


def _capture_ctx(monkeypatch):
    import weatherbrief.analysis.advisories as adv_pkg

    captured: dict = {}

    def fake_eval(ctx, *a, **k):
        captured["ctx"] = ctx
        return []

    monkeypatch.setattr(adv_pkg, "evaluate_all", fake_eval)
    monkeypatch.setattr(advise, "_compute_route_sun", lambda *a: None)
    monkeypatch.setattr(advise, "_resolve_analyses", lambda a, *rest: a)
    return captured


class TestDepartureWiring:
    """Every RouteContext builder must fill ``departure_approaches``, or the card
    silently degrades to UNAVAILABLE on that path (#829, the #509 lesson)."""

    def _grade(self, ctx):
        from weatherbrief.analysis.advisories.departure_feasibility import (
            DepartureFeasibilityEvaluator,
        )
        return DepartureFeasibilityEvaluator.evaluate(ctx, {})

    def test_briefing_and_recalc_grade_the_same(self, monkeypatch, tmp_path):
        from weatherbrief.models.airport_conditions import FlightCategory
        from weatherbrief.models import AdvisoryStatus
        import weatherbrief.tasks.artifacts as artifacts

        seen: list = []
        monkeypatch.setattr("weatherbrief.airports.get_runway_approaches", _fake_lookup(seen))
        conds = _dep_conditions(FlightCategory.MVFR)
        captured = _capture_ctx(monkeypatch)

        # Briefing path: the resolved route names the origin.
        monkeypatch.setattr(advise, "_compute_airport_conditions", lambda *a, **k: conds)
        advise.run_advisories(
            [], [], None, ["ecmwf", "gfs"], _FullRoute("LSZF", "EDNY"), 100.0,
            airports_db_path="nav.db", declared_approach_icaos=["EGTF"],
        )
        briefing_ctx = captured.pop("ctx")

        # Recalc path: no RouteConfig, the origin comes from the recomputed
        # airport conditions.
        manifest = SimpleNamespace(
            analyses=[], cruise_altitude_ft=8000, models=["ecmwf", "gfs"],
            total_distance_nm=100.0, sun=None,
        )
        monkeypatch.setattr(artifacts, "load_route_analyses", lambda p: manifest)
        monkeypatch.setattr(artifacts, "load_cross_sections", lambda p: [])
        monkeypatch.setattr(artifacts, "load_elevation_profile", lambda p: None)
        advise.run_advisories_from_pack(
            tmp_path, None, flight_ceiling_ft=12000,
            airports_db_path="nav.db",
            airport_conditions_recompute=lambda *a: conds,
            declared_approach_icaos=["EGTF"], persist=False,
        )
        recalc_ctx = captured.pop("ctx")

        assert briefing_ctx.departure_approaches is not None
        assert recalc_ctx.departure_approaches == briefing_ctx.departure_approaches
        assert (("LSZF",), ["EGTF"]) in seen
        a, b = self._grade(briefing_ctx), self._grade(recalc_ctx)
        assert a.aggregate_status == b.aggregate_status == AdvisoryStatus.AMBER
        assert a.aggregate_detail == b.aggregate_detail

    def test_alt_time_rerun_grades_the_origin_at_the_alt_time(self, monkeypatch, tmp_path):
        """``run_alt_from_pack`` re-runs at another departure TIME from the same
        origin: the card reads that origin's procedures and the alt-time
        category (here VFR, so GREEN where the primary briefing was AMBER)."""
        from weatherbrief.models.airport_conditions import FlightCategory
        from weatherbrief.models import AdvisoryStatus
        import weatherbrief.tasks.analyze as analyze
        import weatherbrief.tasks.artifacts as artifacts
        from tests.analysis.advisories.conftest import _make_rpa, _make_sounding

        seen: list = []
        monkeypatch.setattr("weatherbrief.airports.get_runway_approaches", _fake_lookup(seen))
        captured = _capture_ctx(monkeypatch)
        rpas = [_make_rpa(0, 0.0, sounding={"ecmwf": _make_sounding(), "gfs": _make_sounding()})]
        monkeypatch.setattr(artifacts, "load_cross_sections", lambda p: [object()])
        monkeypatch.setattr(
            artifacts, "load_route_points",
            lambda p: [SimpleNamespace(distance_from_origin_nm=0.0)],
        )
        monkeypatch.setattr(artifacts, "load_elevation_profile", lambda p: None)
        monkeypatch.setattr(analyze, "analyze_all_route_points", lambda **kw: rpas)

        route = SimpleNamespace(
            name="t", flight_duration_hours=1.0, cruise_altitude_ft=8000,
            flight_ceiling_ft=14000,
            origin=SimpleNamespace(icao="LSZF"), destination=SimpleNamespace(icao="EDNY"),
        )
        advise.run_alt_from_pack(
            tmp_path, datetime(2026, 10, 19, 14, 0), route,
            airports_db_path="nav.db",
            airport_conditions_recompute=lambda *a: _dep_conditions(FlightCategory.VFR),
            declared_approach_icaos=["EGTF"], persist=False, detect_fronts=False,
        )
        ctx = captured["ctx"]
        assert ctx.departure_approaches is not None
        assert ctx.departure_approaches.icao == "LSZF"
        assert (("LSZF",), ["EGTF"]) in seen
        assert self._grade(ctx).aggregate_status == AdvisoryStatus.GREEN
