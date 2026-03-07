# Raw GRIB2 Weather Engine

> Direct GRIB2 fetch from cloud storage to enrich Open-Meteo data with variables it doesn't provide

## Intent

Open-Meteo provides a convenient API but lacks key variables like **Cloud Liquid Water Mixing Ratio (CLWMR)** and **Ice Mixing Ratio (ICMR)** that directly measure supercooled water in clouds. By fetching raw GRIB2 data from public S3 buckets, we get these variables for much more accurate icing assessment.

The GRIB2 engine is an **enrichment layer** — it supplements Open-Meteo, not replaces it. Open-Meteo remains the primary data source for temperature, wind, humidity, cloud cover, etc.

## What's Implemented

### GFS GRIB2 enrichment
Via `fetch/grib/` (gfs_idx.py, grib_fetch.py, decode.py):
- **Pressure-level variables:** CLMR (Cloud Liquid Water Mixing Ratio), ICMR (Ice Mixing Ratio) at all pressure levels
- **Cloud diagnostics:** LCC/MCC/HCC/TCC (cloud cover by layer), PRES (cloud base/top per layer), TMP (cloud top temperatures), GH (cloud ceiling). Decoded into `NWPCloudDiagnostics` model.
- Source: `noaa-gfs-bdp-pds.s3.amazonaws.com` (public, no auth)
- Uses `.idx` companion files for HTTP Range byte-range downloads (only fetches needed messages)
- Two separate fetch paths: `plan_byte_ranges()` for CLWMR/ICMR, `plan_cloud_diag_byte_ranges()` for cloud diagnostics
- Bilinear spatial interpolation to route points via cfgrib + xarray
- Disk cache with 48h TTL at `data/.cache/grib/gfs/{date}_{cycle}z/`

### ICON-EU GRIB2 enrichment
Via `fetch/grib/` (icon_eu_fetch.py, icon_eu_levels.py):
- Variables: QC → `cloud_liquid_water_kg_kg`, QI → `ice_mixing_ratio_kg_kg`
- Source: `opendata.dwd.de/weather/nwp/icon-eu/grib/` (public, no auth)
- Individual bz2-compressed files per variable/level/timestep (no .idx files)
- Data on model levels (35–74) with P field for vertical interpolation
- Log-pressure interpolation from model levels to ICON pressure levels (1000–300 hPa)
- Parallel download with ThreadPoolExecutor (8 workers)
- Domain: 29.5–70.5°N, 23.5°W–62.5°E (Europe) — routes outside skip silently
- Cycles: every 3h (00–21z), ~3h publication delay
- Disk cache at `data/.cache/grib/icon-eu/{date}_{cycle}z/`
- Download volume: ~240 files (~115 MB) per 2 bracketing forecast hours

See [fetch.md](./fetch.md) for implementation details.

## Data Source Registry

### A. NOAA GFS (Global Forecast System) — IMPLEMENTED
- **Bucket:** `s3://noaa-gfs-bdp-pds/`
- **Path:** `gfs.{YYYYMMDD}/{HH}/atmos/gfs.t{HH}z.pgrb2.0p25.f{FFF}`
  - `HH`: Cycle run (00, 06, 12, 18)
  - `FFF`: Forecast hour (000 to 384)
- **Resolution:** 0.25° (~27km), regular lat/lon grid
- **Index files:** `.idx` companion files list byte offset of every GRIB2 message
- **Key detail:** GFS uses `CLMR` (not `CLWMR`) as the variable name in `.idx` files
- **Availability:** ~4.5h after init time
- **Currently fetching:** CLMR, ICMR at all pressure levels; cloud diagnostics (LCC/MCC/HCC/TCC covers, PRES bases/tops, TMP cloud-top temps, GH ceiling)
- **Available but not yet used:** TMP, HGT, UGRD, VGRD, VVEL, RH (could replace Open-Meteo entirely)

### B. ECMWF (IFS HRES - Open Data) — FUTURE
- **Bucket:** `s3://ecmwf-forecasts/`
- **Path:** `/{YYYYMMDD}/{HH}z/0p4-beta/oper/{YYYYMMDD}{HH}0000-{FFF}h-oper-fc.grib2`
- **Resolution:** 0.4° (~40km)
- **Challenge:** No `.idx` files — would need to download full GRIB2 or use ecCodes filtering
- **Missing:** Vertical velocity (ω) often absent in open data; derivable from divergence
- **Value:** CLWMR equivalent for cross-model icing comparison

### C. DWD ICON-EU (Regional Europe) — IMPLEMENTED
- **Server:** `https://opendata.dwd.de/weather/nwp/icon-eu/grib/`
- **Model-level path:** `{HH}/{var}/icon-eu_europe_regular-lat-lon_model-level_{YYYYMMDDHH}_{FFF}_{LL}_{VAR}.grib2.bz2`
  - `HH`: Cycle hour (00, 03, 06, ..., 21)
  - `FFF`: Forecast hour (000–120), hourly to 78h, 3-hourly to 120h
  - `LL`: Model level number (35–74 for aviation range)
- **Single-level path:** `{HH}/{var}/icon-eu_europe_regular-lat-lon_single-level_{YYYYMMDDHH}_{FFF}_{VAR}.grib2.bz2`
  - No level number in filename (scalar fields)
- **Resolution:** ~6.5km, regular lat-lon grid (unlike ICON-Global's icosahedral grid)
- **Domain:** 29.5–70.5°N, 23.5°W–62.5°E
- **Variables fetched:**
  - Model-level: QC (cloud liquid water), QI (ice mixing ratio), P (pressure for vertical interp)
  - Single-level: CEILING (cloud ceiling), HBAS_CON/HTOP_CON (convective base/top), CLCL/CLCM/CLCH/CLCT (layer + total cloud cover %)
- **Model levels → pressure levels:** Log-pressure interpolation using P field; targets ICON_PRESSURE_LEVELS
- **Single-level → NWPCloudDiagnostics:** Heights in meters converted to feet (× 3.28084)
- **Publication delay:** ~3h after init time
- **Data retention:** DWD deletes files after ~24h (only latest run available per cycle)
- **Download:** Individual bz2-compressed files, parallel with 8 workers

### ICON-EU Variable Reference

Comprehensive listing of DWD ICON-EU opendata variables. Organized by level type.

#### Single-Level Variables

| Variable | Description | Unit | Status | Aviation Use |
|----------|-------------|------|--------|-------------|
| `CEILING` | Cloud ceiling height | m | **Implemented** | Primary IFR/VFR metric. Converted to ft for NWPCloudDiagnostics. |
| `HBAS_CON` | Convective cloud base height | m | **Implemented** | Cb base altitude. |
| `HTOP_CON` | Convective cloud top height | m | **Implemented** | Cb top altitude. |
| `CLCL` | Low cloud cover | % | **Implemented** | SFC–6500ft cloud fraction. Populates `low.cover_pct`. |
| `CLCM` | Medium cloud cover | % | **Implemented** | 6500–20000ft cloud fraction. Populates `mid.cover_pct`. |
| `CLCH` | High cloud cover | % | **Implemented** | >20000ft cloud fraction. Populates `high.cover_pct`. |
| `CLCT` | Total cloud cover | % | **Implemented** | Full-column cloud fraction. Populates `total_cover_pct`. |
| `T_2M` | 2m temperature | K | Available | Screen-level temperature. |
| `TD_2M` | 2m dewpoint | K | Available | Screen-level dewpoint. |
| `U_10M` | 10m U wind component | m/s | Available | Surface wind (east-west). |
| `V_10M` | 10m V wind component | m/s | Available | Surface wind (north-south). |
| `VMAX_10M` | 10m max wind gust | m/s | Available | Peak surface gust. |
| `PMSL` | Mean sea level pressure | Pa | Available | Altimeter setting. |
| `TOT_PREC` | Total precipitation | kg/m² | Available | Hourly accumulated precipitation. |
| `CAPE_ML` | Mixed-layer CAPE | J/kg | Available | Convective potential energy. |
| `CIN_ML` | Mixed-layer CIN | J/kg | Available | Convective inhibition. |

#### Model-Level Variables

| Variable | Description | Unit | Levels | Status | Note |
|----------|-------------|------|--------|--------|------|
| `QC` | Cloud liquid water mixing ratio | kg/kg | 35–74 | **Implemented** | Log-p interpolated to pressure levels. |
| `QI` | Ice mixing ratio | kg/kg | 35–74 | **Implemented** | Same interpolation as QC. |
| `P` | Pressure | Pa | 35–74 | **Implemented** | Used for vertical interpolation. |
| `T` | Temperature | K | 1–74 | Available | Could replace Open-Meteo for ICON. |
| `U` | U wind component | m/s | 1–74 | Available | East-west wind. |
| `V` | V wind component | m/s | 1–74 | Available | North-south wind. |
| `W` | Vertical velocity | m/s | 1–74 | Available | Physical vertical velocity (not omega). |
| `QV` | Specific humidity | kg/kg | 35–74 | Available | Moisture content. |
| `QR` | Rain water mixing ratio | kg/kg | 35–74 | Available | Precipitation in column. |
| `QS` | Snow mixing ratio | kg/kg | 35–74 | Available | Frozen precipitation in column. |

### D. DWD ICON-Global — FUTURE
- **Bucket:** `s3://dwd-icon-global-pds/`
- **Path:** `icon_global_icosahedral_single-level_{YYYYMMDD}{HH}_{FFF}_{VAR_UPPER}.grib2`
- **Resolution:** ~13km (icosahedral grid — NOT regular lat/lon)
- **Challenge:** Variables stored in separate files; icosahedral grid needs special interpolation
- **Advantage:** `omega` explicitly available (unlike Open-Meteo's ICON endpoint)

### E. Météo-France ARPEGE — METADATA TRACKED, GRIB2 FUTURE
- **Bucket:** `s3://meteo-france-models/arpege-world/`
- **Open-Meteo metadata:** `https://api.open-meteo.com/data/meteofrance_arpege_world025/static/meta.json`
- **Challenge:** Variable path conventions differ from GFS; lower priority

### F. UKMO (UK Met Office Global Deterministic) — METADATA TRACKED
- **Open-Meteo metadata:** `https://api.open-meteo.com/data/ukmo_global_deterministic_10km/static/meta.json`
- **Resolution:** ~10km
- **Status:** Freshness metadata tracked via Open-Meteo; no direct GRIB2 fetch

### G. GFS WAFS Turbulence Products (G-GTG) — RESEARCHED, NOT FEASIBLE

The ICAO WAFS (World Area Forecast System) produces aviation turbulence and icing products derived from GFS post-processing via the **G-GTG (Global Graphical Turbulence Guidance)** algorithm. These are *derived* products, not raw model fields — G-GTG combines Richardson number, wind shear, deformation, convergence, frontogenesis, and mountain wave algorithms into a single EDR metric.

**GRIB2 parameters** (Discipline 0, Category 19 — Physical Atmospheric Properties):

| Param # | Short Name | Description | Units |
|---------|-----------|-------------|-------|
| 28 | MWTURB | Mountain Wave Turbulence (EDR) | m^(2/3) s^(-1) |
| 29 | CATEDR | Clear Air Turbulence (EDR) | m^(2/3) s^(-1) |
| 30 | EDPARM | Eddy Dissipation Parameter | m^(2/3) s^(-1) |
| 31 | MXEDPRM | Maximum of EDR in Layer (MaxEDR) | m^(2/3) s^(-1) |
| 50 | CITEDR | Convectively-Induced Turbulence (EDR) | m^(2/3) s^(-1) |

In current WAFS products, CATEDR and MWTURB are combined into **MaxEDR (MXEDPRM)** as the primary operational field.

**File:** `gfs.t{HH}z.wafs_0p25.f{FFF}.grib2` (formerly `gfs.t{HH}z.gtg.0p25.f{FFF}.grib2`, renamed in GFS v16)

**Why it's not feasible for us:**
- **Not on the S3 bucket** — `wafs_0p25` files are absent from `noaa-gfs-bdp-pds`. The S3 bucket only has pgrb2, pgrb2b, goessimpgrb2, and legacy wafs_grb45 (met fields, not turbulence).
- **Limited distribution** — available through WIFS (requires registration) or possibly NOMADS (not in standard grib filter datasets).
- **Limited forecast range** — f006 to f036 only (3-hourly), vs our 7-day requirement.
- **Flight levels, not pressure levels** — 26 levels from FL100–FL450 at 1000ft intervals. Would need a separate decode path.
- **No .idx files** — can't use our byte-range download infrastructure; would need full file downloads.

**Practical alternative:** Compute turbulence indices from raw GFS fields we already have access to (see Future Extensions §6 Ellrod index, §1 VVEL fetch).

## Gap-Filling Strategy

GRIB enrichment targets native model forecast hours only (e.g. every 3h for GFS at longer lead times), and some GRIB grid cells may return None. Three axes of gap-filling ensure consistent data for all route points, all hours, and all pressure levels:

| Axis | Strategy | Module | Applies to |
|------|----------|--------|------------|
| **Time** | Forward-fill from preceding native GRIB hour | `fetch/grib/fill.py` | Cloud diagnostics, CLW, ICMR |
| **Spatial** | Linear interpolation between neighboring route points (max 100 nm gap, both neighbors required) | `analysis/spatial_interpolation.py` | Cloud diagnostics, CLW, ICMR |
| **Vertical** | Linear interpolation in pressure-space between native GRIB pressure levels | `analysis/sounding/__init__.py` | CLW, ICMR only |

**Pipeline order:**
1. GRIB enrichment assigns values at native hours for all route points
2. `propagate_all()` forward-fills all fields to interpolated hours (time axis)
3. `interpolate_all_spatially()` fills remaining gaps along the route (spatial axis)
4. `_interpolate_cloud_water()` fills intermediate pressure levels during sounding analysis (vertical axis)

**When adding new GRIB-enriched fields:**
1. Add a forward-fill call in `fill.py` → `propagate_all()`
2. Add a spatial interpolation function in `spatial_interpolation.py` → `interpolate_all_spatially()`
3. If per-pressure-level, add vertical interpolation in sounding analysis

## Future Extensions

### Near-term (high value, moderate effort)

**1. Additional GFS variables** — The `.idx` infrastructure already supports any GFS variable. High-value additions:
- `VVEL` (vertical velocity in Pa/s) — currently from Open-Meteo which may smooth it; raw GFS would give sharper CAT signal
- `CAPE`, `CIN` — surface-based convective indices at 0.25° resolution
- `VIS` — visibility at surface, useful for airport condition assessment
- Full temperature/wind column — could replace Open-Meteo for GFS entirely, removing API dependency

**2. Time interpolation (linear)** — Forward-fill from the preceding native hour is implemented for all GRIB fields (see Gap-Filling Strategy above). A future improvement would be to fetch *both* bracketing forecast hours and linearly interpolate between them for mid-hour targets. Infrastructure is already in place (`bracket_forecast_hours()` finds both hours). This would improve accuracy over forward-fill but doubles GRIB download volume.

**3. Concurrent downloads** — `fetch_byte_ranges()` currently downloads messages sequentially. `asyncio` or `concurrent.futures.ThreadPoolExecutor` would parallelize the ~50 HTTP Range requests.

### Medium-term (high value, significant effort)

**4. ECMWF GRIB2 enrichment** — Would enable cross-model LWC comparison for icing. Challenge: no `.idx` files, so need different byte-range strategy (download variable-filtered GRIB2 via ecCodes `MARS`-style requests or full file with filtering).

**5. Derived vertical velocity from divergence** — For models missing ω (ECMWF open data):
```
∂ω/∂p = -(∂u/∂x + ∂v/∂y)
```
Integrate from surface upward using `metpy.calc.divergence(u, v)`. Requires full wind column.

**6. Ellrod turbulence index** — Grid-based CAT metric superior to point-based Richardson number:
```
TI = VWS × (DEF + CVG)
```
Requires 2D wind fields (not just point values), so needs the raw GRIB2 grid, not interpolated points. Would give route-wide turbulence map. This is our best path to operationally-useful CAT prediction — the WAFS G-GTG product (which uses a similar but more sophisticated algorithm) is not accessible from S3 (see §G above). UGRD/VGRD are available in pgrb2 at all pressure levels via .idx.

### Long-term (speculative)

**7. Full GRIB2-primary pipeline** — Replace Open-Meteo entirely for GFS with direct GRIB2 fetch. Pros: no API dependency, full variable access, native resolution. Cons: much more data to download (~30MB per forecast hour vs ~150KB from Open-Meteo), needs robust caching and error handling.

**8. ICON-Global icosahedral grid support** — ICON-Global's triangular grid needs scipy `griddata` or ICON-specific regridding (DWD provides weight files). High effort. Note: ICON-EU (regular lat-lon grid, ~6.5km) is already implemented and covers European flights.

## Gotchas from Implementation

### GFS
- **cfgrib lazy loading** — `open_datasets()` only reads the GRIB2 index; actual field data is loaded lazily during interpolation. Temp file must stay alive until all `.values` calls complete.
- **GFS variable names** — `.idx` files use `CLMR`; cfgrib may decode as either `clmr` or `clwmr` depending on version. Map both.
- **Longitude convention** — GFS uses 0–360°; route points use -180–180°. Normalize with `lon % 360`.
- **S3 availability delay** — GFS data appears ~4.5h after init time. `find_latest_run()` checks backward from newest cycle.
- **Pressure coordinate names** — cfgrib may use `isobaricInhPa`, `level`, or `pressure` depending on the GRIB2 message structure. Check all three.

### ICON-EU
- **Model levels, not pressure levels** — QC/QI are on model levels (35–74). The P variable provides per-gridpoint pressure at each level. Must interpolate vertically using log-pressure.
- **Longitude convention** — ICON-EU uses -180 to +180° (same as route points). No normalization needed (unlike GFS).
- **Level coordinate names** — cfgrib may use `generalVerticalLayer`, `generalVertical`, `level`, or `hybrid` for model-level data. Check all variants.
- **bz2 decompression** — Files are bz2-compressed. Decompress before passing to cfgrib.
- **Data retention** — DWD deletes files after ~24h. Only the latest run per cycle is available.
- **Download volume** — ~240 files per enrichment (3 vars × 40 levels × 2 forecast hours). Parallel download essential.

## References

- Implementation: `src/weatherbrief/fetch/grib/`
- Fetch design: [fetch.md](./fetch.md)
- Icing analysis (LWC consumer): [analysis.md](./analysis.md)
- Data models (CLWMR/ICMR fields): [data-models.md](./data-models.md)
- [GRIB2 Table 4.2-0-19 (Physical Atmospheric Properties)](https://www.nco.ncep.noaa.gov/pmb/docs/grib2/grib2_doc/grib2_table4-2-0-19.shtml) — EDR parameter definitions
- [WAFS Help (Aviation Weather Center)](https://aviationweather.gov/wafs/help.html) — WAFS product descriptions
- [GFS v16 Service Change Notice SCN21-20](https://www.weather.gov/media/notification/pdf2/scn21-20gfs_v16.0_aac.pdf) — wafs_0p25 file rename, variable additions
- [G-GTG turbulence prediction (BAMS 2018)](https://journals.ametsoc.org/view/journals/bams/99/11/bams-d-17-0117.1.xml) — algorithm behind WAFS turbulence
