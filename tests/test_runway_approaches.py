"""Tests for ``get_runway_approaches`` — approaches joined to runway ends (#509).

Uses the real euro_aip ``Airport`` / ``Runway`` / ``Procedure`` classes and mocks
only the DB layer, so a change to the euro_aip contract (procedure fields, the
``procedures_query`` collection) fails here rather than silently degrading every
destination to "no approach".
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from euro_aip.models.airport import Airport
from euro_aip.models.procedure import Procedure
from euro_aip.models.runway import Runway

from weatherbrief.airports import (
    _is_circling_name,
    _normalize_runway_ident,
    _procedure_coverage_countries,
    get_runway_approaches,
)


def _airport(
    ident: str, runways: list[Runway], procedures: list[Procedure],
    iso_country: str = "GB",
) -> Airport:
    airport = Airport(ident=ident)
    airport.runways = runways
    airport.procedures = procedures
    airport.iso_country = iso_country
    return airport


def _runway(le: str, he: str, *, closed: bool = False, headings: bool = True) -> Runway:
    return Runway(
        airport_ident="EGKA", closed=closed,
        le_ident=le, le_heading_degT=20.0 if headings else None,
        he_ident=he, he_heading_degT=200.0 if headings else None,
    )


def _approach(name: str, approach_type: str | None, runway_ident: str | None) -> Procedure:
    return Procedure(
        name=name, procedure_type="approach",
        approach_type=approach_type, runway_ident=runway_ident,
    )


class _FakeCollection:
    def __init__(self, airports: dict[str, Airport]):
        self._airports = airports

    def get(self, icao):
        return self._airports.get(icao)

    def with_procedures(self):
        """Mirrors euro_aip's AirportCollection.with_procedures()."""
        return [a for a in self._airports.values() if getattr(a, "procedures", None)]


class _FakeModel:
    def __init__(self, airports: dict[str, Airport]):
        self.airports = _FakeCollection(airports)


def _lookup(airports: dict[str, Airport], icaos: list[str], declared=None):
    """Look up approaches with the dataset-coverage gate pinned OPEN.

    These tests exercise the approach/runway join, not dataset coverage, and a
    one-airport fake world has no other field to establish that its country is
    surveyed. Pinning every present country as covered keeps the gate a no-op
    here; :class:`TestProcedureCoverageGate` exercises the real thing.
    """
    covered = frozenset(
        c for c in (getattr(a, "iso_country", None) for a in airports.values()) if c
    )
    _procedure_coverage_countries.cache_clear()
    with patch("weatherbrief.airports._load_airport_model", return_value=_FakeModel(airports)), \
            patch("weatherbrief.airports._procedure_coverage_countries", return_value=covered):
        return get_runway_approaches(icaos, "nav.db", declared)


def _lookup_real_coverage(airports: dict[str, Airport], icaos: list[str]):
    """Look up approaches with the real coverage computation in play."""
    _procedure_coverage_countries.cache_clear()
    with patch("weatherbrief.airports._load_airport_model", return_value=_FakeModel(airports)):
        return get_runway_approaches(icaos, "nav.db")


class TestNormalizeRunwayIdent:
    @pytest.mark.parametrize("raw,expected", [
        ("02", "2"), ("2", "2"), ("13L", "13L"), ("rwy 09r", "9R"),
        (" 27 ", "27"), (None, None), ("", None), ("A", None),
    ])
    def test_canonicalizes_for_joining(self, raw, expected):
        assert _normalize_runway_ident(raw) == expected


class TestCirclingDetection:
    @pytest.mark.parametrize("name", ["RNP A", "NDB C", "LOC A", "VOR/DME B"])
    def test_letter_suffix_is_circling(self, name):
        assert _is_circling_name(name) is True

    @pytest.mark.parametrize("name", [
        # The #509 finding: 223 of 262 ident-less rows are parser artifacts,
        # so a naive "no runway ident => circling" rule is wrong ~85% of the time.
        "MIPS: RNP (LNAV) ARINC CODING",
        "RWY13 ILS LOC",
        # X/Y/Z are straight-in variant designators, not circling letters.
        "ILS Z",
        "",
    ])
    def test_other_names_are_not_circling(self, name):
        assert _is_circling_name(name) is False


class TestGetRunwayApproaches:
    def test_joins_approach_to_a_live_runway_end(self):
        airports = {"EGKA": _airport(
            "EGKA", [_runway("02", "20")], [_approach("RWY02 ILS", "ILS", "02")],
        )}
        result = _lookup(airports, ["EGKA"])["EGKA"]
        assert result.has_procedure_data is True
        assert result.has_iap is True
        assert result.served_runway_ids == {"02"}
        assert result.approaches[0].approach_type == "ILS"

    def test_zero_padding_does_not_break_the_join(self):
        airports = {"EGKA": _airport(
            "EGKA", [_runway("02", "20")], [_approach("RWY2 ILS", "ILS", "2")],
        )}
        assert _lookup(airports, ["EGKA"])["EGKA"].served_runway_ids == {"02"}

    def test_approach_on_a_runway_the_field_does_not_have_is_unresolved(self):
        """Not collapsed into "circling" — unresolved alignment is its own state."""
        airports = {"EGKA": _airport(
            "EGKA", [_runway("02", "20")], [_approach("RWY09 RNP", "RNP", "09")],
        )}
        result = _lookup(airports, ["EGKA"])["EGKA"]
        assert result.has_iap is True
        assert result.served_runway_ids == set()
        assert result.approaches[0].circling is False

    def test_closed_runway_is_not_a_landing_option(self):
        airports = {"EGKA": _airport(
            "EGKA", [_runway("02", "20", closed=True)],
            [_approach("RWY02 ILS", "ILS", "02")],
        )}
        assert _lookup(airports, ["EGKA"])["EGKA"].served_runway_ids == set()

    def test_end_without_a_true_heading_cannot_be_paired_with_wind(self):
        airports = {"EGKA": _airport(
            "EGKA", [_runway("02", "20", headings=False)],
            [_approach("RWY02 ILS", "ILS", "02")],
        )}
        assert _lookup(airports, ["EGKA"])["EGKA"].served_runway_ids == set()

    def test_circling_procedure_is_flagged(self):
        airports = {"EGKA": _airport(
            "EGKA", [_runway("02", "20")], [_approach("RNP A", "RNP", None)],
        )}
        approach = _lookup(airports, ["EGKA"])["EGKA"].approaches[0]
        assert approach.circling is True
        assert approach.runway_id is None

    def test_airport_with_no_procedures_reports_no_procedure_data(self):
        airports = {"EGTF": _airport("EGTF", [_runway("06", "24")], [])}
        result = _lookup(airports, ["EGTF"])["EGTF"]
        assert result.has_procedure_data is False
        assert result.has_iap is False

    def test_departure_procedures_are_not_approaches(self):
        sid = Procedure(name="RWY02 SID", procedure_type="departure", runway_ident="02")
        airports = {"EGKA": _airport("EGKA", [_runway("02", "20")], [sid])}
        result = _lookup(airports, ["EGKA"])["EGKA"]
        assert result.has_procedure_data is True  # rows exist…
        assert result.has_iap is False            # …but none is an approach

    def test_unknown_icao_degrades_without_raising(self):
        result = _lookup({}, ["ZZZZ"])["ZZZZ"]
        assert result.has_procedure_data is False
        assert result.approaches == []

    def test_the_egka_shape_from_the_issue(self):
        """IAPs on 02/20, nothing on 06/24 or 13/31 — the misalignment case."""
        airports = {"EGKA": _airport(
            "EGKA",
            [_runway("02", "20"), _runway("06", "24"), _runway("13", "31")],
            [_approach("RWY02 RNP", "RNP", "02"), _approach("RWY20 RNP", "RNP", "20")],
        )}
        result = _lookup(airports, ["EGKA"])["EGKA"]
        assert result.served_runway_ids == {"02", "20"}
        assert "24" not in result.served_runway_ids


class TestFailureNeverImpersonatesAbsence:
    """A data gap must not become "this field has no instrument approach".

    That string is the grade map's most severe input (RED at IFR/LIFR), so
    every path that fails to determine the picture sets ``lookup_failed``
    instead of returning an empty approach list.
    """

    def test_unknown_icao_is_a_failed_lookup_not_an_absence(self):
        result = _lookup({}, ["ZZZZ"])["ZZZZ"]
        assert result.lookup_failed is True
        assert result.has_procedure_data is False
        assert result.approaches == []

    def test_raising_procedure_query_is_a_failed_lookup(self):
        class _Exploding(Airport):
            @property
            def procedures_query(self):
                raise ValueError("malformed procedure row")

        airport = _Exploding(ident="EGKA")
        airport.runways = [_runway("02", "20")]
        airport.procedures = []
        result = _lookup({"EGKA": airport}, ["EGKA"])["EGKA"]
        assert result.lookup_failed is True
        assert result.approaches == []

    def test_a_genuine_absence_is_not_a_failed_lookup(self):
        """EGTF Fairoaks really has no approach — that IS a fact to grade on."""
        airports = {"EGTF": _airport("EGTF", [_runway("06", "24")], [])}
        result = _lookup(airports, ["EGTF"])["EGTF"]
        assert result.lookup_failed is False
        assert result.has_iap is False


class TestDeclaredUnpublishedApproaches:
    """Injection of the pilot's declared unpublished approaches (issue #510).

    Resolving the declaration in the collection step is what keeps the
    evaluator pure — it sees one approach list and only has to recognise the
    source. These pin that the injected entry can never confer credit it has no
    basis for.
    """

    def test_declared_icao_gets_a_synthetic_approach(self):
        """EGTF Fairoaks: zero procedure rows, but the pilot flies a cloud break."""
        airports = {"EGTF": _airport("EGTF", [_runway("06", "24")], [])}
        result = _lookup(airports, ["EGTF"], ["EGTF"])["EGTF"]
        assert result.has_iap is True
        assert result.has_declared_approach is True
        assert result.has_published_iap is False
        assert result.published == []

    def test_undeclared_icao_is_untouched(self):
        airports = {"EGTF": _airport("EGTF", [_runway("06", "24")], [])}
        result = _lookup(airports, ["EGTF"], ["EGSX"])["EGTF"]
        assert result.has_iap is False
        assert result.has_declared_approach is False

    def test_declaration_carries_no_alignment_and_no_class(self):
        """The type is fixed and the runway absent BY CONSTRUCTION.

        Letting a user declare "ILS" would hand them a 200 ft decision height on
        a procedure with no plate; a runway would earn straight-in alignment
        credit against the wind. Neither is available to declare, so neither can
        be claimed.
        """
        airports = {"EGTF": _airport("EGTF", [_runway("06", "24")], [])}
        declared = _lookup(airports, ["EGTF"], ["EGTF"])["EGTF"].approaches[0]
        assert declared.runway_id is None
        assert declared.approach_type is None
        assert declared.is_declared is True

    def test_declaration_does_not_add_a_served_runway(self):
        """``served_runway_ids`` drives the alignment copy — it must stay published."""
        airports = {
            "EGKA": _airport(
                "EGKA", [_runway("02", "20")],
                [_approach("RWY02 ILS", "ILS", "02")],
            ),
        }
        result = _lookup(airports, ["EGKA"], ["EGKA"])["EGKA"]
        assert result.served_runway_ids == {"02"}
        assert result.has_declared_approach is True
        assert [a.name for a in result.published] == ["RWY02 ILS"]

    def test_matching_is_case_and_whitespace_insensitive(self):
        airports = {"EGTF": _airport("EGTF", [_runway("06", "24")], [])}
        result = _lookup(airports, ["EGTF"], [" egtf ", ""])["EGTF"]
        assert result.has_declared_approach is True

    def test_declaration_never_overrides_a_failed_lookup(self):
        """``lookup_failed`` means "we could not determine the picture".

        A declaration does not determine it either — and turning that state
        into a gradeable one would let a data gap produce a verdict, which is
        the failure mode ``lookup_failed`` exists to prevent.
        """
        result = _lookup({}, ["EGTF"], ["EGTF"])["EGTF"]
        assert result.lookup_failed is True
        assert result.has_declared_approach is False
        assert result.has_iap is False


class TestProcedureCoverageGate:
    """Zero procedure rows means two different things depending on coverage.

    While ``nav.db`` was euro_aip's European-only build, "no procedure rows at
    this airport" genuinely meant "no published approach" — the fact EGTF
    Fairoaks and the #510 declared-approach feature are built on. Once the DB
    gained ~3,600 US airports with runways and no procedures at all, the same
    emptiness came to mean "unsurveyed" there, and grading it as "no IAP" hands
    the grade map its most severe input on missing data (observed: every US
    arrival graded Approach Feasibility RED).

    The discriminator is per-COUNTRY coverage, never geography: a country is
    surveyed when any of its airports carries procedures. Per-airport emptiness
    proves nothing — euro_aip holds procedures for 128 of 430 French airports
    and the rest genuinely have none.
    """

    def _world(self):
        """A surveyed country (GB, one field with an IAP) plus an unsurveyed one."""
        return {
            # GB is surveyed: EGKK carries a procedure.
            "EGKK": _airport("EGKK", [_runway("08", "26")],
                             [_approach("RWY08 ILS", "ILS", "08")], iso_country="GB"),
            # ...so EGTF's own emptiness is a real "no published approach".
            "EGTF": _airport("EGTF", [_runway("06", "24")], [], iso_country="GB"),
            # US has runways everywhere and procedures nowhere: a coverage gap.
            "KORD": _airport("KORD", [_runway("10L", "28R")], [], iso_country="US"),
        }

    def test_unsurveyed_country_cannot_assert_no_approach(self):
        """The regression: a US field must abstain, not grade."""
        result = _lookup_real_coverage(self._world(), ["KORD"])["KORD"]
        assert result.lookup_failed is True
        assert result.coverage_gap is True
        assert result.has_iap is False

    def test_surveyed_country_still_grades_an_empty_field(self):
        """EGTF must keep grading — #510 exists precisely for this case.

        This is the half a blanket "disable outside Europe" switch would get
        right by accident and a per-airport emptiness check would get wrong.
        """
        result = _lookup_real_coverage(self._world(), ["EGTF"])["EGTF"]
        assert result.lookup_failed is False
        assert result.coverage_gap is False
        assert result.has_iap is False
        assert result.has_procedure_data is False

    def test_airport_with_approaches_is_untouched(self):
        result = _lookup_real_coverage(self._world(), ["EGKK"])["EGKK"]
        assert result.lookup_failed is False
        assert result.coverage_gap is False
        assert [a.name for a in result.published] == ["RWY08 ILS"]

    def test_coverage_is_derived_from_data_not_hardcoded(self):
        """Ingesting US procedures must switch the US on with no code change."""
        world = self._world()
        _procedure_coverage_countries.cache_clear()
        with patch("weatherbrief.airports._load_airport_model",
                   return_value=_FakeModel(world)):
            assert _procedure_coverage_countries("nav.db") == frozenset({"GB"})

        # Now one US field gains a published approach.
        world["KJFK"] = _airport("KJFK", [_runway("04L", "22R")],
                                 [_approach("RWY04L ILS", "ILS", "04L")],
                                 iso_country="US")
        _procedure_coverage_countries.cache_clear()
        with patch("weatherbrief.airports._load_airport_model",
                   return_value=_FakeModel(world)):
            assert _procedure_coverage_countries("nav.db") == frozenset({"GB", "US"})

        # ...and KORD stops abstaining, with no change to this module.
        result = _lookup_real_coverage(world, ["KORD"])["KORD"]
        assert result.lookup_failed is False
        assert result.coverage_gap is False

    def test_procedure_rows_that_are_not_approaches_still_grade(self):
        """``has_procedure_data`` and the coverage gate are different questions.

        A field with departure/arrival procedures but no approach has real
        data saying "no IAP here" — that must not be mistaken for a gap.
        """
        world = self._world()
        world["EGSX"] = _airport(
            "EGSX", [_runway("04", "22")],
            [Procedure(name="SID FOO", procedure_type="departure",
                       approach_type=None, runway_ident="04")],
            iso_country="GB",
        )
        result = _lookup_real_coverage(world, ["EGSX"])["EGSX"]
        assert result.lookup_failed is False
        assert result.coverage_gap is False
        assert result.has_procedure_data is True
        assert result.has_iap is False
