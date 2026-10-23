"""The airport card's readings (#880): meter, sky, weather words, short wind
and visibility, crosswind, trend, and the TAF by hour."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from weatherbrief.analysis.airport_card import (
    ceiling_meter,
    crosswind_reading,
    metar_trend,
    sky_text,
    visibility_meter,
    visibility_text,
    weather_words,
    wind_short,
)
from weatherbrief.models.observations import AirportObservation
from weatherbrief.models.runway_wind import EndComponents, RunwayWindPicture, WindAtAirport, WindSample


def _utc(day, hour, minute=0):
    return datetime(2026, 9, day, hour, minute, tzinfo=timezone.utc)


# --- Meter --------------------------------------------------------------------


@pytest.mark.parametrize("ft, pos", [
    (0, 0.0), (250, 0.125), (500, 0.25), (750, 0.375), (1000, 0.5), (2000, 0.625),
    (3000, 0.75), (4000, 0.875), (5000, 1.0), (12000, 1.0),
])
def test_ceiling_meter_is_four_equal_bands(ft, pos):
    assert ceiling_meter(ft, has_metar=True) == pytest.approx(pos, abs=1e-3)


def test_ceiling_meter_without_a_ceiling_or_a_metar():
    assert ceiling_meter(None, has_metar=True) == 1.0  # CAVOK, FEW/SCT only
    assert ceiling_meter(None, has_metar=False) is None


def test_visibility_meter_uses_the_classifier_edges():
    assert visibility_meter(1609) == pytest.approx(0.25, abs=1e-3)
    assert visibility_meter(8000) == pytest.approx(0.75)
    assert visibility_meter(9999) == pytest.approx(1.0, abs=1e-3)
    assert visibility_meter(300) < 0.25
    assert visibility_meter(None) is None


# --- Text ---------------------------------------------------------------------


@pytest.mark.parametrize("raw, words, cavok", [
    ("METAR LFAT 230930Z 30014KT 6000 -DZ FEW008 BKN012 OVC030 12/11 Q1012", "BKN 1,200 ft", False),
    ("METAR LFAT 230930Z 30014KT CAVOK 12/11 Q1012", "CAVOK", True),
    ("METAR LELL 230630Z 00000KT 0300 FG VV001 10/10 Q1020", "VV 100 ft", False),
    ("METAR LFMD 231200Z 25010KT 9999 SCT025 FEW040CB 22/14 Q1010", "SCT 2,500 ft", False),
    ("METAR LFMD 231200Z 25010KT 9999 BKN015CB 22/14 Q1010", "BKN 1,500 ft CB", False),
    ("METAR EGLL 231200Z 25010KT 9999 NSC 15/08 Q1010", "No significant cloud", False),
    # The trend is a forecast, never the sky now.
    ("METAR EGLL 231200Z 25010KT 9999 FEW040 15/08 Q1010 TEMPO BKN008", "FEW 4,000 ft", False),
    (None, None, False),
])
def test_sky_text(raw, words, cavok):
    assert sky_text(raw) == (words, cavok)


def test_visibility_and_wind_text():
    assert [visibility_text(v) for v in (9999, 10000, 6000, 5000, 4800, 300, None)] == [
        "10 km+", "10 km+", "6 km", "5 km", "4,800 m", "300 m", None]
    assert wind_short(290, 15, 25) == "290/15G25"
    assert wind_short(None, 3, None) == "VRB 03"
    assert wind_short(40, 0, None) == "Calm"
    assert wind_short(None, None, None) is None


@pytest.mark.parametrize("code, text, icon", [
    ("-DZ", "Light drizzle", "drizzle"),
    ("VCTS", "Thunder nearby", "thunder"),
    ("FG", "Fog", "fog"),
    ("BR", "Mist", "mist"),
    ("+SHRA", "Heavy rain showers", "showers"),
    ("TSRA", "Thunderstorm with rain", "thunder"),
    ("-TSRA", "Thunderstorm with light rain", "thunder"),
    ("TS", "Thunderstorm", "thunder"),
    ("FZFG", "Freezing fog", "freezing"),
    ("-FZDZ", "Light freezing drizzle", "freezing"),
    ("BCFG", "Fog patches", "fog"),
    ("VCSH", "Showers nearby", "showers"),
    ("-RASN", "Light rain and snow", "snow"),
    ("BLSN", "Blowing snow", "snow"),
    ("HZ", "Haze", "haze"),
    ("+FC", "Tornado", "funnel"),
    ("XYZ", "XYZ", "other"),
])
def test_weather_words(code, text, icon):
    assert weather_words([code]) == [{"code": code, "text": text, "icon": icon}]


def test_trend_ranks_categories():
    assert metar_trend("MVFR", "VFR") == "worse"
    assert metar_trend("VFR", "IFR") == "better"
    assert metar_trend("VFR", "VFR") is None
    assert metar_trend("VFR", None) is None


# --- Crosswind ------------------------------------------------------------------


def _picture(xw, gust_xw, side="left", best="25R", calm=False):
    return RunwayWindPicture(icao="ZZDP", winds=[WindAtAirport(
        wind=WindSample(source="metar", direction_true=290, speed_kt=15, gust_kt=25, calm=calm),
        ends=[EndComponents(ident="25R", headwind_kt=10.0, crosswind_kt=xw, side=side,
                            gust_crosswind_kt=gust_xw, max_crosswind_kt=abs(gust_xw or xw))],
        best_end=best, advisory="amber",
    )])


def test_crosswind_keeps_a_gust_only_past_the_display_rule():
    assert crosswind_reading(_picture(-9.6, -16.2)) == {
        "runway": "25R", "side": "left", "kt": 10, "gust_kt": 16, "text": "10G16 kt"}
    # 10 vs 12: within GUST_DISPLAY_MIN_EXCESS_KT, the gust is noise.
    assert crosswind_reading(_picture(-9.6, -12.1))["text"] == "10 kt"
    assert crosswind_reading(_picture(0.2, None, side="right"))["side"] == ""
    assert crosswind_reading(_picture(-9.6, -16.2, best=None)) is None
    assert crosswind_reading(None) is None


# --- On the model ---------------------------------------------------------------


def test_the_model_ships_the_readings_and_reads_them_back():
    obs = AirportObservation(
        icao="ZZDS", distance_from_route_nm=0.0, nearest_waypoint_icao="ZZDS", has_metar=True,
        metar_raw="METAR ZZDS 230930Z 30014KT 6000 -DZ BKN012 12/11 Q1012",
        metar_flight_category="MVFR", metar_previous_flight_category="VFR",
        metar_ceiling_ft=1200, metar_visibility_m=6000, metar_weather=["-DZ"],
        metar_wind_dir=300, metar_wind_speed_kt=14, runway_wind=_picture(-3.0, None),
    )
    data = obs.model_dump(mode="json")
    assert data["metar_trend"] == "worse"
    assert data["metar_sky_text"] == "BKN 1,200 ft" and data["metar_cavok"] is False
    assert data["metar_ceiling_meter"] == pytest.approx(0.525)
    assert data["metar_visibility_text"] == "6 km"
    assert data["metar_wind_short"] == "300/14"
    assert data["metar_weather_words"] == [{"code": "-DZ", "text": "Light drizzle", "icon": "drizzle"}]
    assert data["metar_crosswind"]["text"] == "3 kt"
    # Stored packs carry the computed keys: reading them back ignores them.
    assert AirportObservation.model_validate(data).metar_trend == "worse"


# --- TAF by hour ---------------------------------------------------------------------

euro_aip = pytest.importorskip("euro_aip.briefing.weather.models")

EGSS = (
    "TAF EGSS 130459Z 1306/1412 23008KT 9999 BKN008\n"
    "TEMPO 1306/1310 6000 -DZ BKN004\n"
    "BECMG 1310/1313 SCT020\n"
    "TEMPO 1310/1314 4000 SHRA -RADZ\n"
    "PROB30\n"
    "TEMPO 1310/1314 +SHRA"
)


def _hours(now, eta):
    from weatherbrief.analysis.taf_reading import read_taf_hours

    taf = euro_aip.WeatherReport.from_taf(EGSS, reference=_utc(13, 5))
    return read_taf_hours(taf, now, eta)


def test_taf_hours_cross_the_eta_with_becmg_tempo_and_prob():
    hours = _hours(_utc(13, 7, 20), _utc(13, 11, 35))
    assert [(h.time.hour, h.prevailing_category, h.temporary_category, h.is_eta) for h in hours] == [
        (7, "IFR", "LIFR", False), (8, "IFR", "LIFR", False), (9, "IFR", "LIFR", False),
        (10, "IFR", "LIFR", False),
        (11, "IFR", None, True),  # read at 11:35, as the at-ETA fields
        (12, "IFR", None, False),
        (13, "VFR", "IFR", False),  # BECMG done; the TEMPO is now worse
    ]
    assert hours[0].temporary_type == "TEMPO" and hours[4].temporary_type is None


def test_taf_hours_keep_the_eta_when_it_is_far_ahead():
    hours = _hours(_utc(13, 5, 10), _utc(13, 14, 30))
    assert len(hours) == 8
    assert [h.time.hour for h in hours] == list(range(9, 17))
    assert [h.time.hour for h in hours if h.is_eta] == [14]


def test_taf_hours_stop_at_the_validity():
    hours = _hours(_utc(14, 9, 10), _utc(14, 10, 30))
    assert [h.time.hour for h in hours] == [9, 10, 11, 12]  # valid to 14/12Z
    assert _hours(_utc(15, 9), _utc(15, 10)) == []


def test_a_proxied_terminal_takes_its_stations_taf_hours_and_readings():
    from weatherbrief.models.observations import ObsProxy, RouteObservations, TafHour

    station = AirportObservation(
        icao="ZZST", distance_from_route_nm=3.0, nearest_waypoint_icao="ZZDS", has_metar=True, has_taf=True,
        metar_raw="METAR ZZST 230930Z 30014KT CAVOK 12/11 Q1012", metar_flight_category="VFR",
        metar_wind_dir=300, metar_wind_speed_kt=14, taf_raw="TAF ZZST ...",
        taf_hours=[TafHour(time=_utc(23, 10), prevailing_category="VFR", is_eta=True)],
    )
    field = AirportObservation(
        icao="ZZDS", distance_from_route_nm=0.0, nearest_waypoint_icao="ZZDS",
        metar_proxy=ObsProxy(icao="ZZST", distance_nm=6.0), taf_proxy=ObsProxy(icao="ZZST", distance_nm=6.0),
    )
    obs = RouteObservations(corridor_nm=30.0, fetch_time=_utc(23, 9), airports_found=2, airports_with_metar=1, airports_with_taf=1,
                            airports=[field, station])
    eff = obs.terminal("ZZDS").effective()
    assert eff.icao == "ZZDS"
    assert [h.is_eta for h in eff.taf_hours] == [True]
    assert eff.metar_cavok and eff.metar_sky_text == "CAVOK" and eff.metar_wind_short == "300/14"


@pytest.mark.parametrize("code, text", [
    ("+BLSN", "Heavy blowing snow"),
    ("VCBLSN", "Blowing snow nearby"),
    ("DRSA", "Drifting sand"),
    ("VCBLDU", "Blowing dust nearby"),
])
def test_blowing_and_drifting_keep_intensity_and_vicinity(code, text):
    assert weather_words([code])[0]["text"] == text
