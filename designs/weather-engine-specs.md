# Raw GRIB2 Weather Engine

> Direct GRIB2 fetch to enrich or replace Open-Meteo data with higher-resolution variables

## Intent

Open-Meteo provides a convenient API but lacks key variables (cloud liquid water, ice mixing ratio, cloud area fraction) and has limited pressure levels (13 for ECMWF, 19 for ICON). By fetching raw GRIB2 data, we get:
- **Cloud microphysics** (CLWMR/ICMR/cloud fraction) for accurate icing assessment
- **Full sounding replacement** (ECMWF: 25 levels, ICON: 40 model levels) for higher-resolution cross-sections
- **Cloud diagnostics** (ceiling, layer covers, convective base/top) for NWP-based cloud analysis

The enrichment strategy differs by model:
- **GFS:** Open-Meteo is primary (28 levels); GRIB **patches** cloud microphysics + diagnostics onto existing levels
- **ECMWF/ICON:** GRIB **replaces the entire pressure-level sounding** with higher-resolution data; Open-Meteo provides surface fields only

## Field Attribution Matrix

### Raw NWP Fields (`PressureLevelData`) — Source by Model

| Field | GFS | ECMWF | ICON |
|-------|-----|-------|------|
| **pressure_hpa** | Open-Meteo (28 lvls) | GRIB (25 lvls) | GRIB (interpolated from 40 model lvls) |
| **temperature_c** | Open-Meteo | GRIB (`t`, K→°C) | GRIB (`t`, K→°C) |
| **relative_humidity_pct** | Open-Meteo | GRIB (`r`) | GRIB (derived from `qv`+T+P) |
| **dewpoint_c** | Open-Meteo | GRIB (derived from T+RH) | GRIB (derived from T+RH) |
| **wind_speed_kt** | Open-Meteo | GRIB (`u`,`v` → speed) | GRIB (`u`,`v` → speed) |
| **wind_direction_deg** | Open-Meteo | GRIB (`u`,`v` → dir) | GRIB (`u`,`v` → dir) |
| **geopotential_height_m** | Open-Meteo | GRIB (`z` ÷ 9.80665) | **None** — not on ICON model levels |
| **vertical_velocity_pa_s** | Open-Meteo | GRIB (`w`) | GRIB (`w`, m/s → omega via −ρ·g·w) |
| **cloud_liquid_water_kg_kg** | GRIB (`CLMR` patch) | GRIB (`clwc`) | GRIB (`qc`) |
| **ice_mixing_ratio_kg_kg** | GRIB (`ICMR` patch) | GRIB (`ciwc`) | GRIB (`qi`) |
| **cloud_area_fraction_pct** | — | GRIB (`cc`, 0–1→%) | GRIB (`clc`, already %) |

### Derived Level Fields (`DerivedLevel`) — Computed in Sounding Analysis

All models share the same computation pipeline; inputs vary by what the raw data provides.

| Field | Computed from |
|-------|--------------|
| **altitude_ft** | geopotential_height_m, or std atmosphere fallback if missing |
| **wet_bulb_c** | T + Td via MetPy |
| **dewpoint_depression_c** | T − Td |
| **theta_e_k** | T + Td + P via MetPy |
| **lapse_rate_c_per_km** | ΔT/Δz between adjacent levels |
| **omega_pa_s / w_fpm** | vertical_velocity_pa_s → ft/min via MetPy |
| **richardson_number** | N²/S² (Brunt-Väisälä / wind shear²) |
| **bv_freq_squared_per_s2** | (g/θ)(dθ/dz) |
| **cloud_liquid_water_g_m3** | CLW × ρ_air × 1000 |
| **cloud_liquid_water_g_kg** | CLW × 1000 |
| **ice_mixing_ratio_g_kg** | ICE × 1000 |
| **icing_index** (Ogimet-DD) | T curve × DD attenuation |
| **icing_index_nwp** (Ogimet-NWP) | T curve × cloud_fraction × glaciation(CLW, ICE) |
| **sfip_raw/100/severity** | Fuzzy logic: T + RH + CLW (or proxy) + omega |
| **precip_phase** | Wet-bulb thresholds + warm-nose detection |

### Surface Cloud Diagnostics (`NWPCloudDiagnostics`) — Source by Model

| Field | GFS | ECMWF | ICON |
|-------|-----|-------|------|
| **ceiling_ft** | GRIB (GH) | GRIB (`ceil`, m→ft) | GRIB (`ceiling`, m→ft) |
| **low.cover_pct** | GRIB (LCDC) | GRIB (`lcc`) | GRIB (`clcl`) |
| **mid.cover_pct** | GRIB (MCDC) | GRIB (`mcc`) | GRIB (`clcm`) |
| **high.cover_pct** | GRIB (HCDC) | GRIB (`hcc`) | GRIB (`clch`) |
| **total_cover_pct** | GRIB (TCDC) | GRIB (`tcc`) | GRIB (`clct`) |
| **convective base/top** | GRIB | Top only (`hcct`); base = LCL proxy | GRIB (`hbas_con`, `htop_con`) |
| **freezing_level_ft** | — | GRIB (`deg0l`) → overwrites `hourly.freezing_level_m` | — |
| **boundary_cover_pct** | GRIB | — | — |

### Known Gaps

| Gap | Model | Impact |
|-----|-------|--------|
| `z` on pressure levels | ECMWF | Commercial feed delivers `z` only at 1 hPa — GH on 25 levels derived via hypsometric equation from T+P (accurate ~1%). Order amendment pending. |
| `geopotential_height_m` | ICON | Not on model levels → derived via hypsometric equation from T+P (same path as ECMWF). |
| Surface vars (2t, 10u, CAPE, vis…) | ECMWF | ~10 surface vars now decoded via `build_ecmwf_surface_snapshot` (t2m, d2m, u10, v10, fg10, vis, tp, sf, mucape, sp) but only consumed by the standalone verification pipeline — the user-facing forecast still uses Open-Meteo surface fields. Remaining a1 vars unprocessed. |

## What's Implemented

### GFS GRIB2 enrichment
Via `fetch/grib/` (gfs_idx.py, grib_fetch.py, decode.py):
- **Pressure-level variables:** CLMR (Cloud Liquid Water Mixing Ratio), ICMR (Ice Mixing Ratio) at all pressure levels — **patched onto existing Open-Meteo levels**
- **Cloud diagnostics:** LCC/MCC/HCC/TCC (cloud cover by layer), PRES (cloud base/top per layer), TMP (cloud top temperatures), GH (cloud ceiling). Decoded into `NWPCloudDiagnostics` model.
- Source: `noaa-gfs-bdp-pds.s3.amazonaws.com` (public, no auth)
- Uses `.idx` companion files for HTTP Range byte-range downloads (only fetches needed messages)
- Two separate fetch paths: `plan_byte_ranges()` for CLWMR/ICMR, `plan_cloud_diag_byte_ranges()` for cloud diagnostics
- Bilinear spatial interpolation to route points via cfgrib + xarray
- Disk cache with 24h TTL at `data/.cache/grib/gfs/{date}_{cycle}z/` (per-model TTL in `cache.py` → `MODEL_TTL_SECONDS`; ICON-EU gets 12h since it's precached each run)

### ECMWF IFS GRIB2 enrichment
Via `fetch/grib/` (ecmwf_fetch.py, decode.py):
- **Full sounding replacement** — pressure levels (a2 files): t, r, u, v, z, w, d, cc, clwc, ciwc at 25 levels. **Replaces entire `pressure_levels` list**, discarding Open-Meteo levels.
- **Cloud diagnostics** — surface (a1 files): ceil, cbh, lcc, mcc, hcc, tcc, hcct, deg0l → `NWPCloudDiagnostics` (hcct populates `convective_top_ft`; deg0l populates `freezing_level_ft` and overwrites `hourly.freezing_level_m`)
- Source: ECPDS push delivery to local directory (`ECMWF_GRIB_DIR`)
- No HTTP, no cache — local disk I/O
- Unit conversions: K→°C, m²/s²→m (geopotential), 0–1→% (cloud fractions), m/s→kt (wind)

### ICON-EU / ICON-D2 GRIB2 enrichment (the `icon` slot)
Via `fetch/grib/` (icon_eu_fetch.py, icon_eu_levels.py, decode.py):
- **Full sounding replacement** — model levels 35–74 (EU) / 16–65 (D2): t, qv, u, v, qc, qi, clc, p, w. Log-pressure interpolated to standard pressure levels. **Replaces entire `pressure_levels` list**. `w` (m/s) is converted to omega (`vertical_velocity_pa_s`) per level via −ρ·g·w.
- **Cloud diagnostics** — single-level: ceiling, hbas_con, htop_con, clcl, clcm, clch, clct, cape_ml, cin_ml, rain_con → `NWPCloudDiagnostics`
- Source: `opendata.dwd.de/weather/nwp/{icon-eu,icon-d2}/grib/` (public, no auth)
- Individual bz2-compressed files per variable/level/timestep
- Parallel download with ThreadPoolExecutor (8 workers)
- **Variant selection (issue #456):** the `icon` slot is served by **ICON-D2** (2.2 km, convection-permitting) when the *whole* route fits the D2 domain (43.18–58.08°N, 3.94°W–20.34°E) AND a complete D2 run's 48h horizon reaches the flight-window end; otherwise by **ICON-EU** (6.5 km, all-Europe) exactly as before. All-or-nothing — never a per-point mix of D2 and EU within one briefing. On total D2 failure the icon slot re-runs cleanly on ICON-EU (never a half-D2 pack).
- The two variants share the whole download/decode path via `IconVariant` (a config object holding domain, cycles, horizon, level slice, filename conventions, cache slug and freshness source key). ICON-D2 filename quirks: model token `icon-d2`, region token `germany`, **lowercase** variable suffix (`…_60_t`), and a `_2d_` segment on single-level files (`…_006_2d_ceiling`).
- Domain: EU 29.5–70.5°N, 23.5°W–62.5°E; D2 43.18–58.08°N, 3.94°W–20.34°E. Routes outside the chosen domain skip silently.
- Cycles: both every 3h (00–21z). EU ~3h publication delay, hourly to 78h then 3-hourly to 120h. D2 ~2h delay, hourly to 48h.
- Disk cache at `data/.cache/grib/{icon-eu,icon-d2}/{date}_{cycle}z/` (EU 12h TTL, D2 6h TTL). Per-variant cache-key prefix (`ICON_EU_*` / `ICON_D2_*`) keeps them distinct.
- Pack metadata records which source produced the icon slot via `model_sources["icon"]` = `icon_eu:dwd` or `icon_d2:dwd`; the freshness bar badges `ICON (D2)` when D2 supplied the run.

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

### B. ECMWF IFS (Commercial via ECPDS) — IMPLEMENTED (full sounding)
- **Delivery:** ECPDS push to local directory (`ECMWF_GRIB_DIR`, default `/data/ecmwf`). Read-only Docker volume mount.
- **Model:** ifs-ens-cf (IFS Ensemble Control Forecast), 0.25° over Europe + US
- **Files:** Two parts per forecast step — a1 (surface, 29 vars) and a2 (pressure levels, 10 vars × 25 levels)
- **Cycles:** 00z/06z/12z/18z. Horizon per cycle is derived from the max step observed on disk, not from the stream name (see `find_best_ecmwf_run` in `fetch/grib/ecmwf_fetch.py`). Subscription shape post-2026-04-22 amendment: 00/12z → 168h, 06/18z → 144h. From IFS Cycle 50r1 (12-May-2026), all four cycles arrive with `stream=oper` — the `scda` label is gone. Init hour, not stream, determines the expected manifest (`delivery_config.json` is keyed by cycle hour).
- **Publication delay:** ~6–8h after init time
- **Naming convention:** `dest_feed_model_class_stream_type_baseTime_validTime_step[_expver]` — no `.grib2` extension by default. `expver` is absent on prod operational files, and `X0080` on TPREd Release Candidate files (ECMWF_ACCEPT_RCP_EXPVER=1 opt-in for staging).
- **Pressure-level (a2):** t, r, u, v, z, w, gh, cc, clwc, ciwc — **full sounding replacement** (replaces Open-Meteo pressure levels entirely). Post-amendment (2026-04-22): `d` (divergence) was dropped, `gh` (geopotential height) added at all 25 levels, removing the hypsometric fallback from the decode path. `z` is still delivered only at 1 hPa (catalogue limitation).
- **Surface (a1) — cloud diagnostics:** ceil, cbh, lcc, mcc, hcc, tcc, hcct, deg0l, **kx, totalx, mlcape100, mlcin100, cp** → `NWPCloudDiagnostics` (hcct → `convective_top_ft`; deg0l → `freezing_level_ft` + overwrites `hourly.freezing_level_m`; kx/totalx → `k_index`/`total_totals`; mlcape100/mlcin100 → `ml_cape_jkg`/`ml_cin_jkg`; **cp** is accumulated-since-init, de-accumulated by step-difference in the ECMWF merge loop → `convective_precip_mm_h`). These feed the model-native convective track's firing gate + corroboration (#283).
- **Surface (a1) — surface snapshot:** t2m, d2m, u10, v10, fg10, vis, tp, sf, mucape, sp → `build_ecmwf_surface_snapshot` (unit-converted), consumed by the standalone verification pipeline only — NOT yet wired into the user-facing forecast (which still uses Open-Meteo surface fields)
- **Surface (a1) — native convective indices:** kx, totalx → `nwp_k_index` / `nwp_total_totals` on `HourlyForecast`, copied onto `ThermodynamicIndices.nwp_k_index/nwp_total_totals` during sounding analysis. The convective character advisory prefers these over the MetPy-derived K/Total-Totals for ECMWF (issue #294). `kx` is delivered in Kelvin and normalized to °C via `_k_index_to_c` (#283); Total Totals is offset-immune and passes through unchanged.
- **Surface (a1) — delivered but not yet processed:** 10fg, blh, capes, degm10l, fzra, lsp, msl, ptype
- **Multi-grid:** Files may contain multiple geographic sub-grids; cfgrib splits into separate Datasets, decoder uses first-wins per point
- **No HTTP, no cache** — local disk I/O, no byte-range download needed

### C. DWD ICON-EU (Regional Europe) — IMPLEMENTED (full sounding)
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
  - Model-level: QC, QI, CLC, P, T, QV, U, V, W — **full sounding replacement** (replaces Open-Meteo pressure levels entirely)
  - Single-level: CEILING, HBAS_CON/HTOP_CON, CLCL/CLCM/CLCH/CLCT, **CAPE_ML/CIN_ML**, **RAIN_CON** → `NWPCloudDiagnostics` (cape_ml/cin_ml → `ml_cape_jkg`/`ml_cin_jkg`, instantaneous, feed the native convective track #283; rain_con → `convective_precip_mm_h`, accumulated-since-init and de-accumulated in the merge loop, #421). rain_con is kg/m² ≡ mm — already mm, so **no** ×1000 (unlike ECMWF `cp`, m water equivalent). The ICON merge prepends one leading single-level step (`icon_eu_previous_step`) so the first window hour has a predecessor to difference against, and the cloud-diag cache key is bumped (`ICON_EU_CLOUD_DIAG_V2`) so warm caches re-fetch.
- **Model levels → pressure levels:** Log-pressure interpolation using P field; 40 model levels → standard pressure levels (`EXTENDED_PRESSURE_LEVELS`, 28-level set)
- **Single-level → NWPCloudDiagnostics:** Heights in meters converted to feet (× 3.28084)
- **W → omega:** physical vertical velocity (m/s) converted to omega (`vertical_velocity_pa_s`) per level via −ρ·g·w
- **Gaps:** No geopotential (FI not on model levels) — derived via hypsometric equation from T+P
- **Publication delay:** ~3h after init time
- **Data retention:** DWD deletes files after ~24h (only latest run available per cycle)
- **Download:** Individual bz2-compressed files, parallel with 8 workers

### C.2 DWD ICON-D2 (Central Europe, convection-permitting) — IMPLEMENTED (full sounding, #456)
- **Server:** `https://opendata.dwd.de/weather/nwp/icon-d2/grib/`
- **Model-level path:** `{HH}/{var}/icon-d2_germany_regular-lat-lon_model-level_{YYYYMMDDHH}_{FFF}_{LL}_{var}.grib2.bz2` — note **lowercase** variable suffix (vs ICON-EU's uppercase).
- **Single-level path:** `{HH}/{var}/icon-d2_germany_regular-lat-lon_single-level_{YYYYMMDDHH}_{FFF}_2d_{var}.grib2.bz2` — note the `2d` segment (vs ICON-EU's absent segment).
- **Resolution:** ~2.2 km, regular lat-lon grid (same grid *type* as ICON-EU → existing bilinear interpolation works unchanged).
- **Domain:** 43.18–58.08°N, 3.94°W–20.34°E (Germany, Alps, Benelux, most of France, SE England; excludes Brittany, Scotland, Spain). ~906 k grid points ≈ ICON-EU's ~905 k.
- **Download volume — grid-point parity does NOT mean byte parity.** Measured on run `20260721 09z`, same route, same 3 forecast hours: **D2 494 MB/fhour vs ICON-EU 189 MB/fhour — 2.6×**. Two compounding causes: D2 takes **50 model levels (16–65) vs EU's 40 (35–74)** = 1.25×, and a 2.2 km convection-permitting field is far less compressible than a smooth 6.5 km one, so **per level D2 costs 2.05×** EU (1.54 MB vs 0.75 MB per level-file) at near-identical grid-point counts. The single-level diag blob shows the same effect — 20.6 MB vs 9.6 MB per fhour (2.15×) despite D2's list being *shorter*. Breakdown of a D2 run: model-level sounding 93.5%, explicit-convection fields (#462) 3.8%, single-level diag 2.7% — the sounding data is the cost driver, not the explicit-convection addition.
- **Model levels:** 65 total (bottom = 65; numbering NOT comparable to ICON-EU's 74). Aviation slice **16–65** (50 levels) — level 16 ≈ 9,460 m ≈ FL310, matching ICON-EU's level-35 (~300 hPa) top. Validated against DWD's HHL (half-level height) field decoded from the live feed 2026-07-21; the original guess of 25 sat at ~6,300 m ≈ FL207 and would have truncated every D2 sounding.
- **Cycles/horizon:** 8 runs/day (00–21z every 3h), hourly steps to **48h** (no coarse tail). Publication delay ~1–2h.
- **Variables:** model-level set identical to ICON-EU (`t, qv, u, v, p, w, qc, qi, clc` — all verified live). Single-level cloud-diag set is **smaller**: `ceiling, clcl/clcm/clch/clct, cape_ml/cin_ml` only. D2 runs no deep-convection scheme, so `hbas_con`/`htop_con` don't exist (404; the shallow-only `hbas_sc`/`htop_sc` would mislead) and `rain_con` is near-zero even in explicit storms — all three deliberately unfetched → downstream fields **None** (missing-data semantics).
- **Explicit-convection fields (#462, implemented):** `dbz_ctmax` (column-max reflectivity, dBZ, max over previous hour — the firing signal), `echotop` (shortName `min_pres`, Pa, min per **15-min window, 4 messages/file**, sentinel −999 = no 18 dBZ echo), `lpi_max` (J/kg, hour max), `w_ctmax` (m/s, hour max 0–10 km), `uh_max` (m²/s², SIGNED hour max amplitude, 2–8 km AGL), `grau_gsp` (kg/m² ≡ mm, accumulated since init; on-the-hour message + 3 quarter-hour messages/file — de-accumulate from the on-the-hour messages ONLY). Fetched into per-variable blobs (`ICON_D2_EXPL_<VAR>_V1`), decoded message-level by `stepRange` (minutes — reading `startStep`/`endStep` as ints silently truncates to hours!) and reduced to **corridor extrema** over a ~10 NM route buffer. `dbz_cmax` (instantaneous) unfetched in v1; `tcond10_mx` (condensate above the −10 °C isotherm) deferred. Feeds `NWPExplicitConvectiveDiagnostics` → `assess_convective_explicit` (meteorology-decisions §19).
- **Gating (all-or-nothing):** used for the `icon` slot only when every route point is inside the D2 domain AND `flight_window_end ≤ selected_run_init + 48h` AND (#462) the route's entire ~10 NM corridor buffer lies in valid cells of the product bitmap (~17% of the regular-ll grid is masked — native domain is not a lat/lon rectangle, and the files carry **no rotated-pole metadata**; the validity mask is built once from a delivered message's bitmap and cached, failing open to the bbox gate). If any check fails → pure ICON-EU. If the freshest complete D2 run doesn't cover the window, fall back to ICON-EU rather than a stale D2 run.
- **Freshness:** source key `icon_d2:dwd` in `SOURCE_REGISTRY` (readiness check `icon_d2_dwd`), cache dir `data/.cache/grib/icon-d2/{date}_{cycle}z/` (6h TTL).
- **Out of scope:** per-point D2/EU mixing, D2's 15-min sub-hourly output as forecast steps, ICON-D2-EPS ensemble.

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
| `T` | Temperature | K | 35–74 | **Implemented** | Full sounding replacement (K→°C). |
| `U` | U wind component | m/s | 35–74 | **Implemented** | Full sounding replacement (m/s→kt). |
| `V` | V wind component | m/s | 35–74 | **Implemented** | Full sounding replacement (m/s→kt). |
| `QV` | Specific humidity | kg/kg | 35–74 | **Implemented** | Used to derive RH via Magnus formula. |
| `CLC` | Cloud area fraction | % | 35–74 | **Implemented** | Per-level cloud cover (0–100%). |
| `W` | Vertical velocity | m/s | 35–74 | **Implemented** | Physical vertical velocity (m/s, upward positive); converted to omega via −ρ·g·w. |
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
| **Time — GFS averaged fields** | Window-midpoint linear interp between native steps; layer geometry held from higher-cover endpoint; sub-5 % covers dropped; followed by RH/condensate gate that drops bands where pressure-level RH and condensate disagree with averaged cover. Requires `gfs_init`. See [meteorology-decisions §3](./meteorology-decisions.md#3-gfs-cloud-diagnostics-window-midpoint-interp--rhcondensate-gate). | `fetch/grib/fill.py` | GFS low/mid/high cloud cover + geometry |
| **Time — everything else** | Forward-fill for ICON-EU / ECMWF cloud diagnostics and the GFS fallback path (no `gfs_init`); step-time linear interp for GFS CLW/ICMR overlay; linear interp for ECMWF surface scalars and ECMWF / ICON-EU pressure-level soundings (dewpoint derived from interpolated T+RH via Magnus). | `fetch/grib/fill.py` | Cloud diagnostics (non-GFS bands), CLW, ICMR, ECMWF surface, sounding rebuild |
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

**1. ECMWF surface variables → forecast** — ~10 a1 surface vars (t2m, d2m, u10, v10, fg10, vis, tp, sf, mucape, sp) are already decoded via `build_ecmwf_surface_snapshot` but only consumed by the standalone verification pipeline. Wiring them into the user-facing forecast (supplement/replace Open-Meteo surface fields) is the remaining work. Other a1 vars (CAPE variants, freezing level family, precip type, etc.) still undecoded.

**2. ECMWF order: add `z` on all 25 pressure levels** — Currently `z` (paramId 129) is delivered only at `isobaricInhPa=1.0 hPa` (single level). Other pressure-level vars (t, r, u, v, w, d, cc, clwc, ciwc) are at all 25 levels as ordered. Interim hypsometric fill from T+P is accurate within ~1% but real `z` is preferred.

**3. Additional GFS variables** — The `.idx` infrastructure supports any GFS variable. High-value additions:
- `VVEL` (vertical velocity in Pa/s) — raw GFS would give sharper CAT signal than Open-Meteo
- `CAPE`, `CIN` — surface-based convective indices at 0.25° resolution
- Full temperature/wind column — could enable full sounding replacement for GFS too

**4. Time interpolation (linear)** — GFS cloud diagnostics now use window-midpoint linear interpolation when `gfs_init` is provided (see [meteorology-decisions §3](./meteorology-decisions.md#3-gfs-cloud-diagnostics-window-midpoint-interp--rhcondensate-gate)), GFS CLW/ICMR use step-time linear interp, and ECMWF / ICON-EU pressure-level soundings + ECMWF surface scalars are linearly interpolated between native steps. ICON-EU / ECMWF cloud diagnostics still use forward-fill (instantaneous fields where persistence is the right semantic). The remaining linear-interp opportunity is GFS pressure-level fields (T, RH, wind) — currently sourced from Open-Meteo, so this only becomes relevant if/when GFS moves to full GRIB-primary sourcing (item 6). Infrastructure exists (`bracket_forecast_hours()`).

**5. Ellrod turbulence index** — Grid-based CAT metric:
```
TI = VWS × (DEF + CVG)
```
Requires 2D wind fields (not just point values), so needs raw GRIB2 grid, not interpolated points. UGRD/VGRD available in GFS pgrb2 via .idx.

### Long-term (speculative)

**6. Full GRIB2-primary pipeline** — Replace Open-Meteo entirely for GFS with direct GRIB2 fetch. Pros: no API dependency, full variable access. Cons: ~30MB per forecast hour vs ~150KB from Open-Meteo.

**7. ICON-Global icosahedral grid support** — Triangular grid needs special interpolation. ICON-EU (regular lat-lon, ~6.5km) covers European flights.

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
