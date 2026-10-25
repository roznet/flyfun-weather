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


def _seed_icon_run(data_dir, *, skip_pressure_steps=(), levels=None, steps=STEPS,
                   variant=None, t2m_k=288.15, cover_pct=40.0):
    """Cache everything the ICON snapshot builder and the cloud diag read.

    ICON-EU by default; ``variant=ICON_D2`` seeds the D2 run (#928), whose
    cloud diagnostics carry no accumulated ``rain_con``.
    """
    from weatherbrief.fetch.grib.icon_eu_fetch import (
        ICON_EU,
        ICON_EU_RAIN_CON_CACHE_KEY,
        icon_cloud_diag_cache_key,
        icon_pressure_cache_key,
        icon_snapshot_pressure_levels,
        icon_surface_cache_key,
    )

    variant = variant or ICON_EU
    is_eu = variant.slug == "icon-eu"
    run_dir = cache_dir_for_run(data_dir, INIT_DATE, 0, model=variant.slug)
    levels = icon_snapshot_pressure_levels(variant) if levels is None else levels
    for fhour in steps:
        if fhour not in skip_pressure_steps:
            for level in levels:
                h_m = _isa_height_m(level)
                t_k = 288.15 - 0.0065 * min(h_m, 11000.0)
                for var, value in (
                    ("t", t_k), ("relhum", 60.0), ("u", 10.0), ("v", 0.0),
                    ("fi", 9.80665 * h_m),
                ):
                    _put(run_dir, fhour, icon_pressure_cache_key(var, level, variant), value)
        for var, value in (
            ("t_2m", t2m_k), ("td_2m", 283.15), ("relhum_2m", 72.0),
            ("u_10m", 5.0), ("v_10m", 0.0), ("vmax_10m", 8.0),
            ("pmsl", 101325.0), ("ps", 101000.0), ("vis", 20000.0),
            ("hzerocl", 2500.0),
        ):
            _put(run_dir, fhour, icon_surface_cache_key(var, variant), value)
        # Accumulated since init, on the step and its hourly predecessor.
        for h in (h for h in (fhour - 1, fhour) if h >= 0):
            for var in ("tot_prec", "rain_gsp", "rain_con", "snow_gsp", "snow_con"):
                mm = PRECIP_MM_PER_H * h if var == "tot_prec" else 0.0
                _put(run_dir, h, icon_surface_cache_key(var, variant), mm)
        # Cloud diagnostics (#635 feed): total cover + accumulated RAIN_CON.
        # (No accumulated message at step 0: eccodes refuses an empty window.)
        rain_con = _grib_message(centre=78, category=1, number=76, value=0.0,
                                 step_h=fhour, accumulated=True) if fhour and is_eu else b""
        put_cached(
            run_dir, cache_key(fhour, icon_cloud_diag_cache_key(variant)),
            _grib_message(centre=78, category=6, number=1, value=cover_pct, step_h=fhour)
            + rain_con,
        )
        if fhour > 1 and is_eu:
            put_cached(
                run_dir, cache_key(fhour - 1, ICON_EU_RAIN_CON_CACHE_KEY),
                _grib_message(centre=78, category=1, number=76, value=0.0,
                              step_h=fhour - 1, accumulated=True),
            )
    return run_dir


class _OpenMeteoFake:
    """``OpenMeteoClient.fetch_multi_point`` stand-in that records each call."""

    def __init__(self, horizon_h: int = (DAYS + 1) * 24):
        self.calls: list[list[str]] = []
        self.horizon_h = horizon_h

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
                for h in range(self.horizon_h)
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
    monkeypatch.delenv("WB_GRIB_SNAPSHOTS_D2", raising=False)
    monkeypatch.setattr(sg, "_ICON_404_UNTIL", {})
    import weatherbrief.tasks.standalone_verification as sv

    # The D2 mask and verdicts are per-process memos: start every test empty.
    monkeypatch.setattr(sv, "_D2_MASK", None)
    monkeypatch.setattr(sv, "_D2_POINT_OK", {})
    # No validity mask (it would be a DWD download): the D2 bbox decides.
    monkeypatch.setattr("weatherbrief.fetch.grib.load_d2_validity_mask",
                        lambda *a, **kw: None)
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
        (None, ("icon", "gfs")), ("1", ("icon", "gfs")), ("0", ()), ("off", ()),
        ("icon", ("icon",)), ("gfs", ("gfs",)), ("ecmwf", ()),
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
        from weatherbrief.tasks.standalone_grib import GribSnapshotStep
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
        step = GribSnapshotStep(
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


# ---------------------------------------------------------------------------
# ICON-D2 for the short range inside its domain (#928)
# ---------------------------------------------------------------------------

D2_T2M_K = 296.15     # 23 °C: tells a D2 row from an EU (15 °C) or Open-Meteo (20 °C) one
D2_COVER = 70.0
IN_D2 = {"EGLL", "LFPG", "EDDF"}   # LIRF (41.8 N) is south of the D2 box


def _seed_d2(data_dir, **kw):
    from weatherbrief.fetch.grib.icon_eu_fetch import ICON_D2

    return _seed_icon_run(data_dir, variant=ICON_D2, t2m_k=D2_T2M_K,
                          cover_pct=D2_COVER, **kw)


def _by(snaps):
    return {
        (s["icao"], int((s["forecast_hour"] - INIT).total_seconds() // 3600)): s
        for s in snaps
    }


class TestIconD2:
    def test_d2_inside_the_domain_eu_outside(self, env):
        data_dir, om = env
        _seed_icon_run(data_dir)
        _seed_d2(data_dir)

        snaps, api_calls = _run()

        assert api_calls == 0 and om.calls == []
        assert len(snaps) == len(AIRPORTS) * len(STEPS)
        for (icao, _h), s in _by(snaps).items():
            if icao in IN_D2:
                assert s["temperature_2m_c"] == pytest.approx(23.0, abs=0.01)
                assert s["cloud_cover_pct"] == pytest.approx(D2_COVER)
            else:
                assert s["temperature_2m_c"] == pytest.approx(15.0, abs=0.01)
                assert s["cloud_cover_pct"] == pytest.approx(40.0)
            assert s["precipitation_mm"] == pytest.approx(PRECIP_MM_PER_H, abs=1e-3)
            assert s.get("freezing_level_ft") is not None

    def test_d2_hour_short_falls_back_to_eu_not_open_meteo(self, env):
        data_dir, _ = env
        _seed_icon_run(data_dir)
        _seed_d2(data_dir, skip_pressure_steps=(30,))

        snaps, api_calls = _run()

        assert api_calls == 0
        by = _by(snaps)
        for icao in IN_D2:
            assert by[(icao, 30)]["temperature_2m_c"] == pytest.approx(15.0, abs=0.01)
            assert by[(icao, 36)]["temperature_2m_c"] == pytest.approx(23.0, abs=0.01)

    def test_beyond_the_d2_horizon_is_eu(self, env):
        from weatherbrief.tasks import standalone_verification as sv

        data_dir, _ = env
        _seed_icon_run(data_dir, steps=[6, 30, 54])
        _seed_d2(data_dir, steps=[6, 30])

        snaps, api_calls = sv.fetch_model_snapshots(
            "icon", INIT, AIRPORTS, MagicMock(), sample_hours=[6], days=2,
        )
        by = _by(snaps)
        assert api_calls == 0
        assert by[("EGLL", 6)]["temperature_2m_c"] == pytest.approx(23.0, abs=0.01)
        assert by[("EGLL", 54)]["temperature_2m_c"] == pytest.approx(15.0, abs=0.01)

    def test_switch_off_is_eu_only(self, env, monkeypatch):
        data_dir, _ = env
        _seed_icon_run(data_dir)
        _seed_d2(data_dir)
        monkeypatch.setenv("WB_GRIB_SNAPSHOTS_D2", "0")

        snaps, _ = _run()

        assert all(s["temperature_2m_c"] == pytest.approx(15.0, abs=0.01) for s in snaps)

    def test_validity_mask_keeps_masked_airports_on_eu(self, env, monkeypatch):
        data_dir, _ = env
        _seed_icon_run(data_dir)
        _seed_d2(data_dir)
        monkeypatch.setattr("weatherbrief.fetch.grib.load_d2_validity_mask",
                            lambda *a, **kw: {"mask": True})
        # Pretend EDDF sits in a masked corner.
        monkeypatch.setattr(
            "weatherbrief.fetch.grib.decode.d2_corridor_fully_valid",
            lambda mask, lats, lons, radius_nm: lons[0] < 5.0,
        )

        snaps, _ = _run()

        by = _by(snaps)
        assert by[("EDDF", 6)]["temperature_2m_c"] == pytest.approx(15.0, abs=0.01)
        assert by[("EGLL", 6)]["temperature_2m_c"] == pytest.approx(23.0, abs=0.01)

    def test_validity_mask_and_verdicts_are_kept_per_process(self, env, monkeypatch):
        from weatherbrief.tasks.standalone_verification import _icon_d2_airports

        loads: list = []
        checks: list = []
        monkeypatch.setattr(
            "weatherbrief.fetch.grib.load_d2_validity_mask",
            lambda *a, **kw: loads.append(1) or {"mask": True},
        )

        def check(mask, lats, lons, radius_nm):
            checks.append(lons[0])
            return lons[0] > 5.0

        monkeypatch.setattr(
            "weatherbrief.fetch.grib.decode.d2_corridor_fully_valid", check,
        )
        inside = [
            WatchlistAirport(icao="EDDF", lat=50.03, lon=8.57),
            WatchlistAirport(icao="LFPG", lat=49.01, lon=2.55),
        ]
        for _ in range(3):
            assert [a.icao for a in _icon_d2_airports(inside, "20261025", 0)] == ["EDDF"]
        assert loads == [1]
        assert sorted(checks) == [2.55, 8.57]

    def test_failed_mask_is_retried_not_kept(self, env, monkeypatch):
        from weatherbrief.tasks.standalone_verification import _icon_d2_airports

        loads: list = []
        monkeypatch.setattr(
            "weatherbrief.fetch.grib.load_d2_validity_mask",
            lambda *a, **kw: loads.append(1),
        )
        inside = [WatchlistAirport(icao="EDDF", lat=50.03, lon=8.57)]
        for _ in range(2):
            assert [a.icao for a in _icon_d2_airports(inside, "20261025", 0)] == ["EDDF"]
        assert loads == [1, 1]

    def _spy_eu(self, monkeypatch):
        from weatherbrief.tasks import standalone_verification as sv

        asked: list[list[int]] = []
        real = sv._fetch_icon_fields

        def spy(init_date, init_hour, steps, airports, variant, priority):
            if variant.slug == "icon-eu":
                asked.append(list(steps))
            return real(init_date, init_hour, steps, airports, variant, priority)

        monkeypatch.setattr(sv, "_fetch_icon_fields", spy)
        return asked

    def test_all_airports_in_d2_fetch_eu_only_for_d2_shortfalls(self, env, monkeypatch):
        from weatherbrief.tasks import standalone_verification as sv

        data_dir, _ = env
        _seed_icon_run(data_dir)
        _seed_d2(data_dir, skip_pressure_steps=(30,))
        asked = self._spy_eu(monkeypatch)
        inside = [a for a in AIRPORTS if a.icao in IN_D2]

        snaps, api_calls = sv.fetch_model_snapshots(
            "icon", INIT, inside, MagicMock(), sample_hours=SAMPLE_HOURS, days=DAYS,
        )

        assert api_calls == 0
        assert asked == [[30]]  # no EU up front; f030 only, where D2 fell short
        by = _by(snaps)
        assert by[("EGLL", 30)]["temperature_2m_c"] == pytest.approx(15.0, abs=0.01)
        assert by[("EGLL", 36)]["temperature_2m_c"] == pytest.approx(23.0, abs=0.01)

    def test_no_d2_rows_at_all_logs_the_token(self, env, caplog):
        data_dir, _ = env
        _seed_icon_run(data_dir)  # no D2 run on disk; DWD answers 404

        with caplog.at_level("WARNING", logger="weatherbrief"):
            snaps, api_calls = _run()

        assert api_calls == 0
        assert "ICON_D2_SNAPSHOTS_EMPTY" in caplog.text
        assert all(s["temperature_2m_c"] == pytest.approx(15.0, abs=0.01) for s in snaps)

    def test_d2_rows_keep_their_own_grib_columns(self):
        from weatherbrief.tasks.standalone_verification import (
            _apply_grib_columns,
            _snapshot_hour_key,
        )

        class _Lookup:
            def get(self, icao, valid):
                return MagicMock(nwp_ceiling_ft=1200.0, cloud_base_ft=900.0)

        d2 = {"icao": "EGLL", "model": "icon", "forecast_hour": INIT,
              "nwp_ceiling_ft": 3000.0}
        eu = {"icao": "LIRF", "model": "icon", "forecast_hour": INIT}
        _apply_grib_columns([d2, eu], "icon", _Lookup(),
                            skip={_snapshot_hour_key("EGLL", INIT)})
        assert d2["nwp_ceiling_ft"] == 3000.0
        assert eu["nwp_ceiling_ft"] == 1200.0


class TestDownloads:
    def test_a_404_is_not_asked_again_within_the_ttl(self, tmp_path, monkeypatch):
        import weatherbrief.tasks.standalone_grib as sg

        monkeypatch.setattr(sg, "_ICON_404_UNTIL", {})
        asked: list[str] = []

        def fake(url, session):
            asked.append(url)
            return (None, 404) if url.endswith("missing") else (b"GRIB", 200)

        monkeypatch.setattr("weatherbrief.fetch.grib.icon_eu_fetch._download_one_file", fake)
        files = [("https://x/missing", "f001_A.grib2"), ("https://x/ok", "f001_B.grib2")]

        assert sg._download_icon_files(tmp_path, files, None, "t") == 4
        assert sg._download_icon_files(tmp_path, files, None, "t") == 0
        assert asked == ["https://x/missing", "https://x/ok"]

        monkeypatch.setattr(sg, "_ICON_404_TTL_S", -1.0)
        monkeypatch.setattr(sg, "_ICON_404_UNTIL", {})
        sg._download_icon_files(tmp_path, files, None, "t")
        sg._download_icon_files(tmp_path, files, None, "t")
        assert asked.count("https://x/missing") == 3

    def test_the_404_memo_drops_expired_entries(self, monkeypatch):
        import weatherbrief.tasks.standalone_grib as sg

        monkeypatch.setattr(sg, "_ICON_404_UNTIL", {})
        sg._note_404("a", 0.0)
        sg._note_404("b", sg._ICON_404_TTL_S + 1.0)  # "a" has expired by now
        assert set(sg._ICON_404_UNTIL) == {"b"}

    def test_ensure_fetches_the_files_the_snapshot_reads(self, tmp_path, monkeypatch):
        """The pre-warm helper (#929) leaves exactly the snapshot path's cache."""
        import weatherbrief.tasks.standalone_grib as sg
        from weatherbrief.fetch.grib.icon_eu_fetch import ICON_D2

        monkeypatch.setattr(sg, "purge_old_runs", lambda *a, **kw: None)
        monkeypatch.setattr(sg, "_ICON_404_UNTIL", {})
        monkeypatch.setattr(
            "weatherbrief.fetch.grib.icon_eu_fetch._download_one_file",
            lambda url, session: (b"12345", 200),
        )
        n = sg.ensure_icon_snapshot_files(
            INIT_DATE, 0, [6], variant=ICON_D2, session=MagicMock(), data_dir=tmp_path,
        )
        wanted, _ = sg._icon_snapshot_plan(INIT_DATE, 0, [6], ICON_D2)
        run_dir = cache_dir_for_run(tmp_path, INIT_DATE, 0, model="icon-d2")
        assert n == 5 * len(wanted)
        assert all((run_dir / ck).exists() for _url, ck in wanted.values())
        # 11 levels x 5 variables + 15 surface + 5 accumulated at f005.
        assert len(wanted) == 11 * 5 + 15 + 5


class TestFallbackAlert:
    def _airports(self, n):
        return [WatchlistAirport(icao=f"X{i:03d}", lat=35.0, lon=float(i % 10))
                for i in range(n)]

    def test_large_fallback_share_logs_the_token(self, env, caplog):
        from weatherbrief.tasks import standalone_verification as sv

        with caplog.at_level("WARNING", logger="weatherbrief"):
            sv.fetch_model_snapshots(
                "icon", INIT, self._airports(60), MagicMock(),
                sample_hours=SAMPLE_HOURS, days=DAYS,
            )
        assert "GRIB_PRIMARY_FALLBACK_HIGH icon" in caplog.text

    def test_small_calls_stay_quiet(self, env, caplog):
        with caplog.at_level("WARNING", logger="weatherbrief"):
            _run()  # 4 airports, all on the fallback: an alternates-sized call
        assert "GRIB_PRIMARY_FALLBACK_HIGH" not in caplog.text
