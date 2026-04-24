#!/usr/bin/env python3
"""Standalone Hewson smoke-test plot against an ERA5 GRIB.

Bypasses the calibration-case directory machinery (which is wired for
0.5° Open-Meteo cached JSON). Produces the same 6-panel figure as the
`plot-hewson` CLI, but from ERA5 at native 0.25°.

Usage:
    python scripts/plot_era5_hewson.py \\
        --grib data/era5/era5_hewson_smoke_2023-11-02.grib \\
        --time 2023-11-02T12:00 \\
        --level 850 \\
        --output data/era5/hewson_ciaran_850_12Z.png
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import numpy as np

from weatherbrief.era5 import load_era5_fields
from weatherbrief.frontal.detect import compute_hewson_diagnostics


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n", 1)[0])
    parser.add_argument("--grib", required=True, help="Path to ERA5 GRIB file")
    parser.add_argument("--time", required=True, help="ISO timestamp to plot (e.g. 2023-11-02T12:00)")
    parser.add_argument("--level", type=int, default=850, help="Pressure level hPa (default 850)")
    parser.add_argument("--output", help="Output PNG path")
    args = parser.parse_args()

    # Load
    print(f"Loading ERA5: {args.grib}")
    fields = load_era5_fields(args.grib, args.time, level_hPa=args.level)
    print(f"  Grid: {len(fields['lat'])} lat × {len(fields['lon'])} lon at level {fields['level']} hPa")
    print(f"  Time: {fields['time']}")

    # Report sanity stats
    print(f"  T {args.level}:    min={fields['T850'].min():.1f}  mean={fields['T850'].mean():.1f}  max={fields['T850'].max():.1f}  °C")
    print(f"  Td {args.level}:   min={fields['Td850'].min():.1f}  mean={fields['Td850'].mean():.1f}  max={fields['Td850'].max():.1f}  °C")
    print(f"  θe {args.level}:   min={fields['theta_e'].min():.1f}  mean={fields['theta_e'].mean():.1f}  max={fields['theta_e'].max():.1f}  K")
    wind_mag = np.sqrt(fields['u850']**2 + fields['v850']**2)
    print(f"  Wind:   max={wind_mag.max():.0f} km/h")

    # Compute Hewson diagnostics
    print("Computing Hewson diagnostics...")
    diag = compute_hewson_diagnostics(
        fields["theta_e"], fields["lat"], fields["lon"],
        u=fields["u850"], v=fields["v850"],
        terrain_mask=None,  # skip terrain mask for ERA5 smoke — keep simple
    )

    print(f"  |∇θe|:         min={diag['gradient'].min():.2f}  mean={diag['gradient'].mean():.2f}  max={diag['gradient'].max():.2f}  K/100km")
    print(f"  −∇²θe:         min={diag['neg_laplacian'].min():.2f}  max={diag['neg_laplacian'].max():.2f}  K/(100km)²")
    print(f"  TFP:           min={diag['tfp'].min():.2f}  max={diag['tfp'].max():.2f}")
    print(f"  −V·∇θe:        min={diag['advection'].min():.2f}  max={diag['advection'].max():.2f}  K/h")

    # Plot
    output = args.output or f"data/era5/hewson_{Path(args.grib).stem}_L{args.level}.png"
    _plot_hewson_simple(fields, diag, args.level, output)
    print(f"Plot saved: {output}")
    return 0


def _plot_hewson_simple(fields, diag, level_hPa, output):
    """6-panel figure, ERA5 smoke-test version (no zone boxes, no case integration)."""
    import cartopy.crs as ccrs
    import cartopy.feature as cfeature
    import matplotlib.pyplot as plt

    lat = fields["lat"]
    lon = fields["lon"]
    lon_grid, lat_grid = np.meshgrid(lon, lat)
    extent = [float(lon.min()), float(lon.max()), float(lat.min()), float(lat.max())]

    fig, axes = plt.subplots(
        2, 3, figsize=(22, 12),
        subplot_kw={"projection": ccrs.PlateCarree()},
    )

    def _decorate(ax, title):
        ax.add_feature(cfeature.COASTLINE, linewidth=0.7)
        ax.add_feature(cfeature.BORDERS, linewidth=0.4, linestyle=":")
        ax.set_extent(extent, crs=ccrs.PlateCarree())
        ax.set_title(title, fontweight="bold")

    # Panel 1 — θe field
    ax = axes[0, 0]
    te_min = float(np.nanpercentile(diag["field_smooth"], 5))
    te_max = float(np.nanpercentile(diag["field_smooth"], 95))
    im = ax.pcolormesh(
        lon_grid, lat_grid, diag["field_smooth"],
        cmap="RdYlBu_r", vmin=te_min, vmax=te_max,
        transform=ccrs.PlateCarree(), alpha=0.85,
    )
    plt.colorbar(im, ax=ax, shrink=0.7, label="θe (K)")
    _decorate(ax, f"1. θe ({level_hPa} hPa)")

    # Panel 2 — |∇θe|
    ax = axes[0, 1]
    im = ax.pcolormesh(
        lon_grid, lat_grid, diag["gradient"],
        cmap="YlOrRd", vmin=0, vmax=10,
        transform=ccrs.PlateCarree(), alpha=0.85,
    )
    plt.colorbar(im, ax=ax, shrink=0.7, label="K / 100km")
    _decorate(ax, "2. |∇θe| — gradient magnitude")

    # Panel 3 — −∇²θe
    ax = axes[0, 2]
    lap = diag["neg_laplacian"]
    vlim = float(np.nanpercentile(np.abs(lap), 98))
    im = ax.pcolormesh(
        lon_grid, lat_grid, lap,
        cmap="RdBu_r", vmin=-vlim, vmax=vlim,
        transform=ccrs.PlateCarree(), alpha=0.85,
    )
    plt.colorbar(im, ax=ax, shrink=0.7, label="K/(100km)²")
    ax.contour(lon_grid, lat_grid, lap, levels=[0], colors="black", linewidths=0.5, transform=ccrs.PlateCarree())
    _decorate(ax, "3. −∇²θe (Hewson concavity)")

    # Panel 4 — TFP
    ax = axes[1, 0]
    tfp = diag["tfp"]
    vlim_tfp = float(np.nanpercentile(np.abs(tfp), 98))
    im = ax.pcolormesh(
        lon_grid, lat_grid, tfp,
        cmap="PuOr_r", vmin=-vlim_tfp, vmax=vlim_tfp,
        transform=ccrs.PlateCarree(), alpha=0.85,
    )
    plt.colorbar(im, ax=ax, shrink=0.7, label="K/(100km)²")
    ax.contour(lon_grid, lat_grid, tfp, levels=[0], colors="black", linewidths=0.7, linestyles="dashed", transform=ccrs.PlateCarree())
    _decorate(ax, "4. TFP(θe) — zero-crossings = Hewson locator")

    # Panel 5 — advection
    ax = axes[1, 1]
    adv = diag["advection"]
    vlim_adv = float(np.nanpercentile(np.abs(adv), 98))
    im = ax.pcolormesh(
        lon_grid, lat_grid, adv,
        cmap="RdBu_r", vmin=-vlim_adv, vmax=vlim_adv,
        transform=ccrs.PlateCarree(), alpha=0.85,
    )
    plt.colorbar(im, ax=ax, shrink=0.7, label="K/h  (red=warm, blue=cold)")
    _decorate(ax, "5. −V · ∇θe (advection)")

    # Panel 6 — tendency not available for single-timestamp ERA5
    ax = axes[1, 2]
    ax.text(
        0.5, 0.5,
        "∂θe/∂t requires ±1h\n(not computed in smoke test)",
        ha="center", va="center", transform=ax.transAxes, fontsize=14,
    )
    _decorate(ax, "6. ∂θe/∂t (not computed)")

    fig.suptitle(
        f"ERA5 Hewson diagnostics — {fields['time']} — {level_hPa} hPa — 0.25°",
        fontsize=15, fontweight="bold",
    )
    plt.tight_layout(rect=[0, 0.02, 1, 0.96])
    fig.savefig(output, dpi=130, bbox_inches="tight")


if __name__ == "__main__":
    sys.exit(main())
