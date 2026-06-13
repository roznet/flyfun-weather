# Full GRIB Sounding Plan — ICON-EU

**Status: IMPLEMENTED (this plan is now historical).** Full sounding replacement
shipped for BOTH ICON-EU (40 model levels) and ECMWF (25 pressure levels). The
durable design now lives in [weather-engine-specs.md](../weather-engine-specs.md)
(§B ECMWF + §C ICON-EU, both marked "IMPLEMENTED (full sounding)"). Read that doc,
not this one, for current truth. This file is kept only as a record of the original
plan and how the shipped code diverged from it — it should be archived.

**How the shipped code differs from the plan below:**
- No `icon_eu_sounding.py` module and no `build_icon_sounding()`. The conversion
  lives in `decode.py` as `build_pressure_levels_from_grib()` + `_convert_raw_sounding()`.
- `_replace_pressure_levels_from_grib()` (in `fetch/grib/__init__.py`) was built
  **model-agnostic** — it serves both ICON and ECMWF rather than ICON-specific.
- Variable maps are `_ICON_FULL_VAR_MAP` / `_ECMWF_FULL_VAR_MAP` in `decode.py`
  (using a `raw_` prefix convention), not the proposed `_SOUNDING_VAR_MAP`.
- ICON model levels are log-pressure **interpolated to standard pressure levels**
  (`EXTENDED_PRESSURE_LEVELS`, 28-level set), not kept on raw native levels — so the
  "pressure_hpa stays int / round native levels" discussion below was sidestepped.
- `w` (vertical velocity) was added beyond the plan's T/QV/U/V; geopotential is
  derived via the hypsometric equation from T+P (the plan's optional item).

Original goal: replace Open-Meteo pressure-level data with complete ICON-EU GRIB
soundings for higher vertical resolution (~500 ft vs ~3000 ft from Open-Meteo's 19
standard levels).

## Scope

ICON-EU only (40 model levels from DWD opendata). ECMWF full sounding replacement
is being built separately. Build this ICON-specific; generalize later if needed.

## Background

ICON-EU uses hybrid model levels (35-74), each with a different pressure at each
geographic point. We already fetch P, QC, QI, CLC from GRIB and interpolate to
Open-Meteo's 19 standard pressure levels. This loses resolution — e.g. a 100 hPa
gap between 800-700 hPa misses thin cloud layers that the native ~25 hPa model-level
spacing would catch.

The CLC cloud boundary derivation (added March 2026) proved the value: using 40
model levels directly gave ~500 ft cloud boundary precision vs ~3000 ft from
interpolated data.

### Current state (April 2026)

All three GRIB sources (GFS, ICON-EU, ECMWF) currently only enrich cloud fields
onto existing Open-Meteo pressure levels via the shared
`_merge_cloud_water_into_sections()`. No model replaces the full sounding today.

Current ICON-EU variables: `ICON_EU_VARIABLES = ("qc", "qi", "clc", "p")`

Current `_VAR_MAP` maps only cloud fields:
- `qc` → `cloud_liquid_water_kg_kg`
- `qi` → `ice_mixing_ratio_kg_kg`
- `clc` → `cloud_area_fraction_pct`

## Goal

When ICON-EU GRIB is available, use it as the primary source for pressure-level
data (T, humidity, wind, cloud water, cloud fraction). Fall back to Open-Meteo
when GRIB is unavailable (DWD retains data ~48h only).

Surface variables (2m temp, precipitation, visibility, CAPE, weather code, etc.)
continue to come from Open-Meteo regardless.

## Variables to Add

| Variable | GRIB shortName | Unit | Derived from |
|----------|---------------|------|-------------|
| Temperature | T | K | Convert to C |
| Specific humidity | QV | kg/kg | Compute RH, dewpoint |
| U-wind component | U | m/s | Compute speed + direction |
| V-wind component | V | m/s | Compute speed + direction |

Already fetched: P, QC, QI, CLC.

### Derived quantities to compute

```
RH = (QV / QV_sat(T, P)) * 100
    where QV_sat = 0.622 * e_sat / (P - e_sat)
    and e_sat = 611.2 * exp(17.67 * T_c / (T_c + 243.5))  [Buck eq.]

Dewpoint_C = (243.5 * ln(e/611.2)) / (17.67 - ln(e/611.2))
    where e = QV * P / (0.622 + QV)

Wind_speed_kt = sqrt(U^2 + V^2) * 1.94384
Wind_dir_deg = (270 - atan2(V, U) * 180/pi) % 360

Geopotential_height: use hypsometric equation layer-by-layer from surface P
    (or skip — only needed for Skew-T plotting, not core analysis)
```

## Key Design Decisions

### pressure_hpa stays `int` — round to nearest hPa

`PressureLevelData.pressure_hpa` is `int` throughout the codebase. Changing to
`float` would ripple through dict keys, JSON serialization, cross-section lookups,
and frontend code. Rounding to nearest integer hPa loses ~30 ft precision — negligible
for aviation. ICON model-level spacing is ~20-30 hPa in the troposphere, so rounded
values will mostly be unique.

### No shared "full sounding" abstraction yet

The current enrichment pipeline (`_merge_cloud_water_into_sections()`) only writes
cloud fields onto existing `PressureLevelData` objects. A new code path is needed
to *replace* the entire `pressure_levels` list on `HourlyForecast`. Build this
ICON-specific; if ECMWF full sounding replacement is needed later, factor out then.

### Memory: keep per-variable chunked decode

`decode_icon_eu_per_point_chunked()` processes one variable at a time (~270 MB peak)
and accumulates small per-point results. Adding T, QV, U, V doubles the variables
but fits the same pattern — accumulate raw model-level values across variable passes,
then build `PressureLevelData` objects from the accumulated result at the end.

## Download Impact

Current: 4 vars (P, QC, QI, CLC) x 40 levels = 160 files per forecast hour
New:     8 vars (+ T, QV, U, V) x 40 levels = 320 files per forecast hour

Each file is ~70 KB compressed (bz2), so ~22 MB per forecast hour.
Typical flight needs 2-4 forecast hours = 44-88 MB total.

The existing parallel downloader (`MAX_DOWNLOAD_WORKERS=8`) handles this fine.
Consider bumping to 12 workers if latency matters.

## Implementation Steps

### Phase 1: Fetch and decode additional GRIB variables

**File: `src/weatherbrief/fetch/grib/icon_eu_fetch.py`**
- Add "t", "qv", "u", "v" to `ICON_EU_VARIABLES`

**File: `src/weatherbrief/fetch/grib/decode.py`**
- These new variables should NOT go through `_VAR_MAP` or `interpolate_model_to_pressure_levels()`.
  `_VAR_MAP` maps to cloud field names for the cloud-only enrichment path. T/QV/U/V
  are raw inputs to derived-quantity computation and need to stay on native model
  levels (not interpolated to 19 standard levels).
- In `decode_icon_eu_per_point_chunked()`, handle the new variables with a separate
  mapping that preserves raw model-level values per point:
  ```
  _SOUNDING_VAR_MAP = {
      "t": "temperature_k",
      "qv": "specific_humidity_kg_kg",
      "u": "u_wind_ms",
      "v": "v_wind_ms",
  }
  ```
- Accumulate these alongside existing cloud fields in the per-point result dict,
  keyed by model level (not pressure level) until Phase 2 converts them.

### Phase 2: Compute derived quantities and build PressureLevelData

**New module: `src/weatherbrief/fetch/grib/icon_eu_sounding.py`**

```python
def build_icon_sounding(
    model_level_data: dict[int, dict[str, float]],
    # model_level → {temperature_k, specific_humidity_kg_kg, u_wind_ms,
    #                v_wind_ms, pressure_pa, cloud_liquid_water_kg_kg,
    #                ice_mixing_ratio_kg_kg, cloud_area_fraction_pct}
) -> list[PressureLevelData]:
    """Convert raw ICON model-level data to PressureLevelData list.

    For each model level with all required fields (T, QV, U, V, P):
    1. Convert T from K to C
    2. Compute RH from QV, T, P (Buck equation)
    3. Compute dewpoint from QV, P
    4. Compute wind speed (kt) and direction from U, V
    5. Round pressure to int hPa
    6. Carry through CLW, ice, CLC if present

    Returns one PressureLevelData per model level, sorted by descending
    pressure (surface first), with duplicate int pressures merged.
    """
```

Output `PressureLevelData` fields must match what Open-Meteo provides so downstream
code (`analyze_sounding()`, cross-section renderer) works unchanged:
- `temperature_c`, `relative_humidity_pct`, `dewpoint_c`
- `wind_speed_kt`, `wind_direction_deg`
- `cloud_liquid_water_kg_kg`, `ice_mixing_ratio_kg_kg`, `cloud_area_fraction_pct`

### Phase 3: Replace pressure levels in enrichment pipeline

**File: `src/weatherbrief/fetch/grib/__init__.py`**

Add a new function alongside `_merge_cloud_water_into_sections()`:

```python
def _replace_pressure_levels_from_grib(
    sections: list[RouteCrossSection],
    all_forecasts: list[WaypointForecast],
    route_points: list[RoutePoint],
    sounding_per_point: list[list[PressureLevelData]],
    model_value: str,
    valid_utc: datetime,
) -> int:
    """Replace Open-Meteo pressure levels with full GRIB sounding.

    Unlike _merge_cloud_water_into_sections() which adds fields to existing
    PressureLevelData, this replaces the entire pressure_levels list on
    matching HourlyForecast entries.
    """
```

Modify `_decode_and_merge_icon_eu()`:
- When full sounding variables are present (T, QV, U, V all decoded):
  1. Call `build_icon_sounding()` per point to get 40-level `PressureLevelData` lists
  2. Call `_replace_pressure_levels_from_grib()` to swap onto `HourlyForecast`
  3. Still run `_derive_clc_cloud_layers()` for cloud diagnostics (uses native CLC)
- When partial data (missing T or QV):
  - Fall back to current behavior — cloud-only enrichment via `_merge_cloud_water_into_sections()`

**Fallback logic**: If GRIB enrichment produces full levels for some forecast
hours but not others, the pack has mixed resolution. Accept mixed — the analysis
already works per-hour with arbitrary level counts.

### Phase 4: Cloud boundary derivation update

With full GRIB soundings, `_derive_clc_cloud_layers()` remains useful for filling
`NWPCloudDiagnostics` base/top values. No change needed — it already runs on
native model levels.

Additionally, with per-level CLC available in the full sounding, `analyze_sounding()`
could use CLC as a primary cloud detection signal alongside dewpoint depression,
improving cloud layer detection for ICON. This is an enhancement to explore after
the core sounding replacement is working.

### Phase 5: Cross-section visualization

The cross-section renderer (`web/ts/visualization/cross-section/`) currently
handles data on standard pressure levels for the y-axis grid. With native model
levels:
- The y-axis maps altitude, not pressure directly — more levels just means more
  data points for the interpolation grid
- `VizPoint` carries per-level data — variable level counts already work
- `data-extract.ts` builds VizPoints from route analyses — needs to handle
  variable level counts per model
- Performance: 40 levels x 8 points = 320 data points per model (vs 152 now).
  Should be fine for canvas rendering.

## Data Flow (After)

```
ICON-EU GRIB (DWD opendata)
  |
  +-- Single-level files: ceiling, hbas_con, htop_con, clcl/clcm/clch/clct
  |     |
  |     +-> NWPCloudDiagnostics (cover_pct, base_ft, top_ft per layer)
  |
  +-- Model-level files: P, T, QV, U, V, QC, QI, CLC (40 levels each)
        |
        +-> build_icon_sounding() per point
        |     |
        |     +-> 40 PressureLevelData per point (T, RH, Td, wind, CLW, QI, CLC)
        |           |
        |           +-> _replace_pressure_levels_from_grib()
        |                 REPLACES Open-Meteo pressure_levels on HourlyForecast
        |
        +-> _derive_clc_cloud_layers() (fills NWPCloudDiagnostics base/top)

Open-Meteo (api.open-meteo.com)
  |
  +-- Surface variables only: 2m temp/RH/wind, precip, visibility, CAPE, etc.
  |     |
  |     +-> HourlyForecast surface fields (unchanged)
  |
  +-- Pressure-level variables (FALLBACK only, when GRIB unavailable)
        |
        +-> 19 PressureLevelData per point (current behavior)

GFS / ECMWF GRIB
  |
  +-> Cloud-only enrichment (unchanged — _merge_cloud_water_into_sections)
```

## Risks and Mitigations

| Risk | Mitigation |
|------|-----------|
| DWD server downtime | Fall back to Open-Meteo (already works) |
| GRIB retention ~48h | Pre-fetch during pack creation; cache locally |
| Larger pack JSON files | ~2x for forecasts.json; consider gzip or exclude from briefing.json |
| Model-level pressure rounding | Round to int hPa — max 30 ft error, acceptable |
| Mixed resolution across hours | Analysis already handles variable level counts |
| Download latency | Parallel fetch (8-12 workers), already proven with current 4 vars |
| Duplicate int pressures after rounding | Merge levels with same rounded pressure (pick lower model level) |
| Per-variable decode memory | Existing chunked approach handles 8 vars same as 4 |

## Testing Strategy

1. Unit test `build_icon_sounding()` with known T/QV/U/V/P → verify RH, Td, wind
   match meteorological reference values
2. Unit test pressure rounding and duplicate-merge logic
3. Integration test: fetch real GRIB, build sounding, verify `PressureLevelData`
   fields populate correctly on `HourlyForecast`
4. Integration test: run `analyze_sounding()` on GRIB-replaced sounding → compare
   cloud layers, icing zones with Open-Meteo-based analysis
5. Regression: ensure GFS/ECMWF cloud-only enrichment paths unchanged
6. Visual: compare cross-sections before/after for same flight — verify more
   vertical detail without rendering artifacts
