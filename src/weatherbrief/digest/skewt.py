"""Skew-T log-P diagram generation using MetPy."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import matplotlib
matplotlib.use("agg")
import matplotlib.pyplot as plt
import metpy.calc as mpcalc
import numpy as np
from metpy.plots import SkewT
from metpy.units import units

from weatherbrief.models import ForecastSnapshot, HourlyForecast, Waypoint

logger = logging.getLogger(__name__)


def generate_skewt(
    forecast: HourlyForecast,
    waypoint: Waypoint,
    model_name: str,
    output_path: Path,
) -> Path:
    """Generate a Skew-T diagram for a single waypoint/model/time.

    Args:
        forecast: HourlyForecast with pressure level data.
        waypoint: Waypoint for labeling.
        model_name: Model name for title.
        output_path: Where to save the PNG.

    Returns:
        Path to the saved PNG.
    """
    # Extract pressure level data — filter to levels with temp data
    levels = [pl for pl in forecast.pressure_levels if pl.temperature_c is not None]
    if len(levels) < 3:
        logger.warning("Insufficient pressure levels for Skew-T at %s", waypoint.icao)
        raise ValueError(f"Need at least 3 levels with temperature, got {len(levels)}")

    # Sort by pressure (high to low = surface to altitude)
    levels.sort(key=lambda pl: pl.pressure_hpa, reverse=True)

    pressure = np.array([pl.pressure_hpa for pl in levels]) * units.hPa
    temperature = np.array([pl.temperature_c for pl in levels]) * units.degC

    # Dewpoint — use available data or skip
    has_dewpoint = all(pl.dewpoint_c is not None for pl in levels)
    dewpoint = None
    if has_dewpoint:
        dewpoint = np.array([pl.dewpoint_c for pl in levels]) * units.degC

    # Wind components
    has_wind = all(
        pl.wind_speed_kt is not None and pl.wind_direction_deg is not None
        for pl in levels
    )
    u_wind = v_wind = None
    if has_wind:
        speed = np.array([pl.wind_speed_kt for pl in levels]) * units.knot
        direction = np.array([pl.wind_direction_deg for pl in levels]) * units.degree
        u_wind, v_wind = mpcalc.wind_components(speed, direction)

    # Create the Skew-T
    fig = plt.figure(figsize=(9, 9))
    skew = SkewT(fig, rotation=45)

    # Temperature
    skew.plot(pressure, temperature, "r", linewidth=2, label="Temperature")

    # Dewpoint
    if dewpoint is not None:
        skew.plot(pressure, dewpoint, "g", linewidth=2, label="Dewpoint")

    # Wind barbs
    if u_wind is not None and v_wind is not None:
        skew.plot_barbs(pressure, u_wind, v_wind)

    # Parcel profile from surface
    if dewpoint is not None:
        try:
            parcel_profile = mpcalc.parcel_profile(pressure, temperature[0], dewpoint[0])
            skew.plot(pressure, parcel_profile, "k--", linewidth=1, label="Parcel")

            # LCL
            lcl_pressure, lcl_temperature = mpcalc.lcl(
                pressure[0], temperature[0], dewpoint[0]
            )
            skew.plot(lcl_pressure, lcl_temperature, "ko", markersize=8, label="LCL")
        except Exception:
            logger.debug("Could not compute parcel profile for %s", waypoint.icao)

    # Icing band annotation (0 to -20 C)
    skew.ax.axvspan(-20, 0, alpha=0.08, color="blue", zorder=0)
    skew.ax.text(
        -10, 400, "Icing\nZone", ha="center", va="center",
        fontsize=8, color="blue", alpha=0.5,
    )

    # Reference lines
    skew.plot_dry_adiabats(linewidth=0.5, alpha=0.3)
    skew.plot_moist_adiabats(linewidth=0.5, alpha=0.3)
    skew.plot_mixing_lines(linewidth=0.5, alpha=0.3)

    # Labels and title
    time_str = forecast.time.strftime("%Y-%m-%d %H:%MZ")
    skew.ax.set_title(f"{waypoint.icao} — {model_name} — {time_str}")
    skew.ax.set_xlabel("Temperature (°C)")
    skew.ax.set_ylabel("Pressure (hPa)")
    skew.ax.legend(loc="upper left", fontsize=8)

    # Set axis limits
    skew.ax.set_ylim(1050, 250)
    skew.ax.set_xlim(-60, 40)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches="tight")
    plt.close(fig)

    return output_path


def generate_all_skewts(
    snapshot: ForecastSnapshot,
    target_time: datetime,
    output_dir: Path,
) -> list[Path]:
    """Generate Skew-T plots for all waypoints and models in a snapshot.

    Args:
        snapshot: Complete forecast snapshot.
        target_time: Target time to extract the closest forecast hour.
        output_dir: Base directory for output PNGs.

    Returns:
        List of paths to generated PNG files.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []

    for wf in snapshot.forecasts:
        hourly = wf.at_time(target_time)
        if not hourly or not hourly.pressure_levels:
            continue

        filename = f"{wf.waypoint.icao}_{wf.model.value}.png"
        out_path = output_dir / filename

        try:
            generate_skewt(hourly, wf.waypoint, wf.model.value, out_path)
            paths.append(out_path)
        except Exception:
            logger.warning(
                "Skew-T failed for %s/%s", wf.waypoint.icao, wf.model.value,
                exc_info=True,
            )

    return paths
