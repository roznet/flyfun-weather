"""GRIB-primary ICON snapshots for the forecast map and alternates (#914).

Real GRIB2 messages (eccodes sample grid) are put in the ICON-EU cache under
the production cache keys and driven through ``fetch_model_snapshots``: cache
-> per-file decode -> sounding/surface builders -> snapshot dict. Open-Meteo
is a fake that counts calls, so "a covered cycle makes no Open-Meteo request"
is asserted directly, and the fallback is exercised by taking files away.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

eccodes = pytest.importorskip("eccodes")
pytest.importorskip("cfgrib")

from weatherbrief.fetch.grib.cache import cache_dir_for_run, cache_key, put_cached  # noqa: E402
from weatherbrief.tasks.airport_watchlist import WatchlistAirport  # noqa: E402
from tests.test_conv_precip_fill_rate import (  # noqa: E402
    _LEVELS,
    _grib_message,
)

INIT = datetime(2026, 9, 30, 0, 0, tzinfo=timezone.utc)
INIT_DATE = "20260930"

AIRPORTS = [
    WatchlistAirport(icao="EGLL", lat=51.47, lon=0.46),
    WatchlistAirport(icao="LFPG", lat=49.01, lon=2.55),
    WatchlistAirport(icao="EDDF", lat=50.03, lon=8.56),
    WatchlistAirport(icao="LIRF", lat=41.80, lon=12.25),
]

# 06Z and 12Z on day 0 and day 1: f006, f012, f030, f036 (all hourly region).
SAMPLE_HOURS = [6, 12]
DAYS = 1
STEPS = [6, 12, 30, 36]
PRECIP_MM_PER_H = 1.5


def _isa_height_m(p_hpa: float) -> float:
    return 44330.8 * (1.0 - (p_hpa / 1013.25) ** 0.190263)


def _const(value: float, step_h: int) -> bytes:
    # The ICON decoders identify a field by its file, not its parameter, so a
    # generic message carrying the right value is enough.
    return _grib_message(centre=78, category=0, number=0, value=value, step_h=step_h)


def _put(run_dir, fhour: int, label: str, value: float) -> None:
    put_cached(run_dir, cache_key(fhour, label), _const(value, fhour))


def _seed_icon_run(data_dir, *, skip_pressure_steps=(), levels=None, steps=STEPS):
    """Cache everything the ICON snapshot builder and the cloud diag read."""
    from weatherbrief.fetch.grib.icon_eu_fetch import (
        ICON_EU,
        ICON_EU_CLOUD_DIAG_CACHE_KEY,
        ICON_EU_RAIN_CON_CACHE_KEY,
        ICON_SNAPSHOT_PRESSURE_LEVELS,
        icon_pressure_cache_key,
        icon_surface_cache_key,
    )

    run_dir = cache_dir_for_run(data_dir, INIT_DATE, 0, model="icon-eu")
    levels = ICON_SNAPSHOT_PRESSURE_LEVELS if levels is None else levels
    for fhour in steps:
        if fhour not in skip_pressure_steps:
            for level in levels:
                h_m = _isa_height_m(level)
                t_k = 288.15 - 0.0065 * min(h_m, 11000.0)
                for var, value in (
                    ("t", t_k), ("relhum", 60.0), ("u", 10.0), ("v", 0.0),
                    ("fi", 9.80665 * h_m),
                ):
                    _put(run_dir, fhour, icon_pressure_cache_key(var, level, ICON_EU), value)
        for var, value in (
            ("t_2m", 288.15), ("td_2m", 283.15), ("relhum_2m", 72.0),
            ("u_10m", 5.0), ("v_10m", 0.0), ("vmax_10m", 8.0),
            ("pmsl", 101325.0), ("ps", 101000.0), ("vis", 20000.0),
            ("hzerocl", 2500.0),
        ):
            _put(run_dir, fhour, icon_surface_cache_key(var, ICON_EU), value)
        # Accumulated since init, on the step and its hourly predecessor.
        for h in (h for h in (fhour - 1, fhour) if h >= 0):
            for var in ("tot_prec", "rain_gsp", "rain_con", "snow_gsp", "snow_con"):
                mm = PRECIP_MM_PER_H * h if var == "tot_prec" else 0.0
                _put(run_dir, h, icon_surface_cache_key(var, ICON_EU), mm)
        # Cloud diagnostics (#635 feed): total cover + accumulated RAIN_CON.
        # (No accumulated message at step 0: eccodes refuses an empty window.)
        rain_con = _grib_message(centre=78, category=1, number=76, value=0.0,
                                 step_h=fhour, accumulated=True) if fhour else b""
        put_cached(
            run_dir, cache_key(fhour, ICON_EU_CLOUD_DIAG_CACHE_KEY),
            _grib_message(centre=78, category=6, number=1, value=40.0, step_h=fhour)
            + rain_con,
        )
        if fhour > 1:
            put_cached(
                run_dir, cache_key(fhour - 1, ICON_EU_RAIN_CON_CACHE_KEY),
                _grib_message(centre=78, category=1, number=76, value=0.0,
                              step_h=fhour - 1, accumulated=True),
            )
    return run_dir


class _OpenMeteoFake:
    """``OpenMeteoClient.fetch_multi_point`` stand-in that records each call."""

    def __init__(self):
        self.calls: list[list[str]] = []

    def __call__(self, client, points, model, *, start_date=None, end_date=None,
                 chunk_size=None, hour_filter=None):
        from weatherbrief.models import HourlyForecast, Waypoint, WaypointForecast
        from weatherbrief.models.analysis import PressureLevelData

        client._record_call()
        self.calls.append([p.waypoint_icao for p in points])
        out = []
        for p in points:
            hourly = [
                HourlyForecast(
                    time=INIT + timedelta(hours=h),
                    temperature_2m_c=20.0, dewpoint_2m_c=15.0,
                    wind_speed_10m_kt=9.0, precipitation_mm=0.0,
                    cloud_cover_pct=10.0,
                    pressure_levels=[
                        PressureLevelData(
                            pressure_hpa=hpa, temperature_c=tc,
                            relative_humidity_pct=rh,
                            wind_speed_kt=25, wind_direction_deg=250,
                        )
                        for hpa, tc, rh in _LEVELS
                    ],
                )
                for h in range((DAYS + 1) * 24)
                if hour_filter is None or (INIT + timedelta(hours=h)).hour in hour_filter
            ]
            out.append(WaypointForecast(
                waypoint=Waypoint(icao=p.waypoint_icao, name=p.waypoint_icao,
                                  lat=p.lat, lon=p.lon),
                model=model, fetched_at=INIT, hourly=hourly,
            ))
        return out


def _no_network(*args, **kwargs):
    raise AssertionError("everything is cached; nothing should be downloaded")


@pytest.fixture
def env(monkeypatch, tmp_path):
    """In-process decode, empty temp cache, no TTL purge, counted Open-Meteo."""
    import weatherbrief.fetch.grib as grib
    import weatherbrief.tasks.standalone_grib as sg

    monkeypatch.setenv("GRIB_DECODE_PRIORITY_ENABLED", "0")
    monkeypatch.setattr(grib, "_get_decode_pool", lambda: None)
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("WB_GRIB_SNAPSHOTS", raising=False)
    monkeypatch.delenv("WB_GRIB_SNAPSHOTS_DRILL", raising=False)
    # Seeded under a fixed INIT that a wall-clock TTL purge would delete.
    monkeypatch.setattr(sg, "purge_old_runs", lambda *a, **kw: None)
    monkeypatch.setattr(
        "weatherbrief.fetch.grib.icon_eu_fetch._download_one_file",
        lambda url, session: (None, 404),
    )
    monkeypatch.setattr(
        "weatherbrief.fetch.grib.icon_eu_fetch.fetch_icon_eu_single_level",
        _no_network,
    )
    fake = _OpenMeteoFake()

    def fetch_multi_point(client, *args, **kwargs):  # bound as a method
        return fake(client, *args, **kwargs)

    monkeypatch.setattr(
        "weatherbrief.fetch.open_meteo.OpenMeteoClient.fetch_multi_point",
        fetch_multi_point,
    )
    return tmp_path, fake


def _run(only_valid_time=None, sample_hours=SAMPLE_HOURS):
    from weatherbrief.tasks import standalone_verification as sv

    return sv.fetch_model_snapshots(
        "icon", INIT, AIRPORTS, MagicMock(),
        sample_hours=sample_hours, days=DAYS, only_valid_time=only_valid_time,
    )


def _hours(snaps, icao=None):
    return sorted(
        int((s["forecast_hour"] - INIT).total_seconds() // 3600)
        for s in snaps if icao is None or s["icao"] == icao
    )


class TestCoveredCycle:
    def test_no_open_meteo_call_and_every_row_present(self, env):
        data_dir, om = env
        _seed_icon_run(data_dir)

        snaps, api_calls = _run()

        assert api_calls == 0
        assert om.calls == []
        assert len(snaps) == len(AIRPORTS) * len(STEPS)
        for a in AIRPORTS:
            assert _hours(snaps, a.icao) == STEPS

    def test_row_values_come_from_the_grib(self, env):
        data_dir, _ = env
        _seed_icon_run(data_dir)

        snaps, _ = _run()

        for s in snaps:
            assert s["temperature_2m_c"] == pytest.approx(15.0, abs=0.01)
            assert s["dewpoint_2m_c"] == pytest.approx(10.0, abs=0.01)
            assert s["wind_speed_10m_kt"] == pytest.approx(9.72, abs=0.01)
            assert s["wind_gusts_10m_kt"] == pytest.approx(15.55, abs=0.01)
            assert s["visibility_m"] == pytest.approx(20000.0)
            assert s["cloud_cover_pct"] == pytest.approx(40.0)
            # Hourly region: a 1 h window, the total over it.
            assert s["precip_period_h"] == 1
            assert s["precipitation_mm"] == pytest.approx(PRECIP_MM_PER_H, abs=1e-3)
            # The lite sounding ran on the GRIB profile.
            assert s.get("freezing_level_ft") is not None
            assert s["lcl_ft"] == pytest.approx(2000.0, abs=1.0)
            assert s["model"] == "icon"
            assert s["model_init_time"] == INIT


class TestFallback:
    def test_missing_step_sends_every_airport_to_open_meteo_for_that_hour_only(self, env):
        data_dir, om = env
        _seed_icon_run(data_dir, skip_pressure_steps=(30,))

        snaps, api_calls = _run()

        assert api_calls == 1
        assert sorted(om.calls[0]) == sorted(a.icao for a in AIRPORTS)
        for a in AIRPORTS:
            assert _hours(snaps, a.icao) == STEPS  # no duplicate hours
            by_hour = {
                int((s["forecast_hour"] - INIT).total_seconds() // 3600): s
                for s in snaps if s["icao"] == a.icao
            }
            # f030 from the Open-Meteo fake (20 °C), the rest from GRIB (15 °C).
            assert by_hour[30]["temperature_2m_c"] == pytest.approx(20.0)
            assert by_hour[6]["temperature_2m_c"] == pytest.approx(15.0, abs=0.01)

    def test_fallback_runs_no_sounding_for_covered_hours(self, env, monkeypatch):
        from weatherbrief.tasks import standalone_verification as sv

        data_dir, _ = env
        _seed_icon_run(data_dir, skip_pressure_steps=(30,))
        calls = []
        real = sv._enrich_with_sounding
        monkeypatch.setattr(
            sv, "_enrich_with_sounding",
            lambda snap, *a, **kw: (calls.append(snap["icao"]), real(snap, *a, **kw)),
        )

        _run()

        # One sounding pass per stored row: 3 GRIB hours + the 1 fallback hour.
        assert len(calls) == len(AIRPORTS) * len(STEPS)

    def test_too_few_levels_is_not_a_sounding(self, env):
        data_dir, om = env
        _seed_icon_run(data_dir, levels=(1000, 850, 700, 500))

        snaps, _ = _run()

        assert len(om.calls) == 1
        assert {s["temperature_2m_c"] for s in snaps} == {20.0}

    def test_switch_off_is_open_meteo_first(self, env, monkeypatch):
        data_dir, om = env
        _seed_icon_run(data_dir)
        monkeypatch.setenv("WB_GRIB_SNAPSHOTS", "0")

        snaps, api_calls = _run()

        assert api_calls == 1
        assert {s["temperature_2m_c"] for s in snaps} == {20.0}

    @pytest.mark.parametrize("drill,n_fallback", [("icon", 4), ("icon:half", 2)])
    def test_failure_drill(self, env, monkeypatch, drill, n_fallback):
        data_dir, om = env
        _seed_icon_run(data_dir)
        monkeypatch.setenv("WB_GRIB_SNAPSHOTS_DRILL", drill)

        snaps, _ = _run()

        assert len(om.calls[0]) == n_fallback
        assert len(snaps) == len(AIRPORTS) * len(STEPS)
        from_om = {s["icao"] for s in snaps if s["temperature_2m_c"] == 20.0}
        assert len(from_om) == n_fallback


class TestAnalysisStep:
    def test_init_hour_is_covered_without_precipitation(self, env):
        """A sample hour equal to the init hour (06/12/18Z runs) is step 0: no
        predecessor, so no precipitation rate. It must still count as covered,
        or every airport would fall back at the full Open-Meteo price."""
        from weatherbrief.tasks import standalone_verification as sv

        data_dir, om = env
        _seed_icon_run(data_dir, steps=[0, 6])

        snaps, api_calls = sv.fetch_model_snapshots(
            "icon", INIT, AIRPORTS, MagicMock(), sample_hours=[0, 6], days=0,
        )

        assert api_calls == 0 and om.calls == []
        assert _hours(snaps) == sorted([0, 6] * len(AIRPORTS))
        for s in snaps:
            at_init = s["forecast_hour"] == INIT
            assert (s["precipitation_mm"] is None) == at_init


class TestAlternatesEtaHour:
    def test_only_the_eta_hour_is_built(self, env):
        data_dir, om = env
        _seed_icon_run(data_dir)

        eta = INIT + timedelta(hours=30, minutes=20)
        snaps, api_calls = _run(only_valid_time=eta, sample_hours=[eta.hour])

        assert api_calls == 0
        assert _hours(snaps) == [30] * len(AIRPORTS)

    def test_planner_keeps_only_that_hour(self):
        from weatherbrief.tasks.standalone_verification import _planned_valid_times

        eta = INIT + timedelta(hours=30, minutes=20)
        assert _planned_valid_times(INIT, 4, [6], eta) == [INIT + timedelta(hours=30)]
        assert len(_planned_valid_times(INIT, 4, [6])) == 5

    def test_off_grid_hour_falls_back(self, env):
        """Past +78 h ICON-EU is 3-hourly: f080 is not a published step."""
        data_dir, om = env

        eta = INIT + timedelta(hours=80)
        from weatherbrief.tasks import standalone_verification as sv

        snaps, api_calls = sv.fetch_model_snapshots(
            "icon", INIT, AIRPORTS, MagicMock(), sample_hours=[eta.hour],
            days=4, only_valid_time=eta,
        )
        assert api_calls == 1
        assert len(om.calls[0]) == len(AIRPORTS)


class TestSwitchGrammar:
    @pytest.mark.parametrize("raw,expected", [
        (None, ("icon",)), ("1", ("icon",)), ("0", ()), ("off", ()),
        ("icon", ("icon",)), ("gfs", ()),
    ])
    def test_grib_snapshot_models(self, monkeypatch, raw, expected):
        from weatherbrief.fetch.grib.primary import grib_snapshot_models

        if raw is None:
            monkeypatch.delenv("WB_GRIB_SNAPSHOTS", raising=False)
        else:
            monkeypatch.setenv("WB_GRIB_SNAPSHOTS", raw)
        assert grib_snapshot_models() == expected


class TestThreeHourlyPrecipitation:
    def test_row_carries_the_hourly_rate_not_the_window_total(self):
        """Past +78 h the step window is 3 h; consumers read the column as hourly (#415)."""
        from weatherbrief.tasks.standalone_grib import IconSnapshotStep
        from weatherbrief.tasks.standalone_verification import (
            _icon_grib_hourly,
            _snapshot_row,
        )

        levels = {
            p: {"raw_temperature_k": 280.0, "raw_relative_humidity_pct": 50.0,
                "raw_u_wind_m_s": 5.0, "raw_v_wind_m_s": 0.0,
                "raw_geopotential_m2_s2": 9.80665 * _isa_height_m(p)}
            for p in (1000, 950, 925, 900, 850, 800, 700, 600, 500, 300)
        }
        step = IconSnapshotStep(
            pressure=[levels],
            surface=[{"t_2m": 285.0, "td_2m": 280.0, "u_10m": 3.0, "v_10m": 0.0,
                      "pmsl": 101300.0, "tot_prec": 106.0}],
            prev_surface=[{"tot_prec": 100.0}],
            window_h=3,
        )
        hourly = _icon_grib_hourly(INIT + timedelta(hours=81), step, 0)
        row = _snapshot_row("EGLL", "icon", INIT, hourly)

        assert row["precip_period_h"] == 1
        assert row["precipitation_mm"] == pytest.approx(2.0)
