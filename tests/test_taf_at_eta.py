"""TAF-at-ETA reading for route observations and digests (#610).

Fixtures are real TAFs from the 2026-09-13 LFRM→EGSC / LFRM→EDDK packs where
the old "last matching group wins" reading was wrong.
"""

from datetime import datetime, timedelta, timezone

import pytest

from weatherbrief.models.observations import AirportObservation, RouteObservations
from weatherbrief.tasks.route_weather import _apply_taf_at_eta

euro_aip = pytest.importorskip("euro_aip.briefing.weather.models")
WeatherReport = euro_aip.WeatherReport


def _utc(day, hour):
    return datetime(2026, 9, day, hour, tzinfo=timezone.utc)


def _obs(icao="EGSC"):
    return AirportObservation(
        icao=icao, distance_from_route_nm=0.0, nearest_waypoint_icao=icao, has_taf=True,
    )


def _read(raw, issued, eta, icao="EGSC"):
    obs = _obs(icao)
    _apply_taf_at_eta(obs, WeatherReport.from_taf(raw, reference=issued), eta)
    return obs


class TestApplyTafAtEta:

    def test_expired_taf_keeps_raw_but_no_reading(self):
        """EGSC on 13 Sep: the latest TAF was issued on the 11th, valid 15-17Z."""
        raw = "TAF EGSC 111404Z 1115/1117 29006KT 9999 SCT035"
        obs = _read(raw, _utc(11, 14), _utc(13, 13))

        assert obs.taf_valid_at_eta is False
        assert obs.taf_raw == raw
        assert obs.taf_valid_from == _utc(11, 15)
        assert obs.taf_valid_to == _utc(11, 17)
        assert obs.taf_flight_category_at_eta is None
        assert obs.taf_prevailing_category_at_eta is None
        assert obs.taf_wind_speed_kt is None
        assert obs.taf_applicable_lines == []
        assert obs.taf_at_eta_line() == "TAF: none valid at ETA (latest TAF valid 11/15Z-11/17Z)"

    def test_worse_tempo_is_reported_beside_prevailing(self):
        """EGSS: an IFR TEMPO at ETA used to lose to a later PROB30 TEMPO with no category."""
        raw = (
            "TAF EGSS 130459Z 1306/1412 23008KT 9999 BKN008\n"
            "TEMPO 1306/1310 6000 -DZ BKN004\n"
            "BECMG 1310/1313 SCT020\n"
            "TEMPO 1310/1314 4000 SHRA -RADZ\n"
            "PROB30\n"
            "TEMPO 1310/1314 +SHRA"
        )
        obs = _read(raw, _utc(13, 5), _utc(13, 13), icao="EGSS")

        assert obs.taf_valid_at_eta is True
        assert obs.taf_prevailing_category_at_eta == "VFR"
        assert obs.taf_temporary_category_at_eta == "IFR"
        assert obs.taf_temporary_type == "TEMPO"
        assert obs.taf_flight_category_at_eta == "IFR"
        assert obs.taf_trend_type == "TEMPO"
        assert obs.taf_at_eta_line() == "TAF at ETA [VFR], TEMPO [IFR]"

    def test_cb_in_tempo_reaches_the_line(self):
        raw = (
            "TAF EDDK 130500Z 1306/1412 17004KT 9999 BKN030\n"
            "BECMG 1306/1309 27007KT\n"
            "PROB40\n"
            "TEMPO 1306/1311 3000 RADZ BKN010\n"
            "TEMPO 1311/1318 3000 SHRA BKN008CB"
        )
        obs = _read(raw, _utc(13, 5), _utc(13, 13), icao="EDDK")

        assert obs.taf_significant_weather == ["CB"]
        assert obs.taf_at_eta_line() == "TAF at ETA [MVFR], TEMPO [IFR] (CB)"
        assert obs.taf_wind_speed_kt == 7  # completed wind-only BECMG

    def test_wind_only_becmg_keeps_the_category(self):
        """LFRK: a wind-only BECMG used to blank the category."""
        raw = (
            "TAF LFRK 130800Z 1309/1318 VRB05KT 9999 SCT040\n"
            "TEMPO 1309/1310 BKN010\n"
            "BECMG 1310/1312 27010KT\n"
            "BECMG 1312/1315 33010KT"
        )
        obs = _read(raw, _utc(13, 8), _utc(13, 12), icao="LFRK")

        assert obs.taf_flight_category_at_eta == "VFR"
        assert obs.taf_temporary_category_at_eta is None
        assert obs.taf_trend_type == "BECMG"
        assert obs.taf_at_eta_line() == "TAF at ETA [VFR]"

    def test_tempo_gust_drives_the_wind_fields(self):
        raw = "TAF LFMD 130500Z 1306/1318 20010KT 9999 SCT040 TEMPO 1312/1316 VRB15G25KT 4000 TSRA BKN030CB"
        obs = _read(raw, _utc(13, 5), _utc(13, 13), icao="LFMD")

        assert obs.taf_wind_gust_kt == 25
        assert obs.taf_significant_weather == ["TSRA", "CB"]


class TestTafAtEtaLine:

    def test_legacy_observation_keeps_the_old_line(self):
        obs = _obs()
        obs.taf_flight_category_at_eta = "IFR"
        obs.taf_trend_type = "TEMPO"
        assert obs.taf_valid_at_eta is None
        assert obs.taf_at_eta_line() == "TAF at ETA [IFR] (TEMPO)"

    def test_llm_context_uses_the_line(self):
        from weatherbrief.digest.prompt_builder import _format_observations_context

        expired = _read(
            "TAF EGSC 111404Z 1115/1117 29006KT 9999 SCT035", _utc(11, 14), _utc(13, 13),
        )
        section = _format_observations_context(RouteObservations(
            corridor_nm=30,
            fetch_time=_utc(13, 9),
            airports_found=1,
            airports_with_metar=0,
            airports_with_taf=0,
            airports=[expired],
        ))
        assert "EGSC" in section
        assert "TAF: none valid at ETA (latest TAF valid 11/15Z-11/17Z)" in section
        assert "TAF at ETA" not in section


class TestAlternateRequirementIgnoresExpiredTaf:

    def test_expired_destination_taf_falls_back_to_nwp(self):
        from weatherbrief.tasks.alternate_requirement import _build_destination_window

        window, conditionals = _build_destination_window(
            "TAF EGSC 111404Z 1115/1117 29006KT 9999 SCT035",
            _utc(13, 13),
            nwp_ceiling=800.0,
            nwp_vis=5000.0,
        )
        assert window.source == "nwp"
        assert conditionals == []

    def test_covering_taf_still_used(self):
        from weatherbrief.tasks.alternate_requirement import _build_destination_window

        window, _ = _build_destination_window(
            "TAF EGSC 131100Z 1312/1321 29006KT 9999 BKN008",
            _utc(13, 13) + timedelta(minutes=30),
            nwp_ceiling=None,
            nwp_vis=None,
        )
        assert window.source == "taf"
        assert window.ceiling_ft == 800
