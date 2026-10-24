"""Tests for Open-Meteo client with mocked HTTP."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch

import pytest
import responses

from weatherbrief.fetch.open_meteo import (
    EmptyForecastError,
    OpenMeteoClient,
    magnus_dewpoint,
)
from weatherbrief.models import ModelSource, RoutePoint, Waypoint


def test_magnus_dewpoint_typical():
    """Magnus formula gives reasonable dewpoint for typical conditions."""
    # At 20C and 50% RH, dewpoint should be ~9.3C
    dp = magnus_dewpoint(20, 50)
    assert 8.5 < dp < 10.5


def test_magnus_dewpoint_saturated():
    """At 100% RH, dewpoint equals temperature."""
    dp = magnus_dewpoint(15, 100)
    assert abs(dp - 15) < 0.1


def test_magnus_dewpoint_very_dry():
    """Very low RH gives very negative dewpoint."""
    dp = magnus_dewpoint(20, 5)
    assert dp < -10


@responses.activate
def test_fetch_forecast_parses_response():
    """Client correctly parses a minimal Open-Meteo response."""
    api_response = {
        "hourly": {
            "time": ["2026-02-21T09:00", "2026-02-21T10:00"],
            "temperature_2m": [5.0, 6.0],
            "relative_humidity_2m": [80, 75],
            "dewpoint_2m": [2.0, 2.5],
            "surface_pressure": [1013, 1013],
            "pressure_msl": [1015, 1015],
            "wind_speed_10m": [12.0, 14.0],
            "wind_direction_10m": [270, 280],
            "wind_gusts_10m": [20.0, 22.0],
            "precipitation": [0.0, 0.1],
            "precipitation_probability": [10, 20],
            "cloud_cover": [50, 60],
            "cloud_cover_low": [20, 30],
            "cloud_cover_mid": [30, 40],
            "cloud_cover_high": [10, 10],
            "freezing_level_height": [2000, 2100],
            "cape": [50, 60],
            "visibility": [10000, 9000],
            "temperature_850hPa": [0.0, 0.5],
            "relative_humidity_850hPa": [85, 80],
            "dewpoint_850hPa": [-1.5, -1.0],
            "wind_speed_850hPa": [25, 28],
            "wind_direction_850hPa": [290, 285],
            "geopotential_height_850hPa": [1450, 1455],
        }
    }

    responses.add(
        responses.GET,
        "https://api.open-meteo.com/v1/gfs",
        json=api_response,
        status=200,
    )

    client = OpenMeteoClient()
    wp = Waypoint(icao="EGTK", name="Oxford", lat=51.836, lon=-1.32)
    result = client.fetch_forecast(wp, ModelSource.GFS)

    assert result.waypoint.icao == "EGTK"
    assert result.model == ModelSource.GFS
    assert len(result.hourly) == 2

    h = result.hourly[0]
    assert h.temperature_2m_c == 5.0
    assert h.wind_speed_10m_kt == 12.0

    # Check pressure level parsing
    level_850 = h.level_at(850)
    assert level_850 is not None
    assert level_850.temperature_c == 0.0
    assert level_850.wind_speed_kt == 25


@responses.activate
@patch("weatherbrief.fetch.open_meteo.time.sleep")
def test_fetch_all_models_continues_on_failure(_mock_sleep):
    """fetch_all_models continues when one model fails."""
    api_response = {
        "hourly": {
            "time": ["2026-02-21T09:00"],
            "temperature_2m": [5.0],
        }
    }

    # GFS succeeds
    responses.add(
        responses.GET,
        "https://api.open-meteo.com/v1/gfs",
        json=api_response,
        status=200,
    )
    # ECMWF fails (all retries)
    responses.add(
        responses.GET,
        "https://api.open-meteo.com/v1/ecmwf",
        json={"error": "server error"},
        status=500,
    )

    client = OpenMeteoClient()
    wp = Waypoint(icao="EGTK", name="Oxford", lat=51.836, lon=-1.32)
    results = client.fetch_all_models(wp, [ModelSource.GFS, ModelSource.ECMWF])

    assert len(results) == 1
    assert results[0].model == ModelSource.GFS


@responses.activate
def test_fetch_all_models_skips_out_of_range():
    """fetch_all_models skips models whose range is shorter than days_out."""
    api_response = {
        "hourly": {
            "time": ["2026-02-21T09:00"],
            "temperature_2m": [5.0],
        }
    }

    # GFS succeeds (max_days=16)
    responses.add(
        responses.GET,
        "https://api.open-meteo.com/v1/gfs",
        json=api_response,
        status=200,
    )
    # ICON should be skipped (max_days=7, days_out=10)
    responses.add(
        responses.GET,
        "https://api.open-meteo.com/v1/dwd-icon",
        json=api_response,
        status=200,
    )

    client = OpenMeteoClient()
    wp = Waypoint(icao="EGTK", name="Oxford", lat=51.836, lon=-1.32)
    results = client.fetch_all_models(
        wp, [ModelSource.GFS, ModelSource.ICON], days_out=10
    )

    # Only GFS should be fetched (ICON skipped due to 7-day range < 10 days out)
    assert len(results) == 1
    assert results[0].model == ModelSource.GFS
    # Only one HTTP call should have been made (GFS), not two
    assert len(responses.calls) == 1


@responses.activate
def test_fetch_forecast_passes_model_param():
    """Client passes models query param for endpoints that need it (e.g. UKMO)."""
    api_response = {
        "hourly": {
            "time": ["2026-02-21T09:00"],
            "temperature_2m": [5.0],
        }
    }

    responses.add(
        responses.GET,
        "https://api.open-meteo.com/v1/forecast",
        json=api_response,
        status=200,
    )

    client = OpenMeteoClient()
    wp = Waypoint(icao="EGTK", name="Oxford", lat=51.836, lon=-1.32)
    client.fetch_forecast(wp, ModelSource.UKMO)

    req = responses.calls[0].request
    assert "models=ukmo_seamless" in req.url


# --- fetch_multi_point tests ---

_MINIMAL_HOURLY = {
    "time": ["2026-02-21T09:00", "2026-02-21T10:00"],
    "temperature_2m": [5.0, 6.0],
}


def _make_route_points():
    """Three route points: two named waypoints with an interpolated point between them."""
    return [
        RoutePoint(lat=51.836, lon=-1.32, distance_from_origin_nm=0.0,
                   waypoint_icao="EGTK", waypoint_name="Oxford Kidlington"),
        RoutePoint(lat=50.4, lon=0.5, distance_from_origin_nm=100.0),
        RoutePoint(lat=48.969, lon=2.441, distance_from_origin_nm=224.0,
                   waypoint_icao="LFPB", waypoint_name="Paris Le Bourget"),
    ]


@responses.activate
def test_fetch_multi_point_parses_list_response():
    """Multi-point returns one WaypointForecast per input point."""
    multi_response = [
        {"hourly": _MINIMAL_HOURLY},
        {"hourly": _MINIMAL_HOURLY},
        {"hourly": _MINIMAL_HOURLY},
    ]

    responses.add(
        responses.GET,
        "https://api.open-meteo.com/v1/gfs",
        json=multi_response,
        status=200,
    )

    client = OpenMeteoClient()
    points = _make_route_points()
    results = client.fetch_multi_point(points, ModelSource.GFS)

    assert len(results) == 3
    assert all(r.model == ModelSource.GFS for r in results)
    assert all(len(r.hourly) == 2 for r in results)


@responses.activate
def test_fetch_multi_point_hour_filter_skips_parsing_other_hours():
    """hour_filter keeps only hourly slots whose UTC hour is in the set.

    Issue #236: the standalone cycle samples 5 of every 24 hours; parsing
    the rest into Pydantic objects only to discard them dominated the fetch
    phase's transient memory.
    """
    hourly = {
        "time": ["2026-02-21T06:00", "2026-02-21T07:00",
                 "2026-02-21T09:00", "2026-02-21T12:00"],
        "temperature_2m": [5.0, 6.0, 7.0, 8.0],
    }
    responses.add(
        responses.GET,
        "https://api.open-meteo.com/v1/gfs",
        json=[{"hourly": hourly}, {"hourly": hourly}, {"hourly": hourly}],
        status=200,
    )

    client = OpenMeteoClient()
    results = client.fetch_multi_point(
        _make_route_points(), ModelSource.GFS, hour_filter={6, 9},
    )

    assert len(results) == 3
    for r in results:
        assert [h.time.hour for h in r.hourly] == [6, 9]
    assert results[0].hourly[0].temperature_2m_c == 5.0
    assert results[0].hourly[1].temperature_2m_c == 7.0


@responses.activate
def test_fetch_multi_point_hour_filter_none_keeps_all_hours():
    """Default behaviour (no filter) is unchanged."""
    hourly = {
        "time": ["2026-02-21T06:00", "2026-02-21T07:00"],
        "temperature_2m": [5.0, 6.0],
    }
    responses.add(
        responses.GET,
        "https://api.open-meteo.com/v1/gfs",
        json=[{"hourly": hourly}, {"hourly": hourly}, {"hourly": hourly}],
        status=200,
    )

    client = OpenMeteoClient()
    results = client.fetch_multi_point(_make_route_points(), ModelSource.GFS)

    assert all(len(r.hourly) == 2 for r in results)


@responses.activate
def test_fetch_multi_point_hour_filter_all_null_still_raises():
    """The empty-forecast guard operates on the filtered slots."""
    hourly = {
        "time": ["2026-02-21T06:00", "2026-02-21T09:00"],
        "temperature_2m": [None, None],
        "wind_speed_10m": [None, None],
    }
    responses.add(
        responses.GET,
        "https://api.open-meteo.com/v1/gfs",
        json=[{"hourly": hourly}, {"hourly": hourly}, {"hourly": hourly}],
        status=200,
    )

    client = OpenMeteoClient()
    with pytest.raises(EmptyForecastError):
        client.fetch_multi_point(
            _make_route_points(), ModelSource.GFS, hour_filter={6, 9},
        )


@responses.activate
def test_fetch_multi_point_raises_when_all_values_null():
    """Open-Meteo answers requests past a run's actual data_end_time with a
    structurally-valid response (correct timestamps) where every value is
    null. We must detect that and raise rather than persist a skeleton."""
    null_hourly = {
        "time": ["2026-02-21T09:00", "2026-02-21T10:00", "2026-02-21T11:00"],
        "temperature_2m": [None, None, None],
        "wind_speed_10m": [None, None, None],
        "surface_pressure": [None, None, None],
    }
    multi_response = [{"hourly": null_hourly}, {"hourly": null_hourly}, {"hourly": null_hourly}]
    responses.add(
        responses.GET,
        "https://api.open-meteo.com/v1/meteofrance",
        json=multi_response,
        status=200,
    )

    client = OpenMeteoClient()
    points = _make_route_points()
    try:
        client.fetch_multi_point(points, ModelSource.METEOFRANCE)
    except EmptyForecastError as exc:
        assert "meteofrance" in str(exc).lower()
        return
    raise AssertionError("EmptyForecastError was not raised on all-null response")


@responses.activate
def test_fetch_multi_point_does_not_raise_when_only_some_null():
    """Don't false-positive: a response with even one non-null reading
    across all hours and points must come through normally."""
    mostly_null = {
        "time": ["2026-02-21T09:00", "2026-02-21T10:00"],
        "temperature_2m": [None, None],
    }
    one_real = {
        "time": ["2026-02-21T09:00", "2026-02-21T10:00"],
        "temperature_2m": [None, 12.5],  # last hour has data
    }
    responses.add(
        responses.GET,
        "https://api.open-meteo.com/v1/gfs",
        json=[{"hourly": mostly_null}, {"hourly": one_real}, {"hourly": mostly_null}],
        status=200,
    )

    client = OpenMeteoClient()
    points = _make_route_points()
    results = client.fetch_multi_point(points, ModelSource.GFS)
    assert len(results) == 3  # no exception


@responses.activate
def test_fetch_multi_point_preserves_waypoint_names():
    """Named waypoints get full airport name; interpolated points get synthetic label."""
    multi_response = [
        {"hourly": _MINIMAL_HOURLY},
        {"hourly": _MINIMAL_HOURLY},
        {"hourly": _MINIMAL_HOURLY},
    ]

    responses.add(
        responses.GET,
        "https://api.open-meteo.com/v1/gfs",
        json=multi_response,
        status=200,
    )

    client = OpenMeteoClient()
    points = _make_route_points()
    results = client.fetch_multi_point(points, ModelSource.GFS)

    assert results[0].waypoint.icao == "EGTK"
    assert results[0].waypoint.name == "Oxford Kidlington"
    assert results[1].waypoint.icao == "RP100"  # synthetic from distance
    assert results[2].waypoint.icao == "LFPB"
    assert results[2].waypoint.name == "Paris Le Bourget"


@responses.activate
def test_fetch_multi_point_sends_date_window():
    """start_date/end_date are passed as query params instead of forecast_days."""
    multi_response = [{"hourly": _MINIMAL_HOURLY}]

    responses.add(
        responses.GET,
        "https://api.open-meteo.com/v1/gfs",
        json=multi_response,
        status=200,
    )

    client = OpenMeteoClient()
    points = [RoutePoint(lat=51.836, lon=-1.32, distance_from_origin_nm=0.0,
                         waypoint_icao="EGTK")]
    client.fetch_multi_point(
        points, ModelSource.GFS,
        start_date="2026-02-21", end_date="2026-02-21",
    )

    req = responses.calls[0].request
    assert "start_date=2026-02-21" in req.url
    assert "end_date=2026-02-21" in req.url
    assert "forecast_days" not in req.url


@responses.activate
def test_fetch_multi_point_sends_comma_separated_coords():
    """Latitude and longitude are comma-separated in the request."""
    multi_response = [
        {"hourly": _MINIMAL_HOURLY},
        {"hourly": _MINIMAL_HOURLY},
    ]

    responses.add(
        responses.GET,
        "https://api.open-meteo.com/v1/gfs",
        json=multi_response,
        status=200,
    )

    client = OpenMeteoClient()
    points = [
        RoutePoint(lat=51.836, lon=-1.32, distance_from_origin_nm=0.0),
        RoutePoint(lat=48.969, lon=2.441, distance_from_origin_nm=224.0),
    ]
    client.fetch_multi_point(points, ModelSource.GFS)

    req = responses.calls[0].request
    assert "latitude=51.836%2C48.969" in req.url or "latitude=51.836,48.969" in req.url
    assert "longitude=-1.32%2C2.441" in req.url or "longitude=-1.32,2.441" in req.url


@responses.activate
def test_fetch_multi_point_single_point_dict_response():
    """Single-point response (dict, not list) is handled correctly."""
    single_response = {"hourly": _MINIMAL_HOURLY}

    responses.add(
        responses.GET,
        "https://api.open-meteo.com/v1/gfs",
        json=single_response,
        status=200,
    )

    client = OpenMeteoClient()
    points = [RoutePoint(lat=51.836, lon=-1.32, distance_from_origin_nm=0.0,
                         waypoint_icao="EGTK")]
    results = client.fetch_multi_point(points, ModelSource.GFS)

    assert len(results) == 1
    assert results[0].waypoint.icao == "EGTK"


# ── Vertical velocity units (#833) ──────────────────────────────────
#
# Open-Meteo serves vertical_velocity_{N}hPa as geometric w (m/s, up
# positive) for every model that has it; we store omega (Pa/s, ascent
# negative). At 700 hPa / −5 °C, ρ = 70000 / (287.05 · 268.15) ≈ 0.9094 kg/m³,
# so w = +0.1 m/s → ω ≈ −0.892 Pa/s.

_VV_HOURLY = {
    "time": ["2026-02-21T09:00"],
    "temperature_2m": [5.0],
    "temperature_700hPa": [-5.0],
    "vertical_velocity_700hPa": [0.1],
}

# Real endpoint per model that publishes vertical velocity on Open-Meteo.
_VV_SOURCES = [
    (ModelSource.GFS, "https://api.open-meteo.com/v1/gfs"),
    (ModelSource.ECMWF, "https://api.open-meteo.com/v1/ecmwf"),
    (ModelSource.UKMO, "https://api.open-meteo.com/v1/forecast"),
]


def _vv_response(unit: str) -> dict:
    return {
        "hourly_units": {"time": "iso8601", "vertical_velocity_700hPa": unit},
        "hourly": _VV_HOURLY,
    }


def test_w_to_omega_sign_and_scale():
    """Upward w is negative omega, ~10x larger in magnitude at 700 hPa."""
    from weatherbrief.fetch.omega import w_to_omega_pa_s

    assert w_to_omega_pa_s(0.1, 700, 268.15) == pytest.approx(-0.892, abs=1e-3)
    assert w_to_omega_pa_s(-0.1, 700, 268.15) == pytest.approx(0.892, abs=1e-3)
    assert w_to_omega_pa_s(0.0, 700, 268.15) == 0.0


@responses.activate
@pytest.mark.parametrize("model,url", _VV_SOURCES)
def test_fetch_forecast_converts_w_to_omega(model, url):
    """Each source's m/s vertical velocity is stored as omega in Pa/s."""
    responses.add(responses.GET, url, json=_vv_response("m/s"), status=200)
    wp = Waypoint(icao="EGTK", name="Oxford", lat=51.836, lon=-1.32)
    result = OpenMeteoClient().fetch_forecast(wp, model)

    lv = result.hourly[0].level_at(700)
    assert lv.vertical_velocity_pa_s == pytest.approx(-0.892, abs=1e-3)


@responses.activate
@pytest.mark.parametrize("model,url", _VV_SOURCES)
def test_fetch_multi_point_converts_w_to_omega(model, url):
    """The route (multi-point) path converts per point, using each point's units."""
    responses.add(
        responses.GET, url,
        json=[_vv_response("m/s"), _vv_response("m/s")], status=200,
    )
    points = _make_route_points()[:2]
    results = OpenMeteoClient().fetch_multi_point(points, model)

    for r in results:
        lv = r.hourly[0].level_at(700)
        assert lv.vertical_velocity_pa_s == pytest.approx(-0.892, abs=1e-3)


@responses.activate
def test_omega_unit_passes_through():
    """If Open-Meteo ever serves omega itself, it is stored unchanged."""
    responses.add(
        responses.GET, "https://api.open-meteo.com/v1/gfs",
        json=_vv_response("Pa/s"), status=200,
    )
    wp = Waypoint(icao="EGTK", name="Oxford", lat=51.836, lon=-1.32)
    result = OpenMeteoClient().fetch_forecast(wp, ModelSource.GFS)

    assert result.hourly[0].level_at(700).vertical_velocity_pa_s == 0.1


@responses.activate
@pytest.mark.parametrize("units", [{"vertical_velocity_700hPa": "cm/s"}, {}])
def test_unknown_or_missing_unit_drops_vertical_velocity(units, caplog, _fresh_vv_log):
    """No recognised unit → vertical velocity is dropped, never guessed.

    An unrecognised unit is logged as an error; values with no unit at all
    (absent ``hourly_units``) only warn — see ``test_values_without_unit_warn_once``.
    """
    body = {"hourly": _VV_HOURLY}
    if units:
        body["hourly_units"] = units
    responses.add(
        responses.GET, "https://api.open-meteo.com/v1/gfs", json=body, status=200,
    )
    wp = Waypoint(icao="EGTK", name="Oxford", lat=51.836, lon=-1.32)
    with caplog.at_level("ERROR", logger="weatherbrief.fetch.open_meteo"):
        result = OpenMeteoClient().fetch_forecast(wp, ModelSource.GFS)

    lv = result.hourly[0].level_at(700)
    assert lv.vertical_velocity_pa_s is None
    assert lv.temperature_c == -5.0  # the rest of the level is unaffected
    assert ("not recognised" in caplog.text) == bool(units)


@pytest.fixture
def _fresh_vv_log(monkeypatch):
    """Unit problems log once per process; give each test a clean slate."""
    import weatherbrief.fetch.open_meteo as om

    monkeypatch.setattr(om, "_VV_UNIT_PROBLEMS_LOGGED", set())


def test_undefined_unit_is_no_data_not_an_error(caplog, _fresh_vv_log):
    """Models without vertical velocity report 'undefined' — not an error."""
    from weatherbrief.fetch.open_meteo import vertical_velocity_unit

    with caplog.at_level("WARNING", logger="weatherbrief.fetch.open_meteo"):
        assert vertical_velocity_unit({
            "hourly_units": {"vertical_velocity_700hPa": "undefined"},
            "hourly": {"vertical_velocity_700hPa": [None]},
        }) is None
    assert caplog.text == ""


def test_values_without_unit_warn_once(caplog, _fresh_vv_log):
    """Values present but no unit declared → dropped, with one warning."""
    from weatherbrief.fetch.open_meteo import vertical_velocity_unit

    body = {"hourly": {"vertical_velocity_700hPa": [0.1]}}
    with caplog.at_level("WARNING", logger="weatherbrief.fetch.open_meteo"):
        assert vertical_velocity_unit(body) is None
        assert vertical_velocity_unit(body) is None
    assert caplog.text.count("no unit") == 1


def test_unrecognised_unit_logs_once(caplog, _fresh_vv_log):
    from weatherbrief.fetch.open_meteo import vertical_velocity_unit

    body = {"hourly_units": {"vertical_velocity_700hPa": "cm/s"},
            "hourly": {"vertical_velocity_700hPa": [10.0]}}
    with caplog.at_level("ERROR", logger="weatherbrief.fetch.open_meteo"):
        for _ in range(3):
            assert vertical_velocity_unit(body) is None
    assert caplog.text.count("not recognised") == 1


def test_key_fingerprint_shows_only_last_four():
    from weatherbrief.fetch.open_meteo import key_fingerprint

    assert key_fingerprint("abcdefghijkl1234") == "…1234"
    assert key_fingerprint("short") == "****"
    assert key_fingerprint("12345678") == "****"


def test_customer_key_logged_as_fingerprint_only(monkeypatch, caplog):
    monkeypatch.setenv("OPENMETEO_API_KEY", "secret-dev-key-9z9z")
    monkeypatch.setenv("ENVIRONMENT", "development")
    with caplog.at_level("INFO", logger="weatherbrief.fetch.open_meteo"):
        client = OpenMeteoClient()
    assert client.has_api_key
    text = caplog.text
    assert "key …9z9z" in text
    assert "environment development" in text
    assert "secret-dev-key" not in text
