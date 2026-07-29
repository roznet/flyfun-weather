"""Tests for the destination-approach wiring behind ``approach_feasibility`` (#509).

The advisory reads ``RouteContext.arrival_approaches``. Two wiring points build
that context — the briefing run and the recalculate/preview re-run — and the
recalculate one has no resolved ``RouteConfig``, so it resolves the destination
from the recomputed airport conditions instead. If either point drops the
lookup, the advisory silently degrades to a grey UNAVAILABLE card; these tests
pin both.
"""

from __future__ import annotations

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
        assert advise._collect_arrival_approaches("EGKA", "nav.db") is expected
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
        advise._collect_arrival_approaches("EGTF", "nav.db", ["EGTF", "EGSX"])
        assert seen["declared"] == ["EGTF", "EGSX"]

    @pytest.mark.parametrize("icao,db_path", [(None, "nav.db"), ("EGKA", None), ("EGKA", "")])
    def test_missing_inputs_degrade_to_none(self, icao, db_path):
        """None means "could not assess", which the evaluator reports as UNAVAILABLE."""
        assert advise._collect_arrival_approaches(icao, db_path) is None

    def test_lookup_failure_never_breaks_the_briefing(self, monkeypatch):
        def _boom(icaos, db_path):
            raise RuntimeError("nav.db is corrupt")

        monkeypatch.setattr("weatherbrief.airports.get_runway_approaches", _boom)
        assert advise._collect_arrival_approaches("EGKA", "nav.db") is None
