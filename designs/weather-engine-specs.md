# Raw GRIB2 Weather Engine

> Direct GRIB2 fetch to enrich or replace Open-Meteo data with higher-resolution variables

## Intent

Open-Meteo provides a convenient API but lacks key variables (cloud liquid water, ice mixing ratio, cloud area fraction) and has limited pressure levels (13 for ECMWF, 19 for ICON). By fetching raw GRIB2 data, we get:
- **Cloud microphysics** (CLWMR/ICMR/cloud fraction) for accurate icing assessment
- **Full sounding replacement** (ECMWF: 25 pressure levels, ICON: 40 (EU) / 50 (D2) model levels) for higher-resolution cross-sections
- **Cloud diagnostics** (ceiling, layer covers, convective base/top) for NWP-based cloud analysis

The enrichment strategy differs by model:
- **GFS:** Open-Meteo is primary (28 levels); GRIB **patches** cloud microphysics + diagnostics onto existing levels
- **GFS slot on CONUS routes (#457):** when the whole route fits the HRRR domain and the window is within the run's horizon, the slot is sourced from **HRRR** as a **full sounding replacement** (ICON/ECMWF pattern) — badged `GFS (HRRR)`
- **ECMWF/ICON:** GRIB **replaces the entire pressure-level sounding** with higher-resolution data; Open-Meteo provides surface fields only

## Field Attribution Matrix

### Raw NWP Fields (`PressureLevelData`) — Source by Model

The GFS column has two states: plain GFS (Open-Meteo base + patch) and the
HRRR-upgraded slot on qualifying CONUS routes (#457), where the whole
sounding is GRIB like ECMWF/ICON.

| Field | GFS | GFS slot from HRRR (#457) | ECMWF | ICON |
|-------|-----|---------------------------|-------|------|
| **pressure_hpa** | Open-Meteo (28 lvls) | GRIB (35 lvls, 150–1000 hPa @ 25) | GRIB (25 lvls) | GRIB (interpolated from 40 model lvls) |
| **temperature_c** | Open-Meteo | GRIB (`TMP`, K→°C) | GRIB (`t`, K→°C) | GRIB (`t`, K→°C) |
| **relative_humidity_pct** | Open-Meteo | GRIB (`RH`) | GRIB (`r`) | GRIB (derived from `qv`+T+P) |
| **dewpoint_c** | Open-Meteo | GRIB (`DPT`, **direct**) | GRIB (derived from T+RH) | GRIB (derived from T+RH) |
| **wind_speed_kt** | Open-Meteo | GRIB (`UGRD`,`VGRD` grid→earth rotated) | GRIB (`u`,`v` → speed) | GRIB (`u`,`v` → speed) |
| **wind_direction_deg** | Open-Meteo | GRIB (same, rotated) | GRIB (`u`,`v` → dir) | GRIB (`u`,`v` → dir) |
| **geopotential_height_m** | Open-Meteo | GRIB (`HGT`, **direct**) | GRIB (`gh`, **direct** post-amendment; `z` ÷ 9.80665 then hypsometric as fallbacks for old archives) | **None** — not on ICON model levels |
| **vertical_velocity_pa_s** | Open-Meteo | GRIB (`VVEL`, already Pa/s) | GRIB (`w`) | GRIB (`w`, m/s → omega via −ρ·g·w) |
| **cloud_liquid_water_kg_kg** | GRIB (`CLMR` patch) | GRIB (`CLMR`) | GRIB (`clwc`) | GRIB (`qc`) |
| **ice_mixing_ratio_kg_kg** | GRIB (`ICMR` patch) | GRIB (`CIMIXR`) | GRIB (`ciwc`) | GRIB (`qi`) |
| **cloud_area_fraction_pct** | — | — (no 3D cloud fraction in HRRR) | GRIB (`cc`, 0–1→%) | GRIB (`clc`, already %) |
| **rain/snow/graupel_water_kg_kg** | — | — | — | GRIB (`qr`,`qs`,`qg`) — **ICON-D2 only** (#530); EU doesn't publish them |

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
| **rain_water_g_kg / supercooled_rain** | qr × 1000; `is_supercooled_rain(qr, T)` (#530) — ICON-D2 only, defaults everywhere else |

### Surface Cloud Diagnostics (`NWPCloudDiagnostics`) — Source by Model

| Field | GFS | GFS slot from HRRR (#457) | ECMWF | ICON |
|-------|-----|---------------------------|-------|------|
| **ceiling_ft** | GRIB (GH) | GRIB (HGT@cloud ceiling, gpm→ft) | GRIB (`ceil`, m→ft) | GRIB (`ceiling`, m→ft) |
| **low.cover_pct** | GRIB (LCDC, averaged) | GRIB (LCDC, instant) | GRIB (`lcc`) | GRIB (`clcl`) |
| **low.base_ft** | GRIB (avg PRES) | GRIB (HGT@cloud base — the OVERALL base, ECMWF-`cbh`-style) | GRIB (`cbh`) | — |
| **low/mid/high base/top/temp geometry** | GRIB (averaged PRES/TMP) | — (not published by HRRR) | — | — |
| **mid.cover_pct** | GRIB (MCDC) | GRIB (MCDC) | GRIB (`mcc`) | GRIB (`clcm`) |
| **high.cover_pct** | GRIB (HCDC) | GRIB (HCDC) | GRIB (`hcc`) | GRIB (`clch`) |
| **total_cover_pct** | GRIB (TCDC) | GRIB (TCDC) | GRIB (`tcc`) | GRIB (`clct`) |
| **convective base/top** | GRIB | — | Top only (`hcct`); base = LCL proxy | GRIB (`hbas_con`, `htop_con`) |
| **convective precip rate** | GRIB (`CPRAT`, instantaneous — no de-accumulation) | — | GRIB (`cp`, accumulated → de-accumulated) | GRIB (`crr`, accumulated → de-accumulated) |
| **ml_cape/ml_cin (J/kg)** | GRIB (CAPE/CIN 180-0 mb; CIN already negative, shares HRRR's `_ncep_cin_jkg`) | GRIB (CAPE/CIN 90-0 mb; CIN already negative) | GRIB (`mlcape100`/`mlcin100`) | GRIB (`cape_ml`/`cin_ml`) |
| **freezing_level_ft** | — | — | GRIB (`deg0l`) → overwrites `hourly.freezing_level_m` | — |
| **boundary_cover_pct** | GRIB | — | — | — |

### Known Gaps

| Gap | Model | Impact |
|-----|-------|--------|
| `z` on pressure levels | ECMWF | **Closed by the 2026-04-22 amendment**: `gh` is now delivered on all 25 levels and decoded directly. `z` itself is still 1 hPa only (catalogue limitation), and the `z`/g + hypsometric fills survive only as fallbacks for pre-amendment archives. |
| `geopotential_height_m` | ICON | Not on model levels → derived via hypsometric equation from T+P (the same fallback path). |
| Remaining a1 surface vars | ECMWF | The ~10 vars in `build_ecmwf_surface_snapshot` are now live on the user-facing forecast too (see B). The rest of the a1 manifest (10fg, capes, degm10l, fzra, lsp, msl, ptype) is still undecoded. |

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

### HRRR GRIB2 enrichment (the `gfs` slot on CONUS routes, #457)
Via `fetch/grib/` (hrrr_fetch.py, decode.py):
- **Full sounding replacement** — TMP, DPT (direct, no Magnus), RH, UGRD/VGRD (grid-relative → earth-relative rotation), VVEL (already Pa/s), HGT (direct, no hypsometric fallback), CLMR, CIMIXR on 35 integer levels (150–1000 hPa at 25 hPa spacing; the 50–125 hPa top and the fractional `1013.2 mb` near-surface extra are skipped). **Replaces the entire `pressure_levels` list** via the ECMWF replacement flow (`_replace_pressure_levels_from_grib`), unlike plain GFS's patch.
- **Cloud diagnostics** — all instantaneous: LCDC/MCDC/HCDC/TCDC covers, HGT@cloud-ceiling, HGT@cloud-base (the overall base → `low.base_ft`, ECMWF-`cbh`-style — HRRR has NO per-band base/top/temp geometry), CAPE/CIN at 90-0 mb (the delivered 90-hPa-layer product, comparable in intent — not algorithm — to ECMWF's 100-hPa `mlcape100`) → `ml_cape_jkg`/`ml_cin_jkg`. HRRR CIN is already negative (internal convention) — it must NOT go through `_normalize_model_cin`; a clearly positive value (> +5 J/kg) would mean the convention flipped upstream and is dropped to None (unknown) rather than floored to "no cap".
- **No convective-realization channel:** HRRR runs no parameterized deep convection, and its explicit products (REFC, echo tops, LTNG) are not ingested yet — so `build_hrrr_cloud_diagnostics` sets `convective_scheme_absent=True`. `assess_convective_nwp` reads that marker and uses its CAPE fallback (`nwp_cape_fallback`) instead of reading the generic band covers as "native scheme present but quiet", which would have graded a CAPE-2000 column NONE (PR #508 review). The marker is a meta field (`NWP_CLOUD_DIAG_META_FIELDS`): both interpolation axes carry it through unchanged, never lerped. Ingesting REFC/echo-top as a real realization channel (corridor-extremum reduction, D2-style §19) is the designed follow-up.
- Source: `noaa-hrrr-bdp-pds.s3.amazonaws.com` (public, no auth) — same `.idx` byte-range infra as GFS (note **2-digit** forecast hour: `wrfprsf{FF}`). Everything lives in the single `wrfprs` file per fhour; sounding set ≈ 190 MB/fhour, diagnostics ≈ 10 MB/fhour. Adjacent planned messages are **coalesced** into single HTTP requests (CLMR|CIMIXR, HGT|TMP|RH|DPT runs — no extra bytes, ~⅓ the requests) and **streamed to the cache file in offset-ordered batches** (`put_cached_from_chunks`) so the ~190 MB payload never sits in memory (the accumulate-then-write pattern held ~3× transiently). Per-fhour logs record messages, HTTP requests, and MB. The live idx names cloud liquid water `CLMR`; `CLWMR` (NCO's documented spelling) is planned too, defensively.
- **Gating (all-or-nothing, same product rule as ICON-D2):** every route point inside the HRRR CONUS grid AND `flight_window_end ≤ run_init + horizon`. The grid is **Lambert-projected** — "inside CONUS" is not a lat/lon rectangle — so the gate transforms route points to grid x/y with the same pyproj projection the decoder uses and checks grid bounds. On any gate failure, or if HRRR enrichment merges nothing, the slot falls back to plain GFS whole (a *partial* HRRR success keeps HRRR and lets fill cover gaps — never half-and-half). A run-finder miss is classified via `hrrr_window_out_of_range` so an S3 outage logs distinctly from the expected beyond-horizon skip.
- **Slot atomicity — staged commit (PR #508 reviews 2–4):** the gfs slot is HRRR-or-untouched at SLOT level. Every requested hour is fetched, decoded and VALIDATED before anything is written (`_stage_one_hrrr_hour` mutates nothing); one unusable hour fails the whole slot back to plain GFS. Validation is three-stage: (1) the `.idx` plan must offer every mandatory variable on every level — T, U, V, HGT, RH|DPT, **and liquid (CLMR|CLWMR) AND ice (CIMIXR) independently** (a one-sided condensate column is a truncated artifact, not a dry forecast; HRRR publishes real 0.0 on clear levels — verified zero NaN-masking — so requiring both never rejects a clear column); (2) any failed byte-range span aborts the artifact instead of committing a truncated cache file; (3) every route point must decode a structurally complete column (same field set; decoded zeros valid). A cached artifact failing (3) is deleted and re-fetched once (pre-validation partials). Cloud diagnostics stay subordinate: their failure costs cover/CAPE for the hour, never the slot.
- **Cycles/horizon:** hourly cycles, but only 00/06/12/18z extend to 48h; the rest stop at 18h. Run selection prefers the freshest cycle whose horizon covers the window; the publication probe HEADs the .idx of the *last needed* forecast hour (HRRR publishes fhours progressively). Full file set ~45–60 min after init. Window hours are the CONTIGUOUS `floor(departure)..ceil(end)` range — never sample-and-`round()`, whose ties-to-even collapses :30 departures onto even hours and silently drops every other native hour.
- **Fill semantics:** all HRRR fields are instantaneous, so the GFS averaged-window machinery (window-midpoint interp, `apply_gfs_rh_condensate_gate`, `_PREFER_AVERAGED_PAIRS`) is **disabled** when HRRR sourced the slot (`gfs_init=None` into `propagate_all`); diagnostics forward-fill, the replaced sounding rides the model-agnostic pressure-level linear interp. With hourly output the gaps are minimal anyway.
- Disk cache at `data/.cache/grib/hrrr/{date}_{cycle}z/` — 9h TTL (an EXTENDED run must outlive the 6h gap to its successor plus the ~1.25h publication delay, or every extended-cycle transition re-downloads ~1 GB); 8 GiB size-cap backstop enforced from the daily retention pass (HRRR is not precached, so there is no warm-loop enforcement site). **Active-run pins (PR #508 review 4):** enrichment holds `pin_run_dir(run_dir)` for its whole stage+commit span, and BOTH eviction rules (TTL + size cap) skip every pinned directory whoever pinned it — a per-call "protect my run" argument could not shield briefing A's older run from briefing B's purge during an extended-cycle transition. Pinned bytes still count toward the enforced total so the cap can't claim success over an omitted directory. In-process registry (single-uvicorn-worker is a standing assumption, see refresh-durability), reference-counted.
- **Native cloud envelope (meteorology-decisions §23):** HRRR ships 3D microphysics but no 3D cloud fraction and no per-band base/top, so a third native source, `build_nwp_cloud_layers_from_condensate`, derives layer GEOMETRY from contiguous runs of condensate ≥ 1e-7 kg/kg (HRRR's own documented cloud-base detection threshold, calibrated against the product's measured packing — liquid quantizes at 1e-5 so its behaviour is unchanged; ice packs to 1e-9 with 98.9% of real values below 1e-5, so the earlier 1e-5 threshold erased thin cirrus) and takes the AMOUNT from the model's own diagnostic band covers (`nwp_cloud_diagnostics.low/mid/high.cover_pct`), selected by NCEP's pressure band definitions (LCDC sfc–642 hPa, MCDC 642–350, HCDC above — runs crossing a boundary split per band), falling back to the Open-Meteo bulk pct with ICAO altitude bands when NO diagnostic band cover exists at all **or when the diagnostics are not NCEP-defined** (`band_definition != "ncep"` — ECMWF/ICON publish band covers over a ~800/400 hPa split, so the NCEP rule must not be applied to them should they ever reach this builder) — never mixed per-band, since a pressure-carved segment must not be graded with a slab-measured number; a single missing diagnostic band degrades to unknown-extent BKN with pct None. A known sub-FEW cover maps to FEW (never the unknown-cover BKN default, never a dropped layer). This is what makes the default `ogimet_nwp` icing method and native SFIP reachable for HRRR. The COMMIT phase of the staged slot replacement is rollback-journaled (`nwp_state_snapshot`/`restore_nwp_state`) so a mid-commit failure restores the pack exactly and falls back to plain GFS cleanly.
- Pack metadata records `model_sources["gfs"]` = `gfs:noaa` or `hrrr:noaa`; the freshness bar badges **`GFS (HRRR)`** — HRRR (WRF-ARW regional) is a genuinely different model from GFS (FV3 global), so the upgrade is visible, and day-over-day assessment jumps at the horizon boundary are expected.
- Freshness source `hrrr:noaa` tracks **extended cycles only** (00/06/12/18z): tracking all 24 hourly cycles would flag short-lead US packs stale (and auto-refresh them) every hour.
- Out of scope: HRRR-Alaska, sub-hourly output, HRRR ensemble; RRFS migration is designed to be a bucket/path + grid-constants swap inside `hrrr_fetch.py`.

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
- Parallel download with ThreadPoolExecutor (`MAX_DOWNLOAD_WORKERS`, default 16 — 37.1 MB/s vs 29.6 at 8, #469; env-tunable)
- **Variant selection (issue #456):** the `icon` slot is served by **ICON-D2** (2.2 km, convection-permitting) when the *whole* route fits the D2 domain (43.18–58.08°N, 3.94°W–20.34°E) AND a complete D2 run's 48h horizon reaches the flight-window end; otherwise by **ICON-EU** (6.5 km, all-Europe) exactly as before. All-or-nothing — never a per-point mix of D2 and EU within one briefing. On total D2 failure the icon slot re-runs cleanly on ICON-EU (never a half-D2 pack).
- The two variants share the whole download/decode path via `IconVariant` (a config object holding domain, cycles, horizon, level slice, filename conventions, cache slug and freshness source key). ICON-D2 filename quirks: model token `icon-d2`, region token `germany`, **lowercase** variable suffix (`…_60_t`), and a `_2d_` segment on single-level files (`…_006_2d_ceiling`).
- Domain: EU 29.5–70.5°N, 23.5°W–62.5°E; D2 43.18–58.08°N, 3.94°W–20.34°E. Routes outside the chosen domain skip silently.
- Cycles: both every 3h (00–21z). EU ~3h publication delay, hourly to 78h then 3-hourly to 120h — but only on the MAIN cycles (00/06/12/18z); the 03/09/15/21z short runs are capped at **30h** (`horizon_short_h`) because f031–f047 aren't published, so a longer flight falls back to the prior main run and keeps a uniform hourly grid. D2 ~2h delay, hourly to 48h on all 8 cycles.
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

### A.2 NOAA HRRR (High-Resolution Rapid Refresh) — IMPLEMENTED (#457)
- **Bucket:** `s3://noaa-hrrr-bdp-pds/`
- **Path:** `hrrr.{YYYYMMDD}/conus/hrrr.t{HH}z.wrfprsf{FF}.grib2` — `HH` hourly 00–23, `FF` **2-digit** forecast hour. `.idx` format identical to GFS.
- **Resolution:** 3 km, **Lambert conformal grid** (1799×1059, tangent 38.5°N, LoV 262.5°E, first point 21.138123°N/237.280472°E, sphere R=6371229 m) — NOT a regular lat/lon grid. Decode interpolates on the projected (y, x) axes via pyproj (grid is regular in projected space, so bilinear stays exact and vectorized); the domain gate uses the same projection.
- **Horizon:** 48h on 00/06/12/18z, 18h on all other hourly cycles. Full file set published ~45–60 min after init.
- **Currently fetching:** full sounding (TMP, DPT, RH, UGRD, VGRD, VVEL, HGT, CLMR, CIMIXR — note **CIMIXR**, HRRR's name for ice mixing ratio, not ICMR; eccodes has no shortName for it and decodes it as `unknown`) at 150–1000 hPa; LCDC/MCDC/HCDC/TCDC, HGT@cloud-ceiling/cloud-base, CAPE/CIN@90-0 mb.
- **Available but not fetched:** RWMR/SNMR/GRLE species, HGT/PRES@cloud-top (no `NWPCloudDiagnostics` home), surface/180-0/255-0 mb CAPE (would collide with the 90-0 pair in cfgrib's `pressureFromGroundLayer` key), VIS, GUST, REFC, LTNG (candidate convective corroborators; would need new surface-field plumbing — future work).
- **Winds are grid-relative** (`uvRelativeToGrid=1`) — rotated to earth-relative at decode (up to ~±15–20° across CONUS).

### B. ECMWF IFS (Commercial via ECPDS) — IMPLEMENTED (full sounding)
- **Delivery:** ECPDS push to local directory (`ECMWF_GRIB_DIR`, default `/data/ecmwf`). Read-only Docker volume mount.
- **Model:** ifs-ens-cf (IFS Ensemble Control Forecast), 0.25° over Europe + US
- **Files:** Two parts per forecast step — a1 (surface, 29 vars) and a2 (pressure levels, 10 vars × 25 levels)
- **Cycles:** 00z/06z/12z/18z. Horizon per cycle is derived from the max step observed on disk, not from the stream name (see `find_best_ecmwf_run` in `fetch/grib/ecmwf_fetch.py`). Subscription shape post-2026-04-22 amendment: 00/12z → 168h, 06/18z → 144h. From IFS Cycle 50r1 (12-May-2026), all four cycles arrive with `stream=oper` — the `scda` label is gone. Init hour, not stream, determines the expected manifest (`delivery_config.json` is keyed by cycle hour).
- **Publication delay:** ~6–8h after init time
- **Naming convention:** `dest_feed_model_class_stream_type_baseTime_validTime_step[_expver]` — no `.grib2` extension by default. `expver` is absent on prod operational files, and `X0080` on TPREd Release Candidate files (ECMWF_ACCEPT_RCP_EXPVER=1 opt-in for staging).
- **Pressure-level (a2):** t, r, u, v, z, w, gh, cc, clwc, ciwc — **full sounding replacement** (replaces Open-Meteo pressure levels entirely). Post-amendment (2026-04-22): `d` (divergence) was dropped, `gh` (geopotential height) added at all 25 levels, removing the hypsometric fallback from the decode path. `z` is still delivered only at 1 hPa (catalogue limitation).
- **Surface (a1) — cloud diagnostics:** ceil, cbh, lcc, mcc, hcc, tcc, hcct, deg0l, **blh**, **kx, totalx, mlcape100, mlcin100, cp** → `NWPCloudDiagnostics` (hcct → `convective_top_ft`; deg0l → `freezing_level_ft` + overwrites `hourly.freezing_level_m`; **blh** → `boundary_layer_top_ft`, AGL→MSL with the model's own orography exactly as deg0l, feeding the CAT boundary-layer ceiling (#540); kx/totalx → `k_index`/`total_totals`; mlcape100/mlcin100 → `ml_cape_jkg`/`ml_cin_jkg`; **cp** is accumulated-since-init, de-accumulated by step-difference in the ECMWF merge loop → `convective_precip_mm_h`). These feed the model-native convective track's firing gate + corroboration (#283).
- **Surface (a1) — surface snapshot:** t2m, d2m, u10, v10, fg10, vis, tp, sf, mucape, sp → `build_ecmwf_surface_snapshot` (unit-converted). Consumed by BOTH the standalone verification pipeline and — via `_apply_ecmwf_surface_to_hourly` in `fetch/grib/__init__.py` — the user-facing briefing, which writes them straight onto `HourlyForecast` (ungated, overwriting Open-Meteo per covered point; uncovered points keep Open-Meteo). Two field classes: `_ECMWF_HOURLY_INSTANT_FIELDS` (T/dewpoint, wind/gust, vis, CAPE, surface pressure, nwp_k_index, nwp_total_totals) written only at the matching `valid_utc` and linearly interpolated later in `fill.py`; `_ECMWF_HOURLY_RATE_FIELDS` (`precipitation_mm`, `snowfall_cm`) step-differenced from the prior a1's cumulative value and spread evenly over the window — with no prior step, Open-Meteo's value stands. The surface write is **coupled to the cloud-diag write** at the same valid time: `fill.py` uses `nwp_cloud_diagnostics is not None` as its GRIB-anchor detector, so the two must stay in the same loop iteration.
- **Surface (a1) — native convective indices:** kx, totalx → `nwp_k_index` / `nwp_total_totals` on `HourlyForecast`, copied onto `ThermodynamicIndices.nwp_k_index/nwp_total_totals` during sounding analysis. The convective character advisory prefers these over the MetPy-derived K/Total-Totals for ECMWF (issue #294). `kx` is delivered in Kelvin and normalized to °C via `_k_index_to_c` (#283); Total Totals is offset-immune and passes through unchanged.
- **Surface (a1) — delivered but not yet processed:** 10fg, capes, degm10l, fzra, lsp, msl, ptype
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
  - Single-level: CEILING, HBAS_CON/HTOP_CON, CLCL/CLCM/CLCH/CLCT, **CAPE_ML/CIN_ML**, **RAIN_CON**, **LPI_CON_MAX/CAPE_CON** → `NWPCloudDiagnostics` (cape_ml/cin_ml → `ml_cape_jkg`/`ml_cin_jkg`, instantaneous, feed the native convective track #283; rain_con → `convective_precip_mm_h`, accumulated-since-init and de-accumulated in the merge loop, #421). rain_con is kg/m² ≡ mm — already mm, so **no** ×1000 (unlike ECMWF `cp`, m water equivalent). The ICON merge prepends one leading single-level step (`icon_eu_previous_step`) so the first window hour has a predecessor to difference against, and the cloud-diag cache key is bumped so warm caches re-fetch — the blob holds all its variables under ONE key, so its label carries the schema version (`_V2` = rain_con #421, **`_V3` = lpi_con_max/cape_con #530**; the same suffix is used for both variants so their schemas cannot drift). lpi_con_max/cape_con → `lpi_con_max_j_kg`/`conv_cape_jkg` (#530): lightning potential across the WHOLE EU domain, where D2's `lpi_max` sees only 43.18–58.08°N/−3.94–20.34°E, plus the convection scheme's own CAPE beside the diagnostic `cape_ml`. **Data availability only — no grader consumes either.** Their cfgrib shortNames were not verifiable against a live message; if DWD delivers them under another name the fields stay `None` (missing-data semantics) — confirm before grading on them. `lpi_con_max` is a window MAX, so it is classified in `NWP_CLOUD_DIAG_RATE_SCALARS` (covering-interval hold), not as an instantaneous scalar.
- **Model levels → pressure levels:** Log-pressure interpolation using P field; 40 model levels → standard pressure levels (`EXTENDED_PRESSURE_LEVELS`, 28-level set)
- **Single-level → NWPCloudDiagnostics:** Heights in meters converted to feet (× 3.28084)
- **W → omega:** physical vertical velocity (m/s) converted to omega (`vertical_velocity_pa_s`) per level via −ρ·g·w
- **Gaps:** No geopotential (FI not on model levels) — derived via hypsometric equation from T+P
- **Publication delay:** ~3h after init time
- **Data retention:** DWD deletes files after ~24h (only latest run available per cycle)
- **Download:** Individual bz2-compressed files, parallel with `MAX_DOWNLOAD_WORKERS` (default 16, env-tunable)

### C.2 DWD ICON-D2 (Central Europe, convection-permitting) — IMPLEMENTED (full sounding, #456)
- **Server:** `https://opendata.dwd.de/weather/nwp/icon-d2/grib/`
- **Model-level path:** `{HH}/{var}/icon-d2_germany_regular-lat-lon_model-level_{YYYYMMDDHH}_{FFF}_{LL}_{var}.grib2.bz2` — note **lowercase** variable suffix (vs ICON-EU's uppercase).
- **Single-level path:** `{HH}/{var}/icon-d2_germany_regular-lat-lon_single-level_{YYYYMMDDHH}_{FFF}_2d_{var}.grib2.bz2` — note the `2d` segment (vs ICON-EU's absent segment).
- **Resolution:** ~2.2 km, regular lat-lon grid (same grid *type* as ICON-EU → existing bilinear interpolation works unchanged).
- **Domain:** 43.18–58.08°N, 3.94°W–20.34°E (Germany, Alps, Benelux, most of France, SE England; excludes Brittany, Scotland, Spain). ~906 k grid points ≈ ICON-EU's ~905 k.
- **Download volume — grid-point parity does NOT mean byte parity.** Measured on run `20260721 09z`, same route, same 3 forecast hours: **D2 494 MB/fhour vs ICON-EU 189 MB/fhour — 2.6×**. Two compounding causes: D2 takes **50 model levels (16–65) vs EU's 40 (35–74)** = 1.25×, and a 2.2 km convection-permitting field is far less compressible than a smooth 6.5 km one, so **per level D2 costs 2.05×** EU (1.54 MB vs 0.75 MB per level-file) at near-identical grid-point counts. The single-level diag blob shows the same effect — 20.6 MB vs 9.6 MB per fhour (2.15×) despite D2's list being *shorter*. Breakdown of a D2 run: model-level sounding 93.5%, explicit-convection fields (#462) 3.8%, single-level diag 2.7% — the sounding data is the cost driver, not the explicit-convection addition.
- **Model levels:** 65 total (bottom = 65; numbering NOT comparable to ICON-EU's 74). Aviation slice **16–65** (50 levels) — level 16 ≈ 9,460 m ≈ FL310, matching ICON-EU's level-35 (~300 hPa) top. Validated against DWD's HHL (half-level height) field decoded from the live feed 2026-07-21; the original guess of 25 sat at ~6,300 m ≈ FL207 and would have truncated every D2 sounding.
- **Cycles/horizon:** 8 runs/day (00–21z every 3h), hourly steps to **48h** (no coarse tail). Publication delay ~1–2h.
- **Variables (#530 — the model-level set is now PER VARIANT):** `t, qv, u, v, p, w, qc, qi, clc` **plus the three precipitating hydrometeors `qr, qs, qg`** (rain water / snow / graupel), verified live 2026-07-30 on the same `regular-lat-lon` model-level layout — `qr`'s file count (3185) is identical to `qc`'s. **ICON-EU publishes none of the three** (its moisture set is `qc/qi/qv`), which is why `IconVariant.model_level_variables` exists rather than one shared tuple. Cost: measured 00z/step-003 sizes are `qg` 2.4 KB, `qr` 18.7 KB against `qc`'s 13.6 KB — the three together ≈ 2.5× one `qc`, i.e. **~21 MB over 50 levels × 12 forecast hours**, negligible against the 494 MB/fhour sounding. Decoded to `rain_water_kg_kg` / `snow_water_kg_kg` / `graupel_water_kg_kg` — names chosen model-agnostically because Météo-France AROME publishes the same ICE3 species (#529) and will fill the same fields. They feed the precipitation-phase partition and supercooled-rain detection (meteorology-decisions §24). `qg` reading 0 is normal, not a bug. Single-level cloud-diag set is **smaller**: `ceiling, clcl/clcm/clch/clct, cape_ml/cin_ml` only — and it does NOT gain #530's `lpi_con_max`/`cape_con`, which are parameterized-convection products a convection-permitting model does not compute. D2 runs no deep-convection scheme, so `hbas_con`/`htop_con` don't exist (404; the shallow-only `hbas_sc`/`htop_sc` would mislead) and `rain_con` is near-zero even in explicit storms — all three deliberately unfetched → downstream fields **None** (missing-data semantics).
- **Explicit-convection fields (#462, implemented; `grau_gsp` dropped #468):** `dbz_ctmax` (column-max reflectivity, dBZ, max over previous hour — the firing signal), `echotop` (shortName `min_pres`, Pa, min per **15-min window, 4 messages/file**, sentinel −999 = no 18 dBZ echo), `lpi_max` (J/kg, hour max), `w_ctmax` (m/s, hour max 0–10 km), `uh_max` (m²/s², SIGNED hour max amplitude, 2–8 km AGL). Fetched into per-variable blobs (`ICON_D2_EXPL_<VAR>_V1`), decoded message-level by `stepRange` (minutes — reading `startStep`/`endStep` as ints silently truncates to hours!) and reduced to **corridor extrema** over a ~10 NM route buffer. `grau_gsp` (surface graupel precip) shipped in #462 but was dropped in #468 — it is a SURFACE accumulation, ~always 0 under warm-season corridor cores, not the column mixed-phase property it was meant to be. `dbz_cmax` (instantaneous) unfetched in v1; `tcond10_mx` (condensate above the −10 °C isotherm) deferred — the candidate replacement corroborator. Feeds `NWPExplicitConvectiveDiagnostics` → `assess_convective_explicit` (meteorology-decisions §19).
- **Gating (all-or-nothing):** used for the `icon` slot only when every route point is inside the D2 domain AND `flight_window_end ≤ selected_run_init + 48h` AND (#462) the route's entire ~10 NM corridor buffer lies in valid cells of the product bitmap (~17% of the regular-ll grid is masked — native domain is not a lat/lon rectangle, and the files carry **no rotated-pole metadata**; the validity mask is built once from a delivered message's bitmap and cached, failing open to the bbox gate). If any check fails → pure ICON-EU. If the freshest complete D2 run doesn't cover the window, fall back to ICON-EU rather than a stale D2 run.
- **Freshness:** source key `icon_d2:dwd` in `SOURCE_REGISTRY` (readiness check `icon_d2_dwd`), cache dir `data/.cache/grib/icon-d2/{date}_{cycle}z/` (6h TTL).
- **Flight-driven cache warming (#469 phase 3):** after each D2 run publishes, `precache_icon_d2_flights` warms the D2 cache for the *actual* flights in the DB departing within the next 48h (D2's horizon), using their real routes and window hours. It reuses the briefing prepare+prefetch path verbatim (`_prepare_icon_eu` → `_prefetch_icon_eu_data`), so the warmed cache is byte-for-byte what an on-demand briefing reads — same run, same per-level files over the same unconditional full model column — giving a near-100% hit rate. It deliberately **rejects** a broad daylight-band precache (measured ~37–75 GB/day, ~20% hit rate, 2–4× more DWD bandwidth than on-demand). Only D2-eligible flights are warmed here (EU warming is the airport-profile precache's job). Wired into `run_grib_precache_loop` on every fresh `icon_d2:dwd` marker (all 8 cycles, keyed `icon_d2:flights` so it can't collide with the EU/GFS `last_done` entries); gated by `WB_GRIB_PRECACHE_ENABLED` (off in dev), and restricted to a 03Z–21Z wall-clock window (see below).
- **Measured warming sizing (#475, real prod data 2026-07-22 — supersedes the earlier "a few GB/day" estimate, which was ~20× low because dev has 2 future flights and prod has 73):** DWD serves whole-domain files with no byte-range/`.idx` subsetting, so warming downloads all of Germany to sample a 10 NM corridor — the only levers are fewer hours/levels/runs.
  - **Unit cost:** one `(variable, level)` model-level file = 948 KB bz2 / 1.62 MB on disk (ratio 0.58).
  - **Per forecast-hour** (the full column, unconditionally — the ceiling cut was removed in #478): 9 vars × 50 levels (16–65) = 450 files ≈ 427 MB download / 730 MB disk.
  - **Prod load:** 73 future flights, 20–26 D2-eligible per run, per-run union 21–29 forecast-hours. All 8 passes/day = 817 forecast-hours ≈ **71.8 GB/day**.
  - **What the 03Z–21Z window actually saves: ~13%, not the 24% first estimated in #475.** The naive estimate assumed both the 23Z and 02Z passes vanish. Only the 23Z one does. The 02Z pass is *deferred, not dropped*: at 02Z the 00z run has published but the window is shut, so `last_done` is left untouched — and at 03Z, when the window opens, that same 00z run is still the freshest (03z doesn't publish until 05Z), so it is warmed then. That is the intended "warm whatever is freshest at window-open" behaviour, and it is desirable (prod has 05Z departures, whose pilots check around 03–04Z). Net: 7 of 8 passes run, ~104 of 817 forecast-hours saved ≈ **~43 GB** over the measured ~4.75-day window, landing near **~62 GB/day**. Only the run published at 23Z is never warmed.
  - **Peak retained cache** (whole run dirs expiring by TTL, *once aged by init time* — see cache retention below): TTL 6h → **~41.6 GB** (2 runs); TTL 3h → ~21.2 GB (1 run, loses the prior-run fallback).
- **Cache retention (#475):** three changes land the warming safely on prod's ~52 GB free.
  - **Age by init time, not dir mtime** (`purge_old_runs`): the init in the dir name (`{YYYYMMDD}_{HH}z`) is authoritative; dir mtime resets to "now" every time a later briefing tops a run up with a new `(fhour, var, level)` file, silently stretching the TTL ~75% (observed ICON-EU: 12z run survived to ~init+21h). Falls back to mtime for names that don't parse. Expect a one-off ICON-EU cache shrink after deploy — that is the fix working.
  - **Size-cap backstop** (`cache_cap_bytes` → `_enforce_size_cap`, `enforce_cap=True`): after the TTL rule, evict whole run dirs oldest-init-first until the model total fits `WB_GRIB_CACHE_CAP_GB_<MODEL>` (default 45 GiB for `icon-d2`), never below a floor of 2 runs (current + prior fallback). Whole-dir, not per-file: per-level re-download would trade scarce DWD bandwidth for disk. Not LRU — a stale run is stale regardless of read recency, so init age is the right signal. Every eviction is logged. The cap check needs a recursive size walk, so it runs **only from the scheduled contexts** — the warm loop (after each warm) and the daily retention pass — never on the per-briefing `_prepare_icon_eu` path (which passes `enforce_cap=False` and does the cheap name-based TTL purge only; init-time aging already bounds it to ~2 runs). All the purge helpers degrade-to-skip on a concurrent-vanish race rather than propagating into enrichment.
  - **03Z–21Z wall-clock warming window** (`MODEL_WARMING_WINDOW_UTC` → `is_within_warming_window`/`should_warm`): gate on the wall-clock time the pass runs (not the run's init hour), per-model so US expansion is config not code. Default `[3, 21)` for `icon-d2` (drops the 23Z/02Z passes) and `icon-eu` (drops the 21Z pass); GFS ungated (cheapest model, and the one US expansion needs running overnight). A pass outside the window is **skipped, not deferred**: `last_done` is left untouched and the run is never backfilled, so the next in-window tick warms whatever run is freshest then.
- **Warming yields to interactive briefings (#490):** warming is the one GRIB consumer that can be interrupted for free — it is idempotent, and everything already fetched is an `is_cached` skip next tick. A briefing cannot be interrupted, and the two collide badly: warm cache writes charge page cache to the container cgroup, pinning it near its limit (observed 6058/6144 MB at ~2.2 GB real RSS), so a concurrent briefing's decode workers find no reclaim headroom — on 2026-07-23 05:09Z the OOM killer took the container down mid-refresh and the user retried twice. So the warm pass polls `precache.interactive_refresh_active()` only when a real download is pending — before each ICON-EU variable/cloud-diag fetch, before each uncached GFS hour, and — for D2 — between every `(fhour, variable)` download unit via the prefetch's `abort_if` — never ahead of a free `is_cached` hit, so a fully-warmed flight has no jobs and completes even mid-refresh, and a resumption pass fast-forwards through the warmed prefix instead of stalling at unit 0 for a whole burst (PR #498 review).

  The D2 check was **once per flight** until #501: a pass that had just cleared it was committed to that flight's whole job list, ~80 s during which its buffers and half the connection pool sit on top of any briefing that starts, including the moment that briefing peaks. Per-unit cuts that window to a single download. Stopping mid-flight is safe because every finished unit is its own atomic `put_cached`, so the next pass rebuilds a shorter list — but the interrupted flight is only partly warmed and is not counted in `flights_warmed`. Note the gate can only fire as often as it is *consulted against real progress*: on the parallel (`outer > 1`) path, submission is throttled to completions, because `submit()` is non-blocking and a check merely interleaved between dispatches would drain the whole list in microseconds while the gate's state moves in seconds (PR #504 review). It bails out while `refresh_registry` holds anything queued/refreshing (user or scheduler — the 05Z burst is largely scheduler auto-refreshes) plus a 60 s cooldown after the last finishes, so a warm never restarts into the memory tail of a refresh. A bailed pass returns `deferred=1`; `run_grib_precache_loop` then leaves `last_done` unset and skips the cap walk, so the next 5-min tick resumes the same run. Same "skipped, not recorded" contract as the wall-clock window above, different trigger. Registry entries older than 30 min (`STALE_ENTRY_SECONDS`) are treated as leaked rather than active, so one missed unregister can't silently disable warming until the next restart.
- **Ceiling-limited fetch (#469 phase 2) — EVALUATED, REJECTED, REMOVED (#478).** The idea was to fetch the wind/cloud sounding variables (`u, v, w, qc, qi, clc`) only down to a ceiling-derived model level while keeping `t, qv, p` full column for the CAPE integral. It shipped gated off, was re-landed as PR #477, and was then removed outright. **Why it failed on economics:** the cut has to reach `ceiling + 5,000 ft`, because the cross-section and Skew-T render that high and ICON-D2 would otherwise draw a blank strip where the full-column models draw. Measured against 73 real prod flights that leaves **45% of flights (33/73) truncating nothing at all** and a **flight-weighted saving of ~9.8%** — roughly 6 GB/day of ~62 GB/day. **Why it failed on safety:** the cut is necessarily *asymmetric* (t/qv/p full, the rest cut), so upper pressure levels carry temperature but no wind and no cloud fraction. Nothing recorded that this was intentional, so consumers had to **infer** truncation from the shape of the decoded data — an inference that cannot distinguish intent from fetch failure (all-`u`/`v`-failed, clc-only-failed, and legacy whole-column blobs all defeat it), and whose every miss fails toward *clear / calm / smooth*: the exact direction the safety asymmetry forbids. Making it correct needs explicit fetch provenance threaded through fetch → decode → analysis → pack schema → DTOs → web → iOS, which is not worth <10% of one model's fetch volume. **Do not re-propose without new numbers**: re-measure the marginal saving first, given phase-3 warming already runs at a near-100% hit rate. If it is ever revisited, a **uniform** cut across all variables avoids the mixed column entirely (no asymmetry, so nothing to infer) but must then solve CAPE, which genuinely needs the thermodynamic column above the flight.
- **Per-level cache layout (#469 phase 1, `per_level_cache=True`):** the model-level sounding is cached one file per (variable, level) — `f029_ICON_D2_T_L27.grib2` (`icon_model_level_var_label`) — not one whole-column blob per variable like ICON-EU. The justification is **partial-write safety**, not efficiency: DWD publishes one file per level either way, so the layout is volume-neutral, but a whole-column cache key cannot distinguish a complete column from a partial one — if some level downloads fail, the concatenated blob is still cached under a key that reports "present", and every later briefing silently reads a short sounding. Per level, `is_cached` answers per level, so a failed level is visibly missing: `_decode_and_merge_icon_eu` **requires the complete set** and otherwise **skips that forecast hour for ICON entirely** (#478) — a missing hour is a state the pipeline handles honestly, a partial column is not — and the next briefing tops up only the files actually absent. The prefetch (`fetch_icon_eu_per_level`) and decode (`decode_icon_chunked`, which concatenates a per-variable *list* of level files) share the path via the `per_level_cache` flag; cfgrib indexes by the level coordinate so concatenation order is irrelevant. Migration is free: whole-column blobs written by older code — both `{prefix}_{VAR}` (`icon_model_level_var_legacy_label`) and the even-older `{prefix}_QC_QI_P` — are refused outright by per-level variants (#478): the writer concatenates whatever levels arrived, so a blob's level count is unverifiable after the fact and trusting one would re-open the very ambiguity the per-level layout closes. ICON-EU keeps the whole-column blob — and there `fetch_icon_eu_per_variable` now caches a variable only when EVERY level downloaded, discarding partials rather than baking a short column into the cache for the run's TTL. Independently of level counts, an hour missing a whole VARIABLE is skipped for every variant: losing u/v alone flattens CAT to a false "smooth".
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
| `CAPE_ML` | Mixed-layer CAPE | J/kg | **Implemented** | Convective potential energy → `ml_cape_jkg` (#283). |
| `CIN_ML` | Mixed-layer CIN | J/kg | **Implemented** | Convective inhibition → `ml_cin_jkg`, sign-normalized (#283). |
| `RAIN_CON` | Convective rain, accumulated | kg/m² ≡ mm | **Implemented** | De-accumulated to `convective_precip_mm_h` (#421). Decodes as shortName `crr`. |
| `LPI_CON_MAX` | Lightning Potential Index, interval max | J/kg | **Implemented** (#530) | → `lpi_con_max_j_kg`. Whole-EU lightning signal where D2's `lpi_max` covers only central Europe. **No consumer yet.** |
| `CAPE_CON` | Convection scheme's own CAPE | J/kg | **Implemented** (#530) | → `conv_cape_jkg`, beside the diagnostic `cape_ml`. **No consumer yet.** |

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
| `QR` | Rain water mixing ratio | kg/kg | — | **NOT PUBLISHED on ICON-EU** | Verified against live directory listings 2026-07-30 (#530): ICON-EU's model-level moisture set is `QC`/`QI`/`QV` only. Earlier revisions of this table listed QR/QS as "Available" — they are not. **ICON-D2 does publish them** (see C.2). |
| `QS` | Snow mixing ratio | kg/kg | — | **NOT PUBLISHED on ICON-EU** | Same (#530). |
| `QG` | Graupel mixing ratio | kg/kg | — | **NOT PUBLISHED on ICON-EU** | Same (#530). |
| `TKE` | Turbulent kinetic energy | m²/s² | 35–74 | Available, **opt-in and off** | Published, but ~903 KB per level-hour (≈36 MB per forecast hour over 40 levels — turbulence compresses badly) and there is no TKE consumer in the analysis. Fetched only when `WB_ICON_TKE` names the variant (`icon-eu`, `icon-d2`, or `all`); unset = off for both. Deliberately outside `IconVariant.model_level_variables`, so a failed optional download can never trip the #478 "incomplete column → skip the hour" guard. |

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
| **Time — everything else** | Forward-fill for ICON-EU / ECMWF cloud diagnostics, the GFS fallback path (no `gfs_init`), and the HRRR-sourced gfs slot (all-instantaneous, so `gfs_init=None` is passed deliberately, #457); step-time linear interp for GFS CLW/ICMR overlay; linear interp for ECMWF surface scalars and replaced pressure-level soundings (ECMWF / ICON / HRRR — dewpoint derived from interpolated T+RH via Magnus). | `fetch/grib/fill.py` | Cloud diagnostics (non-GFS bands), CLW, ICMR, ECMWF surface, sounding rebuild |
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

**1. Remaining ECMWF a1 surface variables** — the ~10 vars in `build_ecmwf_surface_snapshot` are DONE (decoded and live on the user-facing forecast, see B). Still undecoded: 10fg, capes, degm10l, fzra, lsp, msl, ptype — the CAPE variants, freezing-level family and precip type are the interesting ones.

**2. ~~ECMWF order: `z` on all 25 pressure levels~~ — DONE.** The 2026-04-22 amendment added `gh` at all 25 levels (dropping `d`), so geopotential height is read directly. `z` itself is still 1 hPa only, and no longer matters.

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

### HRRR
- **Lambert grid** — cfgrib decodes with dims `(y, x)` (integer-index coords) and 2-D lat/lon auxiliary arrays; there are no 1-D lat/lon dimension coords. The shared decode helpers branch on this: build the projection from the message's own GRIB attributes, transform targets with pyproj, interpolate on the (y, x) axes. Do NOT scipy-`griddata` the 2-D lat/lon arrays.
- **CIMIXR decodes as `unknown`** — NCEP-local parameter with no eccodes shortName. The HRRR var map maps `unknown` → ice mixing ratio, which is safe only because the decoder runs exclusively on our own byte-ranged downloads.
- **Grid-relative winds** — `uvRelativeToGrid=1`; rotate U/V to earth-relative before deriving speed/direction (the rotation is derived numerically from the projection, so it can't drift from the interpolation math).
- **`1013.2 mb` level** — HRRR carries one fractional near-surface level; the integer-hPa idx regex drops it (PressureLevelData is integral; the surface anchor comes from surface fields).
- **All fields instantaneous** — never run the GFS averaged-window machinery on HRRR data.
- **CIN sign** — HRRR CIN is already negative J/kg (verified −745…0); do not renegate via `_normalize_model_cin`.
- **Progressive publication** — a cycle's forecast hours appear over the delivery window; probe the last-needed fhour's .idx, not f00.

### ICON-EU
- **Model levels, not pressure levels** — QC/QI are on model levels (35–74). The P variable provides per-gridpoint pressure at each level. Must interpolate vertically using log-pressure.
- **Longitude convention** — ICON-EU uses -180 to +180° (same as route points). No normalization needed (unlike GFS).
- **Level coordinate names** — cfgrib may use `generalVerticalLayer`, `generalVertical`, `level`, or `hybrid` for model-level data. Check all variants.
- **bz2 decompression** — Files are bz2-compressed. Decompress before passing to cfgrib.
- **Data retention** — DWD deletes files after ~24h. Only the latest run per cycle is available.
- **Download volume** — one file per (variable, level, forecast hour): 9 model-level vars × 40 levels = 360 files *per forecast hour* on EU (12 vars × 50 levels = 600 on D2). Parallel download essential.

## References

- Implementation: `src/weatherbrief/fetch/grib/`
- Fetch design: [fetch.md](./fetch.md)
- Icing analysis (LWC consumer): [analysis.md](./analysis.md)
- Data models (CLWMR/ICMR fields): [data-models.md](./data-models.md)
- [GRIB2 Table 4.2-0-19 (Physical Atmospheric Properties)](https://www.nco.ncep.noaa.gov/pmb/docs/grib2/grib2_doc/grib2_table4-2-0-19.shtml) — EDR parameter definitions
- [WAFS Help (Aviation Weather Center)](https://aviationweather.gov/wafs/help.html) — WAFS product descriptions
- [GFS v16 Service Change Notice SCN21-20](https://www.weather.gov/media/notification/pdf2/scn21-20gfs_v16.0_aac.pdf) — wafs_0p25 file rename, variable additions
- [G-GTG turbulence prediction (BAMS 2018)](https://journals.ametsoc.org/view/journals/bams/99/11/bams-d-17-0117.1.xml) — algorithm behind WAFS turbulence
