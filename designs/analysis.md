# Analysis Layer

> Aviation-specific analysis: wind components, MetPy sounding analysis, model comparison

All modules in `src/weatherbrief/analysis/`. Pure computation — no I/O.

## Intent

Transform raw NWP data into aviation-relevant assessments. Each module is independent, testable, and stateless. The sounding subpackage uses MetPy for physically-based thermodynamic analysis.

> 📐 **Before changing or second-guessing any meteorological choice** (icing-zone
> width, CAPE/convective thresholds, ceiling derivation, GFS cloud gating, DD-vs-NWP
> boundaries), read **[meteorology-decisions.md](./meteorology-decisions.md)**.
> It's a dated log of explicit decisions *with reasoning and what-was-rejected*,
> written so the next reviewer doesn't re-investigate a choice that was already
> made deliberately (e.g. §2 Ogimet icing width, §4 realizable-CAPE/DD-stays-pure,
> §5 NWP-cover vs CAPE risk).

## Wind Components (`analysis/wind.py`)

Decompose wind vector relative to flight track.

```python
wc = compute_wind_components(wind_speed_kt=25, wind_direction_deg=290, track_deg=135)
wc.headwind_kt   # positive = headwind, negative = tailwind
wc.crosswind_kt  # positive = from right, negative = from left
```

- Uses `cos(relative_wind)` for head/tail, `sin(relative_wind)` for crosswind
- Track per waypoint comes from `RouteConfig.waypoint_track()` (circular mean of leg bearings)

## Sounding Analysis (`analysis/sounding/`)

MetPy-based atmospheric analysis subpackage. Single entry point:

```python
from weatherbrief.analysis.sounding import analyze_sounding

result = analyze_sounding(hourly.pressure_levels, hourly)
# Returns SoundingAnalysis | None (None if profile prep fails / <3 valid levels)
```

**Lite → heavy split.** `analyze_sounding()` runs `prepare_profile()` once, then:
1. `analyze_sounding_lite()` — core thermodynamic indices (LCL, parcel, LFC, EL, surface/MU/ML-CAPE, lifted index, freezing level), core derived levels, DD cloud layers (+ NWP coverage reclassification), inversions, ceiling, and convective assessment (thermo + NWP). This is the subset shared with standalone verification.
2. `_analyze_sounding_heavy()` — mutates the lite result in place: extended indices (Showalter, K-index, Total Totals, PW, bulk shear), extended derived levels (wet bulb, theta-e, RH, omega→w), LWC enrichment (with vertical CLW/ICMR interpolation), cloud top uncertainty, NWP cloud layers, SFIP / Ogimet-DD / Ogimet-NWP / IENG icing, precipitation, SLD, stability indicators + vertical motion / turbulence.

E-Shear is a separate post-pass (`tasks/analyze.py:_enrich_e_shear()`) across all route points after every per-point sounding completes (HWS needs neighbors).

Note: inversions are detected **before** NWP cloud building because the synthesis tier uses inversion layers for cloud top capping. Cloud top uncertainty and icing all read `effective_cape()` over the CAPE variants (no separate NWP-value-preservation pass — raw NWP CAPE/CIN are attached to indices inside `analyze_sounding_lite`).

### Prepare (`sounding/prepare.py`)

Pint boundary — converts `list[PressureLevelData]` to `PreparedProfile` with pint-wrapped numpy arrays for MetPy. Derives dewpoint from RH via Magnus formula when not directly available. Filters levels missing temperature, sorts by descending pressure (surface first). Returns `None` if <3 valid levels.

Pint arrays **never leak** beyond the sounding subpackage.

### Thermodynamics (`sounding/thermodynamics.py`)

All MetPy calls live here. Each is split into a **core** function (cheap subset, also used by standalone verification) and an **extended** function (full-briefing fields). `compute_indices()` / `compute_derived_levels()` are convenience wrappers that run core then extended.

**`compute_indices_core(profile) → CoreIndicesResult`** — profile-level values (the `.indices` field is `ThermodynamicIndices`, `.parcel_path` is the captured parcel path for client-side Skew-T CAPE/CIN):
- Parcel profile, LCL, LFC, EL (via MetPy, handles None for stable profiles)
- CAPE/CIN: surface-based, most-unstable, mixed-layer (MU/ML live in **core** — the convective tier needs them everywhere)
- Lifted index
- Temperature crossings: freezing level (0°C), -10°C, -20°C (linear interpolation)

**`compute_indices_extended(profile, idx)`** — mutates `idx` in place, adds:
- Showalter index, K-index, Total Totals
- Precipitable water
- Bulk wind shear: 0-6km and 0-1km

Ceiling (`sounding_ceiling_ft` — lowest BKN/OVC cloud layer, LCL as floor when cloud starts at first level — and `nwp_ceiling_ft` from NWP diagnostics) is set in `analyze_sounding_lite`, not in these functions.

**`compute_derived_levels_core(profile) → list[DerivedLevel]`** — per pressure level, cheap (no MetPy):
- Altitude (ft), temperature, dewpoint (carried through from profile)
- Dewpoint depression (T - Td)
- Lapse rate between adjacent levels (°C/km)
- Wind speed/direction (from profile)
- Omega (Pa/s) raw — read by the convective loaded-gun trigger before extended runs

**`compute_derived_levels_extended(profile, levels)`** — mutates levels in place, adds MetPy-derived:
- Wet-bulb temperature (`mpcalc.wet_bulb_temperature`)
- Theta-E (`mpcalc.equivalent_potential_temperature`)
- Relative humidity (from `mpcalc.relative_humidity_from_dewpoint`)
- Vertical velocity w (ft/min) from omega

Richardson number and Brunt-Vaisala frequency (N²) are added by `compute_stability_indicators(profile, derived_levels)` in `vertical_motion.py` (not here) — stability indicators for CAT assessment.

Every MetPy call is wrapped in try/except — returns None for fields that fail. All pint magnitudes extracted via `.magnitude` before storing in Pydantic models.

### Clouds (`sounding/clouds.py`)

Two cloud detection methods, selectable via `cloud_source` user setting:

**DD (Dewpoint Depression) — default.** Sounding-derived cloud layers from dewpoint depression profiles.

```python
layers = detect_cloud_layers(derived_levels, lcl_altitude_ft=idx.lcl_altitude_ft)
```

- Threshold: dewpoint depression < 3°C = "in cloud"
- Groups consecutive levels into `EnhancedCloudLayer`
- Coverage from mean dewpoint depression: < 1°C → OVC, 1-2°C → BKN, 2-3°C → SCT
- Records base/top altitudes, thickness, mean temperature

**NWP (Model Diagnostics) — alternative.** Native sources only, tried in order of richness.

```python
nwp_layers = build_nwp_cloud_layers(
    diagnostics, cloud_cover_low, cloud_cover_mid, cloud_cover_high,
    pressure_levels=levels,
)
```

- **Source 1 (3D cloud fraction):** ECMWF `cc` / ICON-EU `clc` — `build_nwp_cloud_layers_from_fraction()` scans `cloud_area_fraction_pct` per pressure level, groups contiguous levels ≥12.5% into decks, base/top from `geopotential_height_m`. Tagged `source="nwp_3d"`. Preferred whenever any level carries CAF.
- **Source 2 (GRIB diagnostics):** GFS — uses native model boundaries (HGHL/HGHM/HGHH + LCDC/MCDC/HCDC), tagged `source="grib"`.
- **Returns `None`** when neither native source is available (no model-native cloud envelope). Callers must treat `None` as "no NWP layer data," not "clear sky." Returns `[]` when a native source exists but nothing exceeded threshold (genuine clear forecast).
- **No synthesized tier.** Open-Meteo bulk `cloud_cover_low/mid/high_pct` are intentionally NOT synthesized into layers here — downstream consumers (Ogimet-NWP / IENG icing, cross-section NWP toggle) need a clean "native or absent" signal. (Synthesis from bulk %, with DD/inversion/LCL narrowing, still lives behind the disabled `apply_nwp_coverage` overlay — see `_APPLY_NWP_COVERAGE_OVERLAY` in `sounding/__init__.py`.)
- Coverage mapping: ≥87.5% → OVC, ≥50% → BKN, ≥25% → SCT, ≥12.5% → FEW (3D source uses peak deck CAF).
- Each `EnhancedCloudLayer` carries `source` field ("dd"/"nwp_3d"/"grib").
- `SoundingAnalysis.cloud_method_effective` tracks what method was actually used ("dd", "nwp", "nwp_synthesized"). Its siblings `icing_method_effective` and `convective_method_effective` do the same for the icing and convective axes (#408) — the effective, not requested, method, so a fallback is recorded; the icing/cloud advisory chips badge `method_id` from them.

**Cloud top uncertainty enrichment:** For convective (CAPE > 500): `theoretical_max_top_ft = EL`. For stratiform: `theoretical_max_top_ft = -20°C level`. Only set when exceeding sounding-derived cloud top.

Both methods always computed; `cloud_source` controls which is used by advisory evaluators (method swapping in `tasks/advise.py`).

### Inversions (`sounding/inversions.py`)

Temperature inversion detection from lapse rate analysis.

```python
from weatherbrief.analysis.sounding.inversions import detect_inversions
layers = detect_inversions(derived_levels)
# → list[InversionLayer] with base/top altitudes, strength_c, surface_based flag
```

- Negative lapse rate = temperature increases with altitude → inversion
- Groups consecutive levels with negative lapse rate into `InversionLayer`
- `strength_c` = total temperature gain through the inversion
- `surface_based=True` if the inversion starts at the lowest valid level
- Used in cross-section visualization as an inversion band layer

### Icing (`sounding/icing.py`, `sounding/sfip.py`, `sounding/icing_common.py`)

Four icing methods (Ogimet-DD, Ogimet-NWP, SFIP-NWP, IENG-NWP) — all always computed and stored. The `icing_method` user setting selects which fills the active `icing_zones` slot (only `ogimet_dd`/`ogimet_nwp`/`sfip_nwp` are wired into `_resolve_analyses`; IENG is computed/stored but not currently user-selectable). Shared utilities (cloud layer gating, NWP cloud altitude lookup, icing type classification, zone grouping) live in `icing_common.py`.

#### Cloud Gating Architecture

All icing methods use the same cloud gating function `is_in_cloud_layer()` from `icing_common.py` — a pure altitude-within-range check against `EnhancedCloudLayer` lists:

```python
def is_in_cloud_layer(level, cloud_layers, margin_ft=500) -> bool:
    """True if level.altitude_ft is within any cloud layer ± margin."""
```

No per-level DD re-checking. The cloud layer list already encodes the detection method's decisions (DD threshold, NWP coverage filter, etc.). Two cloud detection methods exist and are used for gating:

| Cloud method | Layers | How built | Display |
|-------------|--------|-----------|---------|
| **DD** | `cloud_layers` | `detect_cloud_layers(DD<3°C)` (published verbatim; `apply_nwp_coverage` overlay is disabled by default) | Gray bands |
| **NWP** | `nwp_cloud_layers` | `build_nwp_cloud_layers()` — native 3D CAF or GRIB diagnostics only (`None` if no native source) | Blue bands |

**Consistency with display:** icing can only be computed where cloud is drawn. If no cloud band is displayed at a given altitude, no icing is computed there. This prevents false alarms in moist-but-clear air (DD<3°C but NWP says <12.5% cloud).

Each icing method is paired with a cloud detection method: `{formula}–{cloud_method}`.

#### Ogimet-DD (default) — `assess_icing_zones_ogimet_dd()`

Ogimet index gated by DD cloud layers, with DD severity modulation.

**Gating:** `is_in_cloud_layer(level, dd_cloud_layers)` — uses DD-detected + NWP-filtered cloud layers (same gray bands drawn on the cross-section).

**Severity:** `effective_index = ogimet_index(T) × dd_attenuation_factor(DD)` — cosine taper from 1.0 at DD=0°C to 0.0 at DD≥2°C modulates severity within cloud (denser cloud → more icing).

Per-level index stored in `icing_index` on DerivedLevel.

#### Ogimet-NWP (alternative) — `assess_icing_zones_ogimet_nwp()`

Ogimet index gated by NWP cloud layers, scaled by cloud fraction and glaciation.

**Gating:** `is_in_cloud_layer(level, nwp_cloud_layers)` — uses pure NWP model cloud layers (same blue bands drawn on the cross-section).

**Severity:** `effective_index = ogimet_index(T) × nwp_cloud_fraction(altitude) × glaciation_factor(CLW, ICMR)` — NWP cloud cover percentage modulates severity; glaciation factor (when CLW/ICMR available) reduces index in glaciated cloud. `nwp_cloud_cover_at_altitude()` (shared in `icing_common.py`) checks all diagnostic layers regardless of ICAO band, falling back to bulk band percentages when diagnostics unavailable.

Per-level index stored in `icing_index_nwp` on DerivedLevel (separate from `icing_index` used by Ogimet-DD).

#### SFIP-NWP — `sounding/sfip.py`

Simplified Forecast Icing Potential — fuzzy-logic index (Belo-Pereira 2015, Morcrette et al. 2019). Same family used by Windy.com and European met services.

**Gating:** Each variant uses the cloud detection method matching its data source:
- **Full variant** (CLW available from GRIB): `is_in_cloud_layer(level, nwp_cloud_layers)` — CLW is model output, gate by model cloud layers.  Additionally, `CLW <= 0` is gated out inside `compute_sfip_level()`.
- **Proxy variant** (no CLW): `is_in_cloud_layer(level, dd_cloud_layers)` — sounding-derived signal, gate by DD-detected + NWP-filtered cloud layers

**Fuzzy logic** — four membership functions (T, RH, CLW, vertical velocity) combined with weights:
- **Six variants**: `full`/`full_no_vv` (SFIP_O, GFS/ICON-EU — has real CLW from GRIB) `0.35×T + 0.15×RH + 0.35×CLW + 0.15×VV`; `proxy`/`proxy_no_vv` (SFIP_4, other models) `0.40×T + 0.25×RH + 0.25×CLW_proxy + 0.10×VV`; `interp`/`interp_no_vv` (same as full but CLW spatially/vertically interpolated). The `_no_vv` suffix indicates omega is unavailable (M_VV = 0).
- **Glaciation factor** (GFS/ICON-EU only): `CLW/(CLW+ICMR)` reduces icing when cloud is glaciated
- **Icing type**: SFIP keeps its **own dry-bulb** thresholds (`_classify_icing_type`, Belo-Pereira 2015 / Morcrette 2019) — it does NOT use the shared wet-bulb `classify_icing_type()` that Ogimet/IENG use (deliberate; see [meteorology-decisions §2](./meteorology-decisions.md))
- Output: `sfip_raw` (0–1), `sfip_100` (0–100), severity, variant (one of six above)

**Temperature membership (M_T):** Piecewise linear ramp to peak 1.0 in [−5, −14]°C, then exponential decay below −14°C: `exp(−k × (|T| − 14))` with `k=0.4` (`_TEMP_DECAY_K`). SLW concentration drops roughly exponentially with decreasing temperature as ice nucleation dominates. This aligns SFIP's effective range with the Ogimet layered formula (which cuts off at −14°C) while maintaining a smooth tail for mixed-phase icing. Reference values: −15°C → 0.67, −17°C → 0.30, −20°C → 0.09. Temperature gating: [0, −25]°C.

#### IENG-NWP — `assess_icing_zones_ieng()`

Temperature curve scaled by NWP cloud fraction. Simpler, coverage-proportional alternative to Ogimet-NWP.

**Gating:** `is_in_cloud_layer(level, nwp_cloud_layers)` — uses pure NWP model cloud layers.

**Severity:** `effective = layered_index(T) × nwp_cloud_fraction(altitude)` — no glaciation correction.

#### Shared Ogimet Index Formulas

Physically-based continuous index peaking at −7°C, matching observed supercooled liquid water distribution:
- **Stratiform (layered):** `100 × (−T) × (T + 14) / 49` — parabola peaking at −7°C, zero at 0°C and −14°C
- **Convective:** `200 × moisture_term × √(temp_term)` — temperature-gated [−20°C, 0°C], moisture-dependent
- **CAPE-based split:** CAPE < 100 → 100% layered; 100–500 → 80/20; 500–1500 → 50/50; > 1500 → 20/80

| Index range | Risk |
|-------------|------|
| < 10 | NONE |
| 10–30 | LIGHT |
| 30–80 | MODERATE |
| ≥ 80 | SEVERE |

**SFIP severity thresholds** (GA-tuned, lower than Ogimet):

| SFIP_100 | Risk |
|----------|------|
| < 15 | NONE |
| 15–30 | LIGHT |
| 30–55 | MODERATE |
| ≥ 55 | SEVERE |

#### Icing Type Classification

The **Ogimet-family** methods (Ogimet-DD, Ogimet-NWP, IENG) use `classify_icing_type()` from `icing_common.py` — wet-bulb temperature (dry-bulb fallback), thresholds shifted ~1°C colder than dry-bulb equivalents because accretion surface temperature is closer to wet-bulb in cloud. **SFIP is the exception**: it keeps its own dry-bulb literature thresholds (see SFIP-NWP above). The shared wet-bulb thresholds:
- CLEAR: −4°C to 0°C
- MIXED: −11°C to −4°C
- RIME: < −11°C

#### Zone Formation

- Adjacent levels grouped into zones (gap ≤ 100 hPa pressure gap between levels)
- Minimum zone thickness: single-level zones expanded to ±500ft (1000ft total) for cross-section visibility
- SLD detection: warm-nose freezing rain only (active). Collision-coalescence mechanism disabled — over-triggers on common deep stratiform clouds. See `designs/future/known-issues.md`.

### CLW/ICMR Interpolation

Two-stage interpolation fills gaps in GRIB2 CLW/ICMR data. Both stages set `clw_interpolated=True` → SFIP reports variant containing `"interp"` → tooltip shows `(INTERP)`.

#### Stage 1: Spatial (`analysis/spatial_interpolation.py`)

Some GFS grid cells return `None` for CLW/ICMR via Open-Meteo, which forces SFIP to use the proxy variant. The proxy variant overestimates icing extent (e.g. 13,400ft SEVERE zone vs 2,400ft MODERATE at neighboring data points).

`interpolate_cloud_water_spatially()` fills these gaps **before** sounding analysis runs, per model cross-section, per hourly entry, per pressure level:

- **Metrics interpolated**: `cloud_liquid_water_kg_kg` (CLWMR) and `ice_mixing_ratio_kg_kg` (ICMR) on `PressureLevelData`
- **Method**: linear interpolation in distance-space between nearest left and right route-point neighbors that have data
- **Max gap**: 100 nm default (`max_gap_nm`). Wider gaps are left unfilled → proxy fallback
- **Edge gaps**: first/last point with no data on one side → not interpolated
- **CLW=0.0 preserved**: treated as real "measured zero", not a gap

#### Stage 2: Vertical (`sounding/__init__.py`)

After `_enrich_lwc()` direct-matches GRIB 50hPa levels to derived levels, it calls `_interpolate_cloud_water()` to fill intermediate 25hPa levels by linear interpolation in pressure-space between enriched neighbors. Only interpolates when both neighbors have data.

### LWC Enrichment (`sounding/__init__.py`)

`_enrich_lwc()` converts CLWMR (kg/kg) from `PressureLevelData` to LWC (g/m³) on `DerivedLevel` using air density from ideal gas law: `LWC = CLWMR × (P / (Rd × T_K)) × 1000`, also setting mixing ratios in g/kg for SFIP, then runs the Stage-2 vertical interpolation. Called from the heavy pass, before cloud-top/icing.

### Convective (`sounding/convective.py`)

Pure threshold logic from `ThermodynamicIndices` — no MetPy dependency. Two assessors run per sounding and are both stored:
- `assess_convective_thermo()` → `convective_thermo` (default `convective`) — sounding-derived risk from `effective_cape` + regime/realizability logic and severity modifiers.
- `assess_convective_nwp()` → `convective_nwp` — model's own convective scheme (cover %, convective base/top geometry). Selected when `convective_method="nwp"`.
- `convective_cross_check(thermo, nwp)` → `ConvectiveCrossCheck | None` — surfaces material dissent between thermo risk and the model's *independent* convective-cover/geometry signal (the model's own `risk_level` is NOT used — it shares CAPE thresholds with thermo so the comparison would be circular). Fires only `dd_not_corroborated` (thermo MODERATE+ but model quiet) or `model_active_dd_quiet` (thermo NONE/MARGINAL but model active).

**Effective CAPE** = max(SB-CAPE, MU-CAPE, ML-CAPE) over available MetPy variants, falling back to NWP raw CAPE **only** when no MetPy variant exists (model-native CAPE diverges from sounding-derived values and could inflate risk if pooled). MU-CAPE catches elevated convection; ML-CAPE captures mixed-layer instability (ICON).

European-calibrated thresholds (lower than US values — European convection produces severe weather at lower CAPE):

| CAPE (J/kg) | Risk |
|-------------|------|
| 0 (with LFC+EL) | MARGINAL |
| < 50 | NONE |
| 50-300 | LOW |
| 300-1000 | MODERATE |
| 1000-2000 | HIGH |
| > 2000 | EXTREME |

- **MARGINAL**: any CAPE > 0 with defined LFC and EL → shallow convection possible
- CIN < -200 J/kg suppresses risk by one level
- Severe modifiers: bulk shear >40kt (supercell), >25kt (multicell), high freezing level + CAPE >1000 (hail), K-index >35, Total Totals >55, LI < -6

### Vertical Motion & CAT (`sounding/vertical_motion.py`)

Classifies vertical motion profiles and identifies clear-air turbulence (CAT) risk layers.

```python
from weatherbrief.analysis.sounding.vertical_motion import assess_vertical_motion
vm = assess_vertical_motion(derived_levels)
# Returns VerticalMotionAssessment | None
```

**Three functions:**
- `compute_stability_indicators(profile, derived_levels)` — computes Brunt-Vaisala frequency (N²) and Richardson number per layer from wind shear and temperature gradients (mutates `derived_levels` in place)
- `classify_vertical_motion(derived_levels)` — classifies omega profile into `VerticalMotionClass` (QUIESCENT, SYNOPTIC_ASCENT/SUBSIDENCE, CONVECTIVE, OSCILLATING)
- `assess_vertical_motion(derived_levels)` — combines classification with CAT risk layer identification

**CAT layer merging:** `_build_cat_layers()` groups adjacent low-Ri levels using dual-gap adjacency: BOTH pressure gap ≤ 100 hPa AND original-index gap ≤ 2. Prevents chaining scattered low-Ri levels across large stable gaps (e.g., GFS 25 hPa spacing where stable levels are simply skipped).

**CAT risk from Richardson number** (loosened from classical 0.25/0.5/1.0 to compensate for NWP vertical resolution bias — 25-50 hPa between levels is too coarse to resolve thin shear layers where KH instability develops, so computed Ri is systematically too high):

| Ri range | CAT Risk |
|----------|----------|
| < 0.5 | SEVERE (Kelvin-Helmholtz instability) |
| 0.5-1.0 | MODERATE |
| 1.0-2.0 | LIGHT |
| > 2.0 | NONE |

**Vertical motion magnitude reference:**

| Scale | Omega (Pa/s) | w (ft/min) | Aviation Impact |
|-------|-------------|------------|-----------------|
| Quiescent | < 0.5 | < 15 | Smooth air |
| Moderate synoptic | 1–5 | 30–160 | Light–moderate bumps |
| Strong forcing | 5–10 | 160–300 | Moderate turbulence |
| Convective | 10–100+ | 300–3000+ | Severe / avoid |

**Data source:** Open-Meteo `vertical_velocity` field (omega in Pa/s, negative = ascent). Available for GFS, ECMWF, and UKMO; unavailable for ICON, Météo-France, and GEM.

Output: `VerticalMotionAssessment` with classification, max omega/w values, `cat_risk_layers` (Richardson), and `e_shear_layers` (E-Shear).

### E-Shear Turbulence (`sounding/e_shear.py`)

Second CAT detection method using combined wind shear magnitude (CloudPath formula):

```
E = (5 × HWS + VWS² + 42) / 4
```

- **VWS** (vertical): ∂U/∂z, ∂V/∂z between adjacent pressure levels (per-sounding)
- **HWS** (horizontal): ∂U/∂x, ∂V/∂x between adjacent route points at matching pressure levels

E-Shear is computed as a **post-processing pass** in `tasks/analyze.py:_enrich_e_shear()` after all per-point sounding analyses complete (HWS requires adjacent route points). For each model, it averages HWS from both neighbors, then calls `compute_e_shear_per_sounding()`.

| E range | Risk |
|---------|------|
| < 40 | NONE |
| 40–80 | LIGHT |
| 80–160 | MODERATE |
| ≥ 160 | SEVERE |

**Key difference from Richardson:** E-Shear measures raw shear magnitude without considering atmospheric stability. Strong shear in a very stable layer (high Ri) can trigger E-Shear but not Richardson. Conversely, Richardson detects instability in moderate shear that E-Shear would miss.

**Model availability:** All models — only requires wind speed/direction at pressure levels, which all Open-Meteo models provide. `DerivedLevel` now carries `wind_speed_kt` and `wind_direction_deg` (populated from `PreparedProfile` during `compute_derived_levels()`).

### Altitude Advisories (`sounding/advisories.py`)

Dynamic altitude advisories replacing static altitude bands. Two layers:

1. **Vertical regimes** — per-model slices derived from actual weather boundaries
2. **Altitude advisories** — actionable highlights aggregated across models

```python
from weatherbrief.analysis.sounding.advisories import compute_altitude_advisories
adv = compute_altitude_advisories(soundings, cruise_altitude_ft=8000, flight_ceiling_ft=18000)
# Returns AltitudeAdvisories with regimes, advisories, cruise icing status
```

**Regime computation** per model:
1. Collect transition altitudes: `{0, ceiling_ft}` + cloud base/top + icing zone base/top + freezing level
2. Classify each segment by checking midpoint against cloud layers and icing zones
3. Merge adjacent regimes with identical conditions (in_cloud + icing_risk + icing_type)
4. Generate label: "Clear" / "In cloud" / "In cloud, icing MOD (mixed)"

**Advisory types:**
- `descend_below_icing`: Per model, escape = max(freezing level, lowest icing-overlapping cloud base) - 500ft — warm air OR clear air each exit icing, so the higher suffices. Aggregate: min() across models. Guards: a model with `freezing_rain_risk` has no descent escape (None); when terrain elevation is known (plumbed from the elevation profile) an escape leaving <1000ft AGL is marked `feasible=False`. See [meteorology-decisions §8](./meteorology-decisions.md).
- `climb_above_icing`: Per model, max(highest icing top, highest cloud top in icing temps) + 500ft. Aggregate: max() across models. `feasible` if ≤ flight_ceiling_ft.
- `cat_turbulence`: CAT risk layers from Richardson number analysis, integrated into regimes.
- `strong_vertical_motion`: Flags altitude bands where |w| > 200 fpm.
- Cruise icing status: any model showing icing at cruise altitude → `cruise_in_icing=True`, worst risk across models.

**Regime enrichment** — vertical regimes now include `cloud_cover_pct` (NWP 3-level cloud data), `cat_risk`, and `strong_vertical_motion` in addition to icing/cloud conditions.

## Model Comparison (`analysis/comparison.py`)

Score agreement across 2+ models for a given variable.

```python
div = compare_models("temperature_c", {"gfs": 5.0, "ecmwf": 6.0, "icon": 5.5})
div.agreement  # → AgreementLevel.GOOD
```

**Thresholds** `DIVERGENCE_THRESHOLDS` (good, poor) — 22 entries (some, e.g. `cape_jkg`/`ceiling_ft`/`visibility_m`, are defined for variables not currently collected by `_run_point_analysis`). `DEFAULT_THRESHOLD = (5.0, 15.0)` for anything unlisted:

| Variable | Good ≤ | Poor > |
|----------|--------|--------|
| temperature_c | 2.0 | 5.0 |
| wind_speed_kt | 5.0 | 15.0 |
| wind_direction_deg | 20° | 60° |
| precipitation_mm | 1.0 | 5.0 |
| cloud_cover_pct | 20.0 | 50.0 |
| freezing_level_m | 200.0 | 600.0 |
| ceiling_ft | 500.0 | 1500.0 |
| visibility_m | 2000.0 | 5000.0 |
| freezing_level_ft | 500.0 | 1500.0 |
| cape_jkg | 200.0 | 500.0 |
| cape_surface_jkg | 200.0 | 500.0 |
| nwp_cape_jkg | 200.0 | 500.0 |
| lcl_altitude_ft | 750.0 | 2000.0 |
| k_index | 5.0 | 15.0 |
| total_totals | 5.0 | 12.0 |
| precipitable_water_mm | 5.0 | 15.0 |
| lifted_index | 2.0 | 5.0 |
| bulk_shear_0_6km_kt | 5.0 | 15.0 |
| max_omega_pa_s | 0.1 | 0.5 |
| snowfall_cm | 0.5 | 2.0 |
| rain_mm | 1.0 | 5.0 |
| pressure_msl_hpa | 2.0 | 5.0 |

**Circular statistics** for `wind_direction_deg` — uses sin/cos sum for mean, max angular difference for spread.

## Pipeline Integration

In `tasks/analyze.py`, shared analysis via `_run_point_analysis()` (used by both waypoint and route-point paths):
1. Find closest pressure level to cruise altitude for wind analysis
2. Run `analyze_sounding()` per model → store in `soundings[model_key]` — computes all four icing methods and both cloud methods per sounding
3. Extract values into the `comp` accumulator for cross-model comparison (sounding-derived + surface metrics)
4. After all models: `compute_altitude_advisories()` → altitude advisories
5. Compute cross-model divergence (`compare_models()`) for every accumulator variable with ≥2 models

Route-point analysis (`analyze_all_route_points()`) adds interpolated time based on distance/speed and per-point track bearing (`compute_route_tracks()`), then runs the E-Shear post-pass (`_enrich_e_shear()`).

**Method resolution for advisories** (`tasks/advise.py`): Before advisory evaluation, `_resolve_analyses()` returns new `RoutePointAnalysis` objects with the user's preferred method resolved into the active slots — originals are never mutated (uses `model_copy()`). Returns the original list unchanged when no swap is needed.
- `icing_method="ogimet_dd"`: no swap needed (default in `icing_zones`)
- `icing_method="ogimet_nwp"`: resolves `icing_ogimet_nwp_zones` → `icing_zones`. Sets `icing_method_effective="ogimet_nwp"` when it ran; leaves it None (+ `active_icing_available=False`) when there is no native cloud envelope to run against (#408)
- `icing_method="sfip_nwp"`: converts `SfipZone` → `IcingZone` into `icing_zones`. Sets `icing_method_effective="sfip_nwp"`
- `cloud_source="dd"`: no swap needed (default in `cloud_layers`)
- `cloud_source="nwp"`: resolves `nwp_cloud_layers` → `cloud_layers` (falls back to `dd_cloud_layers` if NWP unavailable). Sets `cloud_method_effective` to "nwp" (grib sources), "nwp_synthesized" (synthesized sources), or "dd" (fallback)
- `convective_method="thermo"`: no swap needed (default `convective` = `convective_thermo`)
- `convective_method="nwp"`: resolves `convective_nwp` → `convective` (falls back to `convective_thermo` if the model has no NWP convective scheme). Sets `convective_method_effective` to "nwp" or "thermo" (fallback) — recording the silent fallback that used to go unlogged (#408)

**Immutable DD source fields**: `SoundingAnalysis` stores `dd_cloud_layers` and `icing_ogimet_dd_zones` at construction. These preserve the original DD data so resolution can always fall back. Excluded from serialization (redundant in default state); a `model_validator` reconstructs them from `cloud_layers`/`icing_zones` when loading old JSON.

Advisory model filtering: `advisory_models` preference excludes `best_match` by default, as it duplicates the underlying model.

## Gotchas

- `analyze_sounding()` returns None if <3 pressure levels with valid temperature + dewpoint
- MetPy LFC/EL return None/NaN for stable profiles — all checked
- Pressure levels not guaranteed sorted by API — `prepare_profile()` sorts them
- Pint units must not leak beyond sounding subpackage (causes Pydantic serialization issues)
- `matplotlib.use("agg")` required in skewt.py for worker thread compatibility

## Deep-dive analyses

Per-subsystem audit docs that trace data source-by-source, compare the parallel
methods, and flag consistency issues — deeper than this overview. They are
**point-in-time audits** (as-of dates below), not living architecture; trust them
for the *reasoning and the inconsistencies they surface*, but re-verify exact
function/field details against current code. Fetch any with
`get_design_doc(topic=…)`.

- **cloud-layers-analysis** (as-of 2026-05-13) — per-model cloud pipeline: fetch
  → detection (DD vs NWP) → output, source tracing, interpolation, consistency
  across icing methods and visualization.
- **convective-analysis** (as-of 2026-05-20) — per-model convective pipeline,
  dual-method (thermo vs NWP) assessment, user-selectable active method, source
  inconsistencies and recommendations.
- **icing-models-analysis** (as-of 2026-05-03) — input audit for the four icing
  methods + SLD: source tracing, interpolation, per-method consistency.
- **cross-cutting-review** (as-of 2026-03-16) — icing × clouds × convection
  interdependencies, method coupling, and simplification opportunities across the
  three subsystems.

## References

- **Meteorological decisions (read first for any calibration/threshold question):** [meteorology-decisions.md](./meteorology-decisions.md)
- Input models: [data-models.md](./data-models.md)
- Fetch layer: [fetch.md](./fetch.md)
- Output consumers: [digest.md](./digest.md)
- Route advisories: [advisories.md](./advisories.md) (route-level hazard evaluators consuming sounding analysis)
