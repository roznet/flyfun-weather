"""Profile analyze_sounding with a realistic 28-level GFS-like profile.

Run: ./venv/bin/python scripts/profile_analyze.py [N_CALLS]
"""
from __future__ import annotations

import cProfile
import pstats
import sys
import time

from weatherbrief.fetch.variables import EXTENDED_PRESSURE_LEVELS
from weatherbrief.models import HourlyForecast, PressureLevelData
from weatherbrief.analysis.sounding import analyze_sounding


def make_levels() -> list[PressureLevelData]:
    levels = []
    for p in EXTENDED_PRESSURE_LEVELS:
        # Rough standard-atmosphere-ish profile with a moist layer 925-700
        alt_m = 44330.0 * (1.0 - (p / 1013.25) ** 0.1903)
        t = 12.0 - 6.5 * alt_m / 1000.0
        rh = 85.0 if 700 <= p <= 925 else max(20.0, 60.0 - alt_m / 400.0)
        dew = t - (100.0 - rh) / 5.0
        levels.append(PressureLevelData(
            pressure_hpa=p,
            temperature_c=round(t, 1),
            relative_humidity_pct=round(rh, 1),
            dewpoint_c=round(dew, 1),
            wind_speed_kt=10.0 + alt_m / 300.0,
            wind_direction_deg=(240 + p) % 360,
            geopotential_height_m=round(alt_m, 1),
            vertical_velocity_pa_s=-0.2 if 600 <= p <= 850 else 0.05,
            cloud_liquid_water_kg_kg=2e-4 if 700 <= p <= 925 else 0.0,
            ice_mixing_ratio_kg_kg=1e-5 if 400 <= p <= 600 else 0.0,
            cloud_area_fraction_pct=80.0 if 700 <= p <= 925 else 10.0,
        ))
    return levels


def make_hourly(levels) -> HourlyForecast:
    from datetime import datetime, timezone
    return HourlyForecast(
        time=datetime(2026, 6, 12, 12, tzinfo=timezone.utc),
        temperature_2m_c=14.0,
        precipitation_mm=0.2,
        cape_j_kg=350.0,
        freezing_level_m=2500.0,
        pressure_levels=levels,
    )


def main() -> None:
    n = int(sys.argv[1]) if len(sys.argv) > 1 else 100
    levels = make_levels()
    hourly = make_hourly(levels)

    # Warm up (imports, pint registry, metpy lazy init)
    res = analyze_sounding(levels, hourly, icing_severity_enhance=True)
    assert res is not None, "analyze_sounding returned None"

    t0 = time.perf_counter()
    for _ in range(n):
        analyze_sounding(levels, hourly, icing_severity_enhance=True)
    wall = time.perf_counter() - t0
    print(f"{n} calls: {wall:.2f}s total, {wall / n * 1000:.1f} ms/call")
    print(f"-> a 20-point x 5-model route (~100 calls) ~= {wall / n * 100:.1f}s in analyze_sounding\n")

    prof = cProfile.Profile()
    prof.enable()
    for _ in range(n):
        analyze_sounding(levels, hourly, icing_severity_enhance=True)
    prof.disable()
    stats = pstats.Stats(prof)
    stats.sort_stats("cumulative").print_stats(35)


if __name__ == "__main__":
    main()
