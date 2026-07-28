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
    get_runway_approaches,
)


def _airport(ident: str, runways: list[Runway], procedures: list[Procedure]) -> Airport:
    airport = Airport(ident=ident)
    airport.runways = runways
    airport.procedures = procedures
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


class _FakeModel:
    def __init__(self, airports: dict[str, Airport]):
        self.airports = _FakeCollection(airports)


def _lookup(airports: dict[str, Airport], icaos: list[str]):
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
        assert result.for_runway("02")[0].approach_type == "ILS"

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
        assert result.for_runway("24") == []
