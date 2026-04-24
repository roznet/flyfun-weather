"""Unified calibration case format for both Open-Meteo and ERA5 sources.

A case directory holds everything needed for algorithm calibration at a
specific date: raw grid data, reference charts, zone annotations.

Layout:
    data/calibration/<case>/
        meta.json                    # source, grid, models, valid_times, levels
        raw/
            <model>.npz              # per-model: (n_time, n_lat, n_lon) arrays
        reference/                   # DWD / ICON forecast charts
        expected.yaml                # zone-level front annotations

NPZ contents per model, single-level (legacy, 850 hPa only):
    T850, Td850, theta_e, u850, v850  — (n_time, n_lat, n_lon) float32
    valid_times                        — (n_time,) datetime64[ns]

NPZ contents per model, multi-level (when ``meta["levels"][model]``
contains more than one entry — Phase B):
    T_<L>, Td_<L>, theta_e_<L>, u_<L>, v_<L>   for each L in levels
    valid_times

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

import numpy as np

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
    levels: dict[str, list[int]] = field(default_factory=dict)  # model → sorted hPa levels present
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

    def available_levels(self, model: str) -> list[int]:
        """Pressure levels (hPa, sorted) available for this model."""
        return list(self.levels.get(model, [850]))

    def fields(self, model: str, hour: int, level_hPa: int | None = None) -> dict | None:
        """Return field dict for one model × one hour-offset × one level, or None.

        ``level_hPa`` defaults to 850 if present, else the first available level.
        Returned dict always uses the legacy key names (``T850, Td850, theta_e,
        u850, v850``) regardless of the actual level — the ``850`` in the key is
        historical; the values are from the requested level.
        """
        idx = self._index_at_hour(model, hour)
        if idx is None:
            return None
        level_hPa = self._resolve_level(model, level_hPa)
        npz = np.load(self.case_dir / "raw" / f"{model}.npz")
        try:
            return _read_level_slice(npz, idx, level_hPa)
        finally:
            npz.close()

    def fields_all_hours(self, model: str, level_hPa: int | None = None) -> dict[int, dict]:
        """Return {hour_offset: fields_dict} for every available hour at one level."""
        level_hPa = self._resolve_level(model, level_hPa)
        npz = np.load(self.case_dir / "raw" / f"{model}.npz")
        try:
            hours = self.available_hours(model)
            return {
                hours[i]: _read_level_slice(npz, i, level_hPa)
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

    def _resolve_level(self, model: str, level_hPa: int | None) -> int:
        avail = self.available_levels(model)
        if level_hPa is None:
            return 850 if 850 in avail else avail[0]
        if level_hPa not in avail:
            raise ValueError(
                f"Level {level_hPa} hPa not in case levels {avail} for model {model!r}"
            )
        return level_hPa


def _read_level_slice(npz, idx: int, level_hPa: int) -> dict:
    """Read one (time, level) slice from an open NPZ, normalising keys.

    Supports both legacy (``T850, Td850, theta_e, u850, v850``) and the new
    multi-level format (``T_<L>, Td_<L>, theta_e_<L>, u_<L>, v_<L>``). The
    returned dict always uses the legacy keys so downstream code works
    unchanged — the values are from the requested ``level_hPa``.
    """
    if f"T_{level_hPa}" in npz.files:
        return {
            "T850": npz[f"T_{level_hPa}"][idx].astype(np.float64),
            "Td850": npz[f"Td_{level_hPa}"][idx].astype(np.float64),
            "theta_e": npz[f"theta_e_{level_hPa}"][idx].astype(np.float64),
            "u850": npz[f"u_{level_hPa}"][idx].astype(np.float64),
            "v850": npz[f"v_{level_hPa}"][idx].astype(np.float64),
        }
    # Legacy single-level 850 format.
    if level_hPa != 850:
        raise ValueError(
            f"NPZ only has legacy 850 hPa fields; cannot read level {level_hPa}"
        )
    return {
        "T850": npz["T850"][idx].astype(np.float64),
        "Td850": npz["Td850"][idx].astype(np.float64),
        "theta_e": npz["theta_e"][idx].astype(np.float64),
        "u850": npz["u850"][idx].astype(np.float64),
        "v850": npz["v850"][idx].astype(np.float64),
    }


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

    levels_meta = meta.get("levels", {})
    valid_times: dict[str, np.ndarray] = {}
    init_times: dict[str, int] = {}
    levels: dict[str, list[int]] = {}
    for model in meta["models"]:
        npz_path = case_dir / "raw" / f"{model}.npz"
        if not npz_path.exists():
            raise FileNotFoundError(f"Missing raw data: {npz_path}")
        model_levels = [int(L) for L in levels_meta.get(model, [850])]
        with np.load(npz_path) as npz:
            vt = npz["valid_times"]
            shape_key = "T850" if "T850" in npz.files else f"T_{model_levels[0]}"
            if shape_key not in npz.files:
                raise ValueError(
                    f"NPZ {npz_path} missing expected key {shape_key!r} — "
                    f"levels in meta: {model_levels}"
                )
            if npz[shape_key].shape[1:] != (len(lat), len(lon)):
                raise ValueError(
                    f"Shape mismatch in {npz_path}: {shape_key} is "
                    f"{npz[shape_key].shape[1:]} but grid is "
                    f"({len(lat)}, {len(lon)}). "
                    f"Case is from a different grid resolution."
                )
        valid_times[model] = vt
        init_times[model] = meta.get("init_times", {}).get(model, 0)
        levels[model] = sorted(model_levels)

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
        levels=levels,
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
    levels: dict[str, list[int]] | None = None,
) -> None:
    """Write meta.json for a case.

    ``levels`` maps model → list of pressure levels (hPa) stored in the NPZ.
    When omitted, legacy single-level 850 hPa is assumed.
    """
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
        "levels": {m: sorted(int(L) for L in (levels or {}).get(m, [850])) for m in models},
    }
    (case_dir / "meta.json").write_text(json.dumps(meta, indent=2))


def save_model_fields(
    case_dir: Path,
    model: str,
    fields_by_time: list[dict],
    valid_times: np.ndarray,
    levels: list[int] | None = None,
) -> None:
    """Write raw/<model>.npz with the model's field timeseries.

    Parameters
    ----------
    case_dir : case root directory (created if missing)
    model : model identifier (ecmwf, gfs, icon, era5)
    fields_by_time : list of dicts (one per time).
        - Single level (``levels=None`` or ``[850]``): each dict has
          ``T850, Td850, theta_e, u850, v850`` (2D arrays, legacy format).
        - Multi level: each dict is keyed by pressure level (int), and
          ``fields_by_time[i][L]`` is itself a dict with
          ``T850, Td850, theta_e, u850, v850`` — the ``850`` suffix in the
          inner keys is historical; values are at level ``L``.
    valid_times : (n_time,) np.datetime64 array matching fields_by_time
    levels : list of pressure levels (hPa) present in ``fields_by_time``.
        Defaults to ``[850]`` (legacy single-level).
    """
    raw_dir = case_dir / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)

    levels = sorted(int(L) for L in (levels or [850]))
    is_multi = levels != [850]

    def _stack_flat(key: str) -> np.ndarray:
        return np.stack([f[key] for f in fields_by_time], axis=0).astype(np.float32)

    def _stack_at(level: int, key: str) -> np.ndarray:
        return np.stack(
            [f[level][key] for f in fields_by_time], axis=0,
        ).astype(np.float32)

    if is_multi:
        data: dict[str, np.ndarray] = {"valid_times": valid_times.astype("datetime64[ns]")}
        for L in levels:
            data[f"T_{L}"] = _stack_at(L, "T850")
            data[f"Td_{L}"] = _stack_at(L, "Td850")
            data[f"theta_e_{L}"] = _stack_at(L, "theta_e")
            data[f"u_{L}"] = _stack_at(L, "u850")
            data[f"v_{L}"] = _stack_at(L, "v850")
        np.savez_compressed(raw_dir / f"{model}.npz", **data)
    else:
        np.savez_compressed(
            raw_dir / f"{model}.npz",
            T850=_stack_flat("T850"),
            Td850=_stack_flat("Td850"),
            theta_e=_stack_flat("theta_e"),
            u850=_stack_flat("u850"),
            v850=_stack_flat("v850"),
            valid_times=valid_times.astype("datetime64[ns]"),
        )

    logger.info(
        "Wrote %s (%d timesteps, %d level(s), %.1f KB)",
        raw_dir / f"{model}.npz",
        len(fields_by_time),
        len(levels),
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
        levels={m: [850] for m in models},
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
    level_hPa: int | list[int] = 850,
    terrain_mask: np.ndarray | None = None,
) -> None:
    """Build a case from an ERA5 GRIB.

    ``level_hPa`` is either a single int (legacy single-level storage, default
    850) or a list of ints (multi-level storage — one NPZ holding all levels).
    All timestamps present in the GRIB are stored under model key ``era5``.
    """
    import xarray as xr

    from weatherbrief.era5.loader import load_era5_fields
    from weatherbrief.frontal.grid import compute_theta_e, fill_terrain

    grib_path = Path(grib_path)
    levels = [level_hPa] if isinstance(level_hPa, int) else sorted(int(L) for L in level_hPa)
    is_multi = levels != [850]

    # Peek at the GRIB to learn which timestamps are present
    with xr.open_dataset(str(grib_path), engine="cfgrib") as ds:
        timestamps = [np.datetime64(t) for t in ds.time.values]

    if not timestamps:
        raise ValueError(f"No timestamps found in {grib_path} — empty GRIB?")

    fields_by_time: list[dict] = []
    lat: np.ndarray | None = None
    lon: np.ndarray | None = None

    def _apply_terrain_and_rederive(f: dict) -> dict:
        """Fill terrain on T/Td/u/v and re-derive θe for consistency."""
        if terrain_mask is None:
            return {
                "T850": f["T850"], "Td850": f["Td850"],
                "theta_e": f["theta_e"],
                "u850": f["u850"], "v850": f["v850"],
            }
        T = fill_terrain(f["T850"], terrain_mask)
        Td = fill_terrain(f["Td850"], terrain_mask)
        u = fill_terrain(f["u850"], terrain_mask)
        v = fill_terrain(f["v850"], terrain_mask)
        return {
            "T850": T, "Td850": Td,
            "theta_e": compute_theta_e(T, Td),
            "u850": u, "v850": v,
        }

    for t in timestamps:
        per_level: dict[int, dict] = {}
        for L in levels:
            f = load_era5_fields(grib_path, t, level_hPa=L)

            # First iteration overall: capture coords + validate terrain_mask
            # shape up front rather than letting fill_terrain fail deep inside
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
                    levels={"era5": levels},
                )

            per_level[L] = _apply_terrain_and_rederive(f)

        if is_multi:
            fields_by_time.append(per_level)
        else:
            fields_by_time.append(per_level[levels[0]])

    save_model_fields(
        case_dir, "era5", fields_by_time,
        np.array(timestamps, dtype="datetime64[ns]"),
        levels=levels,
    )
