# Raw GRIB2 Weather Engine

> Direct GRIB2 fetch from cloud storage to enrich Open-Meteo data with variables it doesn't provide

## Intent

Open-Meteo provides a convenient API but lacks key variables like **Cloud Liquid Water Mixing Ratio (CLWMR)** and **Ice Mixing Ratio (ICMR)** that directly measure supercooled water in clouds. By fetching raw GRIB2 data from public S3 buckets, we get these variables for much more accurate icing assessment.

The GRIB2 engine is an **enrichment layer** — it supplements Open-Meteo, not replaces it. Open-Meteo remains the primary data source for temperature, wind, humidity, cloud cover, etc.

## What's Implemented

**GFS GRIB2 enrichment** via `fetch/grib/`:
- Variables: CLMR (Cloud Liquid Water Mixing Ratio), ICMR (Ice Mixing Ratio)
- Source: `noaa-gfs-bdp-pds.s3.amazonaws.com` (public, no auth)
- Uses `.idx` companion files for HTTP Range byte-range downloads (only fetches needed messages)
- Bilinear spatial interpolation to route points via cfgrib + xarray
- Disk cache with 48h TTL at `data/.cache/grib/gfs/{date}_{cycle}z/`

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
- **Currently fetching:** CLMR, ICMR at all pressure levels
- **Available but not yet used:** TMP, HGT, UGRD, VGRD, VVEL, RH (could replace Open-Meteo entirely)

### B. ECMWF (IFS HRES - Open Data) — FUTURE
- **Bucket:** `s3://ecmwf-forecasts/`
- **Path:** `/{YYYYMMDD}/{HH}z/0p4-beta/oper/{YYYYMMDD}{HH}0000-{FFF}h-oper-fc.grib2`
- **Resolution:** 0.4° (~40km)
- **Challenge:** No `.idx` files — would need to download full GRIB2 or use ecCodes filtering
- **Missing:** Vertical velocity (ω) often absent in open data; derivable from divergence
- **Value:** CLWMR equivalent for cross-model icing comparison

### C. DWD ICON (Global) — FUTURE
- **Bucket:** `s3://dwd-icon-global-pds/`
- **Path:** `icon_global_icosahedral_single-level_{YYYYMMDD}{HH}_{FFF}_{VAR_UPPER}.grib2`
- **Resolution:** ~13km (icosahedral grid — NOT regular lat/lon)
- **Challenge:** Variables stored in separate files; icosahedral grid needs special interpolation
- **Advantage:** `omega` explicitly available (unlike Open-Meteo's ICON endpoint)

### D. Météo-France ARPEGE — FUTURE
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

**8. ICON icosahedral grid support** — ICON's triangular grid needs scipy `griddata` or ICON-specific regridding (DWD provides weight files). High effort but ICON has best European resolution (~13km).

## Gotchas from Implementation

- **cfgrib lazy loading** — `open_datasets()` only reads the GRIB2 index; actual field data is loaded lazily during interpolation. Temp file must stay alive until all `.values` calls complete.
- **GFS variable names** — `.idx` files use `CLMR`; cfgrib may decode as either `clmr` or `clwmr` depending on version. Map both.
- **Longitude convention** — GFS uses 0–360°; route points use -180–180°. Normalize with `lon % 360`.
- **S3 availability delay** — GFS data appears ~4.5h after init time. `find_latest_run()` checks backward from newest cycle.
- **Pressure coordinate names** — cfgrib may use `isobaricInhPa`, `level`, or `pressure` depending on the GRIB2 message structure. Check all three.

## References

- Implementation: `src/weatherbrief/fetch/grib/`
- Fetch design: [fetch.md](./fetch.md)
- Icing analysis (LWC consumer): [analysis.md](./analysis.md)
- Data models (CLWMR/ICMR fields): [data-models.md](./data-models.md)
