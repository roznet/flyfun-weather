# Analysis Metrics Reference

> Comprehensive catalog of all weather metrics: data sources, derivation methods, per-model availability, physical interpretation, and aviation relevance.

## How to use this doc / source of truth

This doc has two audiences: a **human/meteorologist** reviewing how we interpret weather signals, and **Claude** needing a map of what's available without reading all the code. Its lasting value is the *physical interpretation and aviation relevance* of each metric (what it measures, why it matters for GA) — that content is stable and worth trusting.

Two kinds of content here are **code-derived and may drift** — treat them as a guide, not gospel, and confirm against code when it matters:
- the **per-model availability matrix** (the yes/no columns) mirrors what the fetch layer requests per model — verify against the fetch/GRIB config (`fetch/`, [weather-engine-specs.md](./weather-engine-specs.md));
- the **exact threshold numbers** (e.g. divergence cutoffs, classification bands) mirror constants in the analyzers / advisory registry (`analysis/sounding/`, `analysis/advisories/registry.py`).

## Overview

WeatherBrief computes ~85 metrics from NWP model data across 7 weather models (GFS, ECMWF, ICON, Météo-France, UKMO, GEM, Best-Match). Each metric falls into one of four source categories:

- **API** — fetched directly from Open-Meteo
- **GRIB2** — fetched from raw GRIB2 files (GFS NOAA S3, DWD ICON-EU opendata, ECMWF ECPDS commercial)
- **Derived** — calculated from API/GRIB2 data using MetPy or physics formulas
- **Assessed** — classified from derived values using aviation-specific thresholds

When an API field is unavailable, a documented physical fallback may fill the
gap. If the authoritative method has no documented fallback, the assessment is
partial or unavailable; absence must not be interpreted as a clear value.

### Issue #223 audit boundary

Issue #223 made **objective computation and contract corrections**: SFIP
`_no_vv` variants normalize the weights of present inputs, DD/NWP cloud Jaccard
merges each side before intersecting the unions, native-cloud absence is distinct
from assessed-clear geometry, and the route-map display uses the existing SFIP
15/30/55 boundaries instead of the former 20/50/80 display mapping.

These are not meteorological recalibrations. The audited SFIP risk boundaries,
DD cloud/okta cut-points, Ogimet curves, convective thresholds, turbulence/Ri
rules, SLD rules, and advisory severity thresholds remain unchanged. Any future
calibration change requires literature, an independent oracle, or observations;
see [meteorology-decisions.md](./meteorology-decisions.md).

### Raw Value Preservation

**Design principle:** Always preserve raw NWP model values alongside MetPy-derived equivalents in `ThermodynamicIndices`.

NWP models compute thermodynamic indices internally using their full vertical resolution (50–140 levels). MetPy re-derives them from 8–28 pressure levels available via the API. When pressure data is coarse or has gaps, MetPy values can diverge significantly — e.g., showing convective instability when the model's own CAPE is zero. Preserving both enables validation, fallback, and transparency.

**Naming convention:** `nwp_` prefix on `ThermodynamicIndices` fields, following the existing `nwp_ceiling_ft` and `nwp_cloud_diagnostics` pattern.

**Preserved raw fields:**

| Raw Field | Source | MetPy Equivalent | Divergence Detection |
|-----------|--------|-------------------|---------------------|
| `nwp_cape_jkg` | `HourlyForecast.cape_jkg` | `cape_surface_jkg` | `cape_raw_vs_calc_divergent`: >200 J/kg or >100% relative diff |
| `nwp_cape_type` | Model-specific annotation | — | Indicates what CAPE type the model provides: sb/ml/mu/unknown |
| `nwp_cin_jkg` | `HourlyForecast.convective_inhibition_jkg` | `cin_surface_jkg` | — |
| `nwp_lifted_index` | `HourlyForecast.lifted_index_raw` | `lifted_index` | — |
| `nwp_freezing_level_ft` | `HourlyForecast.freezing_level_m` × 3.28084 | `freezing_level_ft` | — |
| `nwp_ceiling_ft` | `NWPCloudDiagnostics.ceiling_ft` | `sounding_ceiling_ft` | — |

---

## 1. Raw Input Variables (from Open-Meteo API)

### 1.1 Surface Variables

| Variable | Unit | GFS | ECMWF | ICON | MétéoFr | UKMO | GEM | Physics / Interpretation |
|----------|------|-----|-------|------|---------|------|-----|--------------------------|
| `temperature_2m` | °C | yes | yes | yes | yes | yes | yes | Screen-level air temperature. Primary surface condition indicator. |
| `relative_humidity_2m` | % | yes | yes | yes | yes | yes | yes | Surface moisture saturation. Near 100% = fog/mist risk. |
| `dewpoint_2m` | °C | yes | yes | yes | yes | yes | yes | Temperature at which condensation begins. T−Td < 3°C = visible moisture likely. |
| `surface_pressure` | hPa | yes | yes | yes | yes | yes | yes | Actual station pressure. Used to anchor sounding profiles. |
| `pressure_msl` | hPa | yes | yes | yes | yes | yes | yes | Sea-level-corrected pressure. Altimeter setting proxy. |
| `wind_speed_10m` | kt | yes | yes | yes | yes | yes | yes | 10m wind speed for surface ops. |
| `wind_direction_10m` | ° | yes | yes | yes | yes | yes | yes | Surface wind direction (meteorological convention: direction FROM). |
| `wind_gusts_10m` | kt | yes | yes | yes | yes | yes | yes | Peak gust. Relevant for crosswind limits and turbulence on approach. |
| `precipitation` | mm | yes | yes | yes | yes | yes | yes | Hourly accumulated precipitation. Any precip in sub-zero temps = icing concern. |
| `precipitation_probability` | % | yes | yes | yes | **no** | **no** | yes | Ensemble-derived probability. |
| `cloud_cover` | % | yes | yes | yes | yes | yes | yes | Total column cloud cover from model parameterization. |
| `cloud_cover_low` | % | yes | yes | yes | yes | yes | yes | SFC–6500ft (ICAO low). Low ceilings = IFR/LIFR risk. |
| `cloud_cover_mid` | % | yes | yes | yes | yes | yes | yes | 6500–20000ft (ICAO mid). Relevant for en-route icing. |
| `cloud_cover_high` | % | yes | yes | yes | yes | yes | yes | 20000ft+ (ICAO high). Cirrus, usually no icing concern. |
| `freezing_level_height` | m | yes | **no** | yes | **no** | yes | **no** | NWP-computed 0°C isotherm height. Upper boundary of rain, lower boundary of icing. |
| `cape` | J/kg | yes | yes | yes | yes | yes | yes | NWP-computed convective available potential energy. Type varies: GFS/best_match=SB, ECMWF=MU, ICON=ML. |
| `convective_inhibition` | J/kg | yes | yes | **no** | **no** | yes | **no** | NWP-computed CIN. GFS, ECMWF, UKMO. Preserved as `nwp_cin_jkg` for validation vs MetPy-derived CIN. |
| `lifted_index` | — | yes | **no** | **no** | **no** | **no** | **no** | NWP-computed lifted index. GFS only. Preserved as `nwp_lifted_index` for validation vs MetPy-derived LI. |
| `visibility` | m | yes | **no** | yes | **no** | yes | **no** | Parameterized horizontal visibility. < 5000m = marginal VFR. |
| `rain` | mm | yes | yes | yes | yes | yes | yes | Liquid precipitation only. Used for precipitation phase classification. |
| `showers` | mm | yes | yes | yes | yes | yes | yes | Convective precipitation. |
| `snowfall` | cm | yes | yes | yes | yes | yes | yes | Solid precipitation. Used for surface phase assessment. |

### 1.2 Pressure Level Variables

Pressure levels vary by model (range 1000–150 hPa, ~SFC to ~FL450):

| Model | Levels | Count | Note |
|-------|--------|-------|------|
| GFS / Best-Match | 1000, 975, 950, ..., 300, 250, 200, 150 | 28 | 25 hPa spacing below 500, extends to FL450 |
| ECMWF (Open-Meteo) | 1000, 925, 850, 700, 600, 500, 400, 300, 250, 200, 150, 100, 50 | 13 | Fallback when outside direct-GRIB coverage or beyond 7-day range |
| ECMWF (direct GRIB) | 1000, 950, 925, 900, 850, 800, 700, 600, 500, 400, 300, 250, 200, 150, 100, 70, 50, 30, 20, 10, 7, 5, 3, 2, 1 | 25 | ECPDS push, Europe + US, 0–168h at 00/12z, 0–144h at 06/18z. Replaces the 13-level Open-Meteo sounding for covered points. Horizon is derived from files on disk, not stream name (robust to the 50r1 `scda`→`oper` merge). |
| ICON | 1000, 975, 950, 925, 900, 850, 800, 700, 600, 500, 400, 300, 250, 200, 150, 100, 70, 50, 30 | 19 | Open-Meteo (verified Mar 2026), used outside the ICON-EU domain. Within Europe, DWD ICON-EU GRIB now does a **full sounding replacement** to the 28-level EXTENDED set (T/RH/wind/geopotential + omega from `w`, plus CLW/ICMR and 3D cloud fraction `clc`) — same mechanism as ECMWF, see §1.3. |
| Météo-France | 1000, 950, 925, ..., 300, 250, 200, 150 | 19 | |
| UKMO | 1000, 975, 950, ..., 300, 250, 200, 150 | 20 | Supports vertical_velocity |
| GEM | 1000, 950, 925, ..., 300, 250, 200, 150 | 20 | |

| Variable | Unit | GFS | ECMWF | ICON | MétéoFr | UKMO | GEM | Physics / Interpretation |
|----------|------|-----|-------|------|---------|------|-----|--------------------------|
| `temperature` | °C | yes | yes | yes | yes | yes | yes | Air temperature at level. Primary driver for icing type and severity. |
| `relative_humidity` | % | yes | yes | yes | yes | yes | yes | Moisture saturation at level. > 80% suggests cloud, > 95% = likely in cloud. |
| `dewpoint` | °C | yes | yes | yes | yes | yes | yes | Direct dewpoint. More physically meaningful than RH for cloud detection. |
| `wind_speed` | kt | yes | yes | yes | yes | yes | yes | Wind at level for headwind/crosswind and shear calculations. |
| `wind_direction` | ° | yes | yes | yes | yes | yes | yes | Wind direction at level. |
| `geopotential_height` | m | yes | yes | yes | yes | yes | yes | Height of pressure surface. Converts pressure levels to altitude. |
| `vertical_velocity` | Pa/s | yes | yes | **no**† | **no** | yes | **no** | Omega (ω). Negative = ascent, positive = subsidence. Key for vertical motion and turbulence analysis. From Open-Meteo API: GFS, ECMWF, UKMO. †ICON has no API omega, but ICON-EU GRIB now supplies it (derived from model `w`) inside the EU domain — so ICON gets vertical-motion/CAT analysis there. |

### 1.3 GRIB2 Enrichment Variables

Direct GRIB2 fetch supplements Open-Meteo with variables it doesn't provide. See [weather-engine-specs.md](./weather-engine-specs.md) for implementation details. The GRIB model run init times are tracked separately in `BriefingPackMeta.grib_init_times` — when they differ from the Open-Meteo init times, the freshness bar annotates the discrepancy (e.g., "GFS 12Z (GRIB 18Z)").

#### GFS Pressure-Level Variables (via NOAA S3)

| Variable | GRIB2 Name | Unit | Source | Aviation Use |
|----------|-----------|------|--------|-------------|
| Cloud Liquid Water Mixing Ratio | CLMR (idx) / clwmr (cfgrib) | kg/kg | `noaa-gfs-bdp-pds` S3, HTTP Range via `.idx` | Direct measure of supercooled liquid water. Primary input for SFIP icing index. |
| Ice Mixing Ratio | ICMR | kg/kg | Same | Glaciation factor: high ICMR + low CLWMR = mostly glaciated cloud, lower icing risk. |

Fetched at all GFS pressure levels. Bilinear spatial interpolation to route points.

#### GFS Cloud Diagnostics (via NOAA S3)

Scalar fields (no pressure dimension) providing NWP-native cloud structure. Stored in `NWPCloudDiagnostics` model.

| Field | GRIB2 Source | Stored As | Unit | Aviation Use |
|-------|-------------|-----------|------|-------------|
| Low cloud cover | LCC (lowCloudLayer) | `low.cover_pct` | % | SFC–6500ft cloud coverage from model cloud scheme |
| Mid cloud cover | MCC (middleCloudLayer) | `mid.cover_pct` | % | 6500–20000ft coverage |
| High cloud cover | HCC (highCloudLayer) | `high.cover_pct` | % | >20000ft coverage |
| Total cloud cover | TCC (atmosphere) | `total_cover_pct` | % | Full-column coverage |
| Convective cloud cover | TCC (convectiveCloudLayer) | `convective_cover_pct` | % | Convection-specific clouds |
| Boundary layer cloud | TCC (boundaryLayerCloudLayer) | `boundary_cover_pct` | % | Sub-6500ft layer |
| Low cloud base/top | PRES (lowCloudBottom/Top) | `low.base_ft`, `low.top_ft` | Pa→ft | Low ceiling detection |
| Mid cloud base/top | PRES (middleCloudBottom/Top) | `mid.base_ft`, `mid.top_ft` | Pa→ft | En-route cloud layers |
| High cloud base/top | PRES (highCloudBottom/Top) | `high.base_ft`, `high.top_ft` | Pa→ft | Cirrus boundaries |
| Convective base/top | PRES (convectiveCloudBottom/Top) | `convective_base_ft`, `convective_top_ft` | Pa→ft | Cb extent |
| Cloud top temperatures | TMP (low/mid/highCloudTop) | `*.top_temp_c` | K→°C | Cloud-top icing type |
| Cloud ceiling | GH (cloudCeiling) | `ceiling_ft` | gpm→ft | Lowest opaque layer — primary IFR/VFR metric |

Unit conversions: Pa → altitude ft via standard atmosphere, K → °C, gpm → ft (×3.28084).

#### ICON-EU Pressure-Level Variables (via DWD opendata)

ICON-EU GRIB now does a **full sounding replacement** (not just enrichment) for routes in the EU domain — fetched on model levels, log-pressure interpolated to the 28-level EXTENDED set, replacing the 19-level Open-Meteo sounding.

| Variable | GRIB2 Name | Unit | Source | Note |
|----------|-----------|------|--------|------|
| Temperature | T | K | `opendata.dwd.de/weather/nwp/icon-eu/grib/` | Full replacement |
| Specific humidity | QV | kg/kg | Same | → RH/dewpoint (relhum unavailable on these levels) |
| U / V wind | U, V | m/s | Same | → wind speed/direction |
| Vertical velocity | W | m/s | Same | → omega (Pa/s) via `ω = −ρ·g·w`; enables ICON vertical-motion/CAT and SFIP `full` (with ω) |
| Cloud fraction (3D) | CLC | % | Same | Per-level cloud fraction → `nwp_3d` cloud layers |
| Cloud Liquid Water | QC | kg/kg | Same | On model levels, interpolated to pressure levels |
| Cloud Ice Mixing Ratio | QI | kg/kg | Same | Same interpolation |
| Pressure | P | Pa | Same | Used for log-pressure vertical interpolation |

#### ICON-EU Cloud Diagnostics (via DWD opendata)

Single-level scalar fields providing cloud ceiling, convective boundaries, and layer cloud cover percentages. Stored in `NWPCloudDiagnostics` model.

| Field | GRIB2 Variable | Stored As | Unit | Aviation Use |
|-------|---------------|-----------|------|-------------|
| Cloud ceiling | CEILING | `ceiling_ft` | m→ft | Lowest opaque cloud layer height |
| Convective cloud base | HBAS_CON | `convective_base_ft` | m→ft | Cb base altitude |
| Convective cloud top | HTOP_CON | `convective_top_ft` | m→ft | Cb top altitude |
| Low cloud cover | CLCL | `low.cover_pct` | % | SFC–6500ft cloud fraction |
| Medium cloud cover | CLCM | `mid.cover_pct` | % | 6500–20000ft cloud fraction |
| High cloud cover | CLCH | `high.cover_pct` | % | >20000ft cloud fraction |
| Total cloud cover | CLCT | `total_cover_pct` | % | Full-column cloud fraction |

Unit conversion: meters → feet (× 3.28084) for heights. Cloud cover percentages (0–100%) stored as-is. Unlike GFS which uses gpm for ceiling and Pa for cloud boundaries, ICON-EU reports all heights in meters. CLCL/CLCM/CLCH/CLCT supply the bulk band percentages; the per-level `clc` (above) additionally drives `nwp_3d` cloud layers and `_nwp_cloud_cover_at()` with model-native data instead of Open-Meteo ICAO band values.

**ICON-EU specifics:** Domain 29.5–70.5°N, 23.5°W–62.5°E (Europe only). Routes outside are silently skipped. Model-level data (not pressure levels) — log-pressure interpolation using P field to the 28-level EXTENDED set (a full sounding replacement). Single-level data requires no vertical interpolation. ~6.5km resolution. Cycles every 3h, ~3h publication delay, files deleted after ~24h.

#### ECMWF IFS Pressure-Level Variables (via ECPDS commercial)

Full sounding replacement — GRIB a2 files deliver all 25 pressure levels (1000–1 hPa). Replaces the 13-level Open-Meteo sounding entirely for covered points.

| Variable | GRIB2 Name | Unit | Stored As | Note |
|----------|-----------|------|-----------|------|
| Temperature | t | K | `temperature_c` (K→°C) | Full replacement |
| Relative humidity | r | % | `relative_humidity_pct` | Full replacement |
| U wind | u | m/s | → `wind_speed_kt`, `wind_direction_deg` (m/s→kt) | Combined with v |
| V wind | v | m/s | Same | |
| Geopotential | z | m²/s² | `geopotential_height_m` (z/9.80665) | Delivered only at 1 hPa; other 24 levels filled via hypsometric integration from T+P (≈1% accurate until the `z` on all levels amendment lands) |
| Vertical velocity (omega) | w | Pa/s | `vertical_velocity_pa_s` | Enables SFIP_O full variant for ECMWF |
| Cloud liquid water | clwc | kg/kg | `cloud_liquid_water_kg_kg` | SFIP/Ogimet-NWP input |
| Cloud ice water | ciwc | kg/kg | `ice_mixing_ratio_kg_kg` | Glaciation factor input |
| Cloud fraction per level | cc | 0–1 | `cloud_area_fraction_pct` (×100) | Drives per-level cloud layers |
| Divergence | d | 1/s | Not currently consumed | Delivered but unused |

#### ECMWF IFS Cloud Diagnostics (via ECPDS commercial)

Surface (a1) single-level scalar fields. Stored in `NWPCloudDiagnostics` model.

| Field | GRIB2 Variable | Stored As | Unit | Aviation Use |
|-------|---------------|-----------|------|-------------|
| Cloud ceiling | ceil | `ceiling_ft` | m→ft | Lowest opaque cloud layer |
| Cloud base | cbh | Not currently stored | m | Low cloud base (ceil is preferred) |
| Low cloud cover | lcc | `low.cover_pct` | 0–1→% | SFC–6500ft |
| Medium cloud cover | mcc | `mid.cover_pct` | 0–1→% | 6500–20000ft |
| High cloud cover | hcc | `high.cover_pct` | 0–1→% | >20000ft |
| Total cloud cover | tcc | `total_cover_pct` | 0–1→% | Full-column |
| Convective cloud top | hcct | `convective_top_ft` | m→ft | Cb top; base falls back to LCL |
| Freezing level | deg0l | `freezing_level_ft` + overwrites `hourly.freezing_level_m` | m→ft | Model-native 0°C isotherm height |

**Native convective indices (processed — issue #294):** `kx` (K-index) and `totalx` (Total Totals) are decoded from the a1 file into `nwp_k_index` / `nwp_total_totals` and copied onto `ThermodynamicIndices`. ECMWF delivers `kx` in Kelvin, so `_k_index_to_c()` normalizes it to °C before storage and before the convective-character thresholds are applied; Total Totals is offset-immune and is stored as delivered. They are model-native at full IFS resolution — preferred over the MetPy-derived K/TT (computed from the coarse Open-Meteo/GRIB sounding) by the convective character advisory. These are the fields Windy uses to drive ECMWF thunder icons.

**Native convective precipitation (processed — issue #283):** `cp` is decoded from the a1 file, differenced across successive valid times from its accumulated-since-initialization value, converted to `convective_precip_mm_h`, and consumed as the ECMWF native realized-convection signal. It can fire the NWP convective track when the model supplies convective precipitation without a diagnosed tower and is preferred over Open-Meteo `showers` by convective-character reporting.

**Surface fields delivered but not yet processed:** `10fg` (wind gusts), `10u`/`10v` (10m wind), `2t`/`2d` (screen T/Td), `blh` (boundary layer height), `capes` (CAPE-shear), `degm10l` (-10°C level), `fzra` (freezing rain accum), `lsp` (large-scale precip), `msl`/`sp` (pressure), `ptype` (precip type code), `sf` (snowfall), `tp` (total precip), `vis` (visibility).

**ECMWF specifics:** ECPDS push delivery to `ECMWF_GRIB_DIR` (no HTTP, no cache). Coverage is the `ifs-ens-cf` subscription grid: Europe + US at 0.25°. Cycles: 00/12z deliver 0–168h, 06/18z deliver 0–144h (horizon is read from files on disk, not from stream name — robust to the 50r1 `scda`→`oper` merge on 12-May-2026). Publication delay ~6–8h after init. Post-amendment cadence is hourly 0–90h then 3h tail on pressure-level data; surface fields are 3h throughout. Intermediate gap hours within the flight window are filled by per-level **linear time interpolation** (`fetch/grib/fill.py::_linear_interp_pressure_levels` — T, RH, wind, geopotential, omega, CLW/ICMR, cloud cover; dewpoint re-derived via Magnus from interpolated T+RH). Files may contain multiple geographic sub-grids; cfgrib splits them into separate Datasets and the decoder uses first-wins per point.

---

## 2. Derived Quantities (computed from API data)

### 2.1 Per-Level Derivations

These are computed in `analysis/sounding/thermodynamics.py` (`compute_derived_levels_core()` + `compute_derived_levels_extended()`) for each pressure level.

| Metric | Formula / Method | Inputs | Physics | Aviation Use |
|--------|-----------------|--------|---------|-------------|
| **Dewpoint** (fallback when missing) | Magnus formula: `Td = (c·γ)/(b−γ)` where `γ = ln(RH/100) + (b·T)/(c+T)`, b=17.67, c=243.5 | T, RH | Temperature at which air parcel reaches saturation at constant pressure. | Cloud base estimation, moisture availability. Now available directly from API for all models; Magnus derivation retained as fallback. |
| **Wet-bulb temperature** | `mpcalc.wet_bulb_temperature(P, T, Td)` — iterative solution of psychrometric equation | P, T, Td | Lowest temperature achievable by evaporative cooling. Accounts for both temperature and moisture. | Stored per level for diagnostics. Icing severity uses Ogimet/SFIP continuous indices (§3.2); icing type classified by wet-bulb bands via shared `classify_icing_type()` (all three methods). |
| **Dewpoint depression** | `DD = T − Td` | T, Td | Gap between temperature and dewpoint. DD < 3°C = likely in visible moisture (cloud). DD < 1°C = near saturation. | Cloud layer detection: consecutive levels with DD < 3°C form a cloud layer. |
| **Relative humidity** (at level) | `mpcalc.relative_humidity_from_dewpoint(T, Td)` | T, Td | Fraction of saturation. 100% = saturated (cloud or fog). | Icing severity modifier (RH > 95% upgrades risk). |
| **Equivalent potential temperature** (θ_e) | `mpcalc.equivalent_potential_temperature(P, T, Td)` — Bolton (1980) | P, T, Td | Temperature a parcel would have if all moisture condensed and parcel brought adiabatically to 1000 hPa. Conserved in moist adiabatic processes. | Air mass identification. Decreasing θ_e with height = potential instability. |
| **Lapse rate** | `−ΔT/Δz` between adjacent levels, in °C/km | T, height at adjacent levels | Rate of temperature decrease with altitude. DALR = 9.8°C/km, SALR ≈ 5–7°C/km. | > DALR = absolutely unstable (convective). < SALR = absolutely stable. Inversions (negative lapse) = trapping layers. |
| **Vertical velocity** (w) | `mpcalc.vertical_velocity(ω, P, T)` — converts ω (Pa/s) to w (m/s) using hydrostatic relation: `w ≈ −ω/(ρ·g)` | ω, P, T | Physical vertical air speed. Negative ω = upward motion. | Strong updrafts (> 200 ft/min) = turbulence/convection. |
| **Potential temperature** (θ) | `mpcalc.potential_temperature(P, T)` — Poisson equation: `θ = T × (1000/P)^(R/cp)` | P, T | Temperature parcel would have if brought adiabatically to 1000 hPa. Conserved in dry adiabatic processes. | Stability assessment: dθ/dz > 0 = stable, < 0 = unstable. Used for N² and Richardson number. |
| **Brunt-Väisälä frequency²** (N²) | `N² = (g/θ̄) × (dθ/dz)` | θ at adjacent levels, height | Static stability frequency. Positive = stable (oscillations), negative = convectively unstable, zero = neutral. | N² > 0 = stable stratification. Used in Richardson number for CAT. |
| **Richardson number** (Ri) | `Ri = N² / S²` where `S² = (du/dz)² + (dv/dz)²` | N², wind shear between levels | Ratio of buoyancy to shear. Determines whether turbulence is suppressed (high Ri) or generated (low Ri). | Ri < 0.5 = severe CAT, < 1.0 = moderate CAT, < 2.0 = light CAT. Thresholds loosened from classical 0.25/0.5/1.0 to compensate for NWP vertical resolution bias. |
| **Cloud liquid water** (g/m³) | `CLWMR × ρ_air` | CLWMR (GRIB2), P, T | Liquid water content in volume units. | Ogimet icing index input. GFS globally, ECMWF (EU+US ≤ 7 days), ICON-EU (Europe ≤ 5 days). |
| **Cloud liquid water** (g/kg) | `CLWMR × 1000` | CLWMR (GRIB2) | Mixing ratio in g/kg. | SFIP icing index input. GFS globally, ECMWF (EU+US ≤ 7 days), ICON-EU (Europe ≤ 5 days). |
| **Ice mixing ratio** (g/kg) | `ICMR × 1000` | ICMR (GRIB2) | Ice content mixing ratio. | Glaciation factor: CLW/(CLW+ICE). Reduces SFIP when cloud is glaciated. Available wherever CLW is. |
| **SFIP per-level** | Fuzzy-logic membership + weights | T, RH, CLW, ω, CAPE | See §3.2e. 0–100 icing potential at each level. | Stored as `sfip_raw`, `sfip_100`, `sfip_severity`, `sfip_variant` on each DerivedLevel. Six variants: `"full"`, `"full_no_vv"`, `"interp"`, `"interp_no_vv"`, `"proxy"`, `"proxy_no_vv"` — `_no_vv` suffix when omega unavailable. `clw_interpolated` flag tracks spatially- or vertically-interpolated CLW. |
| **Precipitation phase** | Wet-bulb T or GRIB2 ice fraction | Tw or CLWMR+ICMR | Phase of precipitation at altitude. | Stored as `precip_phase` on each DerivedLevel. See §3.6. |

### 2.2 Profile-Level Indices

Computed in `analysis/sounding/thermodynamics.py` (`compute_indices_core()` + `compute_indices_extended()`, wrapped by `compute_indices()`) — one value per sounding profile.

| Index | Method | Physics | Aviation Interpretation |
|-------|--------|---------|------------------------|
| **LCL** (Lifting Condensation Level) | `mpcalc.lcl(P_sfc, T_sfc, Td_sfc)` | Altitude where a surface parcel first saturates when lifted. Always exists. | Theoretical cloud base for convective clouds. Compare with observed cloud cover for consistency check. |
| **LFC** (Level of Free Convection) | `mpcalc.lfc(P, T, Td, parcel)` | Altitude above which a lifted parcel is warmer than environment and rises freely. May not exist in stable profiles. | Convection trigger point. Low LFC = easier convection initiation. None = convection unlikely from surface heating alone. |
| **EL** (Equilibrium Level) | `mpcalc.el(P, T, Td, parcel)` | Altitude where rising parcel temperature equals environment again. Approximate cloud top for deep convection. | Higher EL = taller storms, more severe. EL > FL350 = deep convection, significant hazard. |
| **CAPE** (surface-based) | `mpcalc.cape_cin(P, T, Td, parcel)` | Integrated positive buoyancy of a surface parcel through the troposphere. Energy available for convection. J/kg. | Convective risk uses effective CAPE = max(SB, MU). EU-calibrated thresholds: < 50 = none, 50–300 = low, 300–1000 = moderate, 1000–2000 = high, > 2000 = extreme. |
| **CAPE** (most-unstable) | `mpcalc.most_unstable_cape_cin(P, T, Td)` | CAPE computed from the most unstable parcel in the lowest 300 hPa, not just surface. | Better than surface CAPE when instability is elevated (e.g. warm air aloft over cold surface). Captures convective potential when surface parcel is capped. |
| **CAPE** (mixed-layer) | `mpcalc.mixed_layer_cape_cin(P, T, Td)` | CAPE from a well-mixed boundary layer parcel (average of lowest 100 hPa). | Realistic for afternoon convection when boundary layer is well-mixed. More representative than pure surface parcel. |
| **CIN** (Convective Inhibition) | From `mpcalc.cape_cin()` | Negative buoyancy energy a parcel must overcome to reach the LFC. Acts as a "lid" on convection. J/kg. | CIN < −200 = strong cap, convection unlikely without forced lifting. CIN near 0 = convection initiates easily with modest heating/lift. |
| **Lifted Index** | `mpcalc.lifted_index(P, T, parcel)` | Temperature difference between the environment and a surface parcel lifted to 500 hPa. Negative = unstable. | > 0 = stable, 0 to −3 = marginally unstable, −3 to −6 = moderately unstable, < −6 = extremely unstable. Quick convective potential check. |
| **Showalter Index** | `mpcalc.showalter_index(P, T, Td)` | Like lifted index but using 850 hPa parcel (not surface). Useful when surface layer is unrepresentative. | > 3 = stable, 1–3 = marginal, −2 to 1 = moderate, < −2 = strong instability. Better than LI when surface conditions are anomalous. |
| **K-Index** | `mpcalc.k_index(P, T, Td)` | `K = T_850 − T_500 + Td_850 − (T_700 − Td_700)`. Combines lapse rate, low-level moisture, and mid-level dryness. | < 20 = no thunderstorms, 20–30 = isolated, 30–40 = scattered, > 40 = numerous thunderstorms expected. |
| **Total Totals** | `mpcalc.total_totals_index(P, T, Td)` | `TT = (T_850 − T_500) + (Td_850 − T_500)`. Vertical totals + cross totals. Measures instability and moisture. | < 44 = no storms, 44–50 = scattered, 50–55 = isolated severe, > 55 = numerous severe thunderstorms. |
| **Precipitable Water** | `mpcalc.precipitable_water(P, Td)` | Total water vapor in the atmospheric column if all condensed. Integrated from surface to top of profile. mm. | > 25mm = very moist column, enhances icing severity and precipitation intensity. Climate-dependent: 25mm is extreme for northern Europe, moderate for tropics. |
| **Freezing level** | Linear interpolation where T crosses 0°C | T, height at levels | Altitude of the 0°C isotherm. Above = sub-zero temperatures where icing is possible. | Upper limit of rain, lower limit of snow. Descent below freezing level = primary icing escape strategy. |
| **−10°C level** | Linear interpolation where T crosses −10°C | T, height at levels | Altitude of −10°C. Below this, supercooled water is abundant. | Peak icing risk zone is freezing level to −10°C level (or −14°C per Ogimet). |
| **−20°C level** | Linear interpolation where T crosses −20°C | T, height at levels | Below −20°C, clouds are mostly ice crystals — icing risk drops sharply. | Upper boundary of significant icing concern. |
| **Bulk wind shear (0–6km)** | Vector difference of wind between surface and ~6km | u, v components at surface and 6km | Measures organized convection potential. Strong shear tilts updrafts, allowing storms to persist. | > 40kt = supercell potential. 25–40kt = organized multicell. < 25kt = disorganized/short-lived cells. |
| **Bulk wind shear (0–1km)** | Vector difference of wind between surface and ~1km | u, v components at surface and 1km | Low-level shear. Important for tornado risk and low-level wind shear hazard on approach. | > 20kt = significant low-level wind shear. Relevant for approach/departure. |

---

## 3. Assessed Quantities (from derived values)

### 3.1 Cloud Layers

**Module:** `analysis/sounding/clouds.py`

Two named methods — both always computed, user selects which drives advisories via `cloud_method` setting:

#### DD (Dewpoint Depression) — default

Consecutive pressure levels where dewpoint depression < 3°C are grouped into cloud layers. Sounding-derived, independent of NWP cloud cover parameterization.

**Coverage classification** from mean dewpoint depression within layer:

| Mean DD | Coverage | Okta equivalent |
|---------|----------|-----------------|
| < 1°C | OVC (overcast) | 8/8 |
| 1–2°C | BKN (broken) | 5–7/8 |
| 2–3°C | SCT (scattered) | 3–4/8 |

#### NWP (Model Diagnostics) — alternative

`build_nwp_cloud_layers()` produces layers from native model sources **only**, tried in order of richness. Open-Meteo bulk `cloud_cover_low/mid/high` are intentionally NOT synthesized into layers here — consumers need a clean "native or absent" signal:

**Source 1 — Per-level 3D cloud fraction (ECMWF `cc`, ICON-EU `clc`):** Real deck base/top derived from the model's own per-level cloud fraction. Tagged `source="nwp_3d"`.

**Source 2 — GRIB diagnostics (GFS):** Native model boundaries (base_ft, top_ft) and coverage from GRIB2 cloud diagnostics. Tagged `source="grib"`.

Returns `None` when neither source is available (model has no native NWP cloud envelope — treat as "no data," not "clear sky"); returns an empty list when a native source exists but no level cleared the threshold (genuine clear-sky forecast). Minimum cover threshold: `_MIN_COVER_PCT = 12.5%` (≈ FEW, 1–2 oktas).

**NWP coverage classification:**

| Cover % | Coverage |
|---------|----------|
| ≥ 87.5% | OVC |
| ≥ 50% | BKN |
| ≥ 25% | SCT |

Three ICAO bands: low (SFC–6500ft), mid (6500–20000ft), high (20000–45000ft).

**Source tracking:** Each `EnhancedCloudLayer` carries `source`
(`"dd"`/`"grib"`/`"nwp_3d"`; legacy `"synthesized"` is accepted but no longer
produced). `SoundingAnalysis.cloud_method_effective` records what was actually
used: `"dd"` for DD/default fallback and `"nwp"` for native GRIB or per-level 3D
geometry, including an available empty native list. A legacy all-synthesized
layer set remains explicitly `"nwp_synthesized"`: NWP bulk cover constrained by
a DD-derived envelope is compound provenance and must never be presented as
equivalent to native NWP base/top geometry. Mixed or unknown source sets leave
the effective method unknown rather than guessing.

**Dual cloud data sources** — a known inconsistency:
- **DD (sounding-derived):** from dewpoint depression at pressure levels (8–28 levels, coarse vertical resolution)
- **NWP (grid-scale):** `cloud_cover_low/mid/high` from model parameterization (sub-grid cloud physics, finer)

These can disagree. The NWP cloud cover includes sub-grid processes the sounding approach misses. The `cloud_method` setting lets users choose which source drives advisory evaluators — see §5 Known Issues.

### 3.2 Icing Assessment

**Module:** `analysis/sounding/icing.py` (Ogimet-DD, Ogimet-NWP, IENG) + `analysis/sounding/sfip.py` (SFIP)

Four icing zone lists are computed per sounding (`icing_ogimet_dd_zones`, `icing_ogimet_nwp_zones`, `ieng_icing_zones`, `sfip_zones` on the result); the user's `icing_method` setting selects which drives advisories:

#### 3.2a Ogimet-DD (default)

Continuous Ogimet icing index with dewpoint depression (DD) attenuation as cloud signal.

**Formula:** `effective_index = ogimet_index(T) × dd_attenuation_factor(DD)`

DD attenuation: cosine taper from 1.0 at DD=0°C to 0.0 at DD≥2°C — smooth, no binary gate.

**Ogimet index formulas** (shared with Ogimet-NWP):

```
Combined = (layered% × layered_index + convective% × convective_index) / 200

Layered:     100 × (−t) × (t + 14) / 49      when −14 ≤ t ≤ 0 °C
Convective:  200 × (ρv_base − ρv_cell) / ρv_20sat × √((T − 253.15)/20)
                                                when −20 ≤ t ≤ 0 °C
```

Where ρv = water vapor density, computed as `e_sat(Td) / (R_v × T_K)`. The stratiform/convective cloud split is estimated from CAPE (see §4.4).

**Cloud gating (`assess_icing_zones_ogimet_dd`):** A level is assessed only if `is_in_cloud_layer()` places it within a DD cloud layer (DD-detected, NWP-filtered — the same layers drawn on the cross-section). Within cloud, the DD attenuation factor modulates severity. Per-level index stored in `lv.icing_index`. (The older 2-pass LWC/DD/NWP-fallback gate is no longer used.)

#### 3.2b Ogimet-NWP (alternative)

Same Ogimet index but uses NWP model cloud cover as cloud signal.

**Formula:** `effective_index = ogimet_index(T) × nwp_cloud_fraction(altitude) × glaciation(CLW, ICMR)`

**Requires a model-native cloud envelope:** NWP cloud geometry has two distinct
states: `nwp_cloud_layers is None` means the method is unavailable, while
`nwp_cloud_layers == []` means native geometry was assessed and found clear.
`assess_icing_zones_ogimet_nwp` emits no zones for either state, so advisory
availability must be derived from the retained geometry state rather than from
the zone list alone. It refuses to fabricate icing zones from bulk percentages
(which would produce calls the cross-section cannot anchor to a cloud band).
Gating is `is_in_cloud_layer(lv, nwp_clouds)`.

Within cloud, severity is scaled by the NWP cloud fraction at altitude from `nwp_cloud_cover_at_altitude()` (shared in `icing_common.py`), which checks **all** diagnostic layers regardless of ICAO band and returns the highest cover for any layer whose base/top (± margin) contains the altitude; falls back to bulk ICAO band percentages when diagnostics unavailable. When CLW/ICMR microphysics are present, a temperature-floored glaciation factor further reduces the index in glaciated cloud. Per-level index stored in `icing_index_nwp` (separate from `icing_index` used by Ogimet-DD) to prevent overwriting.

#### 3.2c IENG (alternative)

`assess_icing_zones_ieng` — the Ogimet **layered** temperature curve scaled directly by NWP cloud fraction (no DD attenuation, no glaciation). Like Ogimet-NWP it gates on `is_in_cloud_layer()` against native NWP cloud layers. It emits no zones both for unavailable (`None`) and assessed-clear (`[]`) geometry, so consumers must preserve that distinction from `nwp_cloud_layers`. A convective component is added when CAPE > 100. Stored as `ieng_icing_zones`. Uses the shared `_index_to_risk()` mapping below.

#### 3.2d SFIP-NWP

Fuzzy-logic SFIP index — see §3.2e below for full algorithm details.

#### Shared: Index-to-Risk Mapping

| Ogimet Index | Risk | | SFIP_100 | Risk |
|------|------|-|----------|------|
| < 10 | None | | < 15 | None |
| 10–30 | Light | | 15–30 | Light |
| 30–80 | Moderate | | 30–55 | Moderate |
| ≥ 80 | Severe | | ≥ 55 | Severe |

#### Icing Type Classification

The two **Ogimet** methods (DD, NWP) use `classify_icing_type()` from `icing_common.py`, keyed on **wet-bulb** temperature (dry-bulb fallback). Accretion surface temperature is closer to wet-bulb; thresholds shifted ~1°C colder than dry-bulb equivalents:

| Wet-bulb range | Type | Physics |
|----------------|------|---------|
| −4°C to 0°C | Clear ice | Supercooled water freezes on contact as clear, dense glaze. Most dangerous — adds weight, changes airfoil shape. |
| −11°C to −4°C | Mixed | Mix of supercooled droplets and ice crystals. Irregular accretion. |
| < −11°C | Rime | Smaller droplets freeze instantly as rough, opaque ice. Less aerodynamic penalty than clear. |

**SFIP uses a different (dry-bulb) classifier** — its own `_classify_icing_type()` in `sfip.py`, per Belo-Pereira (2015) / Morcrette (2019): CLEAR −3 to 0°C, MIXED −10 to −3°C, RIME < −10°C. These are dry-bulb thresholds, ~1°C warmer than the Ogimet wet-bulb bands above.

#### Shared: Severity Enhancement

Optional (`icing_severity_enhance=False` default):
- ≥3 levels with RH > 95% + NWP cloud ≥50% → LIGHT → MODERATE
- Same + mean T ≤ −5°C → MODERATE → SEVERE
- Precipitable water > 25mm → LIGHT → MODERATE

### 3.2e SFIP Icing Index

**Module:** `analysis/sounding/sfip.py` — See [analysis.md](./analysis.md) for algorithm summary.

A second icing index computed alongside Ogimet, based on fuzzy-logic membership functions (Belo-Pereira 2015, Morcrette et al. 2019). Same algorithm family used by Windy.com and European operational met services.

**Six variants** depending on data availability (CLW and omega):

| Variant | Name | When | Inputs | Weights |
|---------|------|------|--------|---------|
| `full` | SFIP_O | GFS, ECMWF, or ICON-EU (CLW from GRIB2 + ω) | T, RH, CLW, ω | 0.35, 0.15, 0.35, 0.15 |
| `full_no_vv` | SFIP_O | CLW from GRIB2 but ω unavailable | T, RH, CLW | normalized 0.35/0.15/0.35 → 41.2%/17.6%/41.2% |
| `interp` | SFIP_O | CLW spatially/vertically interpolated + ω | T, RH, CLW(interp), ω | 0.35, 0.15, 0.35, 0.15 |
| `interp_no_vv` | SFIP_O | CLW interpolated, no ω | T, RH, CLW(interp) | normalized 0.35/0.15/0.35 → 41.2%/17.6%/41.2% |
| `proxy` | SFIP_4 | No CLW, has ω (UKMO, or ECMWF outside direct-GRIB coverage) | T, RH, DD+cloud proxy, ω | 0.40, 0.25, 0.25, 0.10 |
| `proxy_no_vv` | SFIP_4 | No CLW, no ω (MétéoFr, GEM) | T, RH, DD+cloud proxy | normalized 0.40/0.25/0.25 → 44.4%/27.8%/27.8% |

The `_no_vv` suffix indicates omega (vertical velocity) is structurally
unavailable. The VV member and its weight are omitted, and the remaining weights
are normalized to sum to one. A real `omega_pa_s=0.0` is still an available,
neutral VV observation and retains the original four-member weights. This was an
objective missing-member correction; the SFIP membership functions and 15/30/55
severity thresholds did not change.

**Membership functions** (all return 0.0–1.0 except VV which returns −0.3 to +0.5):
- **M_T:** Piecewise linear ramp peaking 1.0 in [−5, −14]°C, then **exponential decay** below −14°C: `exp(−k × (|T| − 14))` with `k=0.4` (`_TEMP_DECAY_K`). SLW concentration drops roughly exponentially as ice nucleation dominates at colder temperatures. This aligns SFIP's effective range with the Ogimet layered formula (which cuts off at −14°C) while maintaining a smooth tail for residual mixed-phase icing. Reference values: −15°C → 0.67, −17°C → 0.30, −20°C → 0.09.
- **M_RH:** Ramp from 0 at 50% to 1.0 at 100%, steeper near saturation
- **M_CLW:** Ramp from 0 to 1.0 over [0, 0.2] g/kg (requires GRIB2 CLW data)
- **M_CLW_proxy:** Combines sounding DD score + NWP cloud cover factor (max of both)
- **M_VV:** Boost for ascent (neg ω), penalty for subsidence; neutral at real ω=0; omitted when omega is structurally unavailable

**Glaciation factor** (GFS/ICON-EU/ECMWF): When ICMR data is available, `M_CLW *= CLW / (CLW + ICE)`. Reduces icing when cloud is mostly glaciated.

**Gating:** Temperature [0, −25]°C. Cloud gating is by `is_in_cloud_layer()` (altitude within a cloud layer ± margin), keyed on which layer list: full variant gates on the **NWP** cloud layers (CLW is a model field), proxy variant on the **DD** cloud layers (sounding-derived + NWP-filtered). Full also drops CLW ≤ 0 in `compute_sfip_level`. (The older `is_near_cloud()` per-level DD re-check is deprecated and no longer used here.)

**Altitude-aware NWP cloud check:** The NWP cloud-cover factor (M_VV/M_CLW_proxy inputs) delegates to `nwp_cloud_cover_at_altitude()` (shared in `icing_common.py`), which checks **all** diagnostic layers regardless of ICAO band (handles cross-band cloud layers) and returns the highest cover for any layer whose base/top (± margin) contains the altitude. Falls back to bulk ICAO-band values when diagnostics are unavailable. (This is the cover lookup; the zone-formation gate itself is `is_in_cloud_layer()` per the Gating note above.)

**Severity mapping** (GA-tuned thresholds matching IcingRisk enum):

| SFIP_100 | Risk |
|----------|------|
| < 15 | None |
| 15–30 | Light |
| 30–55 | Moderate |
| ≥ 55 | Severe |

**Output:** `SfipZone` objects (grouped via shared `group_icing_levels()` from `icing_common.py`) with `mean_sfip_100`, `risk`, `icing_type`, `variant` (one of six: `"full"`, `"full_no_vv"`, `"interp"`, `"interp_no_vv"`, `"proxy"`, `"proxy_no_vv"`).

**CLW/ICMR interpolation** — two stages, both flagged `clw_interpolated=True` → SFIP reports variant containing `"interp"` → tooltip shows `(INTERP)`:

1. **Spatial** (`analysis/spatial_interpolation.py`): Before sounding analysis, `interpolate_cloud_water_spatially()` fills gaps per-pressure-level by linear interpolation in distance-space between neighboring route points with data. Max gap: 100 nm (default).
2. **Vertical** (`_interpolate_cloud_water()` in `analysis/sounding/__init__.py`): After direct-match enrichment of GRIB 50hPa levels, linear interpolation in pressure-space fills intermediate 25hPa levels that have no direct GRIB data.

**Per-model behavior:**

| Model | Variant | CLW Source | VV Source | Glaciation |
|-------|---------|-----------|-----------|-----------|
| GFS | `full`/`interp` (SFIP_O) | CLWMR from GFS GRIB2 (interp where gaps filled) | ω from API | Yes (ICMR from GRIB2) |
| ICON | `full`/`interp` (SFIP_O)* | QC from ICON-EU GRIB2 (interp where gaps filled) | ω from ICON-EU GRIB (derived from `w`) | Yes (QI from ICON-EU) |
| ECMWF | `full`/`interp` (SFIP_O)† | clwc from ECMWF GRIB2 | w from ECMWF GRIB2 (replaces Open-Meteo ω) | Yes (ciwc from GRIB2) |
| MétéoFr | `proxy_no_vv` (SFIP_4) | DD + cloud proxy | **None** (ω unavailable) | No |
| UKMO | `proxy` (SFIP_4) | DD + cloud proxy | ω from API | No |
| GEM | `proxy_no_vv` (SFIP_4) | DD + cloud proxy | **None** (ω unavailable) | No |

\* ICON uses full variant only when route is within ICON-EU domain (Europe). Falls back to proxy for routes outside the domain.

† ECMWF uses full variant only when route and flight window fall within the ECPDS GRIB coverage (Europe + US, 0–168h for 00/12z, 0–144h for 06/18z). Outside coverage, no direct GRIB is applied and SFIP falls back to `proxy` (ω from Open-Meteo API still available).

### 3.3 Convective Assessment

**Module:** `analysis/sounding/convective.py`

Classifies convective risk from effective CAPE — max(SB-CAPE, MU-CAPE) — with CIN modulation and severe weather modifiers. MU-CAPE catches elevated convection common in European maritime environments where SB-CAPE is near zero while a warm layer aloft is unstable.

European-calibrated thresholds (lower than US values — European convection produces severe weather at lower CAPE):

| Effective CAPE | Risk | Significance |
|----------------|------|-------------|
| < 50 J/kg | None | Stable or very weakly unstable. Convection not possible. |
| 50–300 | Low | Weak instability. Fair-weather cumulus, weak showers at best. |
| 300–1000 | Moderate | Moderate instability. Thunderstorms possible with sufficient trigger. |
| 1000–2000 | High | Strong instability. Vigorous thunderstorms likely if triggered. Significant turbulence. |
| > 2000 | Extreme | Extreme instability. Severe storms, large hail, possible tornadoes. Avoid at all costs. |

**Severe modifiers** flag additional hazards when thresholds are crossed (shear > 40kt, K > 35, TT > 55, LI < −6, high freezing level + CAPE > 1000).

### 3.4 Vertical Motion & Turbulence

**Module:** `analysis/sounding/vertical_motion.py`

**Profile classification** from omega (ω) distribution:

| Class | Criterion | Meaning |
|-------|-----------|---------|
| Quiescent | max \|ω\| < 0.1 Pa/s | Calm, stable air. Smooth flight expected. |
| Synoptic ascent | mean ω < 0, no large values | Large-scale uplift (front, low pressure). Widespread cloud/precip likely. |
| Synoptic subsidence | mean ω > 0, no large values | Large-scale sinking. Clearing, possible inversions. Often smooth. |
| Oscillating | ≥ 2 sign changes in ω | Wave activity. Mountain waves, gravity waves. Possible turbulence. |
| Convective | max \|ω\| > 1 Pa/s | Vigorous vertical motion. Active convection in model. |

**CAT risk** from Richardson number at each layer (thresholds loosened from classical 0.25/0.5/1.0 to compensate for NWP vertical resolution bias — 25-50 hPa between levels is too coarse to resolve thin shear layers where KH instability develops, so computed Ri is systematically too high):

| Ri | CAT Risk | Physics |
|----|----------|---------|
| < 0.5 | Severe | Shear overwhelms stability. Turbulent breakdown guaranteed (Kelvin-Helmholtz instability). |
| 0.5–1.0 | Moderate | Marginal stability. Turbulence likely, especially with external forcing. |
| 1.0–2.0 | Light | Dynamically stable but approaching critical. Intermittent turbulence possible. |
| > 2.0 | None | Shear insufficient to overcome buoyancy. Laminar flow. |

### 3.5 Altitude Advisories

**Module:** `analysis/sounding/advisories.py`

Aggregates cloud, icing, turbulence, and vertical motion data into vertical regimes (per model) and cross-model advisories.

**Vertical regimes:** Altitude slices with uniform conditions. Transitions at cloud base/top, icing zone boundaries, freezing level, ICAO band boundaries. Adjacent regimes with identical properties are merged.

**Advisory types:**

| Advisory | Aggregation | Logic |
|----------|------------|-------|
| `descend_below_icing` | min() across models | Per model: max(freezing level, lowest icing-cloud base) − 500ft; either warmer air or clear air exits icing, so the higher valid escape is used before taking the conservative cross-model minimum |
| `climb_above_icing` | max() across models | Per model: max(highest icing top, highest cloud-in-icing top) + 500ft. `feasible` if ≤ ceiling. |
| `cat_turbulence` | worst across models | Reports worst CAT layer altitude and risk level. |
| `strong_vertical_motion` | max \|w\| across models | Flags altitudes with \|w\| > 200 ft/min. |

Freezing precipitation overrides the ordinary `min()` aggregation when the
affected model also has icing zones: one model with a warm-nose/freezing-rain
profile makes descent escape infeasible for the aggregate, even if another
model has a finite escape altitude. A model with no icing simply contributes a
`None` per-model altitude and does not veto another model's finite escape.
Empty-zone freezing-rain profiles were not changed by this correction.

### 3.6 Precipitation Assessment

**Module:** `analysis/sounding/precipitation.py`

Classifies precipitation phase at each pressure level and detects hazardous profiles (freezing rain, ice pellets).

**Per-level phase classification** (two methods, GRIB2 preferred):
1. **GRIB2 ice fraction** (when CLWMR + ICMR available): `ice_frac = ICMR / (CLWMR + ICMR)`. >0.8 = snow, 0.2–0.8 = mixed, <0.2 = rain.
2. **Wet-bulb temperature** (fallback): Tw < 0°C = snow, 0 to 1.3°C inclusive = mixed/wet snow, and >1.3°C = rain. These are the shared melting-physics boundaries from meteorology decision §17; GRIB ice fraction still takes precedence where available.

**Warm nose detection:** Identifies temperature inversions where T > 0°C exists between sub-zero layers. Cold surface + warm nose above = freezing rain risk. Deep cold surface layer + significant warm nose = ice pellets.

**Surface phase determination** (priority order):
1. Warm nose flags → freezing rain or ice pellets
2. Model rain/snow breakdown from API (`rain`, `showers`, `snowfall`)
3. Surface wet-bulb temperature
4. Surface temperature (final fallback)

**Surface intensity** from hourly precipitation total: <1 mm/h = light, 1–4 = moderate, >4 = heavy.

**Output:** `PrecipitationAssessment` with surface phase/intensity, `PrecipitationZone` list (vertical zones grouped by phase), warm-nose altitudes, and rain/snow amounts.

`EnroutePrecipEvaluator` computes snow/rain percentages over points that carry a
`PrecipitationAssessment`, not over every point that merely has a sounding.
Missing precipitation assessments still make the expected route domain partial;
they do not dilute an observed hazard by acting as clear votes.

### 3.7 Wind Components

**Module:** `analysis/wind.py`

Decomposes wind vector relative to flight track:
- `headwind = V × cos(wind_dir − track)` — positive = headwind, negative = tailwind
- `crosswind = V × sin(wind_dir − track)` — positive = from right

### 3.8 Model Divergence

**Module:** `analysis/comparison.py`

Compares each metric across models. Spread = max − min (circular statistics for wind direction).

| Agreement | Condition |
|-----------|-----------|
| Good | spread ≤ good_threshold |
| Moderate | good < spread ≤ poor_threshold |
| Poor | spread > poor_threshold |

Poor agreement signals forecast uncertainty — brief conservatively.

`ModelDivergence.mean=None` is the canonical absent-metric signal. The comparison
artifact may still store `spread=0` and `agreement=GOOD` for an all-null value
map, but that means there was nothing to compare, not unanimous agreement.
`ModelAgreementEvaluator` therefore excludes those entries from the assessed
denominator: all-absent metrics are unavailable, valid plus absent metrics are
partial, and a numeric mean remains valid when only some individual model values
are null.

### 3.9 DD/NWP Cloud Interval Agreement

`DDvsNWPAgreementEvaluator` compares same-model DD and native NWP cloud
altitude coverage with interval Jaccard:

```text
J = length(merge(DD) ∩ merge(NWP)) / length(merge(DD) ∪ merge(NWP))
```

Each side is first canonicalized (finite, positive spans only) and merged across
overlapping or touching layers. The two merged unions are then intersected once,
so internal overlaps cannot be double-counted and `J` stays in `[0, 1]`. Two
empty canonical sets agree (`J=1`); exactly one empty set disagrees (`J=0`). This
is an objective set-geometry correction. The existing `cloud_overlap_min` and
route-level AMBER/RED disagreement thresholds are unchanged.

---

## 4. Per-Model Data Availability and Derivation

### 4.1 Availability Matrix

Shows data source for each key quantity per model. **Bold** = derived when API field is unavailable.

| Quantity | GFS | ECMWF | ICON | MétéoFr | UKMO | GEM | Derivation method |
|----------|-----|-------|------|---------|------|-----|-------------------|
| T at levels | API | API | API | API | API | API | — |
| RH at levels | API | API | API | API | API | API | — |
| Dewpoint at levels | API | API | API | API | API | API | — |
| Wind at levels | API | API | API | API | API | API | — |
| Geopotential height | API | API | API | API | API | API | — |
| Omega (ω) | API | API | **GRIB2**† | **n/a** | API | **n/a** | API: GFS, ECMWF, UKMO. ICON gets ω from ICON-EU GRIB (derived from `w`) inside the EU domain → vertical motion/CAT available there; no API omega. Not derivable for MétéoFr, GEM. |
| Cloud cover (total) | API | API | API | API | API | API | — |
| Cloud cover low/mid/high | API | API | API | API | API | API | — |
| Freezing level | API | **GRIB2**‡/**derived** | API | **derived** | API | **derived** | ECMWF: `deg0l` from ECPDS GRIB when in coverage; otherwise linear interpolation of T profile through 0°C |
| CAPE | API | API | API | API | API | API | — |
| CIN | **derived** | **derived** | **derived** | **derived** | **derived** | **derived** | Always from `mpcalc.cape_cin()` |
| Visibility | API | **n/a** | API | **n/a** | API | **n/a** | Not derivable from standard pressure levels |
| Precip probability | API | API | **n/a** | **n/a** | **n/a** | **n/a** | Requires ensemble data |
| CLWMR (cloud liquid water) | **GRIB2** | **GRIB2**‡ | **GRIB2**† | **n/a** | **n/a** | **n/a** | GFS: S3 `.idx`. ECMWF: ECPDS. ICON: DWD model levels. |
| ICMR (ice mixing ratio) | **GRIB2** | **GRIB2**‡ | **GRIB2**† | **n/a** | **n/a** | **n/a** | Same sources as CLWMR |
| NWP cloud diagnostics | **GRIB2** | **GRIB2**‡ | **GRIB2**† | **n/a** | **n/a** | **n/a** | GFS: full (ceiling, base/top/temp per layer). ECMWF: ceiling, covers per tier, convective top (hcct). ICON: ceiling, convective base/top, covers per tier. |
| SFIP icing index | Full | Full‡/Proxy | Full†/Proxy | Proxy | Proxy | Proxy | Full uses GRIB2 CLW (+ω where available); proxy uses DD+cloud cover. ICON full now includes ω from GRIB. |
| Precipitation phase | GRIB2+Tw | Tw only | GRIB2†+Tw | Tw only | Tw only | Tw only | GRIB2 ice fraction preferred, wet-bulb fallback |

† ICON GRIB2 enrichment only available when route is within ICON-EU domain (Europe: 29.5–70.5°N, 23.5°W–62.5°E).

‡ ECMWF GRIB2 enrichment (ECPDS commercial feed) only available when route falls within the ifs-ens-cf coverage grid (Europe + US) and the flight window is within the run's forecast range (0–168h at 00/12z, 0–144h at 06/18z). Outside coverage, ECMWF falls back to Open-Meteo's 13-level sounding and SFIP uses the proxy variant.

### 4.2 Key Derivation Methods

**Dewpoint from T + RH** (Magnus formula, used in `open_meteo.py` for fallback derivation):
```
γ = ln(RH / 100) + (b × T) / (c + T)
Td = (c × γ) / (b − γ)
```
Where b = 17.67, c = 243.5°C (Alduchov & Eskridge, 1996). Accurate to ~0.2°C for typical atmospheric conditions. Note: with the ECMWF, Météo-France, and UKMO API updates (Feb 2026), dewpoint is now available directly at all pressure levels for all models, so this derivation is only used as a fallback when the API field is missing.

**Water vapor density** (ideal gas law, needed for Ogimet convective icing index):
```
e = saturation_vapor_pressure(Td)    # MetPy: mpcalc.saturation_vapor_pressure()
ρv = e / (Rv × T_K)                  # Rv = 461.5 J/(kg·K)
```
At 20°C saturated: ρv ≈ 17.3 g/m³ (the constant in the Ogimet formula).

**CAPE from sounding** (CIN always derived; CAPE used as cross-check when API value available):
```python
parcel = mpcalc.parcel_profile(P, T_sfc, Td_sfc)
cape, cin = mpcalc.cape_cin(P, T, Td, parcel)
```
Integrates positive buoyancy between LFC and EL. Note: as of Feb 2026, all seven models now provide CAPE via API. CIN is always derived from the sounding profile since the API only provides scalar CAPE.

**Cloud cover per ICAO band** (fallback when low/mid/high not in API):
Sounding-derived cloud layers are mapped to ICAO bands (low < 6500ft, mid 6500–20000ft, high > 20000ft). Cloud coverage classified from dewpoint depression. Coarser than NWP parameterization but provides a consistent fallback. Note: as of Feb 2026, all seven models now provide `cloud_cover_low/mid/high` directly via API, so this fallback is rarely needed.

### 4.3 What Cannot Be Derived

| Quantity | Why | Impact |
|----------|-----|--------|
| Omega (ω) for MétéoFr/GEM | Requires model dynamics, not recoverable from T/wind alone | No vertical motion classification or CAT risk for these models. API: GFS, ECMWF, UKMO. ICON gets ω from ICON-EU GRIB (`w`) inside the EU domain. |
| Visibility for ECMWF/MétéoFr/GEM | Parameterized from sub-grid microphysics not available at pressure levels | VFR/IFR assessment limited to cloud cover proxy for these models |
| Freezing level for MétéoFr/GEM | Not in API | Derived via linear interpolation of T profile through 0°C. ECMWF now has `deg0l` directly from ECPDS GRIB (when in coverage); derivation is the fallback outside coverage. |
| Precipitation probability | Requires ensemble spread data | Only available from GFS and ECMWF |
| Stratiform vs. convective cloud split | Not in any Open-Meteo API | Must approximate for Ogimet icing index (see §4.4) |
| CLWMR/ICMR for MétéoFr/UKMO/GEM | No GRIB2 enrichment implemented for these models | SFIP uses proxy variant; precipitation phase uses wet-bulb only |
| Cloud diagnostics for MétéoFr/UKMO/GEM | No GRIB2 cloud diagnostic enrichment for these models | No NWP-native cloud base/top/ceiling. GFS has full diagnostics; ICON-EU has ceiling + convective base/top + covers; ECMWF has ceiling, covers per tier, and convective top (`hcct`) via ECPDS GRIB (base falls back to LCL). |

### 4.4 Approximating Cloud Type Split for Icing Index

The Ogimet formula requires separate stratiform and convective cloud cover percentages. No model provides this directly via Open-Meteo. Current approximation (implemented in `icing.py`):

1. **CAPE-based split** (CAPE now available from API for all models):
   - CAPE < 100 J/kg → 100% layered, 0% convective
   - CAPE 100–500 → 80% layered, 20% convective
   - CAPE 500–1500 → 50% layered, 50% convective
   - CAPE > 1500 → 20% layered, 80% convective

2. **Total cloud cover** used as the overall cloud fraction. Split applies within it:
   - `layered_cover = cloud_cover × layered_fraction`
   - `convective_cover = cloud_cover × convective_fraction`

3. **Fallback** (stable atmosphere, no CAPE): treat all cloud as layered. This is conservative — layered icing is the more common GA hazard in Europe.

---

## 5. Known Issues and Inconsistencies

### "Clear (cloud 100%)" label

**Root cause:** Regime labels in `advisories.py` check `in_cloud` (from sounding DD < 3°C) independently of `cloud_cover_pct` (from NWP grid). When sounding says "not in cloud" but NWP says 100% coverage, the label reads "Clear (cloud 100%)" — contradictory.

**Why they disagree:**
- Sounding has 8 pressure levels (coarse vertical resolution) — thin cloud layers fall between levels
- NWP cloud cover includes sub-grid parameterized clouds not visible in resolved profiles
- NWP cloud cover is an area-average; sounding is a point measurement

**Fix direction:** When cloud_cover > 50% and sounding says clear, label should defer to NWP (e.g. "Overcast" or "Cloudy") rather than "Clear". The sounding may genuinely miss clouds at its coarse resolution.

### ~~Icing severity bands vs. observed climatology~~ (Resolved)

Resolved by switching to the Ogimet continuous icing index (see §3.2). The previous wet-bulb bands classified −3°C to 0°C as SEVERE, but observed maximum supercooled liquid water content peaks at −5°C to −10°C. The Ogimet index parabola peaking at −7°C matches observations.

---

## 6. MetPy Functions Used

| Function | Module | Purpose |
|----------|--------|---------|
| `lcl()` | thermo | Lifting Condensation Level |
| `parcel_profile()` | thermo | Theoretical parcel ascent curve |
| `lfc()` | thermo | Level of Free Convection |
| `el()` | thermo | Equilibrium Level |
| `cape_cin()` | thermo | CAPE and CIN integration |
| `most_unstable_cape_cin()` | thermo | MU-CAPE (most unstable parcel) |
| `mixed_layer_cape_cin()` | thermo | ML-CAPE (boundary layer average) |
| `lifted_index()` | thermo | Lifted Index at 500 hPa |
| `showalter_index()` | thermo | Showalter stability index |
| `k_index()` | thermo | K-Index thunderstorm potential |
| `total_totals_index()` | thermo | Total Totals stability index |
| `precipitable_water()` | thermo | Column precipitable water |
| `wet_bulb_temperature()` | thermo | Wet-bulb T (stored per level, used for diagnostics) |
| `equivalent_potential_temperature()` | thermo | θ_e (air mass tracer) |
| `relative_humidity_from_dewpoint()` | thermo | RH from T and Td |
| `potential_temperature()` | thermo | θ for stability/Ri calculation |
| `wind_components()` | wind | u, v from speed/direction |
| `vertical_velocity()` | thermo | ω (Pa/s) → w (m/s) conversion |

### MetPy Functions Available but Not Yet Used

| Function | What it provides | Potential use |
|----------|-----------------|---------------|
| `saturation_vapor_pressure(T)` | e_sat at given T | Ogimet icing index water vapor density |
| `mixing_ratio_from_relative_humidity(P, T, RH)` | Mixing ratio (kg/kg) | Alternative moisture metric |
| `density(P, T, w)` | Air density (kg/m³) | Water vapor density derivation |
| `virtual_temperature(T, w)` | Virtual temperature | Buoyancy calculations |
| `specific_humidity_from_dewpoint(P, Td)` | Specific humidity | Moisture budget |
| `dewpoint(e)` | Td from vapor pressure | Inverse moisture calculation |

---

## References

- Analysis implementation: [analysis.md](./analysis.md)
- Data models: [data-models.md](./data-models.md)
- Fetch layer & model endpoints: [fetch.md](./fetch.md)
- GRIB2 engine: [weather-engine-specs.md](./weather-engine-specs.md)
- Analysis layer (icing, SFIP, vertical motion): [analysis.md](./analysis.md)
- Ogimet icing index: Autorouter GRAMET documentation
- SFIP references: Belo-Pereira (2015), Morcrette et al. (2019)
- MetPy documentation: https://unidata.github.io/MetPy/
