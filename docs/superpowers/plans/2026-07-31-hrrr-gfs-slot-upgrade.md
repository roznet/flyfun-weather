# HRRR full-sounding upgrade of the `gfs` slot — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Source the `gfs` model slot from HRRR (full sounding replacement) when the whole route fits the HRRR CONUS Lambert grid and a covering run exists; plain GFS otherwise; badged `GFS (HRRR)` (issue #457).

**Architecture:** New `fetch/grib/hrrr_fetch.py` (URLs, run selection, Lambert domain gate, idx sets) + Lambert branch in the shared decode interpolation helpers (pyproj projection of route points onto the grid's x/y axes) + ECMWF-shaped replacement flow gated at the top of `_enrich_gfs_inner`, with whole-slot fallback to plain GFS. Spec: `docs/superpowers/specs/2026-07-31-hrrr-gfs-slot-upgrade-design.md`.

**Tech Stack:** Python 3.12, cfgrib/eccodes, pyproj (promoted to declared dep), xarray/numpy, pytest.

## Global Constraints

- Independent implementation on branch `impl-457-hrrr` (base `42e24645`, pre-#508). Never read the merged #508 code.
- URL layout (verified 2026-07-31): `https://noaa-hrrr-bdp-pds.s3.amazonaws.com/hrrr.{YYYYMMDD}/conus/hrrr.t{HH:02d}z.wrfprsf{FF:02d}.grib2[.idx]` — flat `conus/` dir, 2-digit fhour, `anl` at f00.
- Grid (verified from a real wrfprs message): `gridType=lambert`, Nx=1799, Ny=1059, LaD=38.5, LoV=262.5, Latin1=Latin2=38.5, Dx=Dy=3000 m, first point (21.138123°N, 237.280472°E), `jScansPositively=1`, **`uvRelativeToGrid=1`** (winds grid-relative → rotation mandatory), sphere R=6371229 m.
- Cycles: hourly; 00/06/12/18z → 48h horizon, others → 18h. Publish delay ≈ 1h; probe the last-needed fhour's idx.
- House rules: `None ≠ 0`; total-HRRR-failure → whole-slot plain-GFS fallback, never a half-HRRR pack; no GFS averaged-window machinery on HRRR (all fields instantaneous); CIN internal convention is NEGATIVE; badge is mandatory.
- Commits: TWO — commit 1 = Tasks 1–4 (+ Task 6 wiring), commit 2 = Task 5 (+ Task 7 docs).

---

### Task 1: `hrrr_fetch.py` + idx parametrization

**Files:**
- Create: `src/weatherbrief/fetch/grib/hrrr_fetch.py`
- Modify: `src/weatherbrief/fetch/grib/gfs_idx.py` (parametrize variable sets)
- Test: `tests/test_hrrr_fetch.py`

**Interfaces:**
- Produces: `hrrr_grib2_url(init_date, init_hour, fhour) -> str`, `hrrr_idx_url(...) -> str`, `find_latest_hrrr_run(target_time, cover_until=None, as_of_time=None, session=None) -> tuple[str, int] | None`, `route_in_hrrr_domain(route_points) -> bool`, `hrrr_window_hours(init_date, init_hour, departure_time, flight_duration_hours) -> list[int]`, `HRRR_SOUNDING_VARIABLES: dict[str, set[str]]`, `HRRR_DIAG_VARIABLES: dict[str, set[str]]`, `HRRR_GRID` (NamedTuple: nx, ny, dx, dy, x0, y0, lat0, lon0, lad, lov, latin1, latin2).
- Consumes: `gfs_idx` parse/plan helpers (parametrized), `grib_fetch.fetch_idx`-style GET, pyproj.

Module constants (verified 2026-07-31, real wrfprs message):

```python
HRRR_S3_BASE = "https://noaa-hrrr-bdp-pds.s3.amazonaws.com"
HRRR_PUBLISH_DELAY_HOURS = 1.0
HRRR_EXTENDED_CYCLES = frozenset({0, 6, 12, 18})  # 48h; all other hourly cycles 18h
HRRR_HORIZON_LONG_H = 48
HRRR_HORIZON_SHORT_H = 18

class HrrrGrid(NamedTuple):
    nx: int; ny: int; dx: float; dy: float
    lat0: float; lon0: float          # first grid point (SW corner)
    lad: float; lov: float; latin1: float; latin2: float

HRRR_GRID = HrrrGrid(
    nx=1799, ny=1059, dx=3000.0, dy=3000.0,
    lat0=21.138123, lon0=237.280472,
    lad=38.5, lov=262.5, latin1=38.5, latin2=38.5,
)

def hrrr_projection() -> "pyproj.Proj":
    import pyproj
    return pyproj.Proj(
        proj="lcc", lat_0=HRRR_GRID.lad, lon_0=HRRR_GRID.lov,
        lat_1=HRRR_GRID.latin1, lat_2=HRRR_GRID.latin2,
        a=6371229.0, b=6371229.0,
    )
```

URL builders:

```python
def hrrr_grib2_url(init_date: str, init_hour: int, forecast_hour: int) -> str:
    return (
        f"{HRRR_S3_BASE}/hrrr.{init_date}/conus/"
        f"hrrr.t{init_hour:02d}z.wrfprsf{forecast_hour:02d}.grib2"
    )

def hrrr_idx_url(init_date: str, init_hour: int, forecast_hour: int) -> str:
    return hrrr_grib2_url(init_date, init_hour, forecast_hour) + ".idx"
```

Variable sets for the parametrized idx parsers (level strings are idx-verbatim):

```python
# Sounding (pressure-level) set — 40 levels, 25 hPa spacing, plus surface PRES.
HRRR_SOUNDING_VARIABLES: dict[str, set[str]] = {
    "TMP": {"mb"}, "DPT": {"mb"}, "RH": {"mb"},
    "UGRD": {"mb"}, "VGRD": {"mb"}, "VVEL": {"mb"},
    "HGT": {"mb"}, "CLMR": {"mb"}, "CIMIXR": {"mb"},
    "PRES": {"surface"},
}
# Diagnostics set (~15 MB/fhour).
HRRR_DIAG_VARIABLES: dict[str, set[str]] = {
    "LCDC": {"low cloud layer"},
    "MCDC": {"middle cloud layer"},
    "HCDC": {"high cloud layer"},
    "TCDC": {"entire atmosphere"},
    "HGT": {"cloud ceiling", "cloud base"},
    "CAPE": {"surface", "180-0 mb above ground"},
    "CIN": {"surface", "180-0 mb above ground"},
    "VIS": {"surface"},
    "GUST": {"surface"},
}
```

`gfs_idx.py` parametrization (defaults preserve GFS behaviour exactly):
- `parse_idx(text, variables=None)` — `variables` is the `GRIB_VARIABLES` set override.
- `parse_cloud_diag_idx(text, pairs=None, prefer_averaged=None)` — `pairs` is the `(var, level_str)` set override; `prefer_averaged` defaults to `_PREFER_AVERAGED_PAIRS`.
- `plan_byte_ranges(idx_text, target_levels=None, variables=None)` and
  `plan_cloud_diag_byte_ranges(idx_text, pairs=None, prefer_averaged=None)`.
- New: `plan_hrrr_sounding_byte_ranges(idx_text) -> list[CloudDiagByteRange]` and `plan_hrrr_diag_byte_ranges(idx_text) -> list[CloudDiagByteRange]` — both via the cloud-diag-style parser (HRRR levels are level-strings like "925 mb", parsed to hPa ints downstream by message decode; keep raw level strings here and let the decode group by them).

Run selection (hourly cycles, per-cycle horizon, progressive publication):

```python
def find_latest_hrrr_run(
    target_time, cover_until=None, as_of_time=None, session=None,
) -> tuple[str, int] | None:
    """Freshest HRRR cycle whose horizon covers the window and whose
    last-needed fhour's .idx is published (files appear progressively)."""
    sess = session or requests.Session()
    reference_time = as_of_time or datetime.now(timezone.utc)
    need_until = cover_until or target_time
    for days_back in range(2):
        check_date = reference_time - timedelta(days=days_back)
        for cycle in range(23, -1, -1):           # hourly, freshest first
            init_time = check_date.replace(hour=cycle, minute=0, second=0, microsecond=0)
            if init_time > reference_time:
                continue
            if (reference_time - init_time).total_seconds() / 3600 < HRRR_PUBLISH_DELAY_HOURS:
                continue
            horizon = HRRR_HORIZON_LONG_H if cycle in HRRR_EXTENDED_CYCLES else HRRR_HORIZON_SHORT_H
            if init_time + timedelta(hours=horizon) < need_until:
                continue
            # Probe the LAST-NEEDED fhour's idx, not f000 (progressive publication).
            last_needed = min(
                math.ceil((need_until - init_time).total_seconds() / 3600), horizon,
            )
            try:
                resp = sess.head(
                    hrrr_idx_url(check_date.strftime("%Y%m%d"), cycle, last_needed),
                    timeout=10,
                )
                if resp.status_code == 200:
                    return check_date.strftime("%Y%m%d"), cycle
            except requests.RequestException:
                continue
    return None
```

Domain gate — project route points onto the Lambert grid axes; exact, no bbox:

```python
def _grid_xy_axes() -> tuple["np.ndarray", "np.ndarray"]:
    """1-D projected x/y axes (m) of the HRRR grid, from the verified first point."""
    import numpy as np
    proj = hrrr_projection()
    x0, y0 = proj(HRRR_GRID.lon0, HRRR_GRID.lat0)
    return (
        x0 + np.arange(HRRR_GRID.nx) * HRRR_GRID.dx,
        y0 + np.arange(HRRR_GRID.ny) * HRRR_GRID.dy,
    )

def route_in_hrrr_domain(route_points: list) -> bool:
    """All-or-nothing: every route point projects inside the grid bounds."""
    from weatherbrief.fetch.grib.decode import _frac_grid_indices
    proj = hrrr_projection()
    x_axis, y_axis = _grid_xy_axes()
    for rp in route_points:
        x, y = proj(rp.lon, rp.lat)
        _, x_ok = _frac_grid_indices(x_axis, [x])
        _, y_ok = _frac_grid_indices(y_axis, [y])
        if not (bool(x_ok[0]) and bool(y_ok[0])):
            return False
    return True
```

`hrrr_window_hours(...)`: same shape as `compute_flight_window_hours` (GFS) but hourly to 48 (snap `round`, floor-hour inclusion).

- [ ] **Step 1: failing tests** — `tests/test_hrrr_fetch.py`: URL builders (2-digit fhour, flat conus); run selection (fake HEAD keyed on cycle/fhour: prefers freshest covering cycle, skips too-early cycles, walks back a day, probes last-needed not f000, returns None when nothing covers); domain gate (KDEN in, BREST out, Hawaii out, edge point on the CONUS border); window hours (hourly, round+floor).
- [ ] **Step 2: run tests → FAIL** (`pytest tests/test_hrrr_fetch.py -q`).
- [ ] **Step 3: implement** `hrrr_fetch.py` + `gfs_idx.py` parametrization.
- [ ] **Step 4: run tests → PASS**; also `pytest tests/test_gfs_idx.py tests/test_grib.py -q` (GFS defaults untouched).
- [ ] **Step 5: commit** `feat(fetch): hrrr_fetch — S3 URLs, run selection, Lambert domain gate, idx sets (#457)`.

---

### Task 2: Lambert decode branch + wind rotation + aliases

**Files:**
- Modify: `src/weatherbrief/fetch/grib/decode.py`
- Modify: `src/weatherbrief/fetch/grib/decode_worker.py`
- Test: `tests/test_hrrr_decode.py`

**Interfaces:**
- Produces: `decode_hrrr_pressure_per_point(grib_bytes, latitudes, longitudes) -> tuple[list[dict[int, dict[str, float]]], list[bool]]` (keyed by hPa), `decode_hrrr_diag_per_point(grib_bytes, latitudes, longitudes) -> list[dict[str, float]]`, `_rotate_grid_wind_to_earth(u, v, lons, lov_deg, cone_const) -> tuple[u_e, v_e]`, `_lcc_project_points(grid_attrs, lats, lons) -> tuple[xs, ys]`.
- Consumes: `_bilinear_grid_weights`, `_frac_grid_indices` (axis-agnostic), `hrrr_fetch.hrrr_projection`.

Core pieces:

```python
_HRRR_PRESSURE_VAR_MAP = {
    "t": "raw_temperature_k",
    "dpt": "raw_dewpoint_k",          # direct dewpoint — no Magnus derivation
    "r": "raw_relative_humidity_pct",
    "u": "raw_u_wind_m_s",            # grid-relative — rotated at decode
    "v": "raw_v_wind_m_s",
    "w": "raw_omega_pa_s",            # VVEL, already Pa/s — no −ρgw conversion
    "gh": "raw_geopotential_height_gpm",
    "clwmr": "cloud_liquid_water_kg_kg",   # cfgrib decodes CLMR as "clwmr" (verified)
    "clmr": "cloud_liquid_water_kg_kg",
    "cimixr": "ice_mixing_ratio_kg_kg",    # HRRR's ice name (verified in idx)
    "pres": "surface_pressure_pa",         # surface level only
    "sp": "surface_pressure_pa",
}

def _rotate_grid_wind_to_earth(u, v, lons_deg, lov_deg=262.5, cone_const=None):
    """Grid-relative → earth-relative for a Lambert conformal grid.

    α = (λ − LoV) · k per point (k = sin(φc) = sin 38.5° when Latin1 == Latin2,
    which HRRR satisfies). Rotation is linear, so it commutes with the
    bilinear gather — applied per route point after sampling.
    """
    import numpy as np
    k = cone_const if cone_const is not None else math.sin(math.radians(38.5))
    dl = np.deg2rad((np.asarray(lons_deg, dtype=np.float64) - lov_deg + 180.0) % 360.0 - 180.0)
    a = dl * k
    ca, sa = np.cos(a), np.sin(a)
    u = np.asarray(u, dtype=np.float64)
    v = np.asarray(v, dtype=np.float64)
    return u * ca - v * sa, u * sa + v * ca
```

Lambert branch in decode: a dataset is "projected" when it lacks 1D lat/lon dims but has `y`/`x` dims (cfgrib names for lambert). Build axes from the dataset's grid attrs (`GRIB_LaDInDegrees` etc. via `ds.attrs`/`eccodes` on the temp file — the verified key names are `LaDInDegrees`, `LoVInDegrees`, `Latin1InDegrees`, `Latin2InDegrees`, `DxInMetres`, `DyInMetres`, `latitudeOfFirstGridPointInDegrees`, `longitudeOfFirstGridPointInDegrees`), project targets, reuse `_bilinear_grid_weights(y_axis, x_axis, ys, xs)` (it is axis-agnostic), gather on `(y, x)`. Reuse the same NaN/missing semantics as the GFS path. `decode_worker.py` adds `decode_hrrr_pressure` / `decode_hrrr_diag` entries following `decode_icon_chunked` (bytes read inside the worker).

- [ ] **Step 1: failing tests** — rotation closed form (u=(1,0) at λ=LoV±10° → expected earth-relative via the α formula; α=0 at LoV → identity; sign check against a hand-computed case); synthetic 2-D (y,x) DataArray decode via the new branch (bilinear at fractional grid position); alias map (cimixr key present).
- [ ] **Step 2: run → FAIL.**
- [ ] **Step 3: implement** decode branch + rotation + workers.
- [ ] **Step 4: run → PASS**; `pytest tests/test_grib.py tests/test_icon_d2_explicit_conv.py -q` (no regressions in the shared helpers).
- [ ] **Step 5: commit** `feat(grib): Lambert-grid decode branch + earth-relative wind rotation for HRRR (#457)`.

---

### Task 3: HRRR diagnostics builder

**Files:**
- Modify: `src/weatherbrief/fetch/grib/decode.py`
- Test: `tests/test_hrrr_decode.py` (builder section)

**Interfaces:**
- Produces: `build_hrrr_cloud_diagnostics(raw: dict) -> NWPCloudDiagnostics | None`, `build_hrrr_surface_extras(raw: dict) -> dict[str, float | None]` (visibility_m, wind_gusts_10m_kt, cape_jkg, convective_inhibition_jkg).

Mapping (ECMWF shape — no per-band geometry, no convective-scheme fields):

```python
def build_hrrr_cloud_diagnostics(raw):
    # low/mid/high covers straight through (already %); total cover from TCDC.
    # ceiling_ft / low.base_ft from HGT:cloud ceiling / HGT:cloud base (m → ft).
    # ML CAPE/CIN from the "180-0 mb above ground" entries (mixed layer).
    # CIN: negate a positive magnitude into the internal NEGATIVE convention —
    # VERIFY the delivered sign on the golden sample before choosing; document.
    # No convective_base/top_ft, no convective_cover_pct, no convective precip.
```

- [ ] **Step 1: failing tests** — covers passthrough; ceiling/base m→ft; ML CAPE passthrough; CIN sign convention (positive input → negative output, sentinel ≥9998 → None); empty raw → None.
- [ ] **Step 2: run → FAIL.**
- [ ] **Step 3: implement** builders.
- [ ] **Step 4: run → PASS.**
- [ ] **Step 5: commit** `feat(grib): HRRR diagnostics builder (ECMWF-shaped NWPCloudDiagnostics) (#457)`.

---

### Task 4: Gate + patch-style enrichment + fallback + kill switch + sources (COMMIT 1)

**Files:**
- Modify: `src/weatherbrief/fetch/grib/__init__.py` (`_enrich_gfs`/`_enrich_gfs_inner`, `grib_sources` block ~line 2325, `gfs_init_dt` gating ~line 2336)
- Modify: `src/weatherbrief/fetch/grib/cache.py` (TTL entry)
- Modify: `src/weatherbrief/fetch/freshness/registry.py` (`hrrr:noaa` SourceConfig)
- Modify: `pyproject.toml` (pyproj dep)
- Modify: `web/ts/managers/briefing-ui.ts` (badge, 2 spots at ~470/524)
- Test: `tests/test_hrrr_gate.py`, `web/tests/unit/` badge test update

Behaviour:
- `_enrich_gfs_inner` top: if `WB_HRRR_ENABLED != "0"` and `route_in_hrrr_domain(route_points)` and `find_latest_hrrr_run(departure, cover_until=departure+duration)` → HRRR patch path this commit (Task 5 swaps it to replacement), returning `(ts, "hrrr:noaa")`; on total HRRR failure (no idx / no decoded hours) log + fall through to the plain-GFS path unchanged.
- `_enrich_gfs` returns `(ts, source_key)`; `_enrich_forecasts_inner` block becomes:
  `if gfs_ts is not None: grib_init_times["gfs"] = gfs_ts; grib_sources["gfs"] = gfs_source_key`
- `gfs_init_dt` (window-midpoint fill) set only when source is `gfs:noaa` — HRRR hours never see the averaged-window machinery.
- HRRR patch path (commit 1): per fhour — fetch idx → plan diag + CLMR/CIMIXR ranges → byte-range download → cache (`model="hrrr"`, `cache_key(fhour, "HRRR_DIAG"/"HRRR_CLMR")`) → pool decode → `build_hrrr_cloud_diagnostics` → `_apply_cloud_diagnostics_to_sections(..., "gfs", valid_utc)` + `_merge_cloud_water_into_sections` + surface extras (VIS/GUST/CAPE/CIN) onto matching hourlies.
- cache.py: `MODEL_TTL_SECONDS["hrrr"] = 6 * 3600`.
- registry: `hrrr:noaa` SourceConfig — cycles `(0, 6, 12, 18)`, horizon 48, delivery_offset 1.0h, model_label "HRRR", provider "NOAA", role "primary-sounding", readiness check + description per the SourceConfig pattern.
- Badge: generalize the two `primary.source === 'icon_d2:dwd'` ternaries to a small map: `{ 'icon_d2:dwd': '(D2)', 'hrrr:noaa': '(HRRR)' }` appended to `modelLabel(model)`.
- pyproject: `dependencies` += `"pyproj>=3.5"`.

- [ ] **Step 1: failing tests** — gate picks HRRR when domain+run fit (mocked run finder), picks GFS when outside domain / no run / kill switch; total-failure fallback calls plain GFS path; `grib_sources["gfs"]` records `hrrr:noaa`; `gfs_init_dt` is None when HRRR; TTL + registry entries; badge label map (vitest or the TS unit tests).
- [ ] **Step 2: run → FAIL.**
- [ ] **Step 3: implement** gate + patch path + wiring.
- [ ] **Step 4: run → PASS** (python + `cd web && npm run test -- --run` if configured, else `npx vitest run`).
- [ ] **Step 5: COMMIT 1** `feat(fetch): HRRR upgrade of the gfs slot on CONUS routes — gate, Lambert patch enrichment, badge, wiring (#457)`.

---

### Task 5: Full sounding replacement (COMMIT 2)

**Files:**
- Modify: `src/weatherbrief/fetch/grib/__init__.py` (swap the gated flow from patch to replacement)
- Modify: `src/weatherbrief/fetch/grib/fill.py` (verify/register the GFS-slot sounding interp covers HRRR-replaced hours)
- Test: `tests/test_hrrr_replacement.py`

Behaviour (ECMWF `_enrich_ecmwf_inner`-shaped):
- Per fhour: sounding-set idx plan → byte-range download (40 levels; measure MB, log) → pool decode (pressure per point, keyed by hPa) → `_replace_pressure_levels_from_grib(gfs_sections, all_forecasts, route_points, decoded_points, valid_utc, model_source=ModelSource.GFS)` — the builder already handles HGT-direct heights and DPT-direct dewpoint via `build_pressure_levels_from_grib`; verify `_convert_raw_sounding` accepts `raw_dewpoint_k` (add the direct-DPT branch if missing: dewpoint_c = raw_dewpoint_k − 273.15).
- Then the Task-4 diag pass applies diagnostics + surface extras on the same hourlies (unchanged).
- fill: HRRR-replaced hourlies carry 40 levels vs OM's 28 → the `_interp_levels_hourly` anchor heuristic (level_count) marks them anchors and linearly interpolates gap hours — verify with a test; register explicitly if the heuristic misses GFS sections.
- Fallback: if zero hours replaced → whole-slot plain GFS (same guard as Task 4).

- [ ] **Step 1: failing tests** — synthetic 2-level pressure bytes → replaced `pressure_levels` carry DPT-direct dewpoint + HGT-direct height + rotated winds; zero-coverage → fallback invoked; gap-hour sounding interp between two HRRR anchors.
- [ ] **Step 2: run → FAIL.**
- [ ] **Step 3: implement** replacement flow + DPT branch + fill registration.
- [ ] **Step 4: run → PASS**; full python suite green.
- [ ] **Step 5: COMMIT 2** `feat(fetch): HRRR full sounding replacement for the gfs slot (#457)`.

---

### Task 6: Golden decode test on a real wrfprs sample

**Files:**
- Create: `tests/test_hrrr_golden.py`
- Create: `tests/data/hrrr_samples/` (gitignored; download script committed)
- Modify: `.gitignore` (`tests/data/hrrr_samples/`)

Follows the `tests/test_ecmwf_sample.py` precedent (`skipif` when samples absent).
- Script `scripts/download_hrrr_samples.sh`: byte-ranges one f01 wrfprs (TMP+DPT+RH+UGRD+VGRD 925/850/700/500 mb, HGT cloud ceiling, REFC) into `tests/data/hrrr_samples/`.
- Golden: projected-grid interpolated TMP at KDEN vs nearest-neighbour readback (|Δ| < 0.5 K); rotated vs unrotated wind at a point ≥ 5° from LoV differs meaningfully and matches the α closed form; grid attrs on the sample match `HRRR_GRID`.

- [ ] **Step 1:** download script + sample present locally (skip in CI without it).
- [ ] **Step 2:** golden tests PASS locally, SKIP cleanly without the dir.

---

### Task 7: Docs + suite + PR

**Files:**
- Modify: `designs/weather-engine-specs.md` (HRRR section + field-attribution matrix)
- Modify: `designs/fetch.md` (HRRR fetch section)

- [ ] **Step 1:** write both doc sections (grid, gate, run selection, fetch sets with sizes measured live, decode/rotation, replacement flow, badge, kill switch, out-of-scope).
- [ ] **Step 2:** full suite `./.venv/bin/python -m pytest -q` green (only pre-existing failure allowed: `test_google_login_redirects`).
- [ ] **Step 3:** push `impl-457-hrrr` to fork; PR to `roznet/flyfun-weather:main` (cross-repo, head `downle:impl-457-hrrr`) with the independent-implementation framing (conflicts with merged #508 expected, presented for review like #471).

---

## Self-review log

- Spec coverage: gate/run-selection (T1), Lambert decode/rotation (T2), diagnostics (T3), patch enrichment + fallback + kill switch + badge + registry + pyproj (T4), full replacement + fill (T5), golden test (T6), docs + PR (T7). REFC/RETOP/LTNG consciously absent (spec out-of-scope).
- Type consistency: `_replace_pressure_levels_from_grib(model_source=ModelSource.GFS)` matches the existing signature; builder keys (`raw_dewpoint_k`, `raw_geopotential_height_gpm`) match the ECMWF/ICON raw-sounding contract; `grib_sources["gfs"]` key shape matches the `icon_d2:dwd` precedent consumed by `briefing-ui.ts`.
