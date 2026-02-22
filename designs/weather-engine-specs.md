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
  - Single-level: CEILING (cloud ceiling height), HBAS_CON (convective cloud base), HTOP_CON (convective cloud top)
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
| `CLCL` | Low cloud cover | % | Available | SFC–6500ft cloud fraction. |
| `CLCM` | Medium cloud cover | % | Available | 6500–20000ft cloud fraction. |
| `CLCH` | High cloud cover | % | Available | >20000ft cloud fraction. |
| `CLCT` | Total cloud cover | % | Available | Full-column cloud fraction. |
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

### E. Météo-France ARPEGE — FUTURE
- **Bucket:** `s3://meteo-france-models/arpege-world/`
- **Challenge:** Variable path conventions differ from GFS; lower priority

## Future Extensions

### Near-term (high value, moderate effort)

**1. Additional GFS variables** — The `.idx` infrastructure already supports any GFS variable. High-value additions:
- `VVEL` (vertical velocity in Pa/s) — currently from Open-Meteo which may smooth it; raw GFS would give sharper CAT signal
- `CAPE`, `CIN` — surface-based convective indices at 0.25° resolution
- `VIS` — visibility at surface, useful for airport condition assessment
- Full temperature/wind column — could replace Open-Meteo for GFS entirely, removing API dependency

**2. Time interpolation** — Currently uses single forecast hour closest to target. Bracketing with linear interpolation between F_prev and F_next would improve accuracy for mid-hour targets. Infrastructure is already in place (`bracket_forecast_hours()` finds both hours).

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
Requires 2D wind fields (not just point values), so needs the raw GRIB2 grid, not interpolated points. Would give route-wide turbulence map.

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
