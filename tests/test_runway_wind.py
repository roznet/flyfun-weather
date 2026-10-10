"""Runway + wind picture (#758): components, edge cases, and the one-picker invariant."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from weatherbrief.analysis.runway_wind import (
    build_runway_wind_picture,
    picture_for_observation,
    runway_ends_of,
    wind_sample,
)
from weatherbrief.models.observations import AirportObservation
from weatherbrief.models.runway_wind import RunwayEndInfo, RunwayInfo, RunwayWindPicture
from weatherbrief.tasks.route_weather import compute_wind_advisory

T = datetime(2026, 5, 20, 9, tzinfo=timezone.utc)


def rwy(a: str, ha: float, b: str, hb: float, length_ft: int | None = 6000) -> RunwayInfo:
    return RunwayInfo(
        id=f"{a}/{b}", length_ft=length_ft, surface="ASPH", hard=True,
        ends=[RunwayEndInfo(ident=a, heading_true=ha), RunwayEndInfo(ident=b, heading_true=hb)],
    )


R0927 = rwy("09", 90, "27", 270)


def picture(direction, speed, gust=None, runways=None, var_from=None, var_to=None):
    sample = wind_sample("metar", T, direction, speed, gust, var_from, var_to)
    return build_runway_wind_picture("ZZZZ", [R0927] if runways is None else runways, [sample])


def ends_by_ident(pic: RunwayWindPicture) -> dict:
    return {e.ident: e for e in pic.winds[0].ends}


# --- Components and side -----------------------------------------------------


@pytest.mark.parametrize(
    "direction, ident, side",
    [
        (360, "09", "left"),   # north wind on 09 (east): from the left
        (360, "27", "right"),  # ... on 27 (west): from the right
        (180, "09", "right"),
        (180, "27", "left"),
    ],
)
def test_crosswind_side_in_each_quadrant(direction, ident, side):
    end = ends_by_ident(picture(direction, 10))[ident]
    assert end.side == side
    assert abs(end.crosswind_kt) == pytest.approx(10.0)
    # Signed: positive = from the right.
    assert (end.crosswind_kt > 0) == (side == "right")


def test_headwind_and_tailwind_signs():
    ends = ends_by_ident(picture(270, 12))
    assert ends["27"].headwind_kt == pytest.approx(12.0)
    assert ends["09"].headwind_kt == pytest.approx(-12.0)  # tailwind
    assert ends["27"].crosswind_kt == 0 and ends["27"].side == ""


def test_gust_components():
    end = ends_by_ident(picture(300, 10, gust=20))["27"]
    assert end.gust_headwind_kt == pytest.approx(17.3, abs=0.1)
    assert end.gust_crosswind_kt == pytest.approx(10.0, abs=0.1)
    assert end.max_crosswind_kt == pytest.approx(10.0, abs=0.1)


def test_vrb_is_variable_with_speed_as_worst_case_and_no_best_end():
    pic = picture(None, 6)
    wind = pic.winds[0]
    assert wind.wind.variable is True and wind.wind.calm is False
    assert wind.wind.direction_true is None
    assert all(e.max_crosswind_kt == 6.0 for e in wind.ends)
    # The table has no best runway for VRB either.
    assert wind.best_end is None and wind.advisory is None


def test_variable_range_raises_the_worst_case_crosswind():
    # 270V330 on 27: 330 is 60° off, the worst point in the range.
    end = ends_by_ident(picture(300, 20, var_from=270, var_to=330))["27"]
    assert abs(end.crosswind_kt) == pytest.approx(10.0, abs=0.1)
    assert end.max_crosswind_kt == pytest.approx(17.3, abs=0.1)
    sample = picture(300, 20, var_from=270, var_to=330).winds[0].wind
    assert (sample.variable_from, sample.variable_to) == (270, 330)


def test_calm_is_calm_not_missing():
    pic = picture(0, 0)
    wind = pic.winds[0]
    assert wind.wind.calm is True and wind.wind.variable is False
    # The reported 000 is kept so the best-runway pick matches the table's.
    assert wind.wind.direction_true == 0
    assert all(e.crosswind_kt == 0 and e.headwind_kt == 0 for e in wind.ends)
    assert wind.best_end == "09" and wind.advisory == "green"


def test_no_wind_reported_is_no_sample_never_calm():
    assert wind_sample("metar", T, None, None, None) is None
    assert picture(None, None).winds == []


def test_no_runways_still_carries_the_wind():
    pic = picture(270, 10, runways=[])
    assert pic.runways == []
    assert len(pic.winds) == 1
    assert pic.winds[0].wind.direction_true == 270
    assert pic.winds[0].ends == [] and pic.winds[0].best_end is None


def test_parallel_runways_keep_their_l_r_idents():
    runways = [rwy("05L", 52, "23R", 232), rwy("05R", 52, "23L", 232)]
    pic = picture(230, 10, runways=runways)
    assert [e.ident for e in pic.winds[0].ends] == ["05L", "23R", "05R", "23L"]
    assert pic.winds[0].best_end == "23R"  # first of the two equal ends, as the table


def test_advisory_tier_follows_the_existing_thresholds():
    # 20 kt straight across every end of a single runway → amber (15/25 kt).
    assert picture(360, 20).winds[0].advisory == "amber"
    assert picture(270, 10, gust=36).winds[0].advisory == "red"
    assert picture(270, 10).winds[0].advisory == "green"


# --- The observation → picture path --------------------------------------------


def obs(**kw) -> AirportObservation:
    base = dict(icao="ZZZZ", distance_from_route_nm=0.0, nearest_waypoint_icao="ZZZZ")
    base.update(kw)
    return AirportObservation(**base)


def test_picture_for_observation_carries_metar_and_taf_at_eta():
    eta = datetime(2026, 5, 20, 14, tzinfo=timezone.utc)
    o = obs(
        metar_time=T, metar_wind_dir=250, metar_wind_speed_kt=12, metar_wind_gust_kt=22,
        metar_wind_variable_from=220, metar_wind_variable_to=280,
        taf_valid_at_eta=True, taf_wind_dir=None, taf_wind_speed_kt=4,
    )
    pic = picture_for_observation(o, [R0927], eta)
    metar, taf = pic.winds
    assert metar.wind.source == "metar" and metar.wind.time == T
    assert (metar.wind.variable_from, metar.wind.variable_to) == (220, 280)
    assert taf.wind.source == "taf" and taf.wind.time == eta and taf.wind.variable


def test_expired_taf_gives_no_taf_sample():
    o = obs(metar_wind_dir=250, metar_wind_speed_kt=12,
            taf_valid_at_eta=False, taf_wind_dir=200, taf_wind_speed_kt=10)
    pic = picture_for_observation(o, [R0927], None)
    assert [w.wind.source for w in pic.winds] == ["metar"]


def test_picture_serialises_compactly():
    """The picture rides every corridor airport in every /live tick and pack
    (decision recorded in designs/metar-taf-route-weather.md). Pin the size
    of a typical two-runway airport so growth is a visible choice."""
    o = obs(metar_wind_dir=250, metar_wind_speed_kt=12, metar_wind_gust_kt=22,
            taf_valid_at_eta=True, taf_wind_dir=230, taf_wind_speed_kt=15)
    pic = picture_for_observation(o, [R0927, rwy("04", 40, "22", 220, 3000)], T)
    size = len(pic.model_dump_json())
    assert size < 2500, size


# --- get_runways / get_runway_ends ------------------------------------------------


def _db_runway(le, le_h, he, he_h, closed=False, surface="ASPH", length=5000.0):
    return SimpleNamespace(
        le_ident=le, le_heading_degT=le_h, he_ident=he, he_heading_degT=he_h,
        closed=closed, surface=surface, length_ft=length,
    )


def _fake_model(runways):
    model = MagicMock()
    model.airports = {"ZZZZ": SimpleNamespace(runways=runways)}
    return model


def test_get_runways_drops_closed_and_headingless_and_get_runway_ends_projects_it():
    from weatherbrief.airports import get_runway_ends, get_runways

    model = _fake_model([
        _db_runway("09", 91.5, "27", 271.5),
        _db_runway("04", 40.0, "22", 220.0, closed=True),
        _db_runway("18", None, "36", 0.5, surface="GRASS", length=None),
        _db_runway("H1", None, None, None),
    ])
    with patch("weatherbrief.airports._load_airport_model", return_value=model):
        runways = get_runways(["ZZZZ", "ZZZY"], "/fake/db")["ZZZZ"]
        ends = get_runway_ends(["ZZZZ"], "/fake/db")["ZZZZ"]

    assert [r.id for r in runways] == ["09/27", "18/36"]
    assert [e.ident for e in runways[1].ends] == ["36"]
    assert runways[0].hard is True and runways[1].hard is False
    assert runways[0].length_ft == 5000 and runways[1].length_ft is None
    assert [(e.id, e.heading_deg) for e in ends] == [("09", 91.5), ("27", 271.5), ("36", 0.5)]


def test_run_route_weather_attaches_the_picture_with_the_tables_runway():
    from weatherbrief.models import RouteConfig, Waypoint
    from weatherbrief.tasks.route_weather import run_route_weather

    metar = SimpleNamespace(
        raw_text="ZZZZ 200900Z 31015G27KT 9999 FEW030 15/08 Q1015",
        observation_time=T, flight_category=None, ceiling_ft=None, visibility_meters=9999,
        wind_direction=310, wind_speed=15, wind_gust=27,
        wind_variable_from=None, wind_variable_to=None,
        weather_conditions=[], temperature=15, dewpoint=8, altimeter=1015,
    )
    raw = SimpleNamespace(
        icao="ZZZZ", name="Testfield", distance_from_route_nm=0.0, enroute_distance_nm=0.0,
        latest_metar=metar, latest_taf=None, reports=None,
    )
    service = MagicMock()
    service.fetch_route_weather.return_value = SimpleNamespace(airports=[raw])
    route = RouteConfig(
        name="T",
        waypoints=[Waypoint(icao="ZZZZ", name="A", lat=50.0, lon=0.0),
                   Waypoint(icao="ZZZY", name="B", lat=50.5, lon=1.0)],
        cruise_altitude_ft=5000, flight_duration_hours=1.0,
    )
    runways = {"ZZZZ": [R0927, rwy("04", 40, "22", 220)]}
    with patch("euro_aip.briefing.weather.route_weather.RouteWeatherService", return_value=service), \
         patch("weatherbrief.airports._load_airport_model", return_value=MagicMock()), \
         patch("weatherbrief.airports.get_runways", return_value=runways):
        result = run_route_weather(route, T, 30.0, "/fake/db")

    o = result.airports[0]
    assert o.runway_wind is not None
    metar_wind = o.runway_wind.winds[0]
    assert metar_wind.best_end == o.metar_best_runway_id == "27"
    assert metar_wind.advisory == o.metar_wind_advisory == "amber"


# --- Invariant: one picker -----------------------------------------------------------

SCENARIOS = Path(__file__).parent / "fixtures" / "live_scenarios"

# Synthetic layouts (single, crossing, parallels, off-cardinal) so every real
# wind in the scenario meets ties and near-ties between ends.
LAYOUTS = [
    [R0927],
    [rwy("18", 180, "36", 360), R0927],
    [rwy("05L", 52.3, "23R", 232.3), rwy("05R", 52.3, "23L", 232.3)],
    [rwy("13", 128.4, "31", 308.4), rwy("02", 21.0, "20", 201.0, 2500)],
]


def _scenario_observations():
    for path in sorted(SCENARIOS.glob("*.json")):
        derived = json.loads(path.read_text())["derived"]
        for rows in derived["observations"].values():
            for row in rows:
                yield AirportObservation.model_validate(row)


def test_invariant_picture_best_end_and_advisory_match_the_table():
    """For every real METAR and TAF wind in the shared live scenarios, on
    every layout, the dial's best end and tier are the table's."""
    checked = 0
    for o in _scenario_observations():
        for runways in LAYOUTS:
            pic = picture_for_observation(o, runways, T)
            by_source = {w.wind.source: w for w in pic.winds}
            for source in ("metar", "taf"):
                w = by_source.get(source)
                if w is None:
                    continue
                adv, best, _, _ = compute_wind_advisory(
                    getattr(o, f"{source}_wind_dir"), getattr(o, f"{source}_wind_speed_kt"),
                    getattr(o, f"{source}_wind_gust_kt"), runway_ends_of(runways),
                )
                assert (w.best_end, w.advisory) == (best, adv), (o.icao, source, runways)
                checked += 1
    assert checked > 50
