"""Tests for the Sun advisory evaluator."""

from datetime import datetime, timedelta, timezone

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories.sun import SunEvaluator
from weatherbrief.models import (
    AdvisoryStatus,
    GlareAssessment,
    RoutePointAnalysis,
    RouteSunAnalysis,
    SunSideSegment,
    SunSideSummary,
)


def _defaults() -> dict:
    return {p.key: p.default for p in SunEvaluator.catalog_entry().parameters}


def _ctx(sun: RouteSunAnalysis | None, analyses=None) -> RouteContext:
    if analyses is None:
        start = datetime(2024, 6, 21, 12, 0, tzinfo=timezone.utc)
        analyses = [
            RoutePointAnalysis(
                point_index=i, lat=48.0, lon=2.0 + i * 0.2,
                distance_from_origin_nm=i * 20.0,
                interpolated_time=start + timedelta(minutes=i * 20),
                forecast_hour=start, track_deg=90.0,
            )
            for i in range(5)
        ]
    return RouteContext(
        analyses=analyses, cross_sections=[], elevation=None, models=["gfs"],
        cruise_altitude_ft=5000, flight_ceiling_ft=10000, total_distance_nm=80, sun=sun,
    )


def _side(side="right", pct=78.0, segments=None) -> SunSideSummary:
    return SunSideSummary(
        dominant_side=side, dominant_side_pct=pct,
        segments=segments if segments is not None else [],
    )


class TestSunAdvisory:
    def test_unavailable_when_no_sun(self):
        result = SunEvaluator.evaluate(_ctx(None), _defaults())
        # Per-model entry is UNAVAILABLE (aggregate may resolve to GREEN under MAJORITY).
        assert result.per_model[0].status == AdvisoryStatus.UNAVAILABLE
        assert "sun" in result.per_model[0].detail.lower() or result.per_model[0].detail

    def test_green_midday_with_side_note(self):
        sun = RouteSunAnalysis(night_intervals=[], sun_side=_side("right", 78.0))
        result = SunEvaluator.evaluate(_ctx(sun), _defaults())
        assert result.aggregate_status == AdvisoryStatus.GREEN
        # Note always present, naming the dominant sun side + its share. The
        # seating/photo guidance lives in the (i) description, not this line.
        assert "right" in result.aggregate_detail
        assert "left" not in result.aggregate_detail
        assert "78" in result.aggregate_detail

    def test_green_into_the_sun_note(self):
        sun = RouteSunAnalysis(night_intervals=[], sun_side=_side("ahead", 65.0))
        result = SunEvaluator.evaluate(_ctx(sun), _defaults())
        assert result.aggregate_status == AdvisoryStatus.GREEN
        # "ahead" reads as flying into the sun, not "your ahead".
        assert "into the sun" in result.aggregate_detail.lower()
        assert "65" in result.aggregate_detail
        assert "left" not in result.aggregate_detail
        assert "right" not in result.aggregate_detail

    def test_green_sun_behind_note(self):
        sun = RouteSunAnalysis(night_intervals=[], sun_side=_side("behind", 70.0))
        result = SunEvaluator.evaluate(_ctx(sun), _defaults())
        assert result.aggregate_status == AdvisoryStatus.GREEN
        assert "behind" in result.aggregate_detail.lower()
        assert "70" in result.aggregate_detail

    def test_amber_on_glare_landing(self):
        landing = GlareAssessment(
            phase="landing", airport_icao="LFXX", runway_ident="27",
            runway_heading_true=270.0, sun_azimuth_true=272.0,
            sun_elevation_deg=8.0, relative_bearing_deg=2.0,
            into_sun=True, is_dark=False,
        )
        sun = RouteSunAnalysis(sun_side=_side("left", 60.0), landing=landing)
        result = SunEvaluator.evaluate(_ctx(sun), _defaults())
        assert result.aggregate_status == AdvisoryStatus.AMBER
        assert "27" in result.aggregate_detail

    def test_glare_respects_widened_elevation_param(self):
        # Sun at 20deg is above the default 15deg ceiling -> no glare by default...
        landing = GlareAssessment(
            phase="landing", airport_icao="LFXX", runway_ident="27",
            runway_heading_true=270.0, sun_azimuth_true=272.0,
            sun_elevation_deg=20.0, relative_bearing_deg=2.0,
            into_sun=False, is_dark=False,
        )
        sun = RouteSunAnalysis(sun_side=_side("left", 60.0), landing=landing)
        assert SunEvaluator.evaluate(_ctx(sun), _defaults()).aggregate_status == AdvisoryStatus.GREEN
        # ...but AMBER once the user raises the low-sun ceiling above 20deg.
        params = {**_defaults(), "glare_elev_max_deg": 25}
        assert SunEvaluator.evaluate(_ctx(sun), params).aggregate_status == AdvisoryStatus.AMBER

    def test_sunset_landing_amber_and_suppressed_by_profile(self):
        # Evening leg landing after sunset (is_dark) at a French field.
        start = datetime(2024, 6, 21, 19, 0, tzinfo=timezone.utc)
        analyses = [
            RoutePointAnalysis(
                point_index=i, lat=48.0, lon=2.0, distance_from_origin_nm=i * 20.0,
                interpolated_time=start + timedelta(minutes=i * 30),
                forecast_hour=start, track_deg=270.0,
            )
            for i in range(5)
        ]
        landing = GlareAssessment(
            phase="landing", airport_icao="LFPG", runway_ident="27",
            sun_elevation_deg=-3.0, is_dark=True, into_sun=False,
        )
        sun = RouteSunAnalysis(sun_side=_side("none", 0.0), landing=landing)
        ctx = _ctx(sun, analyses=analyses)
        # Day-VFR default: dusk AMBER.
        assert SunEvaluator.evaluate(ctx, _defaults()).aggregate_status == AdvisoryStatus.AMBER
        # Night-capable profile turns off the dusk warning -> GREEN (no glare).
        params = {**_defaults(), "warn_near_sunset": 0}
        assert SunEvaluator.evaluate(ctx, params).aggregate_status == AdvisoryStatus.GREEN

    def test_glare_amber_survives_warn_off(self):
        # warn_near_sunset off must NOT suppress glare AMBER (glare is glare).
        start = datetime(2024, 6, 21, 19, 0, tzinfo=timezone.utc)
        analyses = [
            RoutePointAnalysis(
                point_index=i, lat=48.0, lon=2.0, distance_from_origin_nm=i * 20.0,
                interpolated_time=start + timedelta(minutes=i * 30),
                forecast_hour=start, track_deg=270.0,
            )
            for i in range(5)
        ]
        landing = GlareAssessment(
            phase="landing", airport_icao="LFPG", runway_ident="27",
            sun_elevation_deg=6.0, relative_bearing_deg=4.0, is_dark=False, into_sun=True,
        )
        sun = RouteSunAnalysis(sun_side=_side("right", 90.0), landing=landing)
        params = {**_defaults(), "warn_near_sunset": 0}
        assert SunEvaluator.evaluate(_ctx(sun, analyses=analyses), params).aggregate_status == AdvisoryStatus.AMBER

    def test_deep_night_landing_no_misleading_sunset_amber(self):
        # A landing at 02:00 UTC is dark but hours past sunset — NOT "near sunset".
        # Must stay GREEN (no glare, no misleading dusk warning), even though the
        # endpoint is_dark. Regression for the is_dark short-circuit.
        start = datetime(2024, 6, 21, 0, 0, tzinfo=timezone.utc)
        analyses = [
            RoutePointAnalysis(
                point_index=i, lat=48.0, lon=2.0, distance_from_origin_nm=i * 20.0,
                interpolated_time=start + timedelta(minutes=i * 30),
                forecast_hour=start, track_deg=90.0,
            )
            for i in range(5)  # last point at 02:00 UTC
        ]
        landing = GlareAssessment(
            phase="landing", airport_icao="LFPG", runway_ident="09",
            sun_elevation_deg=-15.0, is_dark=True, into_sun=False,
        )
        sun = RouteSunAnalysis(sun_side=_side("none", 0.0), landing=landing)
        result = SunEvaluator.evaluate(_ctx(sun, analyses=analyses), _defaults())
        assert result.aggregate_status == AdvisoryStatus.GREEN

    def test_no_swing_clause_when_segments_flip(self):
        # A route whose sun sector flips still gets only the dominant-side note;
        # the "~55%" already implies the rest of the route differs, so no extra
        # "it shifts" clause is appended.
        sun = RouteSunAnalysis(sun_side=_side("right", 55.0, segments=[
            SunSideSegment(side="right", start_distance_nm=0, end_distance_nm=40),
            SunSideSegment(side="left", start_distance_nm=40, end_distance_nm=80),
        ]))
        result = SunEvaluator.evaluate(_ctx(sun), _defaults())
        detail = result.aggregate_detail.lower()
        assert "right" in detail and "55" in detail
        assert "turn" not in detail
        assert "swing" not in detail
