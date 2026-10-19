"""One flight category per airport across advisories (#823).

Every airport-reading advisory (Airport Weather, VFR/IFR Feasibility, Approach
Feasibility) used to print its *representative* model's category, and each
advisory picks its representative on its own, so one briefing could read
``Arr EDQC: IFR`` on one card and ``Arr EDQC MVFR`` on the next. The aggregate
now prints the cross-model consensus (same rule as the grade) and names the
dissenting models; per-model text and every status are unchanged.
"""

from __future__ import annotations

import dataclasses
import re

import pytest

from tests.analysis.advisories.conftest import _make_elevation, _make_rpa, _make_sounding
from weatherbrief.analysis.advisories import RouteContext, evaluate_all
from weatherbrief.analysis.advisories.invariants import check_advisories
from weatherbrief.models import (
    AdvisoryAggregation,
    AdvisoryStatus,
    AirportClause,
    ModelAdvisoryResult,
    RouteAdvisoryResult,
)
from weatherbrief.models.advisories import airport_category_consensus
from weatherbrief.models.airport_conditions import (
    AirportApproaches,
    AirportConditions,
    AirportConditionsSummary,
    AirportModelCondition,
    FlightCategory,
    RunwayApproach,
)
from weatherbrief.models.analysis import CloudCoverage, EnhancedCloudLayer

VFR, MVFR, IFR, LIFR = (
    FlightCategory.VFR, FlightCategory.MVFR, FlightCategory.IFR, FlightCategory.LIFR,
)
MODELS = ["ecmwf", "gfs", "icon"]
AIRPORT_IDS = {"flight_category", "vfr_feasibility", "ifr_feasibility", "approach_feasibility"}

_CEILING = {VFR: 5000, MVFR: 2000, IFR: 800, LIFR: 300}
_VIS_SM = {VFR: 10.0, MVFR: 4.0, IFR: 2.0, LIFR: 0.5}


def _summary(icao: str, cats: dict[str, FlightCategory | None]) -> AirportConditionsSummary:
    return AirportConditionsSummary(icao=icao, name=icao, conditions=[
        AirportModelCondition(
            model=m, flight_category=c, ceiling_ft=_CEILING[c], visibility_sm=_VIS_SM[c],
        )
        for m, c in cats.items() if c is not None
    ])


def _ctx(
    arr: dict[str, FlightCategory | None],
    dep: dict[str, FlightCategory | None] | None = None,
    imc_models: tuple[str, ...] = (),
) -> RouteContext:
    """Three-model route into EDQC; ``imc_models`` cruise in OVC the whole way.

    The en-route IMC is what makes VFR Feasibility pick that model as its
    representative (largest flagged extent), the 10382 shape.
    """
    ovc = EnhancedCloudLayer(base_ft=6000, top_ft=12000, coverage=CloudCoverage.OVC)
    analyses = [
        _make_rpa(i, i * 20.0, sounding={
            m: _make_sounding(cloud_layers=[ovc] if m in imc_models else [])
            for m in MODELS
        })
        for i in range(10)
    ]
    return RouteContext(
        analyses=analyses,
        cross_sections=[],
        elevation=_make_elevation(),
        models=MODELS,
        cruise_altitude_ft=8000,
        flight_ceiling_ft=18000,
        total_distance_nm=200,
        airport_conditions=AirportConditions(
            departure=_summary("EGTK", dep or {m: VFR for m in MODELS}),
            arrival=_summary("EDQC", arr),
        ),
        arrival_approaches=AirportApproaches(
            icao="EDQC", has_procedure_data=True,
            approaches=[RunwayApproach(name="RWY27 ILS", approach_type="ILS", runway_id="27")],
        ),
    )


def _run(ctx: RouteContext, aggregation=AdvisoryAggregation.MAJORITY) -> dict[str, RouteAdvisoryResult]:
    results = evaluate_all(ctx, enabled_ids=AIRPORT_IDS, aggregation=aggregation)
    # IFR Feasibility RED on an airport alone publishes no route coverage — a
    # pre-existing ``flagged_has_coverage`` finding unrelated to the airport text.
    violations = [v for v in check_advisories(results) if v.rule != "flagged_has_coverage"]
    assert not violations, violations
    return {r.advisory_id: r for r in results}


def _printed_arr(detail: str) -> str | None:
    """The arrival category an aggregate sentence prints, or None."""
    m = re.search(r"Arr EDQC:? (VFR|MVFR|IFR|LIFR)\b", detail)
    return m.group(1) if m else None


# --- the helper ---------------------------------------------------------------

class TestConsensusHelper:
    def test_majority_names_the_dissent(self):
        assert airport_category_consensus(
            [("ecmwf", IFR), ("gfs", MVFR), ("icon", IFR)],
        ) == (IFR, [("gfs", MVFR)])

    def test_three_way_split_goes_to_the_worst(self):
        cat, dissent = airport_category_consensus(
            [("ecmwf", VFR), ("gfs", MVFR), ("icon", IFR)],
        )
        assert cat == IFR
        assert dissent == [("ecmwf", VFR), ("gfs", MVFR)]

    def test_worst_mode(self):
        assert airport_category_consensus(
            [("ecmwf", VFR), ("gfs", VFR), ("icon", LIFR)], AdvisoryAggregation.WORST,
        ) == (LIFR, [("ecmwf", VFR), ("gfs", VFR)])

    def test_missing_condition_is_never_counted(self):
        # One model missing must not count as VFR: 1 IFR + 1 VFR is a tie → IFR.
        assert airport_category_consensus(
            [("ecmwf", None), ("gfs", VFR), ("icon", IFR)],
        ) == (IFR, [("gfs", VFR)])
        assert airport_category_consensus([("ecmwf", None)]) == (None, [])


# --- the aggregate --------------------------------------------------------------

class TestAggregateClause:
    def _result(self, model, status, cat, suffix="", head="Arr EDQC "):
        clause = AirportClause(end="arr", category=cat, printed=True, head=head, suffix=suffix)
        return ModelAdvisoryResult.build(
            model=model, status=status, detail=f"{clause.text} | IMC over 50nm/200nm (25%)",
            affected=1, total=1, total_distance_nm=200, airport_clauses=[clause],
        )

    def test_dissenting_representative_keeps_its_suffix_in_the_note(self):
        # All tied on extent, so the first (gfs) speaks for the aggregate.
        per_model = [
            self._result("gfs", AdvisoryStatus.RED, LIFR, suffix=" ceiling 200ft < 500ft min"),
            self._result("ecmwf", AdvisoryStatus.RED, IFR),
            self._result("icon", AdvisoryStatus.RED, IFR),
        ]
        r = RouteAdvisoryResult.from_per_model("ifr_feasibility", per_model, {})
        assert r.representative_model == "gfs"
        assert r.aggregate_detail == (
            "Arr EDQC IFR (gfs LIFR ceiling 200ft < 500ft min) | IMC over 50nm/200nm (25%)"
        )
        assert r.airport_consensus == {"arr": IFR}
        # Per-model text untouched.
        assert per_model[0].detail.startswith("Arr EDQC LIFR ceiling 200ft")

    def test_agreeing_representative_keeps_suffix_after_the_note(self):
        per_model = [
            self._result("ecmwf", AdvisoryStatus.RED, IFR, suffix=" — ILS 27 usable", head="Arr EDQC: "),
            self._result("gfs", AdvisoryStatus.AMBER, MVFR, suffix=" — visual", head="Arr EDQC: "),
            self._result("icon", AdvisoryStatus.RED, IFR, suffix=" — ILS 27 usable", head="Arr EDQC: "),
        ]
        r = RouteAdvisoryResult.from_per_model("approach_feasibility", per_model, {})
        assert r.aggregate_detail.startswith("Arr EDQC: IFR (gfs MVFR) — ILS 27 usable")

    def test_unanimous_is_unchanged(self):
        per_model = [self._result(m, AdvisoryStatus.RED, IFR) for m in MODELS]
        r = RouteAdvisoryResult.from_per_model("vfr_feasibility", per_model, {})
        assert r.aggregate_detail == per_model[0].detail

    def test_old_packs_without_clauses_are_unchanged(self):
        m = ModelAdvisoryResult.build(
            model="gfs", status=AdvisoryStatus.RED, detail="Arr EDQC IFR",
            affected=1, total=1, total_distance_nm=200,
        )
        r = RouteAdvisoryResult.from_per_model("vfr_feasibility", [m], {})
        assert r.aggregate_detail == "Arr EDQC IFR"
        assert r.airport_consensus == {}

    def test_ambiguous_clause_text_is_left_alone(self):
        # The clause text appears twice: rewriting the first occurrence could hit
        # the wrong span, so the representative's sentence stands as written.
        per_model = [self._result(m, AdvisoryStatus.RED, c) for m, c in
                     [("gfs", LIFR), ("ecmwf", IFR), ("icon", IFR)]]
        rep = per_model[0]
        per_model[0] = rep.model_copy(update={"detail": f"{rep.detail} | {rep.airport_clauses[0].text}"})
        r = RouteAdvisoryResult.from_per_model("ifr_feasibility", per_model, {})
        assert r.representative_model == "gfs"
        assert r.aggregate_detail == per_model[0].detail

    def test_dissent_note_is_localized(self):
        from weatherbrief.analysis.advisories.strings import _STRINGS
        assert set(_STRINGS["airport.dissent"]) == {"en", "fr", "de", "es"}


# --- across advisories, through the evaluators -------------------------------------

class TestAcrossAdvisories:
    def test_10382_shape(self):
        """2 IFR + 1 MVFR; VFR Feasibility's representative is the MVFR model."""
        ctx = _ctx({"ecmwf": IFR, "gfs": MVFR, "icon": IFR}, imc_models=("gfs",))
        r = _run(ctx)

        vfr = r["vfr_feasibility"]
        assert vfr.aggregate_status == AdvisoryStatus.RED
        assert vfr.representative_model == "gfs"
        # gfs's own sentence still says MVFR; the aggregate says the consensus.
        gfs = next(m for m in vfr.per_model if m.model == "gfs")
        assert "Arr EDQC MVFR" in gfs.detail
        assert "Arr EDQC IFR (gfs MVFR)" in vfr.aggregate_detail

        assert "Arr EDQC: IFR (gfs MVFR)" in r["flight_category"].aggregate_detail
        for adv in r.values():
            assert adv.airport_consensus.get("arr") == IFR
            printed = _printed_arr(adv.aggregate_detail)
            assert printed in (None, "IFR"), (adv.advisory_id, adv.aggregate_detail)

    def test_10367_shape_three_way_split(self):
        """1-1-1 at both ends: worst wins, both other models named."""
        cats = {"ecmwf": VFR, "gfs": MVFR, "icon": IFR}
        r = _run(_ctx(cats, dep=cats))
        fc = r["flight_category"].aggregate_detail
        assert "Dep EGTK: IFR (ecmwf VFR, gfs MVFR)" in fc
        assert "Arr EDQC: IFR (ecmwf VFR, gfs MVFR)" in fc
        assert "Arr EDQC IFR (ecmwf VFR, gfs MVFR)" in r["vfr_feasibility"].aggregate_detail
        # Approach Feasibility is GREEN in all three, so ecmwf (VFR) speaks for
        # it: its verdict travels inside its own dissent entry, not after the
        # consensus it was never computed for.
        approach = r["approach_feasibility"]
        assert approach.representative_model == "ecmwf"
        assert approach.aggregate_detail.startswith("Arr EDQC: IFR (ecmwf VFR — ")
        assert approach.aggregate_detail.endswith("; gfs MVFR)")

    def test_worst_aggregation(self):
        cats = {"ecmwf": VFR, "gfs": VFR, "icon": LIFR}
        majority = _run(_ctx(cats))
        assert majority["flight_category"].airport_consensus["arr"] == VFR
        assert "Arr EDQC: VFR (icon LIFR)" in majority["flight_category"].aggregate_detail

        worst = _run(_ctx(cats), AdvisoryAggregation.WORST)
        for adv in worst.values():
            assert adv.airport_consensus["arr"] == LIFR
            assert _printed_arr(adv.aggregate_detail) in (None, "LIFR")
        assert "Arr EDQC: LIFR (ecmwf VFR, gfs VFR)" in worst["flight_category"].aggregate_detail
        assert "Arr EDQC LIFR (ecmwf VFR, gfs VFR)" in worst["ifr_feasibility"].aggregate_detail

    def test_missing_condition_is_unavailable_not_vfr(self):
        r = _run(_ctx({"ecmwf": None, "gfs": VFR, "icon": IFR}))
        for adv in r.values():
            assert adv.airport_consensus["arr"] == IFR
        # Approach Feasibility speaks for gfs (ecmwf has no arrival): consensus
        # IFR, gfs named, and no dissent entry for the model with no data.
        approach = r["approach_feasibility"].aggregate_detail
        assert approach.startswith("Arr EDQC: IFR (gfs VFR")
        assert "ecmwf" not in approach

    @pytest.mark.parametrize("locale, word", [
        ("en", "ceiling"), ("fr", "plafond"), ("de", "Decke"), ("es", "techo"),
    ])
    def test_lifr_below_min_suffix_reads_in_every_locale(self, locale, word):
        """``ifr.lifr_below_min`` is a suffix after "{label} {icao} LIFR"."""
        ctx = dataclasses.replace(_ctx({"ecmwf": IFR, "gfs": IFR, "icon": LIFR}), locale=locale)
        ifr = _run(ctx)["ifr_feasibility"]
        icon = next(m for m in ifr.per_model if m.model == "icon")
        assert f"EDQC LIFR {word} 300ft" in icon.detail

    @pytest.mark.parametrize("aggregation", list(AdvisoryAggregation))
    def test_statuses_do_not_depend_on_the_clause(self, aggregation):
        """The consensus only rewrites text: grades match a clause-free rebuild."""
        r = _run(_ctx({"ecmwf": IFR, "gfs": MVFR, "icon": LIFR}, imc_models=("gfs",)), aggregation)
        for adv in r.values():
            stripped = [m.model_copy(update={"airport_clauses": []}) for m in adv.per_model]
            plain = RouteAdvisoryResult.from_per_model(
                adv.advisory_id, stripped, adv.parameters_used, aggregation=aggregation,
            )
            assert plain.aggregate_status == adv.aggregate_status
            assert plain.representative_model == adv.representative_model
