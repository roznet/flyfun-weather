"""Tests for the departure_feasibility advisory evaluator (issue #829).

Pins every row of the agreed grade map: a field with any published or declared
approach is GREEN; with none, VFR is GREEN, the exempt country (GB) is GREEN with
a note, and anything below VFR is AMBER, never RED; a data gap is UNAVAILABLE.
"""

from __future__ import annotations

import pytest

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories.departure_feasibility import (
    ADVISORY_ID,
    EXEMPT_COUNTRIES,
    DepartureFeasibilityEvaluator,
)
from weatherbrief.analysis.advisories.registry import get_catalog
from weatherbrief.analysis.advisories.strings import _STRINGS
from weatherbrief.models import AdvisoryStatus
from weatherbrief.models.airport_conditions import (
    SOURCE_USER_DECLARED,
    AirportApproaches,
    AirportConditions,
    AirportConditionsSummary,
    AirportModelCondition,
    FlightCategory,
    RunwayApproach,
)

MODELS = ["ecmwf", "gfs", "icon"]

_ILS_25 = RunwayApproach(name="RWY25 ILS", approach_type="ILS", runway_id="25")
_DECLARED = RunwayApproach(name="Declared unpublished approach", source=SOURCE_USER_DECLARED)


def _conditions(
    *categories: FlightCategory, dep_icao: str = "LSZF",
) -> AirportConditions:
    """Departure conditions, one category per model in MODELS order."""
    if len(categories) == 1:
        categories = categories * len(MODELS)
    dep = AirportConditionsSummary(icao=dep_icao, name=dep_icao, conditions=[
        AirportModelCondition(model=m, flight_category=c)
        for m, c in zip(MODELS, categories)
    ])
    arr = AirportConditionsSummary(icao="EDNY", name="EDNY", conditions=[
        AirportModelCondition(model=m, flight_category=FlightCategory.VFR) for m in MODELS
    ])
    return AirportConditions(departure=dep, arrival=arr)


def _approaches(
    *approaches: RunwayApproach, icao: str = "LSZF", iso_country: str | None = "CH",
    lookup_failed: bool = False, coverage_gap: bool = False,
) -> AirportApproaches:
    return AirportApproaches(
        icao=icao, has_procedure_data=bool(approaches), iso_country=iso_country,
        lookup_failed=lookup_failed, coverage_gap=coverage_gap,
        approaches=list(approaches),
    )


def _ctx(
    conditions: AirportConditions | None,
    approaches: AirportApproaches | None,
    locale: str | None = None,
) -> RouteContext:
    return RouteContext(
        analyses=[], cross_sections=[], elevation=None, models=MODELS,
        cruise_altitude_ft=8000, flight_ceiling_ft=12000, total_distance_nm=150,
        airport_conditions=conditions, departure_approaches=approaches,
        locale=locale,
    )


def _evaluate(ctx: RouteContext):
    return DepartureFeasibilityEvaluator.evaluate(ctx, {})


class TestGradeMap:
    def test_lszf_mvfr_is_amber_naming_the_field_and_category(self):
        """The #829 pilot report: Birrfeld, no procedures, MVFR at departure."""
        result = _evaluate(_ctx(_conditions(FlightCategory.MVFR), _approaches()))
        assert result.aggregate_status == AdvisoryStatus.AMBER
        assert all(m.status == AdvisoryStatus.AMBER for m in result.per_model)
        assert "LSZF" in result.aggregate_detail
        assert "MVFR" in result.aggregate_detail
        assert "pick-up in the air" in result.aggregate_detail

    @pytest.mark.parametrize("category", [FlightCategory.IFR, FlightCategory.LIFR])
    def test_below_mvfr_is_still_amber_never_red(self, category):
        result = _evaluate(_ctx(_conditions(category), _approaches()))
        assert result.aggregate_status == AdvisoryStatus.AMBER
        assert all(m.status == AdvisoryStatus.AMBER for m in result.per_model)

    @pytest.mark.parametrize("category", list(FlightCategory))
    def test_never_red_at_any_category(self, category):
        for country in ("CH", "GB", None):
            result = _evaluate(_ctx(_conditions(category), _approaches(iso_country=country)))
            assert result.aggregate_status != AdvisoryStatus.RED

    def test_vfr_without_procedures_is_green(self):
        result = _evaluate(_ctx(_conditions(FlightCategory.VFR), _approaches()))
        assert result.aggregate_status == AdvisoryStatus.GREEN
        assert "flown visually" in result.aggregate_detail

    @pytest.mark.parametrize("category", list(FlightCategory))
    def test_field_with_approaches_is_green(self, category):
        """LSZG-like: a CH field with a published approach."""
        result = _evaluate(_ctx(
            _conditions(category, dep_icao="LSZG"),
            _approaches(_ILS_25, icao="LSZG"),
        ))
        assert result.aggregate_status == AdvisoryStatus.GREEN
        assert "1 published approach" in result.aggregate_detail

    def test_declared_approach_is_green_and_names_the_basis(self):
        result = _evaluate(_ctx(_conditions(FlightCategory.IFR), _approaches(_DECLARED)))
        assert result.aggregate_status == AdvisoryStatus.GREEN
        assert "declared unpublished approach" in result.aggregate_detail

    def test_gb_field_without_procedures_is_green_with_note(self):
        """EGTF Fairoaks: zero procedure rows, but the UK is exempt."""
        assert "GB" in EXEMPT_COUNTRIES
        result = _evaluate(_ctx(
            _conditions(FlightCategory.IFR, dep_icao="EGTF"),
            _approaches(icao="EGTF", iso_country="GB"),
        ))
        assert result.aggregate_status == AdvisoryStatus.GREEN
        assert "common in the UK" in result.aggregate_detail

    def test_unknown_country_is_flagged_not_exempt(self):
        result = _evaluate(_ctx(_conditions(FlightCategory.MVFR), _approaches(iso_country=None)))
        assert result.aggregate_status == AdvisoryStatus.AMBER

    def test_models_disagreeing_aggregate_by_majority(self):
        """Two MVFR, one VFR: majority AMBER, and the clause prints the consensus."""
        result = _evaluate(_ctx(
            _conditions(FlightCategory.MVFR, FlightCategory.MVFR, FlightCategory.VFR),
            _approaches(),
        ))
        statuses = [m.status for m in result.per_model]
        assert statuses == [AdvisoryStatus.AMBER, AdvisoryStatus.AMBER, AdvisoryStatus.GREEN]
        assert result.aggregate_status == AdvisoryStatus.AMBER
        assert "MVFR" in result.aggregate_detail


class TestUnavailable:
    def test_no_lookup_is_unavailable(self):
        """Old pack / no nav.db: ``departure_approaches=None``."""
        result = _evaluate(_ctx(_conditions(FlightCategory.MVFR), None))
        assert result.aggregate_status == AdvisoryStatus.UNAVAILABLE
        assert result.per_model == []

    def test_lookup_failed_is_unavailable(self):
        result = _evaluate(_ctx(
            _conditions(FlightCategory.MVFR), _approaches(lookup_failed=True),
        ))
        assert result.aggregate_status == AdvisoryStatus.UNAVAILABLE

    def test_coverage_gap_is_unavailable(self):
        """A US field in an unsurveyed country: no rows proves nothing."""
        result = _evaluate(_ctx(
            _conditions(FlightCategory.IFR, dep_icao="KCLE"),
            _approaches(icao="KCLE", iso_country="US", lookup_failed=True, coverage_gap=True),
        ))
        assert result.aggregate_status == AdvisoryStatus.UNAVAILABLE

    def test_no_airport_conditions_is_unavailable(self):
        result = _evaluate(_ctx(None, _approaches()))
        assert result.aggregate_status == AdvisoryStatus.UNAVAILABLE

    def test_model_without_condition_is_unavailable(self):
        conds = _conditions(FlightCategory.MVFR)
        conds.departure.conditions = conds.departure.conditions[:2]  # icon missing
        result = _evaluate(_ctx(conds, _approaches()))
        by_model = {m.model: m.status for m in result.per_model}
        assert by_model["icon"] == AdvisoryStatus.UNAVAILABLE
        assert by_model["ecmwf"] == AdvisoryStatus.AMBER


class TestCatalogAndCopy:
    def test_registered_in_flight_rules(self):
        entry = next(e for e in get_catalog() if e.id == ADVISORY_ID)
        assert entry.category == "flight_rules"
        assert entry.default_enabled is True
        assert entry.parameters == []

    def test_every_string_has_all_four_locales(self):
        keys = [k for k in _STRINGS if k.startswith("departure_feasibility.")]
        assert len(keys) == 5
        for key in keys:
            assert set(_STRINGS[key]) == {"en", "fr", "de", "es"}, key

    def test_german_briefing_uses_the_german_clause_head(self):
        result = _evaluate(_ctx(_conditions(FlightCategory.MVFR), _approaches(), locale="de"))
        assert result.aggregate_detail.startswith("Abfl LSZF: MVFR")
