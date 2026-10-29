"""Met Office UKV 2 km for the ``ukmo`` briefing slot, read from AWS (#937).

The ``ukmo`` slot used to come from Open-Meteo's ``ukmo_seamless``, mostly the
10 km global model, with no cloud per level and no condensate. It is now UKV
only, read straight from the Met Office's open-data bucket
(``met-office-atmospheric-model-data``, eu-west-2, anonymous, CC BY-SA 4.0),
and only where UKV itself covers the flight: a route with any point outside
the UKV box, or a window past the newest run's horizon, gets no ``ukmo`` slot
(meteorology-decisions §72). There is no Open-Meteo fallback.

**Layout.** ``uk-deterministic-2km/<run>/<VALID>-PT<hhhh>H<mm>M-<param>.nc``,
one NetCDF4 file per parameter per step; the filename prefix is the VALID
time, not the run. Lambert azimuthal equal-area grid (lat_0 54.9, lon_0 -2.5,
GRS80), 1042 x 970 points at 2 km, chunked ``(1, 128, 128)``. Files are read
by range, only the tiles under the route (:mod:`.nc_range`), and the inflated
tiles are cached on disk per run.

**Runs.** Hourly. 03Z and 15Z reach +120 h; 00, 06, 09, 12, 18 and 21Z reach
+54 h; the others +12 h. Steps are hourly to +54 h, then 3-hourly. A run's
first file appears ~5 h 15 min after its init and the last 30-60 min later,
so a run counts as published :data:`UKV_PUBLISH_DELAY` after init, and
discovery still lists the last step before using it.

**Vertical velocity** is not in UKV. As Open-Meteo's ``ukmo_seamless`` does,
it comes from the MO global 10 km model (``global-deterministic-10km``,
``upward_air_velocity`` in m/s on pressure levels), the newest global run not
newer than the UKV run, interpolated in time, and converted to omega with the
UKV temperature. A missing global step leaves omega None; it never fails the
slot.
"""

from __future__ import annotations

import logging
import math
import os
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from functools import lru_cache
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np
import requests

from weatherbrief.fetch.grib.http_retry import get_with_retry
from weatherbrief.fetch.grib.nc_range import (
    RemoteFileMissing,
    TransferStats,
    read_chunks,
)

if TYPE_CHECKING:
    from weatherbrief.models import RoutePoint

logger = logging.getLogger(__name__)

BUCKET_URL = "https://met-office-atmospheric-model-data.s3.eu-west-2.amazonaws.com"
UKV_PREFIX = "uk-deterministic-2km"
GLOBAL_PREFIX = "global-deterministic-10km"

# Freshness registry keys (fetch/freshness/registry.py).
UKV_SOURCE_KEY = "ukmo:ukv"
GLOBAL_SOURCE_KEY = "ukmo_global:metoffice"
# Cache-dir model key (.cache/grib/ukv/<run>/).
CACHE_MODEL = "ukv"

# --- Grid (checked against the files' coordinate vectors on 2026-10-25) ----
UKV_PROJ = (
    "+proj=laea +lat_0=54.9 +lon_0=-2.5 +x_0=0 +y_0=0 "
    "+a=6378137 +rf=298.257222101 +units=m +no_defs"
)
UKV_X0_M = -1_158_000.0
UKV_Y0_M = -1_036_000.0
UKV_DX_M = 2_000.0
UKV_NX = 1042
UKV_NY = 970
UKV_TILE = 128

# A route point must sit at least this far inside the grid edge, so the
# bilinear stencil (and a little timing/position slack) never leaves it.
UKV_DOMAIN_MARGIN_M = 10_000.0

# MO global 10 km: regular lat/lon, cell centres from -89.953125 / -179.929688.
GLOBAL_LAT0 = -90.0 + 180.0 / 1920 / 2
GLOBAL_DLAT = 180.0 / 1920
GLOBAL_LON0 = -180.0 + 360.0 / 2560 / 2
GLOBAL_DLON = 360.0 / 2560
GLOBAL_NLAT = 1920
GLOBAL_NLON = 2560
GLOBAL_TILE = 128

# --- Run schedule -------------------------------------------------------------
UKV_LONG_CYCLES = frozenset({3, 15})          # +120 h
UKV_MID_CYCLES = frozenset({0, 6, 9, 12, 18, 21})  # +54 h
UKV_HOURLY_TO_H = 54
UKV_COARSE_STEP_H = 3
# First file ~run+5:15, run complete 30-60 min later (12Z: 17:30-18:00Z;
# 15Z, 120 h: 20:15-21:16Z, measured 2026-10-24).
UKV_PUBLISH_DELAY = timedelta(hours=6, minutes=30)

GLOBAL_CYCLES = (0, 6, 12, 18)
GLOBAL_PUBLISH_DELAY = timedelta(hours=6)
GLOBAL_HOURLY_TO_H = 54
GLOBAL_COARSE_STEP_H = 3

# Pressure levels the slot carries: the ones Open-Meteo served for ukmo, all
# of them UKV levels.
UKV_PRESSURE_LEVELS: tuple[int, ...] = (
    1000, 975, 950, 925, 900, 850, 800, 750, 700, 650,
    600, 550, 500, 450, 400, 350, 300, 250, 200, 150,
)

# Pressure-level parameter → raw key for decode._convert_raw_sounding.
_PL_PARAMS: dict[str, str] = {
    "temperature_on_pressure_levels": "raw_temperature_k",
    "relative_humidity_on_pressure_levels": "rh_fraction",
    "geopotential_height_on_pressure_levels": "geopotential_height_m",
    "wind_speed_on_pressure_levels": "wind_speed_m_s",
    "wind_direction_on_pressure_levels": "wind_from_deg",
}
_CLOUD_HL_PARAM = "cloud_amount_on_height_levels"
_OROGRAPHY_PARAM = "height_of_orography"

# Single-level parameters read at every step; ``{p}`` is the accumulation or
# max period in hours (1 to +54 h, 3 after). False = optional (a failed read
# loses only that field), True = required for the step to count.
_SURFACE_PARAMS: dict[str, tuple[str, bool]] = {
    "temperature_at_screen_level": ("t2m_k", True),
    "temperature_of_dew_point_at_screen_level": ("td2m_k", False),
    "relative_humidity_at_screen_level": ("rh2m_fraction", False),
    "pressure_at_mean_sea_level": ("mslp_pa", True),
    "pressure_at_surface": ("psfc_pa", False),
    "wind_speed_at_10m": ("ws10_m_s", True),
    "wind_direction_at_10m": ("wd10_deg", True),
    "wind_gust_at_10m_max-PT{p:02d}H": ("gust_m_s", False),
    "visibility_at_screen_level": ("vis_m", False),
    "precipitation_accumulation-PT{p:02d}H": ("precip_m", True),
    "rainfall_accumulation-PT{p:02d}H": ("rain_m", False),
    "snowfall_accumulation-PT{p:02d}H": ("snow_m_we", False),
    "cloud_amount_of_total_cloud": ("total_cloud_fraction", True),
    "cloud_amount_of_low_cloud": ("low_cloud_fraction", False),
    "cloud_amount_of_medium_cloud": ("mid_cloud_fraction", False),
    "cloud_amount_of_high_cloud": ("high_cloud_fraction", False),
    "height_AGL_at_cloud_base_where_cloud_cover_2p5_oktas": ("cloud_base_agl_m", False),
    "height_AGL_at_freezing_level": ("freezing_level_agl_m", False),
    "CAPE_most_unstable_below_500hPa": ("mucape_jkg", False),
}
# Speed/direction pairs blended as vectors (u, v per corner), never as
# scalars: corners at 350° and 010° must give 000°, not 180°.
_PL_WIND = ("wind_speed_on_pressure_levels", "wind_direction_on_pressure_levels")
_SFC_WIND = ("wind_speed_at_10m", "wind_direction_at_10m")
# Fields whose NaN means "none" (no cloud base), sampled nearest-neighbour.
_NEAREST_KEYS = frozenset({"cloud_base_agl_m"})

GLOBAL_W_PARAM = "wind_vertical_velocity_on_pressure_levels"

_FETCH_WORKERS = 8
_CHUNK_WORKERS = 24

_M_TO_FT = 3.28084
_KT_PER_MS = 1.94384
SNOW_CM_PER_MM_WATER = 0.7  # Open-Meteo's snowfall convention (decode.py)
# A level is "broken or more" at 50 % cloud fraction, the app's BKN
# threshold (_NWP_COVERAGE_THRESHOLDS, clouds.py). The ceiling is the lowest
# UKV height level reaching it.
CEILING_FRACTION = 0.5


def ukv_session() -> requests.Session:
    """A session whose pool matches the parallel range reads."""
    from requests.adapters import HTTPAdapter

    session = requests.Session()
    adapter = HTTPAdapter(pool_connections=4, pool_maxsize=_CHUNK_WORKERS + _FETCH_WORKERS)
    session.mount("https://", adapter)
    return session


# ---------------------------------------------------------------------------
# Domain
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _transformer():
    from pyproj import Transformer

    return Transformer.from_crs("EPSG:4326", UKV_PROJ, always_xy=True)


def project_points(lats: list[float], lons: list[float]) -> tuple[np.ndarray, np.ndarray]:
    """Route points → UKV grid coordinates in metres (x, y)."""
    x, y = _transformer().transform(np.asarray(lons, float), np.asarray(lats, float))
    return np.asarray(x, float), np.asarray(y, float)


def route_in_ukv_domain(route_points: list["RoutePoint"]) -> bool:
    """True when every route point is inside the UKV box, with a 10 km inset.

    All-or-nothing, like ``route_in_icon_eu_domain``. Replaces the old
    route-touches-GB gate on the ``ukmo`` endpoint (owner decision on #937).
    """
    if not route_points:
        return False
    x, y = project_points([p.lat for p in route_points], [p.lon for p in route_points])
    x_lo = UKV_X0_M + UKV_DOMAIN_MARGIN_M
    x_hi = UKV_X0_M + (UKV_NX - 1) * UKV_DX_M - UKV_DOMAIN_MARGIN_M
    y_lo = UKV_Y0_M + UKV_DOMAIN_MARGIN_M
    y_hi = UKV_Y0_M + (UKV_NY - 1) * UKV_DX_M - UKV_DOMAIN_MARGIN_M
    return bool(np.all((x >= x_lo) & (x <= x_hi) & (y >= y_lo) & (y <= y_hi)))


# ---------------------------------------------------------------------------
# Schedule (network-free)
# ---------------------------------------------------------------------------


def ukv_run_horizon_h(cycle: int) -> int:
    """Forecast length of the UKV run initialised at hour *cycle*."""
    if cycle in UKV_LONG_CYCLES:
        return 120
    if cycle in UKV_MID_CYCLES:
        return 54
    return 12


def _floor_hour(dt: datetime) -> datetime:
    dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    return dt.replace(minute=0, second=0, microsecond=0)


def published_ukv_runs(as_of_time: datetime | None = None, *, hours_back: int = 30) -> list[datetime]:
    """Runs expected complete at *as_of_time* (default now), newest first."""
    ref = as_of_time or datetime.now(timezone.utc)
    ref = ref if ref.tzinfo else ref.replace(tzinfo=timezone.utc)
    newest = _floor_hour(ref - UKV_PUBLISH_DELAY)
    return [newest - timedelta(hours=h) for h in range(hours_back + 1)]


def ukv_horizon_end(as_of_time: datetime | None = None) -> datetime:
    """Furthest valid time a published UKV run reaches (network-free).

    Mirrors ``icon_eu_horizon_end`` for the fetch-stage horizon skip.
    """
    return max(
        run + timedelta(hours=ukv_run_horizon_h(run.hour))
        for run in published_ukv_runs(as_of_time)
    )


def native_steps(run: datetime, hours: list[datetime]) -> list[int]:
    """UKV steps (hours after *run*) needed to cover *hours*, sorted.

    Hourly to +54 h; past it the two 3-hourly steps bracketing the hour.
    """
    steps: set[int] = set()
    for t in hours:
        step = int((t - run).total_seconds() // 3600)
        if step <= UKV_HOURLY_TO_H:
            steps.add(step)
            continue
        lo = UKV_HOURLY_TO_H + ((step - UKV_HOURLY_TO_H) // UKV_COARSE_STEP_H) * UKV_COARSE_STEP_H
        steps.add(lo)
        if lo != step:
            steps.add(lo + UKV_COARSE_STEP_H)
    return sorted(steps)


def candidate_runs(
    hours: list[datetime], as_of_time: datetime | None = None,
) -> list[datetime]:
    """Published runs whose horizon covers every hour, newest first.

    A run must start before the first hour (step 0 carries no accumulations)
    and reach the last one. The newest such run wins, even a +12 h one.
    """
    if not hours:
        return []
    first, last = min(hours), max(hours)
    out = []
    for run in published_ukv_runs(as_of_time):
        if run >= first:
            continue
        if run + timedelta(hours=ukv_run_horizon_h(run.hour)) < last:
            continue
        steps = native_steps(run, hours)
        if steps and steps[-1] > ukv_run_horizon_h(run.hour):
            continue
        out.append(run)
    return out


def ukv_window_out_of_range(
    departure_time: datetime,
    flight_duration_hours: float,
    as_of_time: datetime | None = None,
) -> bool:
    """True when no published UKV run covers the flight window."""
    from weatherbrief.fetch.grib.primary import required_hours

    return not candidate_runs(
        required_hours(departure_time, flight_duration_hours), as_of_time,
    )


def global_candidate_runs(ukv_run: datetime, as_of_time: datetime | None = None) -> list[datetime]:
    """MO global runs not newer than *ukv_run*, published, newest first (two)."""
    ref = as_of_time or datetime.now(timezone.utc)
    ref = ref if ref.tzinfo else ref.replace(tzinfo=timezone.utc)
    out = []
    t = ukv_run.replace(hour=(ukv_run.hour // 6) * 6)
    while len(out) < 2:
        if t + GLOBAL_PUBLISH_DELAY <= ref:
            out.append(t)
        t -= timedelta(hours=6)
    return out


def _global_steps_for(run: datetime, t: datetime) -> list[tuple[int, float]]:
    """``[(step, weight)]`` giving the global field at *t* by linear interpolation."""
    step_f = (t - run).total_seconds() / 3600.0
    if step_f < 0:
        return []
    if step_f <= GLOBAL_HOURLY_TO_H:
        lo = math.floor(step_f)
        cadence = 1
    else:
        lo = GLOBAL_HOURLY_TO_H + math.floor(
            (step_f - GLOBAL_HOURLY_TO_H) / GLOBAL_COARSE_STEP_H,
        ) * GLOBAL_COARSE_STEP_H
        cadence = GLOBAL_COARSE_STEP_H
    frac = (step_f - lo) / cadence
    if frac < 1e-6:
        return [(int(lo), 1.0)]
    return [(int(lo), 1.0 - frac), (int(lo) + cadence, frac)]


# ---------------------------------------------------------------------------
# Keys and URLs
# ---------------------------------------------------------------------------


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y%m%dT%H%MZ")


def file_name(run: datetime, step: int, param: str) -> str:
    valid = run + timedelta(hours=step)
    return f"{_iso(valid)}-PT{step:04d}H00M-{param}.nc"


def file_url(prefix: str, run: datetime, step: int, param: str) -> str:
    return f"{BUCKET_URL}/{prefix}/{_iso(run)}/{file_name(run, step, param)}"


def _period_h(step: int) -> int:
    return 1 if step <= UKV_HOURLY_TO_H else UKV_COARSE_STEP_H


def surface_params_for(step: int) -> dict[str, tuple[str, bool]]:
    """The single-level parameters (period filled in) for *step*.

    Step 0 has no accumulation or period-max files; :func:`candidate_runs`
    never asks for it, but the list stays truthful.
    """
    p = _period_h(step)
    return {
        name.format(p=p): spec for name, spec in _SURFACE_PARAMS.items()
        if step > 0 or "{p" not in name
    }


def required_params_for(step: int) -> list[str]:
    """Every file a step needs before the run counts as complete."""
    req = [n for n, (_k, required) in surface_params_for(step).items() if required]
    return [*_PL_PARAMS, _CLOUD_HL_PARAM, _OROGRAPHY_PARAM, *req]


# ---------------------------------------------------------------------------
# Run discovery (network)
# ---------------------------------------------------------------------------


def _list_objects(
    session: requests.Session, prefix: str, stats: TransferStats | None,
) -> dict[str, str]:
    """``{basename: LastModified}`` for every object under *prefix*, all pages."""
    import xml.etree.ElementTree as ET
    from urllib.parse import quote

    ns = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
    out: dict[str, str] = {}
    token = None
    for _ in range(20):
        url = f"{BUCKET_URL}/?list-type=2&prefix={quote(prefix)}"
        if token:
            url += f"&continuation-token={quote(token)}"
        resp = get_with_retry(session, url, timeout=30.0)
        resp.raise_for_status()
        if stats is not None:
            stats.add(len(resp.content))
        root = ET.fromstring(resp.content)
        for item in root.findall("s3:Contents", ns):
            key = item.findtext("s3:Key", default="", namespaces=ns)
            if key:
                out[key.rsplit("/", 1)[-1]] = item.findtext(
                    "s3:LastModified", default="", namespaces=ns,
                )
        token = root.findtext("s3:NextContinuationToken", default=None, namespaces=ns)
        if not token:
            break
    return out


def step_published_at(
    session: requests.Session, run: datetime, step: int,
    stats: TransferStats | None = None,
) -> datetime | None:
    """When the last file of *step* landed, or None while any is missing.

    Uploads go step by step and take 30-60 min, so the last step a reader
    needs is the one still missing while a run is landing.
    """
    valid = run + timedelta(hours=step)
    prefix = f"{UKV_PREFIX}/{_iso(run)}/{_iso(valid)}-PT{step:04d}H"
    present = _list_objects(session, prefix, stats)
    names = [file_name(run, step, p) for p in required_params_for(step)]
    missing = [n for n in names if n not in present]
    if missing:
        logger.info(
            "UKV run %s incomplete at +%dh (%d files missing, e.g. %s)",
            _iso(run), step, len(missing), missing[0],
        )
        return None
    stamps = []
    for n in names:
        try:
            stamps.append(datetime.fromisoformat(present[n].replace("Z", "+00:00")))
        except ValueError:
            continue
    return max(stamps) if stamps else valid


def run_has_steps(
    session: requests.Session, run: datetime, steps: list[int],
    stats: TransferStats | None = None,
) -> bool:
    """True when the run's last needed step carries every needed file."""
    if not steps:
        return False
    return step_published_at(session, run, steps[-1], stats) is not None


# ---------------------------------------------------------------------------
# Tile cache
# ---------------------------------------------------------------------------


def _tile_cache_path(run_dir: Path, fname: str, key: tuple[int, ...]) -> Path:
    stem = fname[: -len(".nc")] if fname.endswith(".nc") else fname
    return run_dir / stem / ("_".join(str(k) for k in key) + ".npy")


def _coord_cache_path(run_dir: Path, fname: str, coord: str) -> Path:
    stem = fname[: -len(".nc")] if fname.endswith(".nc") else fname
    return run_dir / stem / f"coord_{coord}.npy"


def _load_tile(path: Path) -> np.ndarray | None:
    try:
        return np.load(path, allow_pickle=False)
    except (OSError, ValueError):
        return None


def _save_tile(path: Path, arr: np.ndarray) -> None:
    import tempfile

    from weatherbrief.fetch.grib.cache import _meter_write

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tile.", suffix=".npy", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            np.save(f, arr.astype(np.float32), allow_pickle=False)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    _meter_write(arr.nbytes)


def _save_coords(run_dir: Path, fname: str, coords: dict[str, np.ndarray]) -> None:
    for c, arr in coords.items():
        try:
            _save_tile(_coord_cache_path(run_dir, fname, c), np.asarray(arr))
        except OSError:
            logger.warning("UKV coordinate cache write failed for %s", fname, exc_info=True)


def read_tiles(
    session: requests.Session,
    url: str,
    keys: set[tuple[int, ...]],
    *,
    run_dir: Path | None,
    pool: ThreadPoolExecutor,
    stats: TransferStats,
    coord_names: tuple[str, ...] = (),
) -> tuple[dict[tuple[int, ...], np.ndarray], dict[str, np.ndarray]]:
    """Tiles at *keys* from the cache, or by range read (then cached).

    Returns ``(tiles, coords)``. The *coord_names* vectors are cached beside
    the tiles, so a fully cached read still returns them for the level
    check; a cache from before that (no coordinate file) re-reads them from
    the file header.
    """
    fname = url.rsplit("/", 1)[-1]
    tiles: dict[tuple[int, ...], np.ndarray] = {}
    missing = set(keys)
    if run_dir is not None:
        for key in list(missing):
            arr = _load_tile(_tile_cache_path(run_dir, fname, key))
            if arr is not None:
                tiles[key] = arr
                missing.discard(key)
    coords: dict[str, np.ndarray] = {}
    if not missing and coord_names and run_dir is not None:
        for c in coord_names:
            arr = _load_tile(_coord_cache_path(run_dir, fname, c))
            if arr is not None:
                coords[c] = arr
        if not coords:
            var, _ = read_chunks(
                session, url, set(), coord_names=coord_names, pool=pool, stats=stats,
            )
            coords = var.coords
            _save_coords(run_dir, fname, coords)
    if missing:
        var, fetched = read_chunks(
            session, url, missing, coord_names=coord_names, pool=pool, stats=stats,
        )
        coords = var.coords
        if run_dir is not None:
            _save_coords(run_dir, fname, coords)
        for key, arr in fetched.items():
            tiles[key] = arr
            if run_dir is not None:
                try:
                    _save_tile(_tile_cache_path(run_dir, fname, key), arr)
                except OSError:
                    logger.warning("UKV tile cache write failed for %s", fname, exc_info=True)
    return tiles, coords


# ---------------------------------------------------------------------------
# Point sampling
# ---------------------------------------------------------------------------


@dataclass
class _Stencil:
    """Bilinear corners of each route point on a regular grid."""

    iy: np.ndarray  # (n, 2) row indices
    ix: np.ndarray  # (n, 2) column indices
    wy: np.ndarray  # (n,) weight of the second row
    wx: np.ndarray  # (n,) weight of the second column

    def tiles(self, tile: int) -> set[tuple[int, int]]:
        out = set()
        for rows, cols in zip(self.iy, self.ix):
            for r in rows:
                for c in cols:
                    out.add(((int(r) // tile) * tile, (int(c) // tile) * tile))
        return out


def _stencil(fy: np.ndarray, fx: np.ndarray, ny: int, nx: int) -> _Stencil:
    y0 = np.clip(np.floor(fy).astype(int), 0, ny - 2)
    x0 = np.clip(np.floor(fx).astype(int), 0, nx - 2)
    return _Stencil(
        iy=np.stack([y0, y0 + 1], axis=1),
        ix=np.stack([x0, x0 + 1], axis=1),
        wy=np.clip(fy - y0, 0.0, 1.0),
        wx=np.clip(fx - x0, 0.0, 1.0),
    )


def ukv_stencil(lats: list[float], lons: list[float]) -> _Stencil:
    x, y = project_points(lats, lons)
    return _stencil((y - UKV_Y0_M) / UKV_DX_M, (x - UKV_X0_M) / UKV_DX_M, UKV_NY, UKV_NX)


def global_stencil(lats: list[float], lons: list[float]) -> _Stencil:
    lat = np.asarray(lats, float)
    lon = ((np.asarray(lons, float) + 180.0) % 360.0) - 180.0
    return _stencil(
        (lat - GLOBAL_LAT0) / GLOBAL_DLAT, (lon - GLOBAL_LON0) / GLOBAL_DLON,
        GLOBAL_NLAT, GLOBAL_NLON,
    )


def _corner_values(
    st: _Stencil, tiles: dict[tuple[int, ...], np.ndarray], level_key: tuple[int, ...], tile: int,
) -> np.ndarray:
    """(n, 2, 2) values at each point's corners; NaN where a tile is absent."""
    n = len(st.iy)
    out = np.full((n, 2, 2), np.nan, dtype=np.float64)
    for i in range(n):
        for a, r in enumerate(st.iy[i]):
            for b, c in enumerate(st.ix[i]):
                r, c = int(r), int(c)
                arr = tiles.get((*level_key, (r // tile) * tile, (c // tile) * tile))
                if arr is None:
                    continue
                arr2 = arr.reshape(arr.shape[-2:])
                out[i, a, b] = arr2[r % tile, c % tile]
    return out


def _bilinear(st: _Stencil, corners: np.ndarray) -> np.ndarray:
    """Bilinear blend; NaN corners drop out and the rest renormalise."""
    wy = np.stack([1.0 - st.wy, st.wy], axis=1)  # (n, 2) row weights
    wx = np.stack([1.0 - st.wx, st.wx], axis=1)  # (n, 2) column weights
    w = wy[:, :, None] * wx[:, None, :]  # (n, 2, 2)
    valid = ~np.isnan(corners)
    wsum = np.where(valid, w, 0.0).sum(axis=(1, 2))
    vals = np.where(valid, corners, 0.0) * w
    with np.errstate(invalid="ignore", divide="ignore"):
        out = vals.sum(axis=(1, 2)) / wsum
    out[wsum <= 1e-9] = np.nan
    return out


def _nearest(st: _Stencil, corners: np.ndarray) -> np.ndarray:
    a = (st.wy >= 0.5).astype(int)
    b = (st.wx >= 0.5).astype(int)
    return corners[np.arange(len(a)), a, b]


def _blend_wind(
    st: _Stencil, speed: np.ndarray, from_deg: np.ndarray,
) -> tuple[np.ndarray, np.ndarray]:
    """Bilinear blend of wind corners as vectors → (speed, from-direction).

    *speed* and *from_deg* are corner arrays, (n, 2, 2) or (n, L, 2, 2).
    Each corner becomes u/v, u and v are blended, and the result goes back
    to speed and meteorological direction, so a northerly stays northerly
    across the 0/360 wrap.
    """
    rad = np.radians(from_deg)
    u = -speed * np.sin(rad)
    v = -speed * np.cos(rad)
    if speed.ndim == 3:
        ub, vb = _bilinear(st, u), _bilinear(st, v)
    else:
        ub = np.stack([_bilinear(st, u[:, j]) for j in range(speed.shape[1])], axis=1)
        vb = np.stack([_bilinear(st, v[:, j]) for j in range(speed.shape[1])], axis=1)
    spd = np.hypot(ub, vb)
    with np.errstate(invalid="ignore"):
        wdir = np.degrees(np.arctan2(-ub, -vb)) % 360.0
    return spd, wdir


# ---------------------------------------------------------------------------
# Decode one step
# ---------------------------------------------------------------------------


@dataclass
class UkvStep:
    """Everything the slot needs at one UKV step, per route point."""

    valid_utc: datetime
    period_h: int
    # Per point: {pressure_hpa: raw dict for decode._convert_raw_sounding}.
    levels: list[dict[int, dict[str, float]]]
    # Per point: surface raw dict (see _SURFACE_PARAMS keys) + derived.
    surface: list[dict[str, float]]
    complete: bool = True
    problem: str | None = None


def _finite(v: float) -> float | None:
    return None if v is None or not np.isfinite(v) else float(v)


def _uv_from_speed_dir(speed: float, from_deg: float) -> tuple[float, float]:
    rad = math.radians(from_deg)
    return -speed * math.sin(rad), -speed * math.cos(rad)


def cloud_fraction_at_heights(
    heights_agl_m: np.ndarray, fractions: np.ndarray, target_agl_m: float,
) -> float | None:
    """Cloud fraction (0-1) at *target_agl_m* by linear interpolation in height.

    None above the top height level (6000 m AGL) or below ground; between
    the ground and the lowest level (5 m) the lowest level's value.
    """
    if not np.isfinite(target_agl_m) or target_agl_m < 0 or target_agl_m > heights_agl_m[-1]:
        return None
    ok = np.isfinite(fractions)
    if not ok.any():
        return None
    h, f = heights_agl_m[ok], fractions[ok]
    return float(np.interp(target_agl_m, h, f))


def ceiling_agl_m(heights_agl_m: np.ndarray, fractions: np.ndarray) -> float | None:
    """Lowest height (m AGL) where cloud fraction reaches :data:`CEILING_FRACTION`.

    Linear crossing between the height levels bracketing it; the lowest
    level itself when it is already at or above the threshold. None when no
    level up to 6000 m AGL reaches it.
    """
    prev_h: float | None = None
    prev_f: float | None = None
    for h, f in zip(heights_agl_m, fractions):
        if not np.isfinite(f):
            prev_h, prev_f = None, None
            continue
        if f >= CEILING_FRACTION:
            if prev_h is None or prev_f is None or prev_f >= CEILING_FRACTION:
                return float(h)
            frac = (CEILING_FRACTION - prev_f) / (f - prev_f)
            return float(prev_h + frac * (h - prev_h))
        prev_h, prev_f = float(h), float(f)
    return None


@dataclass
class UkvFetch:
    """One briefing's UKV read: the run, the points, the transfer counters."""

    run: datetime
    lats: list[float]
    lons: list[float]
    session: requests.Session
    run_dir: Path | None
    as_of_time: datetime | None = None
    stats: TransferStats = field(default_factory=TransferStats)
    global_run: datetime | None = None
    omega_steps: int = 0
    omega_missing_steps: int = 0

    def __post_init__(self) -> None:
        self._ukv_st = ukv_stencil(self.lats, self.lons)
        self._ukv_tiles = self._ukv_st.tiles(UKV_TILE)
        self._glob_st = global_stencil(self.lats, self.lons)
        self._glob_tiles = self._glob_st.tiles(GLOBAL_TILE)
        self._orography: np.ndarray | None = None
        self._global_cache: dict[tuple[datetime, int], np.ndarray | None] = {}

    # --- reads ------------------------------------------------------------

    def _read_field(
        self, step: int, param: str, n_levels: int | None, pool, chunk_pool,
        level_indices: list[int] | None = None, corners: bool = False,
    ) -> tuple[np.ndarray, dict[str, np.ndarray]]:
        """Values at the points: (n_points,) or (n_points, n_levels).

        With *corners*, the unblended corner values instead: (n, 2, 2) or
        (n, n_levels, 2, 2), for fields that must be blended together
        (wind speed and direction, :func:`_blend_wind`).
        """
        url = file_url(UKV_PREFIX, self.run, step, param)
        if n_levels is None:
            keys = {(r, c) for r, c in self._ukv_tiles}
        else:
            idx = level_indices if level_indices is not None else list(range(n_levels))
            keys = {(lv, r, c) for lv in idx for r, c in self._ukv_tiles}
        coord = ("pressure",) if "pressure_levels" in param else (
            ("height",) if "height_levels" in param else ()
        )
        tiles, coords = read_tiles(
            self.session, url, keys, run_dir=self.run_dir, pool=chunk_pool,
            stats=self.stats, coord_names=coord,
        )
        check_level_coords(param, coords)
        if n_levels is None:
            cv = _corner_values(self._ukv_st, tiles, (), UKV_TILE)
            if corners:
                return cv, coords
            key = _SURFACE_PARAMS.get(param, (None,))[0]
            nearest = key in _NEAREST_KEYS
            return (_nearest(self._ukv_st, cv) if nearest else _bilinear(self._ukv_st, cv)), coords
        idx = level_indices if level_indices is not None else list(range(n_levels))
        if corners:
            return np.stack([
                _corner_values(self._ukv_st, tiles, (lv,), UKV_TILE) for lv in idx
            ], axis=1), coords
        cols = np.stack([
            _bilinear(self._ukv_st, _corner_values(self._ukv_st, tiles, (lv,), UKV_TILE))
            for lv in idx
        ], axis=1)
        return cols, coords

    def orography(self, step: int, pool, chunk_pool) -> np.ndarray:
        if self._orography is None:
            vals, _ = self._read_field(step, _OROGRAPHY_PARAM, None, pool, chunk_pool)
            self._orography = vals
        return self._orography

    def _global_w(self, run: datetime, step: int, chunk_pool) -> np.ndarray | None:
        """(n_points, len(UKV_PRESSURE_LEVELS)) w in m/s, or None when missing."""
        key = (run, step)
        if key in self._global_cache:
            return self._global_cache[key]
        url = file_url(GLOBAL_PREFIX, run, step, GLOBAL_W_PARAM)
        idx = [_GLOBAL_LEVEL_INDEX[p] for p in UKV_PRESSURE_LEVELS]
        keys = {(lv, r, c) for lv in idx for r, c in self._glob_tiles}
        try:
            tiles, coords = read_tiles(
                self.session, url, keys, run_dir=self.run_dir, pool=chunk_pool,
                stats=self.stats, coord_names=("pressure",),
            )
            check_level_coords(GLOBAL_W_PARAM, coords)
        except RemoteFileMissing:
            self._global_cache[key] = None
            return None
        except Exception:
            logger.warning("MO global w read failed: %s", url.rsplit("/", 1)[-1], exc_info=True)
            self._global_cache[key] = None
            return None
        cols = np.stack([
            _bilinear(self._glob_st, _corner_values(self._glob_st, tiles, (lv,), GLOBAL_TILE))
            for lv in idx
        ], axis=1)
        self._global_cache[key] = cols
        return cols

    def global_w_at(self, valid: datetime, chunk_pool) -> np.ndarray | None:
        """w at *valid* from the newest usable global run, time-interpolated.

        Sets ``global_run`` on first success so later steps stay on that run.
        Runs on a pool thread but once per step, and steps are read one
        after another, so it is never called concurrently on one fetch.
        """
        runs = [self.global_run] if self.global_run else global_candidate_runs(self.run, self.as_of_time)
        for run in runs:
            parts = _global_steps_for(run, valid)
            if not parts:
                continue
            fields = [(self._global_w(run, s, chunk_pool), w) for s, w in parts]
            if any(f is None for f, _ in fields):
                continue
            self.global_run = run
            return sum(f * w for f, w in fields)
        return None

    # --- one step ---------------------------------------------------------

    def read_step(self, step: int, pool: ThreadPoolExecutor, chunk_pool: ThreadPoolExecutor) -> UkvStep:
        """Read every field of *step* at the route points."""
        valid = self.run + timedelta(hours=step)
        n = len(self.lats)
        pl_idx = [_UKV_LEVEL_INDEX[p] for p in UKV_PRESSURE_LEVELS]
        surface_specs = surface_params_for(step)
        jobs: dict[str, object] = {}
        for param in _PL_PARAMS:
            jobs[param] = pool.submit(
                self._read_field, step, param, 33, pool, chunk_pool, pl_idx,
                param in _PL_WIND,
            )
        jobs[_CLOUD_HL_PARAM] = pool.submit(self._read_field, step, _CLOUD_HL_PARAM, 33, pool, chunk_pool)
        for param in surface_specs:
            jobs[param] = pool.submit(
                self._read_field, step, param, None, pool, chunk_pool, None,
                param in _SFC_WIND,
            )
        oro_job = pool.submit(self.orography, step, pool, chunk_pool)
        w_job = pool.submit(self.global_w_at, valid, chunk_pool)

        results: dict[str, np.ndarray] = {}
        problem = None
        for param, fut in jobs.items():
            required = param in _PL_PARAMS or param == _CLOUD_HL_PARAM or \
                surface_specs.get(param, (None, False))[1]
            try:
                results[param] = fut.result()[0]
            except RemoteFileMissing:
                if required:
                    problem = problem or f"missing {param}"
                logger.info("UKV +%dh: %s missing", step, param)
            except Exception as exc:
                # An optional field that fails (after retries) is lost on its
                # own; only a required one fails the step, and with it the slot.
                if required:
                    problem = problem or f"{param}: {type(exc).__name__}"
                logger.warning("UKV +%dh: %s read failed", step, param, exc_info=True)
        for spd_p, dir_p in (_PL_WIND, _SFC_WIND):
            if spd_p in results and dir_p in results:
                results[spd_p], results[dir_p] = _blend_wind(
                    self._ukv_st, results[spd_p], results[dir_p],
                )
            else:
                results.pop(spd_p, None)
                results.pop(dir_p, None)
        try:
            oro = oro_job.result()
        except Exception:
            logger.warning("UKV orography read failed", exc_info=True)
            oro = np.full(n, np.nan)
            problem = problem or "orography"
        try:
            w = w_job.result()
        except Exception:
            logger.warning("MO global w failed for %s", valid.isoformat(), exc_info=True)
            w = None
        self.omega_steps += 1
        if w is None:
            self.omega_missing_steps += 1

        heights = _UKV_HEIGHTS_M
        levels: list[dict[int, dict[str, float]]] = [dict() for _ in range(n)]
        surface: list[dict[str, float]] = [dict() for _ in range(n)]
        if problem is None:
            cloud = results[_CLOUD_HL_PARAM]
            for i in range(n):
                orog_i = _finite(oro[i])
                for j, p in enumerate(UKV_PRESSURE_LEVELS):
                    raw: dict[str, float] = {}
                    for param, rkey in _PL_PARAMS.items():
                        v = _finite(results[param][i, j])
                        if v is not None:
                            raw[rkey] = v
                    if "raw_temperature_k" not in raw:
                        continue
                    rh = raw.pop("rh_fraction", None)
                    if rh is not None:
                        raw["raw_relative_humidity_pct"] = max(0.0, min(100.0, rh * 100.0))
                    spd = raw.pop("wind_speed_m_s", None)
                    wdir = raw.pop("wind_from_deg", None)
                    if spd is not None and wdir is not None:
                        raw["raw_u_wind_m_s"], raw["raw_v_wind_m_s"] = _uv_from_speed_dir(spd, wdir)
                    gh = raw.get("geopotential_height_m")
                    if gh is not None and orog_i is not None:
                        caf = cloud_fraction_at_heights(heights, cloud[i], gh - orog_i)
                        if caf is not None:
                            raw["cloud_area_fraction_pct"] = caf * 100.0
                    if w is not None:
                        wv = _finite(w[i, j])
                        if wv is not None:
                            raw["raw_w_m_s"] = wv
                    levels[i][p] = raw
                sfc: dict[str, float] = {}
                for param, (rkey, _req) in surface_specs.items():
                    if param in results:
                        v = _finite(results[param][i])
                        if v is not None:
                            sfc[rkey] = v
                if orog_i is not None:
                    sfc["orography_m"] = orog_i
                ceil = ceiling_agl_m(heights, cloud[i])
                if ceil is not None:
                    sfc["ceiling_agl_m"] = ceil
                surface[i] = sfc
            for i in range(n):
                if not levels[i] or "t2m_k" not in surface[i]:
                    problem = "points outside data"
                    break
        return UkvStep(
            valid_utc=valid, period_h=_period_h(step), levels=levels,
            surface=surface, complete=problem is None, problem=problem,
        )


# UKV and MO global share the 33 pressure levels (Pa in the files).
_ALL_PRESSURE_LEVELS_HPA: tuple[int, ...] = (
    1000, 975, 950, 925, 900, 850, 800, 750, 700, 650, 600, 550, 500, 450,
    400, 375, 350, 325, 300, 275, 250, 225, 200, 175, 150, 125, 100, 70, 50,
    40, 30, 20, 10,
)
_UKV_LEVEL_INDEX = {p: i for i, p in enumerate(_ALL_PRESSURE_LEVELS_HPA)}
_GLOBAL_LEVEL_INDEX = _UKV_LEVEL_INDEX
# Height levels of the cloud-fraction file, metres above ground. AGL was
# checked on real data (#937): temperature on height levels matches the
# pressure-level profile to 0.07 K over the Alps read as AGL, 15 K as MSL.
# Other height-level files carry other levels, so only this one is used.
_UKV_HEIGHTS_M = np.array([
    5, 10, 20, 30, 50, 75, 100, 150, 200, 250, 300, 400, 500, 600, 700, 800,
    1000, 1250, 1500, 1750, 2000, 2250, 2500, 2750, 3000, 3250, 3500, 3750,
    4000, 4500, 5000, 5500, 6000,
], dtype=float)


class UkvLayoutChanged(RuntimeError):
    """A file's level coordinate no longer matches the constants above."""


def check_level_coords(param: str, coords: dict[str, np.ndarray]) -> None:
    """Fail loudly if the file's levels differ from the ones indexed by.

    Level indices are constants so cached tiles can be served without
    re-reading coordinates; a silent reorder would mislabel every level.
    *coords* is empty on a cache hit, which was checked when cached.
    """
    if "pressure" in coords:
        got = np.asarray(coords["pressure"], dtype=float) / 100.0
        want = np.asarray(_ALL_PRESSURE_LEVELS_HPA, dtype=float)
    elif "height" in coords and param == _CLOUD_HL_PARAM:
        got = np.asarray(coords["height"], dtype=float)
        want = _UKV_HEIGHTS_M
    else:
        return
    if got.shape != want.shape or not np.allclose(got, want, atol=0.5):
        raise UkvLayoutChanged(f"{param}: levels {got.tolist()} != expected")


# ---------------------------------------------------------------------------
# Conversions for the slot
# ---------------------------------------------------------------------------


def surface_snapshot(raw: dict[str, float], period_h: int) -> dict[str, float | None]:
    """UKV single-level raw values → ``HourlyForecast`` field values.

    Accumulations are per-hour rates over the step's period (1 h, or 3 h past
    +54 h), matching the hourly Open-Meteo convention (amount in the hour
    ending at the valid time).
    """
    def k_to_c(key: str) -> float | None:
        v = raw.get(key)
        return None if v is None else v - 273.15

    def frac_pct(key: str) -> float | None:
        v = raw.get(key)
        return None if v is None else max(0.0, min(100.0, v * 100.0))

    def m_per_h_to_mm(key: str) -> float | None:
        v = raw.get(key)
        return None if v is None else max(0.0, v * 1000.0 / period_h)

    def ms_to_kt(key: str) -> float | None:
        v = raw.get(key)
        return None if v is None else v * _KT_PER_MS

    out: dict[str, float | None] = {
        "temperature_2m_c": k_to_c("t2m_k"),
        "dewpoint_2m_c": k_to_c("td2m_k"),
        "relative_humidity_2m_pct": frac_pct("rh2m_fraction"),
        "pressure_msl_hpa": None if raw.get("mslp_pa") is None else raw["mslp_pa"] / 100.0,
        "surface_pressure_hpa": None if raw.get("psfc_pa") is None else raw["psfc_pa"] / 100.0,
        "wind_speed_10m_kt": ms_to_kt("ws10_m_s"),
        "wind_direction_10m_deg": raw.get("wd10_deg"),
        "wind_gusts_10m_kt": ms_to_kt("gust_m_s"),
        "visibility_m": raw.get("vis_m"),
        "precipitation_mm": m_per_h_to_mm("precip_m"),
        "rain_mm": m_per_h_to_mm("rain_m"),
        "snowfall_cm": None,
        "cloud_cover_pct": frac_pct("total_cloud_fraction"),
        "cloud_cover_low_pct": frac_pct("low_cloud_fraction"),
        "cloud_cover_mid_pct": frac_pct("mid_cloud_fraction"),
        "cloud_cover_high_pct": frac_pct("high_cloud_fraction"),
        "cape_jkg": raw.get("mucape_jkg"),
    }
    snow = m_per_h_to_mm("snow_m_we")
    if snow is not None:
        out["snowfall_cm"] = snow * SNOW_CM_PER_MM_WATER
    return out


def cloud_diagnostics(raw: dict[str, float]):
    """UKV single-level cloud fields → ``NWPCloudDiagnostics``.

    Heights AGL except the freezing level, which goes to MSL with the UKV
    orography (§21). ``ceiling_ft`` is the lowest height level at 50 % cloud
    fraction (the app's BKN threshold); ``low.base_ft`` is UKV's own cloud
    base at 2.5 oktas, a SCT threshold, so it is not used as the ceiling.
    UKV runs no deep-convection scheme, so ``convective_scheme_absent``.
    """
    from weatherbrief.models.analysis import NWPCloudDiagnostics, NWPCloudLayerDiag

    def pct(key: str) -> float | None:
        v = raw.get(key)
        return None if v is None else max(0.0, min(100.0, v * 100.0))

    def ft(v: float | None) -> float | None:
        return None if v is None or v < 0 else round(v * _M_TO_FT)

    fz = raw.get("freezing_level_agl_m")
    oro = raw.get("orography_m")
    fz_ft = ft(fz + oro) if fz is not None and oro is not None else None
    return NWPCloudDiagnostics(
        low=NWPCloudLayerDiag(cover_pct=pct("low_cloud_fraction"), base_ft=ft(raw.get("cloud_base_agl_m"))),
        mid=NWPCloudLayerDiag(cover_pct=pct("mid_cloud_fraction")),
        high=NWPCloudLayerDiag(cover_pct=pct("high_cloud_fraction")),
        total_cover_pct=pct("total_cloud_fraction"),
        ceiling_ft=ft(raw.get("ceiling_agl_m")),
        freezing_level_ft=fz_ft,
        convective_scheme_absent=True,
    )


_POOLS_LOCK = threading.Lock()
_POOLS: tuple[ThreadPoolExecutor, ThreadPoolExecutor] | None = None


def _shared_pools() -> tuple[ThreadPoolExecutor, ThreadPoolExecutor]:
    """Process-wide (file, chunk) pools: concurrent briefings share them, so
    UKV never has more than ``_FETCH_WORKERS + _CHUNK_WORKERS`` requests in
    flight against S3. File tasks wait on chunk tasks, never the reverse,
    so sharing cannot deadlock."""
    global _POOLS
    with _POOLS_LOCK:
        if _POOLS is None:
            _POOLS = (
                ThreadPoolExecutor(max_workers=_FETCH_WORKERS, thread_name_prefix="ukv-file"),
                ThreadPoolExecutor(max_workers=_CHUNK_WORKERS, thread_name_prefix="ukv-chunk"),
            )
        return _POOLS


def fetch_steps(
    fetch: UkvFetch, steps: list[int],
) -> list[UkvStep]:
    """Read *steps* (all files of a step in parallel, steps in order)."""
    pool, chunk_pool = _shared_pools()
    out = []
    for step in steps:
        st = fetch.read_step(step, pool, chunk_pool)
        out.append(st)
        if not st.complete:
            break
    return out
