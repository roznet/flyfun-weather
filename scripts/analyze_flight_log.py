"""Retrospective Hewson analysis on a real flight log.

Workflow:
  CSV flight log → for each flight in the ERA5 window:
    - load the monthly ERA5 GRIB at the 6-hourly analysis nearest brakes-off
    - compute Hewson diagnostics on the European grid
    - sample 12 points along the great-circle-approximated route
    - aggregate (max gradient, max |adv|, TFP zero-crossings, etc.)
    - apply the §3 advisory triggers
    - write one markdown block per flight, sorted by "excitement"

The output is intentionally rough — a tagging tool for the pilot to
cross-reference with their own memory before we commit thresholds in
code (Phase C advisory evaluators).
"""

from __future__ import annotations

import argparse
import csv
import logging
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np

from weatherbrief.airports import resolve_waypoints
from weatherbrief.era5.loader import load_era5_fields
from weatherbrief.frontal.detect import compute_hewson_diagnostics
from weatherbrief.frontal.grid import (
    build_terrain_mask,
    compute_theta_e,
    fill_terrain,
)
from weatherbrief.frontal.route_sampling import bilinear_sample

logger = logging.getLogger("analyze_flight_log")

REPO_ROOT = Path(__file__).resolve().parents[1]
GRIB_DIR_DEFAULT = REPO_ROOT / "data" / "era5" / "hewson"
AIRPORTS_DB_DEFAULT = REPO_ROOT / "data" / "nav.db"
TERRAIN_CACHE = REPO_ROOT / "data" / "era5" / "terrain_mask.npz"


def _prime_terrain_mask(flights: list["Flight"], era5_dir: Path) -> np.ndarray | None:
    """Peek at the first available GRIB to get the grid, then build/load the mask."""
    for flight in flights:
        grib = era5_dir / f"era5_hewson_{flight.date.year}_{flight.date.month:02d}.grib"
        if grib.exists():
            f = load_era5_fields(grib, nearest_synoptic(flight.etd_utc).replace(tzinfo=None))
            return get_terrain_mask(f["lat"], f["lon"])
    logger.warning("No GRIB available to prime terrain mask — proceeding without")
    return None


def get_terrain_mask(lat: np.ndarray, lon: np.ndarray) -> np.ndarray:
    """Build-or-load the terrain mask for this lat/lon grid. Cached on disk.

    SRTM point lookups for ~19k cells take ~30s; one mask is shared by all
    ERA5 flights on the same European 0.25° grid.
    """
    if TERRAIN_CACHE.exists():
        with np.load(TERRAIN_CACHE) as npz:
            cached_lat = npz["lat"]
            cached_lon = npz["lon"]
            if (cached_lat.shape == lat.shape and cached_lon.shape == lon.shape
                    and np.allclose(cached_lat, lat) and np.allclose(cached_lon, lon)):
                return npz["mask"]
            logger.info("Terrain cache grid mismatch — rebuilding")

    logger.info("Building terrain mask (one-off SRTM lookup)...")
    mask = build_terrain_mask(lat, lon)
    TERRAIN_CACHE.parent.mkdir(parents=True, exist_ok=True)
    np.savez(TERRAIN_CACHE, mask=mask, lat=lat, lon=lon)
    return mask


def load_masked_fields(grib_path: Path, analysis_time: datetime, terrain_mask: np.ndarray | None) -> dict:
    """Load ERA5 fields and apply terrain fill + θe re-derivation (matches build_case_from_era5)."""
    f = load_era5_fields(grib_path, analysis_time.replace(tzinfo=None))
    if terrain_mask is not None:
        f["T850"] = fill_terrain(f["T850"], terrain_mask)
        f["Td850"] = fill_terrain(f["Td850"], terrain_mask)
        f["u850"] = fill_terrain(f["u850"], terrain_mask)
        f["v850"] = fill_terrain(f["v850"], terrain_mask)
        f["theta_e"] = compute_theta_e(f["T850"], f["Td850"])
    return f

# Thresholds from designs/future/hewson-fields-aviation-advisories.md §3
GRAD_WARN = 4.0           # K/100km
GRAD_TRIGGER = 6.0        # K/100km
NEG_LAP_TRIGGER = 2.0     # K/(100km)²
ADV_TRIGGER = 1.0         # K/h
ADV_RAPID = 2.0           # K/h
TEND_TRIGGER = 0.5        # K/h
THETA_E_CONVECTIVE = 315.0  # K


@dataclass
class Flight:
    date: date
    etd_utc: datetime            # brakes-off UTC
    eta_utc: datetime | None     # brakes-on UTC (may be None)
    dep_icao: str
    arr_icao: str


# ---------------------------------------------------------------------------
# CSV parsing


def parse_csv(path: Path) -> tuple[list[Flight], dict[str, int]]:
    """Parse a Flight-log CSV. Returns (valid_flights, skip_counts)."""
    valid: list[Flight] = []
    skips = {"missing_date_time": 0, "same_airport": 0, "bad_time": 0}

    with path.open() as fh:
        reader = csv.reader(fh)
        next(reader, None)  # header
        for row in reader:
            if not row or not row[0].strip():
                continue
            try:
                d = datetime.strptime(row[0].strip(), "%d/%m/%Y").date()
            except ValueError:
                skips["missing_date_time"] += 1
                continue

            if len(row) < 5:
                skips["missing_date_time"] += 1
                continue

            dep = row[1].strip().upper()
            arr = row[2].strip().upper()
            off = row[3].strip().lower().rstrip("z")
            on = row[4].strip().lower().rstrip("z")

            if not dep or not arr or not off:
                skips["missing_date_time"] += 1
                continue
            if dep == arr:
                skips["same_airport"] += 1
                continue

            try:
                etd = _parse_hhmm(d, off)
            except ValueError:
                skips["bad_time"] += 1
                continue

            eta: datetime | None = None
            if on:
                try:
                    eta = _parse_hhmm(d, on)
                    if eta < etd:
                        eta += timedelta(days=1)
                except ValueError:
                    eta = None

            valid.append(Flight(d, etd, eta, dep, arr))
    return valid, skips


def _parse_hhmm(d: date, hhmm: str) -> datetime:
    hh, mm = hhmm.split(":")
    return datetime(d.year, d.month, d.day, int(hh), int(mm), tzinfo=timezone.utc)


# ---------------------------------------------------------------------------
# Time + route helpers


def nearest_synoptic(dt: datetime) -> datetime:
    """Snap to nearest 6-hourly ERA5 analysis (00/06/12/18 UTC)."""
    hour = dt.hour + dt.minute / 60.0
    snap = round(hour / 6) * 6
    base = dt.replace(hour=0, minute=0, second=0, microsecond=0)
    if snap == 24:
        base += timedelta(days=1)
        snap = 0
    return base.replace(hour=int(snap))


def build_route_points(
    dep: tuple[float, float], arr: tuple[float, float], n_mid: int = 10,
) -> list[tuple[float, float]]:
    """Linear interpolation in lat/lon — good enough for 200 nm legs within EU."""
    lat_d, lon_d = dep
    lat_a, lon_a = arr
    return [
        (lat_d + t * (lat_a - lat_d), lon_d + t * (lon_a - lon_d))
        for t in np.linspace(0, 1, n_mid + 2)
    ]


# ---------------------------------------------------------------------------
# Hewson compute + sample


def load_diag(grib_path: Path, analysis_time: datetime, terrain_mask: np.ndarray | None = None) -> dict:
    f = load_masked_fields(grib_path, analysis_time, terrain_mask)
    diag = compute_hewson_diagnostics(
        f["theta_e"], f["lat"], f["lon"],
        u=f["u850"], v=f["v850"],
        terrain_mask=terrain_mask,
    )
    diag["theta_e"] = f["theta_e"]
    diag["lat"] = f["lat"]
    diag["lon"] = f["lon"]
    return diag


def sample_route(diag: dict, points: list[tuple[float, float]]) -> list[dict]:
    out = []
    for lat, lon in points:
        out.append({
            "theta_e":       bilinear_sample(diag["theta_e"], diag["lat"], diag["lon"], lat, lon),
            "gradient":      bilinear_sample(diag["gradient"], diag["lat"], diag["lon"], lat, lon),
            "tfp":           bilinear_sample(diag["tfp"], diag["lat"], diag["lon"], lat, lon),
            "neg_laplacian": bilinear_sample(diag["neg_laplacian"], diag["lat"], diag["lon"], lat, lon),
            "advection":     bilinear_sample(diag["advection"], diag["lat"], diag["lon"], lat, lon),
        })
    return out


def aggregate(samples: list[dict]) -> dict:
    theta_e = np.array([s["theta_e"] for s in samples])
    grad = np.array([s["gradient"] for s in samples])
    adv = np.array([s["advection"] for s in samples])
    tfp = np.array([s["tfp"] for s in samples])
    neg_lap = np.array([s["neg_laplacian"] for s in samples])

    signs = np.sign(tfp)
    crossings = int(np.sum(signs[:-1] * signs[1:] < 0))

    adv_max_idx = int(np.nanargmax(np.abs(adv)))

    return {
        "theta_e_min": float(np.nanmin(theta_e)),
        "theta_e_max": float(np.nanmax(theta_e)),
        "theta_e_delta": float(np.nanmax(theta_e) - np.nanmin(theta_e)),
        "grad_max": float(np.nanmax(grad)),
        "grad_mean": float(np.nanmean(grad)),
        "adv_max_abs": float(np.abs(adv)[adv_max_idx]),
        "adv_max_signed": float(adv[adv_max_idx]),
        "neg_lap_max_abs": float(np.nanmax(np.abs(neg_lap))),
        "tfp_zero_crossings": crossings,
    }


def tendency_at(grib_path: Path, analysis: datetime, lat: float, lon: float, terrain_mask: np.ndarray | None = None) -> float:
    """∂θe/∂t at one point using centered 12-h difference (±6h ERA5 step)."""
    try:
        f_prev = load_masked_fields(grib_path, analysis - timedelta(hours=6), terrain_mask)
        f_next = load_masked_fields(grib_path, analysis + timedelta(hours=6), terrain_mask)
    except ValueError:
        return float("nan")
    tp = bilinear_sample(f_prev["theta_e"], f_prev["lat"], f_prev["lon"], lat, lon)
    tn = bilinear_sample(f_next["theta_e"], f_next["lat"], f_next["lon"], lat, lon)
    return (tn - tp) / 12.0


# ---------------------------------------------------------------------------
# Advisory logic


def fire_advisories(agg: dict, tendency: float) -> list[str]:
    out: list[str] = []
    if agg["grad_max"] > GRAD_TRIGGER:
        out.append("air-mass-transition")
    if agg["neg_lap_max_abs"] > NEG_LAP_TRIGGER:
        out.append("sharp-edge")
    if agg["adv_max_signed"] > ADV_RAPID:
        out.append("deteriorating-warm-adv-rapid")
    elif agg["adv_max_signed"] > ADV_TRIGGER:
        out.append("deteriorating-warm-adv")
    if agg["adv_max_signed"] < -ADV_RAPID:
        out.append("improving-cold-adv-rapid")
    elif agg["adv_max_signed"] < -ADV_TRIGGER:
        out.append("improving-cold-adv")
    if agg["theta_e_max"] > THETA_E_CONVECTIVE:
        out.append("convective-outlook")
    if not np.isnan(tendency) and abs(tendency) > TEND_TRIGGER:
        out.append(f"destination-tendency-{'rising' if tendency > 0 else 'falling'}")
    return out


def physical_read(agg: dict, tendency: float, advisories: list[str]) -> str:
    parts: list[str] = []
    if agg["theta_e_delta"] > 10:
        parts.append(f"Δθe {agg['theta_e_delta']:.1f} K along route — substantial air-mass contrast")
    if "deteriorating-warm-adv-rapid" in advisories:
        parts.append("rapid warm advection — frontal passage within 1–2h, ceilings dropping")
    elif "deteriorating-warm-adv" in advisories:
        parts.append("warm advection — stratiform cloud, ceilings lower over time, icing risk rising")
    if "improving-cold-adv-rapid" in advisories:
        parts.append("rapid cold advection — post-frontal, clearing behind, gusty")
    elif "improving-cold-adv" in advisories:
        parts.append("cold advection — clearing, showery, mechanical turbulence")
    if "sharp-edge" in advisories or agg["tfp_zero_crossings"] >= 2:
        parts.append(f"{agg['tfp_zero_crossings']} TFP zero-crossing(s) on route — narrow transition band")
    if "convective-outlook" in advisories:
        parts.append(f"high θe ({agg['theta_e_max']:.0f} K) — convective potential if lapse rate cooperates")
    if agg["theta_e_min"] < 280:
        parts.append("cold polar/arctic air — expect icing in cloud, possible showers")
    if not parts:
        mid = (agg["theta_e_min"] + agg["theta_e_max"]) / 2
        parts.append(f"benign air mass (θe ≈ {mid:.0f} K), no significant gradients or advection")
    return "; ".join(parts)


def excitement_score(agg: dict, tendency: float) -> float:
    """Rough 0–10ish score — max excites, for sorting report."""
    return (
        max(0.0, agg["grad_max"] - GRAD_WARN) / 2.0
        + min(agg["adv_max_abs"], 4.0)
        + min(abs(tendency) if not np.isnan(tendency) else 0.0, 2.0)
        + min(agg["neg_lap_max_abs"], 4.0) / 2.0
        + 0.5 * agg["tfp_zero_crossings"]
    )


# ---------------------------------------------------------------------------
# One-flight analysis (shared with analyze_cancellations.py)


def analyze_one_flight(
    flight: Flight,
    era5_dir: Path,
    airports_db: Path,
    terrain_mask: np.ndarray | None = None,
) -> dict | None:
    """Run the full Hewson pipeline for one flight. Returns None if data missing.

    Output dict:
      flight, analysis, agg, tendency, advisories, physical_read, score
    """
    grib_path = era5_dir / f"era5_hewson_{flight.date.year}_{flight.date.month:02d}.grib"
    if not grib_path.exists():
        logger.warning("No GRIB for %s — skipping", flight.date)
        return None

    try:
        waypoints, _ = resolve_waypoints(
            [flight.dep_icao, flight.arr_icao], str(airports_db),
        )
    except KeyError as e:
        logger.warning("Unresolved ICAO %s→%s: %s", flight.dep_icao, flight.arr_icao, e)
        return None

    dep_ll = (waypoints[0].lat, waypoints[0].lon)
    arr_ll = (waypoints[-1].lat, waypoints[-1].lon)

    analysis = nearest_synoptic(flight.etd_utc)
    try:
        diag = load_diag(grib_path, analysis, terrain_mask)
    except Exception as exc:
        logger.warning("load_diag failed for %s: %s", flight.date, exc)
        return None

    route_pts = build_route_points(dep_ll, arr_ll, n_mid=10)
    samples = sample_route(diag, route_pts)
    agg = aggregate(samples)
    dep_tend = tendency_at(grib_path, analysis, dep_ll[0], dep_ll[1], terrain_mask)

    advisories = fire_advisories(agg, dep_tend)
    return {
        "flight": flight,
        "analysis": analysis,
        "agg": agg,
        "tendency": dep_tend,
        "advisories": advisories,
        "physical_read": physical_read(agg, dep_tend, advisories),
        "score": excitement_score(agg, dep_tend),
    }


# ---------------------------------------------------------------------------
# Main


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--csv", required=True)
    p.add_argument("--output", default="flight_hewson_retrospective.md")
    p.add_argument("--era5-dir", default=str(GRIB_DIR_DEFAULT))
    p.add_argument("--airports-db", default=str(AIRPORTS_DB_DEFAULT))
    p.add_argument("--limit", type=int, help="Max flights (for quick iteration)")
    p.add_argument("--from-date", help="YYYY-MM-DD inclusive (default: 2025-02-01)")
    p.add_argument("--to-date", help="YYYY-MM-DD inclusive (default: 2026-02-28)")
    args = p.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")

    from_date = date.fromisoformat(args.from_date) if args.from_date else date(2025, 2, 1)
    to_date = date.fromisoformat(args.to_date) if args.to_date else date(2026, 2, 28)

    flights, skips = parse_csv(Path(args.csv))
    flights = [f for f in flights if from_date <= f.date <= to_date]
    flights.sort(key=lambda f: f.etd_utc)
    logger.info(
        "Parsed: %d usable flights in [%s, %s]; skipped %s",
        len(flights), from_date, to_date, skips,
    )
    if args.limit:
        flights = flights[: args.limit]

    era5_dir = Path(args.era5_dir)
    airports_db = Path(args.airports_db)

    # Prime the terrain mask from the first flight's GRIB (all ERA5 cycles
    # share the same European 0.25° grid, so one mask serves all analyses).
    terrain_mask = _prime_terrain_mask(flights, era5_dir)

    results: list[dict] = []
    for flight in flights:
        r = analyze_one_flight(flight, era5_dir, airports_db, terrain_mask)
        if r is None:
            continue
        results.append(r)
        if len(results) % 10 == 0:
            logger.info("...%d flights processed", len(results))

    results.sort(key=lambda r: -r["score"])
    _write_report(Path(args.output), results, from_date, to_date)
    print(f"Wrote {args.output} ({len(results)} flights)")


def _write_report(out: Path, results: list[dict], from_date: date, to_date: date) -> None:
    n_fired = sum(1 for r in results if r["advisories"])
    with out.open("w") as fh:
        fh.write("# Flight Log × Hewson Retrospective\n\n")
        fh.write(
            f"Period: {from_date} → {to_date}. "
            f"{len(results)} flights analyzed; {n_fired} trigger at least one advisory.\n\n"
        )
        fh.write(
            "Thresholds (from `designs/future/hewson-fields-aviation-advisories.md` §3): "
            f"|∇θe|>{GRAD_TRIGGER} K/100km, |adv|>{ADV_TRIGGER} K/h "
            f"(rapid >{ADV_RAPID}), |−∇²θe|>{NEG_LAP_TRIGGER} K/(100km)², "
            f"|∂θe/∂t|>{TEND_TRIGGER} K/h, θe>{THETA_E_CONVECTIVE} K for convection.\n\n"
        )
        fh.write(
            "_Sorted by excitement score descending. For each flight, check the "
            "box that best matches your own memory of that flight's weather._\n\n"
        )
        fh.write("---\n\n")

        for i, r in enumerate(results, 1):
            fl: Flight = r["flight"]
            fh.write(
                f"## #{i} — {fl.date} {fl.dep_icao} → {fl.arr_icao} "
                f"[{fl.etd_utc.strftime('%H:%Mz')}"
            )
            if fl.eta_utc:
                fh.write(f" → {fl.eta_utc.strftime('%H:%Mz')}")
            fh.write("]\n")
            fh.write(
                f"*excitement {r['score']:.2f}  |  analysis {r['analysis'].strftime('%Hz')}*\n\n"
            )
            agg = r["agg"]
            fh.write(
                f"- θe range: {agg['theta_e_min']:.1f} → {agg['theta_e_max']:.1f} K "
                f"(Δ{agg['theta_e_delta']:.1f})\n"
            )
            fh.write(
                f"- |∇θe| max {agg['grad_max']:.2f} K/100km  (mean {agg['grad_mean']:.2f})\n"
            )
            fh.write(f"- −V·∇θe max {agg['adv_max_signed']:+.2f} K/h\n")
            tend = r["tendency"]
            tend_str = f"{tend:+.2f}" if not np.isnan(tend) else "NaN"
            fh.write(f"- ∂θe/∂t at dep: {tend_str} K/h\n")
            fh.write(
                f"- |−∇²θe| max {agg['neg_lap_max_abs']:.2f};  "
                f"TFP zero-crossings on route: {agg['tfp_zero_crossings']}\n"
            )
            adv_list = ", ".join(r["advisories"]) or "(none)"
            fh.write(f"- **Advisories fired**: {adv_list}\n")
            fh.write(f"- **Physical read**: {r['physical_read']}\n")
            fh.write(
                "- **Your memory?** `[ ] matches  [ ] false alarm  "
                "[ ] don't remember  [ ] different (describe)`\n\n"
            )


if __name__ == "__main__":
    main()
