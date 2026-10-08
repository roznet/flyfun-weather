"""Exact parity for the #704 vectorised GRIB result loops.

The ECMWF per-point result loops (``_decode_ecmwf_pressure_direct`` /
``_decode_ecmwf_surface_direct``) and the ICON model-level → pressure-level
interpolation (``interpolate_columns_to_pressure_levels`` and its decode.py
callers, plus ``_derive_clc_cloud_layers``) were rewritten from per-value
Python loops to row-wise numpy. They change decoded GRIB data, so the bar is
bit-identical output, not "close": same keys in the same order, same Python
float objects (compared by bit pattern, so -0.0 vs 0.0 counts), same None/NaN
handling.

The pre-#704 functions are copied verbatim below as ``_old_*`` oracles (only
their module-level references are qualified with ``dec.`` so the tests can
feed both versions the same synthetic GRIB series) and compared against the
live code on randomised inputs: NaNs, None, missing levels, short columns,
non-positive and repeated pressures, out-of-range targets, multi-grid
first-wins, single point and empty.
"""

from __future__ import annotations

import math
import struct
from types import SimpleNamespace

import numpy as np
import pytest

from weatherbrief.fetch.grib import decode as dec
from weatherbrief.fetch.grib.icon_eu_levels import (
    TARGET_PRESSURE_LEVELS_HPA,
    bounds_for_field,
    interpolate_columns_to_pressure_levels,
)


# ---------------------------------------------------------------------------
# Oracles: the pre-#704 code, verbatim (globals qualified with ``dec.``)
# ---------------------------------------------------------------------------


def _old_interpolate_model_to_pressure_levels(
    model_pressures_pa: list[float],
    model_values: list[float],
    target_pressures_hpa: list[int] | None = None,
    bounds: tuple[float | None, float | None] | None = None,
) -> dict[int, float]:
    """Interpolate a variable from model levels to standard pressure levels.

    Uses log-pressure interpolation (linear in ln(p)), which is the standard
    approach for atmospheric vertical interpolation.

    Args:
        model_pressures_pa: Pressure at each model level in Pa, one per level.
            Must correspond 1:1 with model_values.
        model_values: Variable values at each model level.
        target_pressures_hpa: Target pressure levels in hPa.
            Defaults to ICON_PRESSURE_LEVELS.
        bounds: Optional ``(min, max)`` clamp applied to each interpolated
            value; either side may be None to leave it unbounded. Defaults to
            None (no clamping) so signed fields (T, U, V, W) pass through
            unchanged. Use :func:`bounds_for_field` to pick per-field bounds.

    Returns:
        Dict mapping pressure_hpa to interpolated value. Levels outside
        the model pressure range are excluded.
    """
    if target_pressures_hpa is None:
        target_pressures_hpa = TARGET_PRESSURE_LEVELS_HPA

    if len(model_pressures_pa) != len(model_values):
        dec.logger.warning(
            "Mismatched lengths: %d pressures vs %d values",
            len(model_pressures_pa), len(model_values),
        )
        return {}

    if len(model_pressures_pa) < 2:
        return {}

    # Convert to numpy arrays
    p_pa = np.array(model_pressures_pa, dtype=np.float64)
    vals = np.array(model_values, dtype=np.float64)

    # Filter out NaN or non-positive pressures
    valid = np.isfinite(p_pa) & (p_pa > 0) & np.isfinite(vals)
    if valid.sum() < 2:
        return {}
    p_pa = p_pa[valid]
    vals = vals[valid]

    # Sort by pressure (ascending — lower pressure = higher altitude)
    sort_idx = np.argsort(p_pa)
    p_pa = p_pa[sort_idx]
    vals = vals[sort_idx]

    # Log-pressure coordinates
    ln_p = np.log(p_pa)

    # Pressure range of model data
    p_min_pa = p_pa[0]
    p_max_pa = p_pa[-1]

    lo, hi = (None, None) if bounds is None else bounds

    result: dict[int, float] = {}
    for target_hpa in target_pressures_hpa:
        target_pa = target_hpa * 100.0
        # Skip if outside model range (no extrapolation)
        if target_pa < p_min_pa or target_pa > p_max_pa:
            continue
        ln_target = np.log(target_pa)
        interp_val = float(np.interp(ln_target, ln_p, vals))
        if lo is not None and interp_val < lo:
            interp_val = lo
        if hi is not None and interp_val > hi:
            interp_val = hi
        result[target_hpa] = interp_val

    return result


def _old_derive_clc_cloud_layers(
    pressure_data: dict[int, list[float | None]],
    clc_data: dict[int, list[float | None]],
    n_points: int,
    clc_threshold: float = 5.0,
) -> list[dict[str, float]]:
    """Derive ICAO-band cloud base/top from model-level CLC profiles.

    Scans each point's CLC profile (on native model levels, not interpolated)
    to find where cloud fraction exceeds the threshold, then classifies into
    low/mid/high ICAO bands and returns base_ft/top_ft for each.

    ICAO band boundaries (by pressure):
      low:  surface to 800 hPa  (~6500 ft)
      mid:  800 to 400 hPa      (~6500–23000 ft)
      high: above 400 hPa       (~23000 ft)

    Returns:
        List of dicts (one per point), each with keys like
        ``low_base_ft``, ``low_top_ft``, ``mid_base_ft``, etc.
        Only populated for bands where cloud was detected.
    """
    from weatherbrief.models.analysis import pressure_pa_to_altitude_ft

    # ICAO layer boundaries in Pa
    LOW_TOP_PA = 80_000   # 800 hPa
    MID_TOP_PA = 40_000   # 400 hPa

    model_levels = sorted(pressure_data.keys())
    results: list[dict[str, float]] = [{} for _ in range(n_points)]

    for pt_idx in range(n_points):
        # Build (pressure_pa, clc%) pairs
        profile: list[tuple[float, float]] = []
        for lev in model_levels:
            p_vals = pressure_data.get(lev)
            c_vals = clc_data.get(lev)
            if p_vals is None or c_vals is None:
                continue
            if pt_idx >= len(p_vals) or pt_idx >= len(c_vals):
                continue
            p_val = p_vals[pt_idx]
            c_val = c_vals[pt_idx]
            if p_val is not None and c_val is not None:
                profile.append((p_val, c_val))

        if not profile:
            continue

        # Sort by pressure descending (surface/high-pressure first)
        profile.sort(key=lambda x: -x[0])

        # Find contiguous cloud layers, then classify into ICAO bands
        cloud_layers: list[tuple[float, float]] = []  # (base_pa, top_pa)
        in_cloud = False
        base_pa = 0.0
        top_pa = 0.0

        for p_pa, clc in profile:
            if clc >= clc_threshold:
                if not in_cloud:
                    base_pa = p_pa
                    in_cloud = True
                top_pa = p_pa  # keep updating top (lower pressure)
            else:
                if in_cloud:
                    cloud_layers.append((base_pa, top_pa))
                    in_cloud = False
        if in_cloud:
            cloud_layers.append((base_pa, top_pa))

        if not cloud_layers:
            continue

        # Classify each cloud layer into ICAO bands.
        # When multiple disjoint clouds overlap a band, pick the lowest
        # (highest base pressure) — most relevant for flight altitude.
        for band_name, band_min_pa, band_max_pa in [
            ("low",  LOW_TOP_PA, float("inf")),
            ("mid",  MID_TOP_PA, LOW_TOP_PA),
            ("high", 0,          MID_TOP_PA),
        ]:
            best_base_pa: float | None = None
            best_top_pa: float | None = None
            for cl_base_pa, cl_top_pa in cloud_layers:
                # Clamp to band boundaries
                clamped_base = min(cl_base_pa, band_max_pa) if band_max_pa != float("inf") else cl_base_pa
                clamped_top = max(cl_top_pa, band_min_pa)
                if clamped_base <= clamped_top:
                    continue  # no overlap with this band
                # Pick the lowest cloud (highest base pressure = lowest altitude)
                if best_base_pa is None or clamped_base > best_base_pa:
                    best_base_pa = clamped_base
                    best_top_pa = clamped_top

            if best_base_pa is not None and best_top_pa is not None:
                results[pt_idx][f"{band_name}_base_ft"] = round(
                    pressure_pa_to_altitude_ft(best_base_pa),
                )
                results[pt_idx][f"{band_name}_top_ft"] = round(
                    pressure_pa_to_altitude_ft(best_top_pa),
                )

    return results


def _old_decode_icon_eu_per_point_chunked(
    var_bytes: dict[str, bytes],
    latitudes: list[float],
    longitudes: list[float],
    target_pressures_hpa: list[int] | None = None,
) -> tuple[list[dict[int, dict[str, float]]], list[dict[str, float]]]:
    """Decode ICON-EU model-level GRIB2 per-variable to limit peak memory.

    Instead of decoding all variables at once (~800MB), this processes one
    variable at a time (~270MB peak), keeping only the small interpolated
    point values between steps.

    Args:
        var_bytes: {variable_name: grib_bytes} — one entry per variable
            (e.g. "p", "qc", "qi", "clc"). Each is concatenated GRIB2 for
            all model levels of that variable.
        latitudes: Target latitudes for interpolation.
        longitudes: Target longitudes for interpolation.
        target_pressures_hpa: Target pressure levels in hPa.

    Returns:
        Tuple of:
        - List of dicts (one per point): [{pressure_hpa: {field: value}}, ...]
        - List of dicts (one per point): CLC-derived cloud layer boundaries
          with keys like ``low_base_ft``, ``low_top_ft``, etc.
    """
    import gc

    from weatherbrief.fetch.grib.icon_eu_levels import (
        TARGET_PRESSURE_LEVELS_HPA,
        bounds_for_field,
    )

    interpolate_model_to_pressure_levels = _old_interpolate_model_to_pressure_levels

    if target_pressures_hpa is None:
        target_pressures_hpa = TARGET_PRESSURE_LEVELS_HPA

    n_points = len(latitudes)

    # Step 1: Decode P (pressure) variable — needed for vertical interpolation.
    # pop() (not get()) so the dict releases the compressed bytes as each
    # variable is consumed — otherwise the whole ~260 MB/hour input set stays
    # resident and the later `del` frees nothing. (#441 efficiency)
    p_bytes = var_bytes.pop("p", b"")
    pressure_data = dec._decode_icon_eu_single_var(p_bytes, latitudes, longitudes)
    del p_bytes
    gc.collect()

    empty_clc_layers: list[dict[str, float]] = [{} for _ in range(n_points)]

    if not pressure_data:
        dec.logger.warning("No pressure (P) data found in ICON-EU GRIB — cannot interpolate")
        return [{} for _ in range(n_points)], empty_clc_layers

    model_levels = sorted(pressure_data.keys())
    results: list[dict[int, dict[str, float]]] = [{} for _ in range(n_points)]
    clc_cloud_layers = empty_clc_layers

    # Step 2: Decode each variable one at a time, interpolate, discard.
    # Cloud variables use final field names; sounding variables use raw_ prefix
    # for later unit conversion via _convert_raw_sounding().
    for var_name, field_key in (
        ("qc", "cloud_liquid_water_kg_kg"),
        ("qi", "ice_mixing_ratio_kg_kg"),
        # Precipitating species (#530) — ICON-D2 only; absent bytes are simply
        # skipped below, so an ICON-EU column produces None for all three.
        ("qr", "rain_water_kg_kg"),
        ("qs", "snow_water_kg_kg"),
        ("qg", "graupel_water_kg_kg"),
        ("clc", "cloud_area_fraction_pct"),
        ("t", "raw_temperature_k"),
        ("qv", "raw_specific_humidity_kg_kg"),
        ("u", "raw_u_wind_m_s"),
        ("v", "raw_v_wind_m_s"),
        ("w", "raw_w_m_s"),
    ):
        raw = var_bytes.pop(var_name, b"")  # release bytes as consumed (#441)
        if not raw:
            continue
        field_data = dec._decode_icon_eu_single_var(raw, latitudes, longitudes)
        del raw
        gc.collect()

        if not field_data:
            continue

        for pt_idx in range(n_points):
            model_pressures: list[float] = []
            model_values: list[float] = []

            for lev in model_levels:
                p_vals = pressure_data.get(lev)
                f_vals = field_data.get(lev)
                if p_vals is None or f_vals is None:
                    continue
                if pt_idx >= len(p_vals) or pt_idx >= len(f_vals):
                    continue
                p_val = p_vals[pt_idx]
                f_val = f_vals[pt_idx]
                if p_val is not None and f_val is not None:
                    model_pressures.append(p_val)
                    model_values.append(f_val)

            if len(model_pressures) < 2:
                continue

            interp_result = interpolate_model_to_pressure_levels(
                model_pressures, model_values, target_pressures_hpa,
                bounds=bounds_for_field(field_key),
            )
            for p_hpa, val in interp_result.items():
                results[pt_idx].setdefault(p_hpa, {})[field_key] = val

        # Derive cloud layer boundaries from model-level CLC before discarding
        if var_name == "clc":
            clc_cloud_layers = _old_derive_clc_cloud_layers(
                pressure_data, field_data, n_points,
            )

        del field_data
        gc.collect()

    return results, clc_cloud_layers


def _old_decode_ecmwf_pressure_direct(
    file_path: Path,
    latitudes: list[float],
    longitudes: list[float],
) -> tuple[list[dict[int, dict[str, float]]], list[bool]]:
    """eccodes body of ``decode_ecmwf_pressure_per_point``."""
    n_points = len(latitudes)
    results: list[dict[int, dict[str, float]]] = [{} for _ in range(n_points)]
    covered: list[bool] = [False] * n_points
    if n_points == 0:
        return results, covered

    def select(msg) -> bool:
        return (
            msg.type_of_level == "isobaricInhPa"
            and msg.var.lower() in dec._ECMWF_FULL_VAR_MAP
        )

    series = dec._gather_series(file_path, latitudes, longitudes, select, bridge_seams=True)
    seams = dec._series_seams(series, latitudes, longitudes)
    for si, s in enumerate(series):
        if len(s.levels) < 2:
            dec.logger.debug("skip %s: single pressure level %s", s.var, list(s.levels))
            continue
        if not s.single_header():
            dec.logger.debug("skip %s: more than one step/member per level", s.var)
            continue
        var_lower = s.var.lower()
        field_name = dec._ECMWF_FULL_VAR_MAP[var_lower]
        scale = 100.0 if var_lower in dec._ECMWF_FRAC_TO_PCT else 1.0
        inb_idx = s.bw.inb_idx
        for lev in sorted(s.levels, reverse=True):
            p_hpa = int(lev)
            dec._add_seam_level(seams, si, s, lev, (p_hpa, field_name, scale))
            row = s.level_values(lev)
            if row is None:
                continue
            for k, pt_idx in enumerate(inb_idx):
                v = float(row[k])
                if math.isnan(v):
                    continue
                level_fields = results[pt_idx].setdefault(p_hpa, {})
                if field_name in level_fields:
                    continue  # first grid wins
                level_fields[field_name] = v * scale
                covered[pt_idx] = True

    # Seam points are outside every grid, so nothing above has filled them;
    # drain_into keeps first-wins explicit all the same.
    if seams is not None:
        seams.drain_into(results, covered, "pressure-level")

    # Mirror the cfgrib path: a level dict is only created when it gets a value.
    return results, covered


def _old_decode_ecmwf_surface_direct(
    file_path: Path,
    latitudes: list[float],
    longitudes: list[float],
) -> tuple[list[dict[str, float]], list[bool]]:
    """eccodes body of ``decode_ecmwf_surface_per_point``."""
    n_points = len(latitudes)
    results: list[dict[str, float]] = [{} for _ in range(n_points)]
    covered: list[bool] = [False] * n_points
    if n_points == 0:
        return results, covered

    def select(msg) -> bool:
        return msg.var.lower() in dec._ECMWF_CLOUD_DIAG_FIELD_MAP

    series = dec._gather_series(file_path, latitudes, longitudes, select, bridge_seams=True)
    # Surface fields: two-sided seam points only (#679). A held value would
    # replace Open-Meteo's correctly placed ECMWF surface value with one from
    # the edge row ~42 km south, so it is not served at all.
    seams = dec._series_seams(
        series, latitudes, longitudes, dec._ecmwf_seam_sentinels(), fill_held=False,
    )
    for si, s in enumerate(series):
        # The surface decoder interpolated 2-D fields only: a series with more
        # than one level or step was a 3-D cfgrib variable and yielded nothing.
        if len(s.levels) != 1 or not s.single_header():
            dec.logger.debug("skip %s: not a single 2-D field", s.var)
            continue
        field_name = dec._ECMWF_CLOUD_DIAG_FIELD_MAP[s.var.lower()]
        lev = next(iter(s.levels))
        dec._add_seam_level(seams, si, s, lev, field_name)
        row = s.level_values(lev)
        if row is None:
            continue
        for k, pt_idx in enumerate(s.bw.inb_idx):
            v = float(row[k])
            if math.isnan(v) or field_name in results[pt_idx]:
                continue
            results[pt_idx][field_name] = v
            covered[pt_idx] = True

    if seams is not None:
        seams.drain_into(results, covered, "surface")

    return results, covered


# ---------------------------------------------------------------------------
# Strict comparison
# ---------------------------------------------------------------------------

def _assert_identical(old, new, path: str = "result") -> None:
    """Deep equality that also pins types, key order and float bit patterns."""
    assert type(old) is type(new), f"{path}: {type(old).__name__} != {type(new).__name__}"
    if isinstance(old, dict):
        assert list(old) == list(new), f"{path}: keys {list(old)} != {list(new)}"
        assert [type(k) for k in old] == [type(k) for k in new], f"{path}: key types"
        for k in old:
            _assert_identical(old[k], new[k], f"{path}[{k!r}]")
    elif isinstance(old, (list, tuple)):
        assert len(old) == len(new), f"{path}: length {len(old)} != {len(new)}"
        for i, (a, b) in enumerate(zip(old, new)):
            _assert_identical(a, b, f"{path}[{i}]")
    elif isinstance(old, float):
        same_bits = struct.pack("<d", old) == struct.pack("<d", new)
        assert same_bits or (math.isnan(old) and math.isnan(new)), f"{path}: {old!r} != {new!r}"
    else:
        assert old == new, f"{path}: {old!r} != {new!r}"


def _assert_same_outcome(old_fn, new_fn, *args) -> None:
    """Both return identical results, or both raise the same exception type.

    Non-physical inputs (a negative pressure) can make the pre-#704 code raise
    — e.g. ``round(nan)`` in the CLC layer heights; the new code must too.
    """
    try:
        old = old_fn(*args)
    except Exception as exc:  # noqa: BLE001 — the type is what is compared
        with pytest.raises(type(exc)):
            new_fn(*args)
        return
    _assert_identical(old, new_fn(*args))


# ---------------------------------------------------------------------------
# ICON synthetic columns
# ---------------------------------------------------------------------------

def _icon_columns(rng, n_points: int, n_levels: int, *, messy: bool):
    """``(levels, pressure_data, field_data)`` shaped like the ICON decoder's output.

    Pressure rises with model level as in a real column. ``messy`` mixes in
    None, NaN, inf, non-positive and repeated pressures, missing and short
    level columns, and a column whose level order is shuffled.
    """
    levels = sorted(rng.choice(np.arange(1, 91), size=n_levels, replace=False).tolist())
    p = np.sort(rng.uniform(1_500.0, 104_000.0, size=(n_points, n_levels)), axis=1)
    scale = 10.0 ** rng.integers(-6, 3)
    v = rng.normal(0.0, 1.0, size=(n_points, n_levels)) * scale
    if rng.random() < 0.3:
        v = np.abs(v) * rng.choice([-1.0, 1.0])  # whole column one-signed: clamp paths
    pressure: dict[int, list] = {}
    field: dict[int, list] = {}
    for li, lev in enumerate(levels):
        pressure[lev] = [float(x) for x in p[:, li]]
        field[lev] = [float(x) for x in v[:, li]]
    if not messy or n_points == 0:
        return levels, pressure, field

    for col in list(pressure.values()) + list(field.values()):
        for i in range(n_points):
            u = rng.random()
            if u < 0.08:
                col[i] = None
            elif u < 0.10:
                col[i] = float("nan")
            elif u < 0.11:
                col[i] = float(rng.choice([np.inf, -np.inf]))
            elif u < 0.115:
                col[i] = -0.0
    for col in pressure.values():
        for i in range(n_points):
            u = rng.random()
            if u < 0.02:
                col[i] = float(rng.choice([0.0, -500.0]))
    # Repeated pressure in one column (non-physical, takes the scalar fallback).
    if n_levels >= 2 and rng.random() < 0.4:
        i = int(rng.integers(n_points))
        a, b = rng.choice(n_levels, size=2, replace=False)
        pressure[levels[b]][i] = pressure[levels[a]][i]
    # A column whose levels are out of pressure order.
    if rng.random() < 0.4:
        i = int(rng.integers(n_points))
        vals = [pressure[lev][i] for lev in levels]
        rng.shuffle(vals)
        for lev, x in zip(levels, vals):
            pressure[lev][i] = x
    # A level missing from the field, and a short column.
    if n_levels >= 3 and rng.random() < 0.5:
        del field[levels[int(rng.integers(n_levels))]]
    if rng.random() < 0.3:
        lev = levels[int(rng.integers(n_levels))]
        target = pressure if rng.random() < 0.5 else field
        if lev in target:
            target[lev] = target[lev][: int(rng.integers(0, n_points + 1))]
    # A value exactly on a target level, and an all-None column.
    if rng.random() < 0.3:
        i = int(rng.integers(n_points))
        col = pressure[levels[int(rng.integers(n_levels))]]
        if i < len(col):
            col[i] = 70_000.0
    if rng.random() < 0.2:
        lev = levels[int(rng.integers(n_levels))]
        if lev in field:
            field[lev] = [None] * n_points
    return levels, pressure, field


_TARGET_SETS = [
    TARGET_PRESSURE_LEVELS_HPA,
    [700],
    [1, 5, 1050, 1100],                  # all out of range
    [1100, 500, 850, 10, 700, 300, 925],  # unsorted, both ends out of range
    [500, 500, 850],                      # repeated target
    [],
]
_BOUNDS = [None, (0.0, None), (0.0, 100.0), (None, 0.5), (-1.0, 1.0)]


def _matrix(level_data, levels, n_points):
    return dec._icon_level_matrix(level_data, levels, n_points)


@pytest.mark.parametrize("seed", range(40))
def test_columns_interp_matches_scalar(seed):
    rng = np.random.default_rng(seed)
    for _ in range(25):
        n_points = int(rng.choice([0, 1, 2, 7, 30]))
        n_levels = int(rng.choice([1, 2, 3, 5, 20, 65]))
        levels, pressure, field = _icon_columns(
            rng, n_points, n_levels, messy=bool(rng.random() < 0.8),
        )
        targets = _TARGET_SETS[int(rng.integers(len(_TARGET_SETS)))]
        bounds = _BOUNDS[int(rng.integers(len(_BOUNDS)))]
        new = interpolate_columns_to_pressure_levels(
            _matrix(pressure, levels, n_points), _matrix(field, levels, n_points),
            targets, bounds,
        )
        # The scalar function on each column gathered the pre-#704 way.
        old = []
        for pt in range(n_points):
            mp, mv = [], []
            for lev in levels:
                pv, fv = pressure.get(lev), field.get(lev)
                if pv is None or fv is None or pt >= len(pv) or pt >= len(fv):
                    continue
                if pv[pt] is not None and fv[pt] is not None:
                    mp.append(pv[pt])
                    mv.append(fv[pt])
            old.append(
                _old_interpolate_model_to_pressure_levels(mp, mv, targets, bounds)
                if len(mp) >= 2 else {}
            )
        _assert_identical(old, new)


def test_columns_interp_dense_targets_on_a_sparse_column():
    """More in-range targets than valid levels: the per-target fallback."""
    p = np.array([[30_000.0, np.nan, 95_000.0]])
    v = np.array([[1.0, 7.0, -2.0]])
    targets = list(range(300, 951, 25))
    new = interpolate_columns_to_pressure_levels(p, v, targets, None)
    old = [_old_interpolate_model_to_pressure_levels([30_000.0, 95_000.0], [1.0, -2.0], targets)]
    _assert_identical(old, new)
    assert len(new[0]) == len(targets)


def test_columns_interp_empty_shapes():
    assert interpolate_columns_to_pressure_levels(np.empty((0, 5)), np.empty((0, 5))) == []
    assert interpolate_columns_to_pressure_levels(np.ones((2, 1)), np.ones((2, 1))) == [{}, {}]


def _fake_single_var(monkeypatch, store: dict[bytes, dict]):
    monkeypatch.setattr(dec, "_decode_icon_eu_single_var", lambda b, lat, lon: store.get(b, {}))


@pytest.mark.parametrize("seed", range(12))
def test_icon_chunked_matches_old(monkeypatch, seed):
    rng = np.random.default_rng(1000 + seed)
    n_points = int(rng.choice([0, 1, 3, 25]))
    n_levels = int(rng.choice([2, 10, 65]))
    levels, pressure, _ = _icon_columns(rng, n_points, n_levels, messy=bool(seed % 3))
    store: dict[bytes, dict] = {b"p": pressure}
    var_bytes: dict[str, bytes] = {"p": b"p"}
    for var in ("qc", "qi", "qr", "clc", "t", "qv", "u", "v", "w"):
        if rng.random() < 0.15:
            continue  # variable not delivered
        _, _, field = _icon_columns(rng, n_points, n_levels, messy=bool(seed % 3))
        # Re-key onto the pressure levels (a field may still lack some).
        field = {lev: col for lev, col in zip(levels, field.values())}
        if var == "clc":
            for col in field.values():
                for i, x in enumerate(col):
                    if x is not None and math.isfinite(x):
                        col[i] = float(abs(x) % 100.0)
        store[var.encode()] = field
        var_bytes[var] = var.encode()
    _fake_single_var(monkeypatch, store)
    lats = [50.0] * n_points
    lons = [8.0] * n_points

    _assert_same_outcome(
        lambda: _old_decode_icon_eu_per_point_chunked(dict(var_bytes), lats, lons),
        lambda: dec.decode_icon_eu_per_point_chunked(dict(var_bytes), lats, lons),
    )


def test_icon_chunked_without_pressure(monkeypatch):
    _fake_single_var(monkeypatch, {})
    old = _old_decode_icon_eu_per_point_chunked({"qc": b"x"}, [50.0], [8.0])
    new = dec.decode_icon_eu_per_point_chunked({"qc": b"x"}, [50.0], [8.0])
    _assert_identical(old, new)


@pytest.mark.parametrize("seed", range(30))
def test_clc_cloud_layers_matches_old(seed):
    rng = np.random.default_rng(2000 + seed)
    n_points = int(rng.choice([0, 1, 4, 40]))
    n_levels = int(rng.choice([1, 2, 12, 65]))
    levels, pressure, clc = _icon_columns(rng, n_points, n_levels, messy=bool(seed % 2))
    for col in clc.values():
        for i, x in enumerate(col):
            if x is not None and math.isfinite(x):
                col[i] = float(abs(x) * 1e6 % 100.0)
    # Keep the pre-#704 contract: the pressure dict may hold levels clc lacks.
    _assert_same_outcome(
        _old_derive_clc_cloud_layers, dec._derive_clc_cloud_layers, pressure, clc, n_points,
    )


# ---------------------------------------------------------------------------
# ECMWF synthetic series
# ---------------------------------------------------------------------------

class _SeamRecorder:
    """Stand-in seam accumulator: records the calls each decoder makes."""

    def __init__(self, active: set[int]) -> None:
        self.active = active
        self.log: list = []

    def has(self, ds_idx: int) -> bool:
        return ds_idx in self.active

    def add(self, ds_idx: int, key, values) -> None:
        self.log.append(("add", ds_idx, key, sorted(values.rows)))

    def drain_into(self, results, covered, label) -> None:
        import copy

        self.log.append(("drain", copy.deepcopy(results), list(covered), label))
        # Exercise the hand-over: a seam value lands after the grid loop.
        if results:
            results[-1].setdefault("seam", 1.5)
            covered[-1] = True


def _row(rng, n: int) -> np.ndarray:
    row = rng.normal(0.0, 1.0, size=n) * 10.0 ** rng.integers(-4, 4)
    u = rng.random(n)
    row[u < 0.15] = np.nan
    row[(u >= 0.15) & (u < 0.17)] = -0.0
    row[(u >= 0.17) & (u < 0.18)] = np.inf
    return row


def _series(rng, n_points: int, var_names: list[str], level_pool: list[float], *, surface: bool):
    """Random ``_Series`` list: several grids, shared vars, skippable series."""
    out = []
    n_grids = int(rng.integers(1, 4))
    header = ("20261008", 0, "fc")
    for _ in range(int(rng.integers(0, 12))):
        var = str(rng.choice(var_names))
        if rng.random() < 0.2:
            var = var.upper()
        rank = int(rng.integers(n_grids))
        k = int(rng.integers(0, n_points + 1))
        inb = np.sort(rng.choice(n_points, size=k, replace=False)).astype(np.intp)
        level_type = "surface" if surface else "isobaricInhPa"
        s = dec._Series(var, level_type, 0, rank, SimpleNamespace(inb_idx=inb), None)
        if surface:
            n_lev = 1 if rng.random() < 0.85 else 2
        else:
            n_lev = 1 if rng.random() < 0.1 else int(rng.integers(2, len(level_pool) + 1))
        for lev in rng.choice(level_pool, size=n_lev, replace=False).tolist():
            gathered = None if (rng.random() < 0.1 or not k) else _row(rng, k)
            s.levels[float(lev)] = [(header, gathered)]
            if rng.random() < 0.05:
                s.levels[float(lev)].append((("20261008", 3, "fc"), gathered))  # 2 steps: skipped
            if rng.random() < 0.3:
                s.edges[float(lev)] = {0: np.zeros(3), 5: np.ones(3)}
        out.append(s)
    return out


def _run_both(monkeypatch, series, n_points, old_fn, new_fn, active):
    recorders = []

    def fake_seams(*args, **kwargs):
        rec = _SeamRecorder(active) if active is not None else None
        recorders.append(rec)
        return rec

    monkeypatch.setattr(dec, "_gather_series", lambda *a, **k: series)
    monkeypatch.setattr(dec, "_series_seams", fake_seams)
    lats = [50.0] * n_points
    lons = [5.0] * n_points
    old = old_fn(None, lats, lons)
    new = new_fn(None, lats, lons)
    _assert_identical(old, new)
    assert len(recorders) in (0, 2)  # n_points == 0 returns before the seams
    if active is not None and recorders:
        _assert_identical(recorders[0].log, recorders[1].log, "seam log")
    return new


_PRESSURE_VARS = sorted(dec._ECMWF_FULL_VAR_MAP)
# 850.5 shares int(lev) with 850: two levels landing on one p_hpa key.
_PRESSURE_LEVELS = [1000.0, 925.0, 850.0, 850.5, 700.0, 500.0, 300.0, 50.0, 1.0]


@pytest.mark.parametrize("seed", range(60))
def test_ecmwf_pressure_matches_old(monkeypatch, seed):
    rng = np.random.default_rng(3000 + seed)
    n_points = int(rng.choice([0, 1, 2, 9, 40]))
    series = _series(rng, n_points, _PRESSURE_VARS, _PRESSURE_LEVELS, surface=False)
    active = None if seed % 3 == 0 else set(range(0, 12, 2))
    _run_both(
        monkeypatch, series, n_points,
        _old_decode_ecmwf_pressure_direct, dec._decode_ecmwf_pressure_direct, active,
    )


@pytest.mark.parametrize("seed", range(60))
def test_ecmwf_surface_matches_old(monkeypatch, seed):
    rng = np.random.default_rng(4000 + seed)
    n_points = int(rng.choice([0, 1, 2, 9, 40]))
    var_names = sorted(dec._ECMWF_CLOUD_DIAG_FIELD_MAP)
    series = _series(rng, n_points, var_names, [0.0, 2.0], surface=True)
    active = None if seed % 3 == 0 else {1, 3, 5}
    _run_both(
        monkeypatch, series, n_points,
        _old_decode_ecmwf_surface_direct, dec._decode_ecmwf_surface_direct, active,
    )


def test_ecmwf_pressure_first_grid_wins_and_scales_cc(monkeypatch):
    """Hand-built: two grids share a point; cc is ×100; NaN never lands."""
    inb_a = np.array([0, 1], dtype=np.intp)
    inb_b = np.array([1, 2], dtype=np.intp)
    a = dec._Series("cc", "isobaricInhPa", 0, 0, SimpleNamespace(inb_idx=inb_a), None)
    b = dec._Series("cc", "isobaricInhPa", 0, 1, SimpleNamespace(inb_idx=inb_b), None)
    h = ("d", 0)
    a.levels = {850.0: [(h, np.array([0.25, np.nan]))], 700.0: [(h, np.array([0.5, 0.75]))]}
    b.levels = {850.0: [(h, np.array([0.1, 0.2]))], 700.0: [(h, np.array([0.3, 0.4]))]}
    got = _run_both(
        monkeypatch, [a, b], 3,
        _old_decode_ecmwf_pressure_direct, dec._decode_ecmwf_pressure_direct, None,
    )
    results, covered = got
    cc = "cloud_area_fraction_pct"
    assert results[0] == {850: {cc: 25.0}, 700: {cc: 50.0}}
    # Point 1: grid A wins at 700, grid B fills its NaN at 850 — level order
    # follows first insertion (700 from A before 850 from B).
    assert list(results[1]) == [700, 850]
    assert results[1][850][cc] == 10.0
    assert covered == [True, True, True]
