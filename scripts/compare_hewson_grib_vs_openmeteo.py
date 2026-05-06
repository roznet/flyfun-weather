"""Spike: compute Hewson diagnostics directly from raw ECMWF GRIB and compare
to the Open-Meteo precompute, for the May 4 LSGS→EGTF flight.

Reads ECMWF a2 GRIB files at /Users/brice/tmp/ecmwf/data, extracts T/RH/U/V
at 925 hPa from the Europe sub-grid (cfgrib dataset whose lat ≈ 35..59.5 N),
derives Td via Magnus, computes θe + Hewson diagnostics on the native ECMWF
0.25° grid (no regrid — frontal grid is a subset), and bilinear-samples at
each route waypoint + mid-leg point. Prints values alongside the matching
Open-Meteo precompute values for direct comparison.

Run from project root:
    python scripts/compare_hewson_grib_vs_openmeteo.py
"""

from __future__ import annotations

import os
import sys
import warnings
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
from dotenv import load_dotenv  # noqa: E402

load_dotenv(ROOT / ".env")

# Silence cfgrib's idx-mismatch warnings (the prod files use checksummed idx
# names that the local cfgrib build doesn't match against the GRIB header).
warnings.filterwarnings("ignore")

from weatherbrief.airports import resolve_waypoints  # noqa: E402
from weatherbrief.frontal.detect import compute_hewson_diagnostics  # noqa: E402
from weatherbrief.frontal.grid import (  # noqa: E402
    compute_theta_e,
    prepare_field,
    wind_to_uv,
)
from weatherbrief.frontal.route_sampling import bilinear_sample  # noqa: E402

ECMWF_DIR = Path(os.environ.get("ECMWF_GRIB_DIR", "/Users/brice/tmp/ecmwf/data"))
OPEN_METEO_SNAPSHOT = ROOT / "data" / "hewson" / "ecmwf" / "2026-05-04T00:00:00Z.npz"

ROUTE_CODES = "LSGS LSGL LFQB LFQA BILGO LFAT EGTF".split()
INTERPOLATE_LEGS = [("LSGL", "LFQB", 2), ("LFQB", "LFQA", 2)]
LEVELS = (925, 850)
HOURS = (7, 8, 9, 10, 11, 12)
INIT_DATE = "2026-05-04"
INIT_HOUR = 0  # use the 00 Z run we rsynced

KT_PER_MS = 1.94384

# §3 thresholds, matching the Open-Meteo comparison script.
THRESHOLDS = {
    "theta_e": None,
    "gradient": (4.0, 8.0),
    "neg_laplacian": (2.0, 4.0),
    "tfp": None,
    "advection": (1.0, 2.0),
    "tendency": (0.5, 1.0),
}


def _color(value: float, thr) -> str:
    if np.isnan(value):
        return f"{'   nan':>7s}"
    label = f"{value:+7.2f}"
    if thr is None:
        return label
    warn, alert = thr
    mag = abs(value)
    if mag >= alert:
        return f"\033[91m{label}\033[0m"
    if mag >= warn:
        return f"\033[93m{label}\033[0m"
    return label


# ---------------------------------------------------------------------------
# GRIB reader
# ---------------------------------------------------------------------------


def _ecmwf_a2_path(valid_hour: int) -> Path:
    """Locate the a2 file for INIT_DATE 00 Z step=valid_hour."""
    init_ts = f"{INIT_DATE.replace('-', '')}T{INIT_HOUR:02d}0000Z"
    valid_ts = f"{INIT_DATE.replace('-', '')}T{valid_hour:02d}0000Z"
    step = valid_hour - INIT_HOUR
    name = (
        f"brg_a2_ifs-ens-cf_od_oper_fc_{init_ts}_{valid_ts}_{step}h"
    )
    p = ECMWF_DIR / name
    if not p.exists():
        raise FileNotFoundError(f"missing ECMWF GRIB: {p}")
    return p


def _open_europe_subgrid(path: Path):
    """Open the GRIB file and return the Europe pressure-level dataset.

    The a2 file has 6 cfgrib sub-grids (single-level z × 3 + pressure-level
    × 3). We want the pressure-level Europe block, identified by lat range
    35..59.5 and the presence of `isobaricInhPa`.
    """
    import cfgrib

    datasets = cfgrib.open_datasets(
        str(path), backend_kwargs={"indexpath": ""},
    )
    for ds in datasets:
        # Need pressure-level data: must have all of t/r/u/v plus a multi-level
        # isobaricInhPa coord (the z-only single-level sub-grids also carry
        # isobaricInhPa as a scalar = 1.0 hPa, those are not what we want).
        needed = {"t", "r", "u", "v"}
        if not needed.issubset(set(ds.data_vars)):
            continue
        levels = np.atleast_1d(ds.isobaricInhPa.values)
        if levels.size < 2:
            continue
        la = ds.latitude.values
        if 34.5 <= la.min() <= 35.5 and la.max() < 60.0:
            return ds
    raise RuntimeError(f"no Europe pressure-level sub-grid in {path}")


def _td_from_t_rh(T_K: np.ndarray, RH_pct: np.ndarray) -> np.ndarray:
    """Dewpoint via Magnus (water-only, T > -45 °C). Returns °C."""
    T_C = T_K - 273.15
    a, b = 17.625, 243.04
    rh = np.clip(RH_pct, 0.1, 100.0)  # avoid log(0)
    gamma = np.log(rh / 100.0) + (a * T_C) / (b + T_C)
    return (b * gamma) / (a - gamma)


def _grib_fields_at_level(ds, level_hPa: int):
    """Return (T_C, Td_C, u_kt, v_kt, lat, lon) at the given pressure level."""
    levels = np.atleast_1d(ds.isobaricInhPa.values)
    matches = np.atleast_1d(levels == float(level_hPa)).nonzero()[0]
    if len(matches) == 0:
        raise ValueError(f"level {level_hPa} hPa not in dataset (have {levels})")
    sl = ds.isel(isobaricInhPa=int(matches[0]))
    T_K = sl["t"].values
    RH = sl["r"].values
    u_ms = sl["u"].values
    v_ms = sl["v"].values
    lat = ds.latitude.values
    lon = ds.longitude.values

    # If lat is descending, flip everything. Hewson code expects ascending.
    if lat[0] > lat[-1]:
        lat = lat[::-1]
        T_K = T_K[::-1, :]
        RH = RH[::-1, :]
        u_ms = u_ms[::-1, :]
        v_ms = v_ms[::-1, :]

    T_C = T_K - 273.15
    Td_C = _td_from_t_rh(T_K, RH)
    u_kt = u_ms * KT_PER_MS
    v_kt = v_ms * KT_PER_MS
    return T_C, Td_C, u_kt, v_kt, lat, lon


def _compute_hewson_at_hour_grib(valid_hour: int, level_hPa: int):
    """Compute Hewson diagnostic grids from raw ECMWF GRIB at one (hour, level)."""
    path = _ecmwf_a2_path(valid_hour)
    ds = _open_europe_subgrid(path)
    T_C, Td_C, u_kt, v_kt, lat, lon = _grib_fields_at_level(ds, level_hPa)

    T_filled = prepare_field(T_C)
    Td_filled = prepare_field(Td_C)
    u_filled = prepare_field(u_kt)
    v_filled = prepare_field(v_kt)
    if any(f is None for f in (T_filled, Td_filled, u_filled, v_filled)):
        raise RuntimeError(f"too much missing data at hour {valid_hour}")

    # wind_to_uv expects (speed_kt, direction_deg). We already have u/v in kt
    # via direct conversion — feed them as-is into compute_hewson_diagnostics
    # which expects km/h. Convert.
    KMH_PER_KT = 1.852
    u_kmh = u_filled * KMH_PER_KT
    v_kmh = v_filled * KMH_PER_KT

    theta_e = compute_theta_e(T_filled, Td_filled, pressure_hPa=float(level_hPa))
    diag = compute_hewson_diagnostics(theta_e, lat, lon, u_kmh, v_kmh)
    return {
        "theta_e": theta_e,
        "gradient": diag["gradient"],
        "neg_laplacian": diag["neg_laplacian"],
        "tfp": diag["tfp"],
        "advection": diag["advection"],
        "lat": lat,
        "lon": lon,
    }


# ---------------------------------------------------------------------------
# Open-Meteo snapshot reader (already-computed grids)
# ---------------------------------------------------------------------------


def _load_openmeteo_snapshot():
    if not OPEN_METEO_SNAPSHOT.exists():
        raise FileNotFoundError(f"missing OM snapshot: {OPEN_METEO_SNAPSHOT}")
    with np.load(OPEN_METEO_SNAPSHOT) as z:
        return {k: z[k] for k in z.files}


def _hour_index(snap: dict, hour: int) -> int:
    target = np.datetime64(f"{INIT_DATE}T{hour:02d}:00:00")
    matches = np.where(snap["valid_times"] == target)[0]
    if len(matches) == 0:
        raise ValueError(f"hour {hour} not in OM snapshot")
    return int(matches[0])


# ---------------------------------------------------------------------------
# Route handling
# ---------------------------------------------------------------------------


def _interpolate_route(route, legs_to_split):
    leg_map = {(a, b): n for a, b, n in legs_to_split}
    expanded = [route[0]]
    for wp_a, wp_b in zip(route[:-1], route[1:]):
        n = leg_map.get((wp_a.icao, wp_b.icao))
        if n:
            for k in range(1, n + 1):
                frac = k / (n + 1)
                class _M: pass
                m = _M()
                m.icao = f".{wp_a.icao}-{wp_b.icao}.{k}/{n+1}"
                m.lat = wp_a.lat + frac * (wp_b.lat - wp_a.lat)
                m.lon = wp_a.lon + frac * (wp_b.lon - wp_a.lon)
                expanded.append(m)
        expanded.append(wp_b)
    return expanded


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> int:
    db = os.environ.get("AIRPORTS_DB") or str(ROOT / "data" / "nav.db")
    print(f"AIRPORTS_DB: {db}")
    print(f"ECMWF_GRIB_DIR: {ECMWF_DIR}")
    print(f"Open-Meteo snapshot: {OPEN_METEO_SNAPSHOT}")
    print(f"Init: {INIT_DATE} {INIT_HOUR:02d} Z   route: {' '.join(ROUTE_CODES)}")
    print()

    waypoints, _ = resolve_waypoints(ROUTE_CODES, db)
    expanded = _interpolate_route(waypoints, INTERPOLATE_LEGS)

    om = _load_openmeteo_snapshot()
    om_lat = om["lat"]
    om_lon = om["lon"]

    cols = ["WP                        ", "  hr",
            "  |∇θe|", "    TFP", " −V·∇θe"]
    header = "  " + " ".join(cols)

    for level in LEVELS:
        print(f"=== {level} hPa — GRIB (raw) vs OM (regrid)  diff = GRIB − OM ===")
        print(header)
        for hour in HOURS:
            try:
                grib = _compute_hewson_at_hour_grib(hour, level)
            except Exception as e:
                print(f"  hour {hour}: GRIB error: {e}")
                continue
            h_om = _hour_index(om, hour)
            for w in expanded:
                # GRIB sample
                g_grad = bilinear_sample(grib["gradient"], grib["lat"], grib["lon"], w.lat, w.lon)
                g_tfp  = bilinear_sample(grib["tfp"],      grib["lat"], grib["lon"], w.lat, w.lon)
                g_adv  = bilinear_sample(grib["advection"], grib["lat"], grib["lon"], w.lat, w.lon)
                # OM sample
                o_grad = bilinear_sample(om[f"gradient_{level}"][h_om], om_lat, om_lon, w.lat, w.lon)
                o_tfp  = bilinear_sample(om[f"tfp_{level}"][h_om],      om_lat, om_lon, w.lat, w.lon)
                o_adv  = bilinear_sample(om[f"advection_{level}"][h_om], om_lat, om_lon, w.lat, w.lon)
                print(
                    f"  {w.icao:<26s} {hour:02d}Z "
                    f"GRIB {_color(g_grad, THRESHOLDS['gradient'])} "
                    f"{_color(g_tfp, THRESHOLDS['tfp'])} "
                    f"{_color(g_adv, THRESHOLDS['advection'])}  | "
                    f"OM   {_color(o_grad, THRESHOLDS['gradient'])} "
                    f"{_color(o_tfp, THRESHOLDS['tfp'])} "
                    f"{_color(o_adv, THRESHOLDS['advection'])}  | "
                    f"Δ {g_grad - o_grad:+5.2f} {g_tfp - o_tfp:+5.2f} {g_adv - o_adv:+5.2f}"
                )
            print()
        print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
