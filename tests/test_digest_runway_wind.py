"""Runway wind components are spelled out for the LLM, not shown as arrows (#717)."""

from __future__ import annotations

from weatherbrief.analysis.advisories.airport_wind import spell_out_runway_wind
from weatherbrief.digest.prompt_builder import _format_route_advisories_context
from weatherbrief.models import (
    AdvisoryCatalogEntry,
    AdvisoryStatus,
    RouteAdvisoriesManifest,
    RouteAdvisoryResult,
)


class TestSpellOutRunwayWind:
    def test_head_and_crosswind_are_named_with_their_side(self):
        # The case the eval caught: "about 18 kt crosswind" for RW27 ↓18 ←6.
        out = spell_out_runway_wind("Arr EGJB: 287@19G25 RW27 \u219318 \u21906")
        assert out == ("Arr EGJB: 287@19G25 RW27 headwind 18 kt, "
                       "crosswind 6 kt from the right")

    def test_left_crosswind_and_tailwind(self):
        out = spell_out_runway_wind("RW31 \u219110 \u21925")
        assert out == "RW31 tailwind 10 kt, crosswind 5 kt from the left"

    def test_both_airports_on_one_line(self):
        out = spell_out_runway_wind(
            "Dep LFRK: 278@12G23 RW31 \u219310 \u21925 | "
            "Arr EGJB: 287@19G25 RW27 \u219318 \u21906"
        )
        assert "\u2193" not in out and "\u2190" not in out
        assert "RW31 headwind 10 kt, crosswind 5 kt from the left" in out
        assert "RW27 headwind 18 kt, crosswind 6 kt from the right" in out

    def test_text_without_runway_components_is_untouched(self):
        assert spell_out_runway_wind("Calm") == "Calm"
        assert spell_out_runway_wind("230@11G25") == "230@11G25"


def _manifest(advisory_id: str, detail: str) -> RouteAdvisoriesManifest:
    return RouteAdvisoriesManifest(
        advisories=[RouteAdvisoryResult(
            advisory_id=advisory_id,
            aggregate_status=AdvisoryStatus.AMBER,
            aggregate_detail=detail,
            per_model=[],
        )],
        catalog=[AdvisoryCatalogEntry(
            id=advisory_id, name="Airport Wind",
            short_description="", description="", category="wind",
        )],
    )


def test_the_llm_context_spells_out_airport_wind():
    text = _format_route_advisories_context(
        _manifest("airport_wind", "Arr EGJB: 287@19G25 RW27 \u219318 \u21906"),
    )
    assert "[AMBER] Airport Wind: Arr EGJB: 287@19G25 RW27 headwind 18 kt, " \
           "crosswind 6 kt from the right" in text


def test_other_advisories_are_not_rewritten():
    detail = "RW27 \u219318 \u21906"  # not a real detail, just proves the gate
    text = _format_route_advisories_context(_manifest("turbulence", detail))
    assert detail in text


class TestRoundTripWithTheEncoder:
    """Encode with ``_format_wind_detail`` and decode: pins the shared format."""

    @staticmethod
    def _detail(wind_dir: float, headwind: float, crosswind: float) -> str:
        from weatherbrief.analysis.advisories.airport_wind import _format_wind_detail
        from weatherbrief.models.airport_conditions import (
            AirportModelCondition, FlightCategory, RunwayWind,
        )

        cond = AirportModelCondition(
            model="ecmwf", flight_category=FlightCategory.VFR,
            wind_direction_deg=wind_dir, wind_speed_kt=15.0, wind_gust_kt=None,
            best_runway=RunwayWind(runway_id="27", heading_deg=270.0,
                                   crosswind_kt=crosswind, headwind_kt=headwind),
        )
        return _format_wind_detail(cond, "27")

    def test_every_head_tail_and_side_combination_decodes(self):
        cases = [
            # (wind from, headwind_kt, expected words)
            (300.0, 12.0, "headwind 12 kt, crosswind 7 kt from the right"),
            (240.0, 12.0, "headwind 12 kt, crosswind 7 kt from the left"),
            # Facing west on RW27: 120° is behind-left, 060° behind-right.
            (120.0, -12.0, "tailwind 12 kt, crosswind 7 kt from the left"),
            (60.0, -12.0, "tailwind 12 kt, crosswind 7 kt from the right"),
        ]
        for wind_dir, headwind, words in cases:
            detail = self._detail(wind_dir, headwind, 7.0)
            decoded = spell_out_runway_wind(detail)
            assert f"RW27 {words}" in decoded, (wind_dir, detail, decoded)
            assert not any(ch in decoded for ch in "←↑→↓")
