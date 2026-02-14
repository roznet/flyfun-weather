"""Skew-T log-P diagram generation using MetPy.

Produces Skew-T log-P diagrams with optional overlays from sounding analysis:
CAPE/CIN shading, hodograph inset, derived indices panel, cloud/icing/inversion
bands, altitude labels, and cruise altitude marker.
"""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

import matplotlib

matplotlib.use("agg")
import matplotlib.pyplot as plt  # noqa: E402
import metpy.calc as mpcalc  # noqa: E402
import numpy as np  # noqa: E402
from matplotlib.transforms import blended_transform_factory  # noqa: E402
from metpy.plots import Hodograph, SkewT  # noqa: E402
from metpy.units import units  # noqa: E402

if TYPE_CHECKING:
    from matplotlib.axes import Axes
    from matplotlib.figure import Figure

from weatherbrief.models import ForecastSnapshot, HourlyForecast  # noqa: E402
from weatherbrief.models.analysis import SoundingAnalysis  # noqa: E402

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

# Standard-atmosphere altitude labels for the right edge of the Skew-T
_ALTITUDE_LABELS: list[tuple[int, str]] = [
    (850, "5,000 ft"),
    (700, "10,000 ft"),
    (500, "FL180"),
    (400, "FL235"),
    (300, "FL300"),
]

# Colour palette — kept as simple hex strings
_C = dict(
    temp="#d62728",
    dewpt="#2ca02c",
    parcel="#1f1f1f",
    barb="#555555",
    cloud="#b0b0b0",
    icing="#4a90d9",
    inversion="#e07020",
    cruise="#9467bd",
    lcl="#2ca02c",
    lfc="#ff7f0e",
    el="#d62728",
)


def _pressure_to_altitude_ft(p_hpa: float) -> float:
    """Standard-atmosphere pressure → altitude (ft)."""
    return 145366.45 * (1.0 - (p_hpa / 1013.25) ** 0.190284)


# ---------------------------------------------------------------------------
# Overlay helpers — each called inside try/except in the main function
# ---------------------------------------------------------------------------


def _draw_altitude_labels(ax: Axes, p_bottom: float, p_top: float) -> None:
    """Altitude labels on the right edge of the Skew-T."""
    trans = blended_transform_factory(ax.transAxes, ax.transData)
    for p_hpa, label in _ALTITUDE_LABELS:
        if p_top <= p_hpa <= p_bottom:
            ax.text(
                1.02, p_hpa, label, transform=trans,
                fontsize=8, va="center", ha="left", color="#777777",
            )
            ax.plot(
                [0.995, 1.01], [p_hpa, p_hpa], transform=trans,
                color="#aaaaaa", linewidth=0.5, clip_on=False,
            )


def _draw_cruise_line(ax: Axes, cruise_altitude_ft: int, p_bottom: float) -> None:
    """Horizontal dashed line at planned cruise altitude."""
    from weatherbrief.models.analysis import altitude_to_pressure_hpa

    cruise_p = altitude_to_pressure_hpa(cruise_altitude_ft)
    if cruise_p > p_bottom:
        return
    ax.axhline(y=cruise_p, color=_C["cruise"], linewidth=1.5,
               linestyle="--", alpha=0.7, zorder=3)
    trans = blended_transform_factory(ax.transAxes, ax.transData)
    fl = cruise_altitude_ft // 100
    ax.text(
        0.02, cruise_p, f"Cruise FL{fl:03d}", transform=trans,
        fontsize=9, va="bottom", ha="left",
        color=_C["cruise"], fontweight="bold", alpha=0.8,
    )


def _draw_cloud_layers(ax: Axes, analysis: SoundingAnalysis) -> None:
    """Gray-shaded bands for detected cloud layers."""
    alpha_map = {"sct": 0.06, "bkn": 0.10, "ovc": 0.16}
    trans = blended_transform_factory(ax.transAxes, ax.transData)
    for cloud in analysis.cloud_layers:
        base_p = cloud.base_pressure_hpa
        top_p = cloud.top_pressure_hpa
        if base_p is None or top_p is None:
            continue
        ax.axhspan(top_p, base_p,
                    alpha=alpha_map.get(cloud.coverage.value, 0.08),
                    color=_C["cloud"], zorder=0)
        mid_p = (base_p + top_p) / 2
        ax.text(0.97, mid_p, cloud.coverage.value.upper(), transform=trans,
                fontsize=7, va="center", ha="right", color="#999999", alpha=0.8)


def _draw_icing_zones(ax: Axes, analysis: SoundingAnalysis) -> bool:
    """Blue-shaded bands for icing zones.  Returns True if any were drawn."""
    drawn = False
    risk_alpha = {"light": 0.06, "moderate": 0.12, "severe": 0.20}
    risk_short = {"light": "ICE-L", "moderate": "ICE-M", "severe": "ICE-S"}
    trans = blended_transform_factory(ax.transAxes, ax.transData)
    for zone in analysis.icing_zones:
        if zone.risk.value == "none":
            continue
        base_p = zone.base_pressure_hpa
        top_p = zone.top_pressure_hpa
        if base_p is None or top_p is None:
            continue
        ax.axhspan(top_p, base_p,
                    alpha=risk_alpha.get(zone.risk.value, 0.06),
                    color=_C["icing"], zorder=0)
        mid_p = (base_p + top_p) / 2
        ax.text(0.03, mid_p, risk_short.get(zone.risk.value, ""),
                transform=trans, fontsize=7, va="center", ha="left",
                color=_C["icing"], fontweight="bold", alpha=0.7)
        drawn = True
    return drawn


def _draw_inversion_layers(ax: Axes, analysis: SoundingAnalysis) -> None:
    """Warm-coloured bands for temperature inversions."""
    for inv in analysis.inversion_layers:
        base_p = inv.base_pressure_hpa
        top_p = inv.top_pressure_hpa
        if base_p is None or top_p is None:
            continue
        alpha = min(0.20, 0.04 + inv.strength_c * 0.03)
        ax.axhspan(top_p, base_p, alpha=alpha, color=_C["inversion"], zorder=0)


def _draw_hodograph(
    fig: Figure, levels: list, u_wind: np.ndarray, v_wind: np.ndarray,
) -> None:
    """Hodograph inset in the top-right area."""
    heights_ft = np.array([
        pl.geopotential_height_m * 3.28084
        if pl.geopotential_height_m is not None
        else _pressure_to_altitude_ft(pl.pressure_hpa)
        for pl in levels
    ])

    max_comp = max(np.max(np.abs(u_wind.magnitude)),
                   np.max(np.abs(v_wind.magnitude)))
    comp_range = max(40.0, float(np.ceil(max_comp * 1.3 / 10) * 10))

    hodo_ax = fig.add_axes((0.70, 0.58, 0.25, 0.32))
    h = Hodograph(hodo_ax, component_range=comp_range)
    h.add_grid(increment=20, ls="-", lw=1, alpha=0.35)
    h.add_grid(increment=10, ls="--", lw=0.5, alpha=0.12)

    h.ax.set_xticklabels([])
    h.ax.set_yticklabels([])
    h.ax.set_xticks([])
    h.ax.set_yticks([])
    h.ax.set_box_aspect(1)

    for spd in range(20, int(comp_range), 20):
        h.ax.annotate(
            f"{spd}", (spd, 0), xytext=(0, 2),
            textcoords="offset pixels", fontsize=7,
            alpha=0.3, ha="center", clip_on=True,
        )

    h.plot_colormapped(u_wind, v_wind, c=heights_ft, cmap="cool", linewidth=2.5)

    u_m = u_wind.magnitude
    v_m = v_wind.magnitude
    h.ax.plot(u_m[0], v_m[0], "o", color="#2ca02c", markersize=4, zorder=5)
    h.ax.plot(u_m[-1], v_m[-1], "^", color="#d62728", markersize=4, zorder=5)

    h.ax.set_title("Hodograph (kt)", fontsize=9, fontweight="bold", pad=4)


def _draw_indices_panel(fig: Figure, analysis: SoundingAnalysis) -> None:
    """Derived-indices text panel in the bottom-right."""
    idx = analysis.indices
    if idx is None:
        return

    # Background rectangle — pushed right, more compact
    fig.patches.extend([plt.Rectangle(
        (0.66, 0.05), 0.325, 0.46,
        edgecolor="#dddddd", facecolor="white", linewidth=0.5, alpha=0.95,
        transform=fig.transFigure, figure=fig,
    )])
    fig.text(0.823, 0.49, "Sounding Indices", fontsize=9.5,
             fontweight="bold", ha="center", va="bottom", color="#333333")

    def _fmt(val: float | None, fmt_str: str = ".0f", suffix: str = "") -> str:
        return "—" if val is None else f"{val:{fmt_str}}{suffix}"

    def _cape_color(val: float | None) -> str:
        if val is None or val < 100:
            return "#888888"
        return "#e89a3c" if val < 500 else "#d62728"

    def _li_color(val: float | None) -> str:
        if val is None:
            return "#888888"
        if val > 2:
            return "#2ca02c"
        return "#e89a3c" if val > -2 else "#d62728"

    y0, dy = 0.47, 0.030
    lx1, vx1 = 0.67, 0.82   # left column
    lx2, vx2 = 0.835, 0.975  # right column
    fs = 8.5

    rows: list[tuple[str, str, str, str, str, str]] = [
        ("SBCAPE:", _fmt(idx.cape_surface_jkg, suffix=" J/kg"),
         _cape_color(idx.cape_surface_jkg),
         "Freezing:", _fmt(idx.freezing_level_ft, suffix=" ft"), "#4a90d9"),

        ("SBCIN:", _fmt(idx.cin_surface_jkg, suffix=" J/kg"), "#4a90d9",
         "\u221210\u00b0C:", _fmt(idx.minus10c_level_ft, suffix=" ft"), "#4a90d9"),

        ("MLCAPE:", _fmt(idx.cape_mixed_layer_jkg, suffix=" J/kg"),
         _cape_color(idx.cape_mixed_layer_jkg),
         "\u221220\u00b0C:", _fmt(idx.minus20c_level_ft, suffix=" ft"), "#4a90d9"),

        ("Lifted Idx:", _fmt(idx.lifted_index, ".1f"), _li_color(idx.lifted_index),
         "LCL:", _fmt(idx.lcl_altitude_ft, suffix=" ft"), "#888888"),

        ("K-Index:", _fmt(idx.k_index, ".0f"), "#888888",
         "LFC:", _fmt(idx.lfc_altitude_ft, suffix=" ft"), "#888888"),

        ("Total-T:", _fmt(idx.total_totals, ".0f"), "#888888",
         "EL:", _fmt(idx.el_altitude_ft, suffix=" ft"), "#888888"),

        ("PW:", _fmt(idx.precipitable_water_mm, ".1f", " mm"), "#888888",
         "Shear 0\u20136:", _fmt(idx.bulk_shear_0_6km_kt, ".0f", " kt"), "#888888"),

        ("Shear 0\u20131:", _fmt(idx.bulk_shear_0_1km_kt, ".0f", " kt"), "#888888",
         "Showalter:", _fmt(idx.showalter_index, ".1f"), "#888888"),
    ]

    for i, (l1, v1, c1, l2, v2, c2) in enumerate(rows):
        y = y0 - i * dy
        fig.text(lx1, y, l1, fontsize=fs, fontweight="bold", ha="left", color="#444444")
        fig.text(vx1, y, v1, fontsize=fs, ha="right", color=c1, fontweight="bold")
        fig.text(lx2, y, l2, fontsize=fs, fontweight="bold", ha="left", color="#444444")
        fig.text(vx2, y, v2, fontsize=fs, ha="right", color=c2, fontweight="bold")


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def generate_skewt(
    forecast: HourlyForecast,
    label: str,
    model_name: str,
    output_path: Path,
    *,
    analysis: SoundingAnalysis | None = None,
    cruise_altitude_ft: int | None = None,
) -> Path:
    """Generate a Skew-T diagram for a single location/model/time.

    Args:
        forecast: HourlyForecast with pressure level data.
        label: Location label for the title (e.g. ICAO code).
        model_name: Model name for title.
        output_path: Where to save the PNG.
        analysis: Optional sounding analysis for overlays (clouds, icing,
            inversions, indices panel, hodograph).
        cruise_altitude_ft: Optional planned cruise altitude for a reference line.

    Returns:
        Path to the saved PNG.
    """
    # --- Extract pressure-level data ---
    levels = [pl for pl in forecast.pressure_levels if pl.temperature_c is not None]
    if len(levels) < 3:
        logger.warning("Insufficient pressure levels for Skew-T at %s", label)
        raise ValueError(f"Need at least 3 levels with temperature, got {len(levels)}")

    levels.sort(key=lambda pl: pl.pressure_hpa, reverse=True)

    pressure = np.array([pl.pressure_hpa for pl in levels]) * units.hPa
    temperature = np.array([pl.temperature_c for pl in levels]) * units.degC

    has_dewpoint = all(pl.dewpoint_c is not None for pl in levels)
    dewpoint = (
        np.array([pl.dewpoint_c for pl in levels]) * units.degC
        if has_dewpoint else None
    )

    has_wind = all(
        pl.wind_speed_kt is not None and pl.wind_direction_deg is not None
        for pl in levels
    )
    u_wind = v_wind = None
    if has_wind:
        speed = np.array([pl.wind_speed_kt for pl in levels]) * units.knot
        direction = np.array([pl.wind_direction_deg for pl in levels]) * units.degree
        u_wind, v_wind = mpcalc.wind_components(speed, direction)

    # --- Figure layout ---
    has_panels = analysis is not None
    if has_panels:
        fig = plt.figure(figsize=(13, 10))
        skew = SkewT(fig, rotation=45, rect=(0.05, 0.05, 0.57, 0.90))
    else:
        fig = plt.figure(figsize=(9, 9))
        skew = SkewT(fig, rotation=45)

    # --- Temperature & dewpoint ---
    skew.plot(pressure, temperature, color=_C["temp"], linewidth=2.5, label="Temperature")
    if dewpoint is not None:
        skew.plot(pressure, dewpoint, color=_C["dewpt"], linewidth=2.5, label="Dewpoint")

    # --- Wind barbs ---
    if u_wind is not None and v_wind is not None:
        skew.plot_barbs(pressure, u_wind, v_wind, color=_C["barb"])

    # --- Parcel profile + CAPE/CIN shading + level markers ---
    if dewpoint is not None:
        try:
            prof = mpcalc.parcel_profile(pressure, temperature[0], dewpoint[0])
            skew.plot(pressure, prof, color=_C["parcel"], linewidth=1.5,
                      linestyle="--", label="Parcel")

            # CAPE / CIN shading
            try:
                skew.shade_cape(pressure, temperature, prof,
                                alpha=0.15, label="CAPE")
            except Exception:
                logger.debug("shade_cape failed for %s", label)
            try:
                skew.shade_cin(pressure, temperature, prof, dewpoint,
                               alpha=0.12, label="CIN")
            except Exception:
                logger.debug("shade_cin failed for %s", label)

            # LCL marker (green circle)
            lcl_p, lcl_t = mpcalc.lcl(pressure[0], temperature[0], dewpoint[0])
            skew.plot(lcl_p, lcl_t, "o", color=_C["lcl"], markersize=8,
                      markeredgecolor="white", markeredgewidth=1,
                      label="LCL", zorder=5)

            # LFC marker (orange square)
            try:
                lfc_p, lfc_t = mpcalc.lfc(pressure, temperature, dewpoint)
                if not np.isnan(lfc_p.magnitude):
                    skew.plot(lfc_p, lfc_t, "s", color=_C["lfc"], markersize=8,
                              markeredgecolor="white", markeredgewidth=1,
                              label="LFC", zorder=5)
            except Exception:
                pass

            # EL marker (red diamond)
            try:
                el_p, el_t = mpcalc.el(pressure, temperature, dewpoint)
                if not np.isnan(el_p.magnitude):
                    skew.plot(el_p, el_t, "D", color=_C["el"], markersize=8,
                              markeredgecolor="white", markeredgewidth=1,
                              label="EL", zorder=5)
            except Exception:
                pass

        except Exception:
            logger.debug("Could not compute parcel profile for %s", label)

    # --- Analysis overlays (cloud, icing, inversions) ---
    icing_drawn = False
    if analysis is not None:
        try:
            icing_drawn = _draw_icing_zones(skew.ax, analysis)
        except Exception:
            logger.debug("Could not draw icing zones for %s", label)
        try:
            _draw_cloud_layers(skew.ax, analysis)
        except Exception:
            logger.debug("Could not draw cloud layers for %s", label)
        try:
            _draw_inversion_layers(skew.ax, analysis)
        except Exception:
            logger.debug("Could not draw inversion layers for %s", label)

    # Fallback generic icing band when no analysis zones drawn
    if not icing_drawn:
        skew.ax.axvspan(-20, 0, alpha=0.06, color="blue", zorder=0)
        skew.ax.text(
            -10, 400, "Icing\nZone", ha="center", va="center",
            fontsize=8, color="blue", alpha=0.4,
        )

    # --- Reference lines ---
    skew.ax.axvline(0, linestyle="--", color="blue", alpha=0.25, linewidth=0.8)
    skew.plot_dry_adiabats(linewidth=0.5, alpha=0.25)
    skew.plot_moist_adiabats(linewidth=0.5, alpha=0.25)
    skew.plot_mixing_lines(linewidth=0.5, alpha=0.25)

    # --- Altitude labels (right edge) ---
    try:
        _draw_altitude_labels(skew.ax, p_bottom=1050, p_top=250)
    except Exception:
        logger.debug("Could not draw altitude labels for %s", label)

    # --- Cruise altitude ---
    if cruise_altitude_ft is not None:
        try:
            _draw_cruise_line(skew.ax, cruise_altitude_ft, p_bottom=1050)
        except Exception:
            logger.debug("Could not draw cruise line for %s", label)

    # --- Axis configuration ---
    skew.ax.set_ylim(1050, 250)
    skew.ax.set_xlim(-60, 40)
    skew.ax.set_xlabel("Temperature (\u00b0C)")
    skew.ax.set_ylabel("Pressure (hPa)")
    skew.ax.legend(loc="upper left", fontsize=8, framealpha=0.8)

    # --- Hodograph inset ---
    if has_panels and u_wind is not None and v_wind is not None:
        try:
            _draw_hodograph(fig, levels, u_wind, v_wind)
        except Exception:
            logger.debug("Could not draw hodograph for %s", label)

    # --- Indices panel ---
    if has_panels:
        try:
            _draw_indices_panel(fig, analysis)  # type: ignore[arg-type]
        except Exception:
            logger.debug("Could not draw indices panel for %s", label)

    # --- Title ---
    time_str = forecast.time.strftime("%Y-%m-%d %H:%MZ")
    fig.suptitle(
        f"{label}  \u00b7  {model_name.upper()}  \u00b7  {time_str}",
        fontsize=14, fontweight="bold", y=0.98,
    )

    # --- Save ---
    output_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_path, dpi=150, bbox_inches="tight", facecolor="white")
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

    # Build a lookup for sounding analysis by (icao, model)
    analysis_lookup: dict[tuple[str, str], SoundingAnalysis] = {}
    cruise_ft: int | None = None
    if snapshot.route:
        cruise_ft = snapshot.route.cruise_altitude_ft
    for wa in snapshot.analyses:
        for model_key, sa in wa.sounding.items():
            analysis_lookup[(wa.waypoint.icao, model_key)] = sa

    for wf in snapshot.forecasts:
        hourly = wf.at_time(target_time)
        if not hourly or not hourly.pressure_levels:
            continue

        filename = f"{wf.waypoint.icao}_{wf.model.value}.png"
        out_path = output_dir / filename

        sa = analysis_lookup.get((wf.waypoint.icao, wf.model.value))

        try:
            generate_skewt(
                hourly, wf.waypoint.icao, wf.model.value, out_path,
                analysis=sa, cruise_altitude_ft=cruise_ft,
            )
            paths.append(out_path)
        except Exception:
            logger.warning(
                "Skew-T failed for %s/%s", wf.waypoint.icao, wf.model.value,
                exc_info=True,
            )

    return paths
