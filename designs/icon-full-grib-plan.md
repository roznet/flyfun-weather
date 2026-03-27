# ICON-EU Full GRIB Sounding Plan

Replace ICON Open-Meteo pressure-level data with complete 40-level GRIB soundings
for ~500 ft vertical resolution (vs ~3000 ft from Open-Meteo's 19 standard levels).

## Background

ICON-EU uses hybrid model levels (35-74), each with a different pressure at each
geographic point. We already fetch P, QC, QI, CLC from GRIB and interpolate to
Open-Meteo's 19 standard pressure levels. This loses resolution — e.g. a 100 hPa
gap between 800-700 hPa misses thin cloud layers that the native ~25 hPa model-level
spacing would catch.

The CLC cloud boundary derivation (added March 2026) proved the value: using 40
model levels directly gave ~500 ft cloud boundary precision vs ~3000 ft from
interpolated data.

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

## Download Impact

Current: 4 vars (P, QC, QI, CLC) x 40 levels = 160 files per forecast hour
New:     8 vars (+ T, QV, U, V) x 40 levels = 320 files per forecast hour

Each file is ~70 KB compressed (bz2), so ~22 MB per forecast hour.
Typical flight needs 2-4 forecast hours = 44-88 MB total.

The existing parallel downloader (`MAX_DOWNLOAD_WORKERS=8`) handles this fine.
Consider bumping to 12 workers if latency matters.

## Implementation Steps

### Phase 1: Fetch additional GRIB variables

**File: `src/weatherbrief/fetch/grib/icon_eu_fetch.py`**
- Add "t", "qv", "u", "v" to `ICON_EU_VARIABLES`

**File: `src/weatherbrief/fetch/grib/decode.py`**
- Add mappings to `_VAR_MAP`:
  ```
  "t": "temperature_k"
  "qv": "specific_humidity_kg_kg"
  "u": "u_wind_ms"
  "v": "v_wind_ms"
  ```
- Add these to the decode loop in `decode_icon_eu_per_point_chunked()`

**File: `src/weatherbrief/models/analysis.py`**
- Consider whether to add raw fields to `PressureLevelData` or compute derived
  quantities during decode. Recommended: compute during decode so downstream
  code sees the same fields (temperature_c, relative_humidity_pct, etc.)

### Phase 2: Compute derived quantities

**New function in `decode.py` or new module `icon_eu_derived.py`:**

```python
def build_icon_pressure_levels(
    pressure_pa: list[float],     # per model level
    temperature_k: list[float],
    specific_humidity: list[float],
    u_wind_ms: list[float],
    v_wind_ms: list[float],
    cloud_liquid_water: list[float] | None,
    ice_mixing_ratio: list[float] | None,
    cloud_area_fraction: list[float] | None,
) -> list[PressureLevelData]:
    """Convert raw ICON model-level data to PressureLevelData list.

    Computes RH, dewpoint, wind speed/direction from raw GRIB fields.
    Returns one PressureLevelData per model level with native (non-integer)
    pressure values.
    """
```

Key decisions:
- **pressure_hpa type**: Currently `int`. Change to `float` to support native
  model-level pressures (761.3 hPa, 784.2 hPa, etc.). Check all callers that
  use `pressure_hpa` as a dict key — may need rounding strategy for lookups.
  Alternative: keep int, round to nearest hPa (~1 hPa = ~30 ft precision loss,
  acceptable).
- **Level count**: 40 levels per point vs current 19. JSON size increases ~2x
  per ICON waypoint. Verify pack file sizes stay reasonable.

### Phase 3: Integration with enrichment pipeline

**File: `src/weatherbrief/fetch/grib/__init__.py`**

When full GRIB data is available (T, QV, U, V all present):
1. Build complete `PressureLevelData` list from GRIB (40 levels)
2. **Replace** the Open-Meteo pressure levels on `HourlyForecast` for that hour
3. Preserve Open-Meteo surface variables (they're separate fields on `HourlyForecast`)

When partial GRIB data (missing T or QV):
- Fall back to current behavior (enrich Open-Meteo levels with CLW/QI/CLC only)

**Fallback logic**: If GRIB enrichment produces full levels for some forecast
hours but not others, the pack would have mixed resolution. Either:
- Accept mixed (analysis handles arbitrary level counts)
- Only use full GRIB if ALL forecast hours have it (simpler, but wastes data)

Recommendation: accept mixed — the analysis already works per-hour.

### Phase 4: Cloud boundary derivation update

With full GRIB soundings, `_derive_clc_cloud_layers()` becomes redundant for
the primary path — we can derive cloud boundaries from the high-res CLC directly
in the sounding analysis. Keep it as fallback for the Open-Meteo-only path.

Also consider: with per-level CLC available, the cloud detection in
`analyze_sounding()` could use CLC as a primary signal alongside dewpoint
depression, improving cloud layer detection for ICON.

### Phase 5: Cross-section visualization

The cross-section renderer (`web/ts/visualization/cross-section/`) currently
assumes data on standard pressure levels for the y-axis grid. With native model
levels:
- The y-axis already maps altitude, not pressure directly
- `VizPoint` carries per-level data — more levels just means more data points
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
        +-> build_icon_pressure_levels()
        |     |
        |     +-> 40 PressureLevelData per point (T, RH, Td, wind, CLW, QI, CLC)
        |           |
        |           +-> REPLACES Open-Meteo pressure_levels on HourlyForecast
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

## Testing Strategy

1. Unit test `build_icon_pressure_levels()` with known T/QV/U/V → verify RH, Td, wind
2. Integration test: fetch real GRIB, build levels, run `analyze_sounding()` → compare
   cloud layers, icing zones with Open-Meteo-based analysis
3. Regression: ensure GFS/ECMWF/UKMO paths unchanged
4. Visual: compare cross-sections before/after for same flight
