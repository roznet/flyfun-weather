"""Unified calibration case format for both Open-Meteo and ERA5 sources.

A case directory holds everything needed for algorithm calibration at a
specific date: raw grid data, reference charts, zone annotations.

Layout:
    data/calibration/<case>/
        meta.json                    # source, grid, models, valid_times
        raw/
            <model>.npz              # per-model: (n_time, n_lat, n_lon) arrays
        reference/                   # DWD / ICON forecast charts
        expected.yaml                # zone-level front annotations

NPZ contents per model:
    T850, Td850, theta_e, u850, v850  — (n_time, n_lat, n_lon) float32
    valid_times                        — (n_time,) datetime64[ns]

For Open-Meteo cases, <model> is ecmwf / gfs / icon. The NPZ carries
hourly forecast data from a single init cycle.

For ERA5 cases, <model> is era5. The NPZ carries N synoptic-time
analyses (00/06/12/18 UTC) — there's no init / horizon concept.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from weatherbrief.era5.loader import load_era5_fields as _

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Case dataclass
# ---------------------------------------------------------------------------


@dataclass
class Case:
    """A calibration case on disk.

    Provides indexed access to the pre-reshaped fields for any model,
    keyed by hour-offset from the case's reference time (the first
    valid time). Works uniformly for Open-Meteo forecast cycles and
    ERA5 analysis stamps.
    """

    case_dir: Path
    case_name: str
    source: str                             # "open_meteo" or "era5"
    resolution_deg: float                   # 0.25
    lat: np.ndarray                         # (n_lat,) ascending
    lon: np.ndarray                         # (n_lon,) ascending
    models: list[str]                       # e.g. ["ecmwf", "gfs", "icon"] or ["era5"]
    valid_times: dict[str, np.ndarray]      # model → (n_time,) datetime64[ns]
    init_times: dict[str, int]              # model → unix seconds; 0 for ERA5
    _meta: dict = field(default_factory=dict, repr=False)
    # Cache of available_hours results per model — lazy, computed once.
    _hours_cache: dict[str, list[int]] = field(default_factory=dict, repr=False)

    # ------------------------------------------------------------------
    # Field access

    def available_hours(self, model: str) -> list[int]:
        """Hour offsets (from valid_times[0]) available for this model.

        For Open-Meteo: every hour 0..N. For ERA5 smoke: 0, 6, 12, 18.
        Cached after first call; callers can loop over hours without
        paying O(n) cast cost per iteration.
        """
        cached = self._hours_cache.get(model)
        if cached is not None:
            return cached

        vt = self.valid_times[model]
        t0_ns = int(vt[0].astype("datetime64[ns]").astype("int64"))
        hours = [
            int((int(t.astype("datetime64[ns]").astype("int64")) - t0_ns) // int(3.6e12))
            for t in vt
        ]
        self._hours_cache[model] = hours
        return hours

    def fields(self, model: str, hour: int) -> dict | None:
        """Return field dict for one model × one hour-offset, or None."""
        idx = self._index_at_hour(model, hour)
        if idx is None:
            return None
        npz = np.load(self.case_dir / "raw" / f"{model}.npz")
        try:
            return {
                "T850": npz["T850"][idx].astype(np.float64),
                "Td850": npz["Td850"][idx].astype(np.float64),
                "theta_e": npz["theta_e"][idx].astype(np.float64),
                "u850": npz["u850"][idx].astype(np.float64),
                "v850": npz["v850"][idx].astype(np.float64),
            }
        finally:
            npz.close()

    def fields_all_hours(self, model: str) -> dict[int, dict]:
        """Return {hour_offset: fields_dict} for every available hour."""
        npz = np.load(self.case_dir / "raw" / f"{model}.npz")
        try:
            hours = self.available_hours(model)
            return {
                hours[i]: {
                    "T850": npz["T850"][i].astype(np.float64),
                    "Td850": npz["Td850"][i].astype(np.float64),
                    "theta_e": npz["theta_e"][i].astype(np.float64),
                    "u850": npz["u850"][i].astype(np.float64),
                    "v850": npz["v850"][i].astype(np.float64),
                }
                for i in range(len(hours))
            }
        finally:
            npz.close()

    def valid_time_at_hour(self, model: str, hour: int) -> np.datetime64 | None:
        idx = self._index_at_hour(model, hour)
        return None if idx is None else self.valid_times[model][idx]

    # ------------------------------------------------------------------
    # Internals

    def _index_at_hour(self, model: str, hour: int) -> int | None:
        hours = self.available_hours(model)
        try:
            return hours.index(hour)
        except ValueError:
            return None


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------


def load_case(case_dir: str | Path) -> Case:
    """Load a case from disk.

    Raises FileNotFoundError if meta.json missing; ValueError if data
    shape doesn't match the declared grid (most likely: a legacy 0.5°
    case under the new 0.25° grid).
    """
    case_dir = Path(case_dir)
    meta_path = case_dir / "meta.json"
    if not meta_path.exists():
        raise FileNotFoundError(
            f"{meta_path} not found — case dir is either legacy (pre-0.25° "
            f"refactor) or not a case dir. Rebuild with `new-case`."
        )

    meta = json.loads(meta_path.read_text())
    lat = np.asarray(meta["lat"], dtype=np.float64)
    lon = np.asarray(meta["lon"], dtype=np.float64)

    valid_times: dict[str, np.ndarray] = {}
    init_times: dict[str, int] = {}
    for model in meta["models"]:
        npz_path = case_dir / "raw" / f"{model}.npz"
        if not npz_path.exists():
            raise FileNotFoundError(f"Missing raw data: {npz_path}")
        with np.load(npz_path) as npz:
            vt = npz["valid_times"]
            if npz["T850"].shape[1:] != (len(lat), len(lon)):
                raise ValueError(
                    f"Shape mismatch in {npz_path}: T850 is "
                    f"{npz['T850'].shape[1:]} but grid is "
                    f"({len(lat)}, {len(lon)}). "
                    f"Case is from a different grid resolution."
                )
        valid_times[model] = vt
        init_times[model] = meta.get("init_times", {}).get(model, 0)

    return Case(
        case_dir=case_dir,
        case_name=meta["case_name"],
        source=meta["source"],
        resolution_deg=float(meta["resolution_deg"]),
        lat=lat,
        lon=lon,
        models=list(meta["models"]),
        valid_times=valid_times,
        init_times=init_times,
        _meta=meta,
    )


def save_case_meta(
    case_dir: Path,
    *,
    case_name: str,
    source: str,
    lat: np.ndarray,
    lon: np.ndarray,
    models: list[str],
    init_times: dict[str, int] | None = None,
) -> None:
    """Write meta.json for a case."""
    case_dir.mkdir(parents=True, exist_ok=True)
    resolution = float(np.diff(lat).mean())
    meta = {
        "case_name": case_name,
        "source": source,
        "resolution_deg": round(abs(resolution), 4),
        "created_at": datetime.now(timezone.utc).isoformat(),
        "lat": lat.tolist(),
        "lon": lon.tolist(),
        "models": models,
        "init_times": init_times or {},
    }
    (case_dir / "meta.json").write_text(json.dumps(meta, indent=2))


def save_model_fields(
    case_dir: Path,
    model: str,
    fields_by_time: list[dict],
    valid_times: np.ndarray,
) -> None:
    """Write raw/<model>.npz with the model's field timeseries.

    Parameters
    ----------
    case_dir : case root directory (created if missing)
    model : model identifier (ecmwf, gfs, icon, era5)
    fields_by_time : list of dicts (one per time) with keys
        T850, Td850, theta_e, u850, v850 (all 2D arrays, same shape)
    valid_times : (n_time,) np.datetime64 array matching fields_by_time
    """
    raw_dir = case_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    def _stack(key: str) -> np.ndarray:
        return np.stack([f[key] for f in fields_by_time], axis=0).astype(np.float32)

    np.savez_compressed(
        raw_dir / f"{model}.npz",
        T850=_stack("T850"),
        Td850=_stack("Td850"),
        theta_e=_stack("theta_e"),
        u850=_stack("u850"),
        v850=_stack("v850"),
        valid_times=valid_times.astype("datetime64[ns]"),
    )
    logger.info(
        "Wrote %s (%d timesteps, %.1f KB)",
        raw_dir / f"{model}.npz",
        len(fields_by_time),
        (raw_dir / f"{model}.npz").stat().st_size / 1024,
    )


# ---------------------------------------------------------------------------
# Source-specific builders
# ---------------------------------------------------------------------------


def build_case_from_open_meteo(
    case_dir: Path,
    raw_responses: dict[str, dict],
    init_times: dict[str, int],
    timestamps: dict[str, list[str]],
    lat: np.ndarray,
    lon: np.ndarray,
    terrain_mask: np.ndarray | None,
    *,
    case_name: str,
) -> None:
    """Build a case from Open-Meteo JSON responses for multiple models.

    Reuses the existing `reshape_to_fields` pipeline to convert raw
    point-major JSON into (n_lat, n_lon) arrays per hour, then stacks
    into (n_time, n_lat, n_lon) NPZ for each model.
    """
    from weatherbrief.frontal.grid import reshape_to_fields

    models = list(raw_responses.keys())
    save_case_meta(
        case_dir,
        case_name=case_name,
        source="open_meteo",
        lat=lat,
        lon=lon,
        models=models,
        init_times=init_times,
    )

    for model, raw in raw_responses.items():
        n_hours = len(timestamps.get(model, []))
        if n_hours == 0:
            # fall back to inferring from data length
            first_var = next(iter(raw.values()))
            n_hours = len(first_var[0]) if first_var and first_var[0] else 0

        fields_by_time: list[dict] = []
        valid_time_list: list[np.datetime64] = []
        init = init_times.get(model, 0)
        init_dt = np.datetime64(datetime.fromtimestamp(init, tz=timezone.utc).replace(tzinfo=None))

        for h in range(n_hours):
            fields = reshape_to_fields(raw, lat, lon, h, terrain_mask)
            if fields is None:
                continue
            fields_by_time.append(fields)
            valid_time_list.append(init_dt + np.timedelta64(h, "h"))

        if not fields_by_time:
            logger.warning("No usable data for %s — skipping", model)
            continue

        save_model_fields(
            case_dir, model, fields_by_time,
            np.array(valid_time_list, dtype="datetime64[ns]"),
        )


def build_case_from_era5(
    case_dir: Path,
    grib_path: str | Path,
    *,
    case_name: str,
    level_hPa: int = 850,
    terrain_mask: np.ndarray | None = None,
) -> None:
    """Build a case from an ERA5 GRIB.

    Currently single-level (the level_hPa argument, default 850). All
    timestamps present in the GRIB are stored under model key "era5".
    """
    import xarray as xr

    from weatherbrief.era5.loader import load_era5_fields
    from weatherbrief.frontal.grid import fill_terrain

    grib_path = Path(grib_path)

    from weatherbrief.frontal.grid import compute_theta_e

    # Peek at the GRIB to learn which timestamps are present
    with xr.open_dataset(str(grib_path), engine="cfgrib") as ds:
        timestamps = [np.datetime64(t) for t in ds.time.values]

    fields_by_time: list[dict] = []
    lat: np.ndarray | None = None
    lon: np.ndarray | None = None

    for t in timestamps:
        f = load_era5_fields(grib_path, t, level_hPa=level_hPa)

        # First iteration: capture coords + validate terrain_mask shape
        # up front rather than letting fill_terrain fail deep inside
        # scipy. Avoids a separate pre-load of timestamps[0].
        if lat is None:
            lat = f["lat"]
            lon = f["lon"]
            if terrain_mask is not None and terrain_mask.shape != f["T850"].shape:
                raise ValueError(
                    f"terrain_mask shape {terrain_mask.shape} does not match "
                    f"ERA5 grid shape {f['T850'].shape}. Rebuild the mask "
                    f"with build_terrain_mask(case_lat, case_lon) using the "
                    f"GRIB's own coordinates, or ensure the GRIB domain "
                    f"matches FRONTAL_GRID."
                )
            save_case_meta(
                case_dir,
                case_name=case_name,
                source="era5",
                lat=lat,
                lon=lon,
                models=["era5"],
                init_times={"era5": 0},  # ERA5 has no init time concept
            )

        # Apply terrain fill to keep derivatives clean at high-elevation cells
        if terrain_mask is not None:
            f["T850"] = fill_terrain(f["T850"], terrain_mask)
            f["Td850"] = fill_terrain(f["Td850"], terrain_mask)
            f["u850"] = fill_terrain(f["u850"], terrain_mask)
            f["v850"] = fill_terrain(f["v850"], terrain_mask)
            # re-derive theta_e after terrain fill for consistency
            f["theta_e"] = compute_theta_e(f["T850"], f["Td850"])
        fields_by_time.append({
            "T850": f["T850"], "Td850": f["Td850"],
            "theta_e": f["theta_e"],
            "u850": f["u850"], "v850": f["v850"],
        })

    save_model_fields(
        case_dir, "era5", fields_by_time,
        np.array(timestamps, dtype="datetime64[ns]"),
    )
