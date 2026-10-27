"""Frost-at-departure advisory (issue #949): heuristic, honesty, grade-neutrality."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest

from weatherbrief.analysis.advisories import RouteContext
from weatherbrief.analysis.advisories.frost import FrostEvaluator
from weatherbrief.analysis.frost import night_bounds
from weatherbrief.models import (
    AdvisoryStatus,
    HourlyForecast,
    ModelSource,
    RouteAdvisoriesManifest,
    RouteAdvisoryResult,
    RouteCrossSection,
    RoutePoint,
    RoutePointAnalysis,
    Waypoint,
    WaypointForecast,
)

# Paris-ish in mid-January: sunrise ~07:40Z, sunset ~16:15Z.
LAT, LON = 48.7, 2.4
DAY = datetime(2026, 1, 15, tzinfo=timezone.utc)
FETCHED = datetime(2026, 1, 14, 12, tzinfo=timezone.utc)


def _defaults() -> dict:
    return {p.key: p.default for p in FrostEvaluator.catalog_entry().parameters}


def _hour(t: datetime, *, temp=0.5, td=-1.0, cloud=5.0, wind=2.0) -> HourlyForecast:
    return HourlyForecast(
        time=t, temperature_2m_c=temp, dewpoint_2m_c=td,
        cloud_cover_pct=cloud, wind_speed_10m_kt=wind,
    )


def _span(start: datetime, end: datetime, **kw) -> list[HourlyForecast]:
    out, t = [], start
    while t <= end:
        out.append(_hour(t, **kw))
        t += timedelta(hours=1)
    return out


def _skeleton(start: datetime, end: datetime) -> list[HourlyForecast]:
    """A GRIB-primary skeleton hour: the slot exists, every value is None."""
    out, t = [], start
    while t <= end:
        out.append(HourlyForecast(time=t))
        t += timedelta(hours=1)
    return out


def _ctx(
    departure: datetime,
    hourly_by_model: dict[str, list[HourlyForecast]],
    models: list[str] | None = None,
) -> RouteContext:
    analyses = [
        RoutePointAnalysis(
            point_index=i, lat=LAT, lon=LON + i * 0.5,
            distance_from_origin_nm=i * 20.0,
            interpolated_time=departure + timedelta(minutes=i * 10),
            forecast_hour=departure, track_deg=90.0,
            waypoint_icao="ZZFA" if i == 0 else None,
        )
        for i in range(2)
    ]
    route_points = [
        RoutePoint(lat=a.lat, lon=a.lon, distance_from_origin_nm=a.distance_from_origin_nm)
        for a in analyses
    ]
    cross_sections = []
    for model, hourly in hourly_by_model.items():
        wp = Waypoint(icao="ZZFA", name="ZZFA", lat=LAT, lon=LON)
        cross_sections.append(RouteCrossSection(
            model=ModelSource(model), route_points=route_points, fetched_at=FETCHED,
            point_forecasts=[
                WaypointForecast(waypoint=wp, model=ModelSource(model), fetched_at=FETCHED, hourly=hourly),
                WaypointForecast(waypoint=wp, model=ModelSource(model), fetched_at=FETCHED, hourly=[]),
            ],
        ))
    return RouteContext(
        analyses=analyses, cross_sections=cross_sections, elevation=None,
        models=models or list(hourly_by_model), cruise_altitude_ft=5000,
        flight_ceiling_ft=10000, total_distance_nm=20.0,
    )


# The night window for a 07Z departure starts at 19Z the evening before
# (departure - 12 h, later than the ~16Z sunset).
NIGHT = (DAY - timedelta(hours=5), DAY + timedelta(hours=23))
DEP_0700 = DAY + timedelta(hours=7)


def _eval(ctx: RouteContext, **overrides) -> RouteAdvisoryResult:
    return FrostEvaluator.evaluate(ctx, {**_defaults(), **overrides})


class TestNightBounds:
    def test_predawn_departure_night_still_running(self):
        start, sunrise = night_bounds(LAT, LON, DEP_0700)
        assert start is not None and sunrise is None
        assert start.date() == (DAY - timedelta(days=1)).date()
        assert 15 <= start.hour <= 17

    def test_after_sunrise_departure_names_todays_sunrise(self):
        start, sunrise = night_bounds(LAT, LON, DAY + timedelta(hours=9))
        assert start is not None and start.date() == (DAY - timedelta(days=1)).date()
        assert sunrise is not None and sunrise.date() == DAY.date() and 7 <= sunrise.hour <= 8

    def test_western_us_summer_evening_sunset_after_midnight_utc(self):
        # Seattle in June: sunset ~04:10Z the next UTC date, sunrise ~12:10Z.
        lat, lon = 47.45, -122.3
        june = datetime(2026, 6, 20, tzinfo=timezone.utc)
        # 11:00 local (18Z): daylight, after sunrise, the night is over.
        start, sunrise = night_bounds(lat, lon, june + timedelta(hours=18))
        assert sunrise is not None and sunrise < june + timedelta(hours=18)
        assert start is not None and start < sunrise
        assert (sunrise - start) < timedelta(hours=10)  # a summer night, not 33 h
        # 21:30 local (04:30Z next UTC date): after sunset, in the night.
        start, sunrise = night_bounds(lat, lon, june + timedelta(hours=28, minutes=30))
        assert sunrise is None and start is not None
        assert (june + timedelta(hours=28, minutes=30)) - start < timedelta(hours=2)

    def test_evening_departure_has_no_sunrise(self):
        start, sunrise = night_bounds(LAT, LON, DAY + timedelta(hours=20))
        assert start is not None and start.date() == DAY.date()
        assert sunrise is None


class TestFrostHeuristic:
    def test_clear_calm_cold_moist_night_is_amber(self):
        r = _eval(_ctx(DEP_0700, {"ukmo": _span(*NIGHT)}))
        assert r.aggregate_status == AdvisoryStatus.AMBER
        assert "Frost likely" in r.aggregate_detail
        assert "+0" not in r.aggregate_detail  # rounding prints 0, not +0
        assert "dewpoint -1" in r.aggregate_detail

    def test_windy_night_is_not_amber(self):
        r = _eval(_ctx(DEP_0700, {"ukmo": _span(*NIGHT, wind=12.0)}))
        assert r.aggregate_status == AdvisoryStatus.GREEN
        assert "windy" in r.aggregate_detail

    def test_cloudy_night_is_not_amber(self):
        r = _eval(_ctx(DEP_0700, {"ukmo": _span(*NIGHT, cloud=90.0)}))
        assert r.aggregate_status == AdvisoryStatus.GREEN
        assert "cloudy" in r.aggregate_detail

    def test_missing_ingredient_is_the_nights_not_the_coldest_hours(self):
        # Clear and calm all night but dry; only the coldest hour clouds over.
        hours = _span(*NIGHT, temp=2.0, td=-8.0)
        hours[10] = _hour(hours[10].time, temp=1.0, td=-7.0, cloud=90.0)
        r = _eval(_ctx(DEP_0700, {"ukmo": hours}))
        assert "dry air" in r.aggregate_detail and "cloudy" not in r.aggregate_detail

    def test_dry_air_is_not_amber(self):
        r = _eval(_ctx(DEP_0700, {"ukmo": _span(*NIGHT, temp=2.0, td=-8.0)}))
        assert r.aggregate_status == AdvisoryStatus.GREEN
        assert "dry air" in r.aggregate_detail

    def test_mild_night_says_no_frost(self):
        r = _eval(_ctx(DEP_0700, {"ukmo": _span(*NIGHT, temp=8.0, td=6.0)}))
        assert r.aggregate_status == AdvisoryStatus.GREEN
        assert "No frost expected" in r.aggregate_detail and "+8" in r.aggregate_detail

    def test_subzero_moist_but_windy_is_possible_not_amber(self):
        r = _eval(_ctx(DEP_0700, {"ukmo": _span(*NIGHT, temp=-1.0, td=-3.0, wind=15.0)}))
        assert r.aggregate_status == AdvisoryStatus.GREEN
        assert "Frost possible" in r.aggregate_detail

    def test_one_radiative_hour_is_possible_not_amber(self):
        hours = _span(*NIGHT, wind=12.0)
        hours[8] = _hour(hours[8].time)  # 03Z alone goes calm
        r = _eval(_ctx(DEP_0700, {"ukmo": hours}))
        assert r.aggregate_status == AdvisoryStatus.GREEN
        assert "Frost possible" in r.aggregate_detail

    def test_never_red(self):
        r = _eval(_ctx(DEP_0700, {"ukmo": _span(*NIGHT, temp=-8.0, td=-9.0)}))
        assert r.aggregate_status == AdvisoryStatus.AMBER


class TestTiming:
    def test_departure_three_hours_after_sunrise_is_green(self):
        dep = DAY + timedelta(hours=10, minutes=45)
        r = _eval(_ctx(dep, {"ukmo": _span(*NIGHT)}))
        assert r.aggregate_status == AdvisoryStatus.GREEN
        assert r.aggregate_detail == ""

    def test_afternoon_departure_is_green_with_no_text(self):
        dep = DAY + timedelta(hours=14)
        r = _eval(_ctx(dep, {"ukmo": _span(*NIGHT), "ecmwf": _skeleton(*NIGHT)}))
        assert r.aggregate_status == AdvisoryStatus.GREEN
        assert all(m.status == AdvisoryStatus.GREEN and m.detail == "" for m in r.per_model)

    def test_shortly_after_sunrise_is_still_assessed(self):
        dep = DAY + timedelta(hours=8, minutes=30)  # ~50 min after sunrise
        r = _eval(_ctx(dep, {"ukmo": _span(*NIGHT)}))
        assert r.aggregate_status == AdvisoryStatus.AMBER

    def test_clear_off_margin_is_a_parameter(self):
        dep = DAY + timedelta(hours=8, minutes=30)
        r = _eval(_ctx(dep, {"ukmo": _span(*NIGHT)}), clear_after_sunrise_min=15)
        assert r.aggregate_status == AdvisoryStatus.GREEN and r.aggregate_detail == ""


class TestHonesty:
    def test_skeleton_model_is_unavailable_not_graded_off_the_departure_hour(self):
        # GRIB-primary slot: only the flight-window hours (07Z, 08Z) carry values,
        # and they are cold, calm and moist. ``at_time`` would happily return the
        # 07Z hour for every night hour; the advisory must not.
        hours = _skeleton(*NIGHT)
        for h in hours:
            if h.time in (DEP_0700, DEP_0700 + timedelta(hours=1)):
                h.temperature_2m_c, h.dewpoint_2m_c = -2.0, -3.0
                h.cloud_cover_pct, h.wind_speed_10m_kt = 0.0, 1.0
        r = _eval(_ctx(DEP_0700, {"ecmwf": hours}))
        assert r.per_model[0].status == AdvisoryStatus.UNAVAILABLE
        assert r.aggregate_status == AdvisoryStatus.UNAVAILABLE
        assert "No overnight forecast hours" in r.per_model[0].detail

    def test_model_absent_from_cross_sections_is_unavailable(self):
        r = _eval(_ctx(DEP_0700, {"ukmo": _span(*NIGHT)}, models=["ukmo", "gfs"]))
        by = {m.model: m.status for m in r.per_model}
        assert by == {"ukmo": AdvisoryStatus.AMBER, "gfs": AdvisoryStatus.UNAVAILABLE}
        assert r.aggregate_status == AdvisoryStatus.AMBER

    def test_night_from_midnight_only_is_graded_and_says_so(self):
        # Open-Meteo slots start at 00Z of the departure date.
        r = _eval(_ctx(DEP_0700, {"ukmo": _span(DAY, NIGHT[1])}))
        assert r.aggregate_status == AdvisoryStatus.AMBER
        assert "from 00Z only" in r.aggregate_detail

    def test_full_night_has_no_partial_note(self):
        r = _eval(_ctx(DEP_0700, {"ukmo": _span(*NIGHT)}))
        assert "only" not in r.aggregate_detail

    def test_no_route_is_unavailable(self):
        ctx = RouteContext(
            analyses=[], cross_sections=[], elevation=None, models=["ukmo"],
            cruise_altitude_ft=5000, flight_ceiling_ft=10000, total_distance_nm=0.0,
        )
        assert _eval(ctx).per_model[0].status == AdvisoryStatus.UNAVAILABLE

    def test_alt_departure_shifts_the_window(self):
        # Same forecast, departure moved from 07Z to 13Z: out of scope.
        hours = {"ukmo": _span(*NIGHT)}
        assert _eval(_ctx(DEP_0700, hours)).aggregate_status == AdvisoryStatus.AMBER
        assert _eval(_ctx(DAY + timedelta(hours=13), hours)).aggregate_detail == ""


class TestRecalc:
    def test_changed_params_regrade_the_same_context(self):
        ctx = _ctx(DEP_0700, {"ukmo": _span(*NIGHT, temp=2.5, td=0.0)})
        assert _eval(ctx).aggregate_status == AdvisoryStatus.AMBER
        stricter = _eval(ctx, t_max_c=1.0)
        assert stricter.aggregate_status == AdvisoryStatus.GREEN
        assert _eval(ctx, min_radiative_hours=20).aggregate_status == AdvisoryStatus.GREEN

    def test_locale(self):
        ctx = replace(_ctx(DEP_0700, {"ukmo": _span(*NIGHT)}), locale="fr")
        assert "Givre probable" in _eval(ctx).aggregate_detail


class TestGradeNeutral:
    def _manifest(self, frost: AdvisoryStatus, other: AdvisoryStatus) -> RouteAdvisoriesManifest:
        return RouteAdvisoriesManifest(advisories=[
            RouteAdvisoryResult(advisory_id="frost", aggregate_status=frost, per_model=[]),
            RouteAdvisoryResult(advisory_id="airport_wind", aggregate_status=other, per_model=[]),
        ])

    def test_catalog_marks_frost_informational(self):
        entry = FrostEvaluator.catalog_entry()
        assert entry.affects_grade is False
        assert entry.category == "airport"

    def test_amber_frost_does_not_move_the_assessment(self):
        from weatherbrief.tasks.advise import derive_assessment_from_advisories

        assessment, reason = derive_assessment_from_advisories(
            self._manifest(AdvisoryStatus.AMBER, AdvisoryStatus.GREEN),
        )
        assert assessment == "GREEN"
        assert "frost" not in reason

    def test_frost_alone_is_not_a_graded_briefing(self):
        from weatherbrief.tasks.advise import (
            ASSESSMENT_UNAVAILABLE,
            advisories_ungradeable,
            derive_assessment_from_advisories,
        )

        m = self._manifest(AdvisoryStatus.AMBER, AdvisoryStatus.UNAVAILABLE)
        assert advisories_ungradeable(m)
        assert derive_assessment_from_advisories(m)[0] == ASSESSMENT_UNAVAILABLE

    def test_per_model_reasons_skip_frost(self):
        from weatherbrief.models import ModelAdvisoryResult
        from weatherbrief.tasks.advise import per_model_reasons_from_manifest

        row = ModelAdvisoryResult.build(
            model="ukmo", status=AdvisoryStatus.AMBER, detail="x",
            affected=1, total=1, total_distance_nm=10.0,
        )
        m = RouteAdvisoriesManifest(advisories=[
            RouteAdvisoryResult(advisory_id="frost", aggregate_status=AdvisoryStatus.AMBER, per_model=[row]),
        ])
        assert per_model_reasons_from_manifest(m) == {}

    def test_sun_still_counts(self):
        """Sun predates the flag and keeps grading; only frost is excluded."""
        from weatherbrief.tasks.advise import derive_assessment_from_advisories

        m = RouteAdvisoriesManifest(advisories=[
            RouteAdvisoryResult(advisory_id="sun", aggregate_status=AdvisoryStatus.AMBER, per_model=[]),
        ])
        assert derive_assessment_from_advisories(m)[0] == "AMBER"

    def test_digest_labels_frost_informational(self):
        from weatherbrief.analysis.advisories import get_catalog
        from weatherbrief.digest.prompt_builder import _format_route_advisories_context

        m = self._manifest(AdvisoryStatus.AMBER, AdvisoryStatus.GREEN)
        m.catalog = get_catalog()
        text = _format_route_advisories_context(m)
        assert "[AMBER] Frost at Departure (informational, not part of the grade):" in text
        assert "Airport Wind (informational" not in text


@pytest.mark.parametrize("loc", ["en", "fr", "de", "es"])
def test_every_frost_string_is_localised(loc):
    from weatherbrief.analysis.advisories.strings import _STRINGS

    for key, templates in _STRINGS.items():
        if key.startswith("frost."):
            assert loc in templates, f"{key} missing {loc}"
