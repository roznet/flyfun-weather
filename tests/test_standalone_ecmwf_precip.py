"""ECMWF precipitation must be a per-period delta, at every step cadence.

``tp``/``sf`` arrive accumulated since init. The step-diff used to reach for
``step - 1``, which only exists inside ECMWF's hourly region (<= 90h). Past
that the cadence thins to 3-hourly and then 6-hourly, so the lookup missed,
the diff was silently skipped, and the value stayed accumulated-since-init.
Scoring treats precipitation as a *presence* flag, so every far-out sample
scored as "raining" if it had rained anywhere earlier in the run (#415).
"""

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from weatherbrief.tasks.airport_watchlist import WatchlistAirport
from weatherbrief.tasks.standalone_verification import fetch_ecmwf_grib_snapshots

INIT = datetime(2026, 7, 14, 0, 0, tzinfo=timezone.utc)

# ECMWF's real cadence: hourly to 90h, 3-hourly to 144h, 6-hourly to 168h.
DELIVERED = list(range(0, 91)) + list(range(93, 145, 3)) + [150, 156, 162, 168]

# Accumulated-since-init rainfall (metres water-equivalent). It rains 1 mm in
# every hour of the forecast, so the accumulation grows without bound — the
# shape that makes the old bug visible.
def _accum_m(step_h: int) -> float:
    return step_h * 0.001  # 1 mm/h, expressed in metres


def _file(step_h: int, part: str):
    return SimpleNamespace(
        step_hours=step_h,
        base_time=INIT,
        path=f"/fake/{part}_{step_h}h.grib",
        is_surface=(part == "a1"),
        is_pressure_level=(part == "a2"),
    )


@pytest.fixture
def run_files():
    return [_file(s, p) for s in DELIVERED for p in ("a1", "a2")]


@pytest.fixture
def airports():
    return [WatchlistAirport(icao="EGLL", lat=51.5, lon=-0.5)]


@pytest.fixture
def fake_decode(monkeypatch):
    """Stand in for the GRIB decoder: surface returns the accumulation."""
    def _dispatch(job: str, path: str, lats, lons, **kwargs):
        step_h = int(path.split("_")[-1].removesuffix("h.grib"))
        if job == "decode_ecmwf_surface":
            return [{
                "temperature_2m_k": 288.0,
                "dewpoint_2m_k": 283.0,
                "total_precip_m": _accum_m(step_h),
                "snowfall_m_we": 0.0,
            }], None
        return None, None  # pressure levels: not exercised here

    monkeypatch.setattr("weatherbrief.fetch.grib._dispatch_decode", _dispatch)
    return _dispatch


def _by_step(snapshots):
    return {
        int((s["forecast_hour"] - INIT).total_seconds() // 3600): s
        for s in snapshots
    }


class TestPrecipStepDiff:
    def test_hourly_region_diffs_against_the_previous_hour(
        self, run_files, airports, fake_decode,
    ):
        snaps = _by_step(fetch_ecmwf_grib_snapshots(run_files, airports, None, 6))
        # D+0 06Z = step 6, inside the hourly region: 1h of rain, 1 mm.
        assert snaps[6]["precip_period_h"] == 1
        assert snaps[6]["precipitation_mm"] == pytest.approx(1.0)

    def test_three_hourly_region_diffs_against_the_delivered_step(
        self, run_files, airports, fake_decode,
    ):
        """D+4 06Z = step 102. Previous *delivered* step is 99, not 101."""
        snaps = _by_step(fetch_ecmwf_grib_snapshots(run_files, airports, None, 6))
        assert snaps[102]["precip_period_h"] == 3
        # 3h window at 1 mm/h — NOT the 102 mm accumulated since init.
        assert snaps[102]["precipitation_mm"] == pytest.approx(3.0)

    def test_six_hourly_region_diffs_against_the_delivered_step(
        self, run_files, airports, fake_decode,
    ):
        """D+6 12Z = step 156. Previous delivered step is 150."""
        snaps = _by_step(fetch_ecmwf_grib_snapshots(run_files, airports, None, 6))
        assert snaps[156]["precip_period_h"] == 6
        assert snaps[156]["precipitation_mm"] == pytest.approx(6.0)

    def test_no_sample_is_left_accumulated_since_init(
        self, run_files, airports, fake_decode,
    ):
        """The regression itself: nothing may report the running total.

        At 1 mm/h the accumulation equals the step number, so a row that
        skipped its diff would report precipitation_mm == step.
        """
        snaps = _by_step(fetch_ecmwf_grib_snapshots(run_files, airports, None, 6))
        assert snaps, "expected samples across the horizon"
        for step, snap in snaps.items():
            assert snap["precipitation_mm"] < step, (
                f"step {step}h reports {snap['precipitation_mm']} mm — that is the "
                f"accumulation since init, not a per-period total"
            )
            assert snap["precip_period_h"] in (1, 3, 6)


class TestPerDayGrid:
    def test_far_day_is_sampled_at_three_hours(self, run_files, airports, fake_decode):
        snaps = _by_step(fetch_ecmwf_grib_snapshots(run_files, airports, None, 6))
        d6 = sorted(s for s in snaps if 144 < s <= 168)
        assert d6 == [150, 156, 162]  # 06/12/18Z; 09Z and 15Z are not delivered

    def test_near_days_are_sampled_at_five_hours(self, run_files, airports, fake_decode):
        snaps = _by_step(fetch_ecmwf_grib_snapshots(run_files, airports, None, 6))
        d5 = sorted(s for s in snaps if 120 < s <= 144)
        assert d5 == [126, 129, 132, 135, 138]

    def test_explicit_sample_hours_still_apply_to_every_day(
        self, run_files, airports, fake_decode,
    ):
        """The alternates path passes a flat hour list — that must not change."""
        snaps = _by_step(fetch_ecmwf_grib_snapshots(run_files, airports, [12], 3))
        assert sorted(snaps) == [12, 36, 60, 84]
