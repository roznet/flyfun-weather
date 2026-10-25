"""GRIB-primary GFS snapshots for the forecast map and alternates (#914).

The ICON half (``test_grib_primary_snapshots.py``) drives real GRIB messages
through the cache. Here the GFS reader (#913's ``read_gfs_grib_points``, tested
in ``test_grib_primary_gfs.py``) is replaced by a fake returning decoded
points, so these tests pin what the snapshot path asks of it and builds from
it: the reduced column, the per-hour predecessor steps, the rows, and the
per-airport Open-Meteo fallback.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from weatherbrief.tasks.airport_watchlist import WatchlistAirport
from tests.test_grib_primary_snapshots import DAYS, _OpenMeteoFake

INIT = datetime(2026, 9, 30, 0, 0, tzinfo=timezone.utc)

AIRPORTS = [
    WatchlistAirport(icao="EGLL", lat=51.47, lon=0.46),
    WatchlistAirport(icao="LFPG", lat=49.01, lon=2.55),
    WatchlistAirport(icao="EDDF", lat=50.03, lon=8.56),
]
SAMPLE_HOURS = [6, 12]
STEPS = [6, 12, 30, 36]
PRECIP_MM_PER_H = 1.5


def _isa_height_m(p_hpa: float) -> float:
    return 44330.8 * (1.0 - (p_hpa / 1013.25) ** 0.190263)


def _column(levels) -> dict[int, dict[str, float]]:
    out = {}
    for level in levels:
        h_m = _isa_height_m(level)
        out[level] = {
            "raw_temperature_k": 288.15 - 0.0065 * min(h_m, 11000.0),
            "raw_relative_humidity_pct": 60.0,
            "raw_u_wind_m_s": 10.0,
            "raw_v_wind_m_s": 0.0,
            "geopotential_height_m": h_m,
        }
    return out


def _surface(fhour: int) -> dict[str, float]:
    return {
        "TMP": 290.15, "DPT": 283.15, "RH": 64.0, "UGRD": 5.0, "VGRD": 0.0,
        "GUST": 9.0, "PRMSL": 101300.0, "PRES": 101000.0, "VIS": 24000.0,
        "HGT": 2600.0, "CAPE": 120.0, "TCDC": 55.0, "LCDC": 30.0,
        "MCDC": 20.0, "HCDC": 10.0,
        # Running totals since init.
        "APCP": PRECIP_MM_PER_H * fhour, "ACPCP": 0.0,
    }


class _Reader:
    """``read_gfs_grib_points`` stand-in: records the call, serves points."""

    def __init__(self, *, skip_sounding=(), levels_per_point=None):
        self.calls: list[dict] = []
        self.skip_sounding = set(skip_sounding)
        self.levels_per_point = levels_per_point

    def __call__(self, init_date, init_hour, fhours, lats, lons, **kw):
        from weatherbrief.fetch.grib import _gfs_previous_step
        from weatherbrief.fetch.grib.gfs_idx import GFS_SNAPSHOT_LEVELS

        self.calls.append({"fhours": list(fhours), **kw})
        levels = self.levels_per_point or GFS_SNAPSHOT_LEVELS
        prev = {_gfs_previous_step(f) for f in fhours} - {None}
        sounding = {
            f: [_column(levels) for _ in lats]
            for f in fhours if f not in self.skip_sounding
        }
        surface = {f: [_surface(f) for _ in lats] for f in set(fhours) | prev}
        return sounding, surface, sorted(set(fhours) | prev)


@pytest.fixture
def env(monkeypatch, tmp_path):
    import weatherbrief.fetch.grib as grib
    import weatherbrief.tasks.standalone_grib as sg

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("WB_GRIB_SNAPSHOTS", raising=False)
    monkeypatch.delenv("WB_GRIB_SNAPSHOTS_DRILL", raising=False)
    monkeypatch.setattr(sg, "purge_old_runs", lambda *a, **kw: None)
    # The #635 cloud-diag leg: nothing (the rows must not depend on it).
    monkeypatch.setattr(sg, "fetch_gfs_cloud_diag", lambda *a, **kw: {})
    reader = _Reader()
    monkeypatch.setattr(grib, "read_gfs_grib_points", reader)
    fake = _OpenMeteoFake(horizon_h=7 * 24)

    def fetch_multi_point(client, *args, **kwargs):  # bound as a method
        return fake(client, *args, **kwargs)

    monkeypatch.setattr(
        "weatherbrief.fetch.open_meteo.OpenMeteoClient.fetch_multi_point",
        fetch_multi_point,
    )
    return reader, fake, monkeypatch


def _run(airports=AIRPORTS, sample_hours=SAMPLE_HOURS, days=DAYS, only_valid_time=None):
    from weatherbrief.tasks import standalone_verification as sv

    return sv.fetch_model_snapshots(
        "gfs", INIT, airports, MagicMock(),
        sample_hours=sample_hours, days=days, only_valid_time=only_valid_time,
    )


def _hours(snaps, icao=None):
    return sorted(
        int((s["forecast_hour"] - INIT).total_seconds() // 3600)
        for s in snaps if icao is None or s["icao"] == icao
    )


class TestCoveredCycle:
    def test_no_open_meteo_call_and_every_row_present(self, env):
        reader, fake, _ = env
        snaps, calls = _run()
        assert calls == 0 and fake.calls == []
        for a in AIRPORTS:
            assert _hours(snaps, a.icao) == STEPS

    def test_reader_gets_the_reduced_column_and_every_predecessor(self, env):
        from weatherbrief.fetch.grib.gfs_idx import (
            GFS_SNAPSHOT_LEVELS,
            GFS_SNAPSHOT_VARIABLES,
        )

        reader, _, _ = env
        _run()
        (call,) = reader.calls
        assert call["fhours"] == STEPS
        assert call["levels"] == list(GFS_SNAPSHOT_LEVELS)
        assert call["variables"] == GFS_SNAPSHOT_VARIABLES
        assert call["every_previous_step"] is True

    def test_row_values_come_from_the_grib(self, env):
        snaps, _ = _run()
        row = next(s for s in snaps if s["icao"] == "LFPG" and _hours([s]) == [12])
        assert row["temperature_2m_c"] == pytest.approx(17.0)
        assert row["visibility_m"] == pytest.approx(24000.0)
        assert row["cloud_cover_pct"] == pytest.approx(55.0)
        assert row["wind_gusts_10m_kt"] == pytest.approx(9.0 * 1.943844, rel=1e-3)
        # f012 - f011 running totals: the hourly rate, a 1 h period.
        assert row["precipitation_mm"] == pytest.approx(PRECIP_MM_PER_H)
        assert row["precip_period_h"] == 1
        # The sounding pass ran on the GRIB column.
        assert row.get("freezing_level_ft") is not None


class TestFallback:
    def test_missing_sounding_hour_falls_back_for_that_hour_only(self, env):
        _, fake, monkeypatch = env
        import weatherbrief.fetch.grib as grib

        monkeypatch.setattr(grib, "read_gfs_grib_points", _Reader(skip_sounding={30}))
        snaps, calls = _run()
        assert calls == 1 and len(fake.calls) == 1
        assert sorted(fake.calls[0]) == sorted(a.icao for a in AIRPORTS)
        for a in AIRPORTS:
            assert _hours(snaps, a.icao) == STEPS
        om = [s for s in snaps if s["temperature_2m_c"] == pytest.approx(20.0)]
        assert set(_hours(om)) == {30}

    def test_too_few_levels_is_not_a_sounding(self, env):
        _, fake, monkeypatch = env
        import weatherbrief.fetch.grib as grib

        monkeypatch.setattr(
            grib, "read_gfs_grib_points", _Reader(levels_per_point=[1000, 850, 700]),
        )
        snaps, calls = _run()
        assert calls == 1
        assert all(s["temperature_2m_c"] == pytest.approx(20.0) for s in snaps)

    def test_reader_raising_falls_back_whole(self, env):
        _, fake, monkeypatch = env
        import weatherbrief.fetch.grib as grib

        def boom(*a, **kw):
            raise RuntimeError("NOAA down")

        monkeypatch.setattr(grib, "read_gfs_grib_points", boom)
        snaps, calls = _run()
        assert calls == 1 and len(snaps) == len(AIRPORTS) * len(STEPS)

    def test_switch_icon_only_rolls_gfs_back(self, env):
        _, fake, monkeypatch = env
        reader, _, _ = env
        monkeypatch.setenv("WB_GRIB_SNAPSHOTS", "icon")
        _, calls = _run()
        assert calls == 1 and reader.calls == []


class TestThreeHourlyRange:
    def test_past_f120_the_rate_is_over_three_hours(self, env):
        reader, _, _ = env
        # Day 5 at 06Z = f126, 3-hourly region: predecessor f123.
        snaps, calls = _run(sample_hours=[6], days=5)
        assert calls == 0
        row = next(s for s in snaps if _hours([s]) == [126])
        assert row["precipitation_mm"] == pytest.approx(PRECIP_MM_PER_H)
        assert row["precip_period_h"] == 1

    def test_off_grid_eta_falls_back(self, env):
        reader, fake, _ = env
        # f127 is not a GFS step (3-hourly past f120).
        eta = INIT + timedelta(hours=127)
        snaps, calls = _run(sample_hours=[7], days=5, only_valid_time=eta)
        assert calls == 1 and reader.calls == []
        assert _hours(snaps) == [127] * len(AIRPORTS)


class TestFallbackAlert:
    def _airports(self, n):
        return [WatchlistAirport(icao=f"X{i:03d}", lat=50.0, lon=float(i % 10)) for i in range(n)]

    def test_large_fallback_share_logs_the_token(self, env, caplog):
        _, _, monkeypatch = env
        import weatherbrief.fetch.grib as grib

        monkeypatch.setattr(grib, "read_gfs_grib_points", _Reader(skip_sounding=set(STEPS)))
        with caplog.at_level(logging.WARNING, logger="weatherbrief"):
            _run(airports=self._airports(60))
        assert "GRIB_PRIMARY_FALLBACK_HIGH gfs" in caplog.text

    def test_covered_cycle_and_small_calls_stay_quiet(self, env, caplog):
        _, _, monkeypatch = env
        import weatherbrief.fetch.grib as grib

        with caplog.at_level(logging.WARNING, logger="weatherbrief"):
            _run(airports=self._airports(60))
            monkeypatch.setattr(
                grib, "read_gfs_grib_points", _Reader(skip_sounding=set(STEPS)),
            )
            _run()  # 3 airports, all on the fallback: an alternates-sized call
        assert "GRIB_PRIMARY_FALLBACK_HIGH" not in caplog.text
