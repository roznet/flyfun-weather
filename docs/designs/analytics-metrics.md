# Analysis Metrics Reference

> Comprehensive catalog of all weather metrics: data sources, derivation methods, per-model availability, physical interpretation, and aviation relevance.

## Overview

WeatherBrief computes ~40 metrics from NWP model data. Each metric falls into one of three source categories:

- **API** — fetched directly from Open-Meteo
- **Derived** — calculated from API data using MetPy or physics formulas
- **Assessed** — classified from derived values using aviation-specific thresholds

When an API field is unavailable for a model, derived alternatives fill the gap so that all assessments work across all models.

---

## 1. Raw Input Variables (from Open-Meteo API)

### 1.1 Surface Variables

| Variable | Unit | GFS | ECMWF | ICON | MétéoFr | UKMO | Physics / Interpretation |
|----------|------|-----|-------|------|---------|------|--------------------------|
| `temperature_2m` | °C | yes | yes | yes | yes | yes | Screen-level air temperature. Primary surface condition indicator. |
| `relative_humidity_2m` | % | yes | **no** | yes | yes | yes | Surface moisture saturation. Near 100% = fog/mist risk. |
| `dewpoint_2m` | °C | yes | yes | yes | yes | **no** | Temperature at which condensation begins. T−Td < 3°C = visible moisture likely. |
| `surface_pressure` | hPa | yes | yes | yes | yes | yes | Actual station pressure. Used to anchor sounding profiles. |
| `pressure_msl` | hPa | yes | yes | yes | yes | yes | Sea-level-corrected pressure. Altimeter setting proxy. |
| `wind_speed_10m` | kt | yes | yes | yes | yes | yes | 10m wind speed for surface ops. |
| `wind_direction_10m` | ° | yes | yes | yes | yes | yes | Surface wind direction (meteorological convention: direction FROM). |
| `wind_gusts_10m` | kt | yes | **no** | yes | yes | yes | Peak gust. Relevant for crosswind limits and turbulence on approach. |
| `precipitation` | mm | yes | yes | yes | yes | yes | Hourly accumulated precipitation. Any precip in sub-zero temps = icing concern. |
| `precipitation_probability` | % | yes | **no** | **no** | **no** | **no** | Ensemble-derived probability. GFS-only (from ensemble spread). |
| `cloud_cover` | % | yes | yes | yes | yes | yes | Total column cloud cover from model parameterization. |
| `cloud_cover_low` | % | yes | **no** | yes | yes | yes | SFC–6500ft (ICAO low). Low ceilings = IFR/LIFR risk. |
| `cloud_cover_mid` | % | yes | **no** | yes | yes | yes | 6500–20000ft (ICAO mid). Relevant for en-route icing. |
| `cloud_cover_high` | % | yes | **no** | yes | yes | yes | 20000ft+ (ICAO high). Cirrus, usually no icing concern. |
| `freezing_level_height` | m | yes | **no** | yes | **no** | **no** | NWP-computed 0°C isotherm height. Upper boundary of rain, lower boundary of icing. |
| `cape` | J/kg | yes | **no** | yes | **no** | **no** | NWP-computed convective available potential energy. |
| `visibility` | m | yes | **no** | yes | **no** | **no** | Parameterized horizontal visibility. < 5000m = marginal VFR. |

### 1.2 Pressure Level Variables

Available at: 1000, 925, 850, 700, 600, 500, 400, 300 hPa (~SFC to ~FL300).

| Variable | Unit | GFS | ECMWF | ICON | MétéoFr | UKMO | Physics / Interpretation |
|----------|------|-----|-------|------|---------|------|--------------------------|
| `temperature` | °C | yes | yes | yes | yes | yes | Air temperature at level. Primary driver for icing type and severity. |
| `relative_humidity` | % | yes | yes | yes | yes | yes | Moisture saturation at level. > 80% suggests cloud, > 95% = likely in cloud. |
| `dewpoint` | °C | yes | **no** | yes | **no** | **no** | Direct dewpoint. More physically meaningful than RH for cloud detection. |
| `wind_speed` | kt | yes | yes | yes | yes | yes | Wind at level for headwind/crosswind and shear calculations. |
| `wind_direction` | ° | yes | yes | yes | yes | yes | Wind direction at level. |
| `geopotential_height` | m | yes | yes | yes | yes | yes | Height of pressure surface. Converts pressure levels to altitude. |
| `vertical_velocity` | Pa/s | yes | yes | **no** | **no** | **no** | Omega (ω). Negative = ascent, positive = subsidence. Key for vertical motion and turbulence analysis. |

---

## 2. Derived Quantities (computed from API data)

### 2.1 Per-Level Derivations

These are computed in `thermodynamics.compute_derived_levels()` for each pressure level.

| Metric | Formula / Method | Inputs | Physics | Aviation Use |
|--------|-----------------|--------|---------|-------------|
| **Dewpoint** (when missing) | Magnus formula: `Td = (b·γ)/(a−γ)` where `γ = (a·T)/(b+T) + ln(RH/100)`, a=17.27, b=237.7 | T, RH | Temperature at which air parcel reaches saturation at constant pressure. | Cloud base estimation, moisture availability. Always available for all models. |
| **Wet-bulb temperature** | `mpcalc.wet_bulb_temperature(P, T, Td)` — iterative solution of psychrometric equation | P, T, Td | Lowest temperature achievable by evaporative cooling. Accounts for both temperature and moisture. | **Primary icing classifier** in current system: clear ice (-3 to 0°C), mixed (-10 to -3°C), rime (-15 to -10°C). |
| **Dewpoint depression** | `DD = T − Td` | T, Td | Gap between temperature and dewpoint. DD < 3°C = likely in visible moisture (cloud). DD < 1°C = near saturation. | Cloud layer detection: consecutive levels with DD < 3°C form a cloud layer. |
| **Relative humidity** (at level) | `mpcalc.relative_humidity_from_dewpoint(T, Td)` | T, Td | Fraction of saturation. 100% = saturated (cloud or fog). | Icing severity modifier (RH > 95% upgrades risk). |
| **Equivalent potential temperature** (θ_e) | `mpcalc.equivalent_potential_temperature(P, T, Td)` — Bolton (1980) | P, T, Td | Temperature a parcel would have if all moisture condensed and parcel brought adiabatically to 1000 hPa. Conserved in moist adiabatic processes. | Air mass identification. Decreasing θ_e with height = potential instability. |
| **Lapse rate** | `−ΔT/Δz` between adjacent levels, in °C/km | T, height at adjacent levels | Rate of temperature decrease with altitude. DALR = 9.8°C/km, SALR ≈ 5–7°C/km. | > DALR = absolutely unstable (convective). < SALR = absolutely stable. Inversions (negative lapse) = trapping layers. |
| **Vertical velocity** (w) | `mpcalc.vertical_velocity(ω, P, T)` — converts ω (Pa/s) to w (m/s) using hydrostatic relation: `w ≈ −ω/(ρ·g)` | ω, P, T | Physical vertical air speed. Negative ω = upward motion. | Strong updrafts (> 200 ft/min) = turbulence/convection. |
| **Potential temperature** (θ) | `mpcalc.potential_temperature(P, T)` — Poisson equation: `θ = T × (1000/P)^(R/cp)` | P, T | Temperature parcel would have if brought adiabatically to 1000 hPa. Conserved in dry adiabatic processes. | Stability assessment: dθ/dz > 0 = stable, < 0 = unstable. Used for N² and Richardson number. |
| **Brunt-Väisälä frequency²** (N²) | `N² = (g/θ̄) × (dθ/dz)` | θ at adjacent levels, height | Static stability frequency. Positive = stable (oscillations), negative = convectively unstable, zero = neutral. | N² > 0 = stable stratification. Used in Richardson number for CAT. |
| **Richardson number** (Ri) | `Ri = N² / S²` where `S² = (du/dz)² + (dv/dz)²` | N², wind shear between levels | Ratio of buoyancy to shear. Determines whether turbulence is suppressed (high Ri) or generated (low Ri). | Ri < 0.25 = severe CAT, < 0.5 = moderate CAT, < 1.0 = light CAT. Standard clear-air turbulence predictor. |
| **Icing index** (Ogimet) | Ogimet layered/convective blend (see §3.2) | T, Td, CAPE, cloud base Td | Continuous icing severity (0-100) peaking at −7°C. Replaces wet-bulb band thresholds. | Primary icing severity indicator. 30-80 = moderate, >80 = severe. |

### 2.2 Profile-Level Indices

Computed in `thermodynamics.compute_indices()` — one value per sounding profile.

| Index | Method | Physics | Aviation Interpretation |
|-------|--------|---------|------------------------|
| **LCL** (Lifting Condensation Level) | `mpcalc.lcl(P_sfc, T_sfc, Td_sfc)` | Altitude where a surface parcel first saturates when lifted. Always exists. | Theoretical cloud base for convective clouds. Compare with observed cloud cover for consistency check. |
| **LFC** (Level of Free Convection) | `mpcalc.lfc(P, T, Td, parcel)` | Altitude above which a lifted parcel is warmer than environment and rises freely. May not exist in stable profiles. | Convection trigger point. Low LFC = easier convection initiation. None = convection unlikely from surface heating alone. |
| **EL** (Equilibrium Level) | `mpcalc.el(P, T, Td, parcel)` | Altitude where rising parcel temperature equals environment again. Approximate cloud top for deep convection. | Higher EL = taller storms, more severe. EL > FL350 = deep convection, significant hazard. |
| **CAPE** (surface-based) | `mpcalc.cape_cin(P, T, Td, parcel)` | Integrated positive buoyancy of a surface parcel through the troposphere. Energy available for convection. J/kg. | < 100 = none, 100–500 = low, 500–1500 = moderate, 1500–3000 = high, > 3000 = extreme. High CAPE + trigger = strong updrafts, hail, turbulence. |
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
| **Water vapor density** (ρv) | `e_sat(Td) / (Rv × T_K)` where Rv = 461.5 J/(kg·K) | Td at each level | Mass of water vapor per unit volume. Reference: ρv at 20°C saturation ≈ 17.3 g/m³. | Input to Ogimet convective icing index. Used at cloud-base level for moisture depletion calculation. |

---

## 3. Assessed Quantities (from derived values)

### 3.1 Cloud Layers

**Module:** `sounding/clouds.py`

**Method:** Consecutive pressure levels where dewpoint depression < 3°C are grouped into cloud layers. This is a **sounding-derived** estimate independent of NWP cloud cover parameterization.

**Coverage classification** from mean dewpoint depression within layer:

| Mean DD | Coverage | Okta equivalent |
|---------|----------|-----------------|
| < 1°C | OVC (overcast) | 8/8 |
| 1–2°C | BKN (broken) | 5–7/8 |
| 2–3°C | SCT (scattered) | 3–4/8 |

**Dual cloud data sources** — a known inconsistency:
- **Sounding-derived:** from dewpoint depression at pressure levels (8 levels, coarse vertical resolution)
- **NWP grid-scale:** `cloud_cover_low/mid/high` from model parameterization (sub-grid cloud physics, finer)

These can disagree. The NWP cloud cover includes sub-grid processes the sounding approach misses. Currently both are reported independently, leading to confusing labels like "Clear (cloud 100%)" — see §5 Known Issues.

**Cloud top uncertainty**

After cloud detection, each cloud layer is enriched with a `theoretical_max_top_ft` advisory value:
- If CAPE > 500 J/kg and EL is available: theoretical max = EL altitude (convective overshoot potential)
- Otherwise: theoretical max = −20°C level altitude (stratiform glaciation limit)
- Only set when theoretical max exceeds the sounding-derived cloud top (otherwise adds no information)

An advisory (`cloud_top_uncertainty`) is triggered when the gap between sounding-derived top and theoretical max exceeds 2000ft, signaling that cloud may extend significantly higher than the coarse sounding resolution suggests.

### 3.1b Temperature Inversions

**Module:** `sounding/inversions.py`

**Method:** Walk derived levels sorted by descending pressure. Levels with negative `lapse_rate_c_per_km` (temperature increasing with altitude) are grouped into inversion layers. The inversion spans from the first negative-lapse-rate level to the next level above the last in the group.

**Fields:**
- `base_ft`, `top_ft` — altitude bounds
- `strength_c` — total temperature gain (top_temp − base_temp)
- `surface_based` — True if starts at the lowest level

**Integration:** Inversion boundaries are added as transitions in vertical regimes. Regimes within inversions are flagged with `inversion: true` and labeled accordingly.

**Aviation relevance:** Surface-based inversions trap haze/fog (low visibility). Elevated inversions cap convective development and indicate smooth air above the inversion top.

### 3.2 Icing Assessment

**Module:** `sounding/icing.py`

**Current method:** Ogimet continuous icing index with cloud proximity check.

Only levels near/in cloud are assessed (DD < 3°C or within 500ft of a cloud layer).

**Ogimet icing index:**

Uses continuous formulas that peak near −7°C, matching observed supercooled liquid water distribution:

```
Combined = (layered_frac × layered_index + convective_frac × convective_index) / 2

Layered:     100 × (−t) × (t + 14) / 49      when −14 ≤ t ≤ 0 °C
Convective:  200 × (ρv_base − ρv_cell) / ρv_20sat × √((T_K − 253.15)/20)
                                                when −20 ≤ t ≤ 0 °C
```

Where ρv = water vapor density = e_sat(Td) / (Rv × T_K), and CAPE determines the layered/convective blend (see §4.4).

**Severity mapping:**

| Icing index | Risk |
|-------------|------|
| 0 | NONE |
| 0–30 | LIGHT |
| 30–80 | MODERATE |
| > 80 | SEVERE |

**Icing type** (from temperature, independent of severity):

| Temperature | Type |
|-------------|------|
| −3°C to 0°C | Clear |
| −10°C to −3°C | Mixed |
| < −10°C | Rime |

**Severity modifiers** (secondary adjustment on top of Ogimet index):
- RH > 95% → upgrade by one level
- Precipitable water > 25mm → upgrade light→moderate

**Previous approach (replaced):** Wet-bulb temperature bands with hard thresholds at -3/-10/-15/-20°C. Over-flagged near 0°C and under-flagged in the −5°C to −10°C zone where supercooled liquid water peaks.

### 3.3 Convective Assessment

**Module:** `sounding/convective.py`

Classifies convective risk from CAPE with CIN modulation and severe weather modifiers.

| CAPE | Risk | Significance |
|------|------|-------------|
| < 100 J/kg | None | Stable or very weakly unstable. Convection not possible. |
| 100–500 | Low | Weak instability. Fair-weather cumulus, weak showers at best. |
| 500–1500 | Moderate | Moderate instability. Thunderstorms possible with sufficient trigger. |
| 1500–3000 | High | Strong instability. Vigorous thunderstorms likely if triggered. Significant turbulence. |
| > 3000 | Extreme | Extreme instability. Severe storms, large hail, possible tornadoes. Avoid at all costs. |

**Severe modifiers** flag additional hazards when thresholds are crossed (shear > 40kt, K > 35, TT > 55, LI < −6, high freezing level + CAPE > 1000).

### 3.4 Vertical Motion & Turbulence

**Module:** `sounding/vertical_motion.py`

**Profile classification** from omega (ω) distribution:

| Class | Criterion | Meaning |
|-------|-----------|---------|
| Quiescent | all \|ω\| < 1 Pa/s | Calm, stable air. Smooth flight expected. |
| Synoptic ascent | mean ω < 0, no large values | Large-scale uplift (front, low pressure). Widespread cloud/precip likely. |
| Synoptic subsidence | mean ω > 0, no large values | Large-scale sinking. Clearing, possible inversions. Often smooth. |
| Oscillating | ≥ 2 sign changes in ω | Wave activity. Mountain waves, gravity waves. Possible turbulence. |
| Convective | \|ω\| > 10 Pa/s | Vigorous vertical motion. Active convection in model. |

**CAT risk** from Richardson number at each layer:

| Ri | CAT Risk | Physics |
|----|----------|---------|
| < 0.25 | Severe | Shear overwhelms stability. Turbulent breakdown guaranteed (Kelvin-Helmholtz instability). |
| 0.25–0.5 | Moderate | Marginal stability. Turbulence likely, especially with external forcing. |
| 0.5–1.0 | Light | Dynamically stable but approaching critical. Intermittent turbulence possible. |
| > 1.0 | None | Shear insufficient to overcome buoyancy. Laminar flow. |

### 3.5 Altitude Advisories

**Module:** `sounding/advisories.py`

Aggregates cloud, icing, turbulence, and vertical motion data into vertical regimes (per model) and cross-model advisories.

**Vertical regimes:** Altitude slices with uniform conditions. Transitions at cloud base/top, icing zone boundaries, freezing level, ICAO band boundaries. Adjacent regimes with identical properties are merged.

**Advisory types:**

| Advisory | Aggregation | Logic |
|----------|------------|-------|
| `descend_below_icing` | min() across models | Per model: min(freezing level, lowest icing-cloud base) − 500ft |
| `climb_above_icing` | max() across models | Per model: max(highest icing top, highest cloud-in-icing top) + 500ft. `feasible` if ≤ ceiling. |
| `cat_turbulence` | worst across models | Reports worst CAT layer altitude and risk level. |
| `strong_vertical_motion` | max \|w\| across models | Flags altitudes with \|w\| > 200 ft/min. |

### 3.6 Wind Components

**Module:** `analysis/wind.py`

Decomposes wind vector relative to flight track:
- `headwind = V × cos(wind_dir − track)` — positive = headwind, negative = tailwind
- `crosswind = V × sin(wind_dir − track)` — positive = from right

### 3.7 Model Divergence

**Module:** `analysis/comparison.py`

Compares each metric across models. Spread = max − min (circular statistics for wind direction).

| Agreement | Condition |
|-----------|-----------|
| Good | spread ≤ good_threshold |
| Moderate | good < spread ≤ poor_threshold |
| Poor | spread > poor_threshold |

Poor agreement signals forecast uncertainty — brief conservatively.

---

## 4. Per-Model Data Availability and Derivation

### 4.1 Availability Matrix

Shows data source for each key quantity per model. **Bold** = derived when API field is unavailable.

| Quantity | GFS | ECMWF | ICON | MétéoFr | UKMO | Derivation method |
|----------|-----|-------|------|---------|------|-------------------|
| T at levels | API | API | API | API | API | — |
| RH at levels | API | API | API | API | API | — |
| Dewpoint at levels | API | **derived** | API | **derived** | **derived** | Magnus formula from T + RH |
| Wind at levels | API | API | API | API | API | — |
| Geopotential height | API | API | API | API | API | — |
| Omega (ω) | API | API | **n/a** | **n/a** | **n/a** | Not derivable. Vertical motion analysis unavailable for these models. |
| Cloud cover (total) | API | API | API | API | API | — |
| Cloud cover low/mid/high | API | **derivable** | API | API | API | From sounding cloud layers mapped to ICAO bands |
| Freezing level | API | **derived** | API | **derived** | **derived** | Linear interpolation of T profile through 0°C |
| CAPE | API | **derived** | API | **derived** | **derived** | `mpcalc.cape_cin()` from sounding profile |
| CIN | **derived** | **derived** | **derived** | **derived** | **derived** | Always from `mpcalc.cape_cin()` (API CAPE is single value, CIN requires profile) |
| Visibility | API | **n/a** | API | **n/a** | **n/a** | Not derivable from standard pressure levels |
| Precipitation probability | API | **n/a** | **n/a** | **n/a** | **n/a** | Requires ensemble data. GFS-only. |
| Water vapor density | **derivable** | **derivable** | **derivable** | **derivable** | **derivable** | `ρv = e_sat(Td) / (Rv × T_K)` where Rv = 461.5 J/(kg·K) |
| Mixing ratio | **derivable** | **derivable** | **derivable** | **derivable** | **derivable** | `mpcalc.mixing_ratio_from_relative_humidity(P, T, RH)` |

### 4.2 Key Derivation Methods

**Dewpoint from T + RH** (Magnus formula, used in `prepare.py`):
```
γ = (17.27 × T) / (237.7 + T) + ln(RH / 100)
Td = (237.7 × γ) / (17.27 − γ)
```
Accurate to ~0.2°C for typical atmospheric conditions.

**Water vapor density** (ideal gas law, needed for Ogimet convective icing index):
```
e = saturation_vapor_pressure(Td)    # MetPy: mpcalc.saturation_vapor_pressure()
ρv = e / (Rv × T_K)                  # Rv = 461.5 J/(kg·K)
```
At 20°C saturated: ρv ≈ 17.3 g/m³ (the constant in the Ogimet formula).

**CAPE from sounding** (when not in API):
```python
parcel = mpcalc.parcel_profile(P, T_sfc, Td_sfc)
cape, cin = mpcalc.cape_cin(P, T, Td, parcel)
```
Integrates positive buoyancy between LFC and EL. Result matches API CAPE within ~10% for well-resolved soundings.

**Cloud cover per ICAO band** (when low/mid/high not in API):
Sounding-derived cloud layers are mapped to ICAO bands (low < 6500ft, mid 6500–20000ft, high > 20000ft). Cloud coverage classified from dewpoint depression. Coarser than NWP parameterization but provides a consistent fallback.

### 4.3 What Cannot Be Derived

| Quantity | Why | Impact |
|----------|-----|--------|
| Omega (ω) for ICON/MétéoFr/UKMO | Requires model dynamics, not recoverable from T/wind alone | No vertical motion classification or CAT risk for these models |
| Visibility for ECMWF/MétéoFr/UKMO | Parameterized from sub-grid microphysics not available at pressure levels | VFR/IFR assessment limited to cloud cover proxy |
| Precipitation probability | Requires ensemble spread data | Only available from GFS ensemble |
| Stratiform vs. convective cloud split | Not in any Open-Meteo API | Must approximate for Ogimet icing index (see §4.4) |

### 4.4 Approximating Cloud Type Split for Icing Index

The Ogimet formula requires separate stratiform and convective cloud cover percentages. No model provides this directly via Open-Meteo. Proposed approximation:

1. **CAPE available** (GFS, ICON, or MetPy-derived for all):
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

**Previous issue:** Wet-bulb bands classified −3°C to 0°C as SEVERE, but observed maximum supercooled liquid water content peaks at −5°C to −10°C.

**Resolution:** Replaced with Ogimet continuous icing index (parabola peaking at −7°C). Severity is now continuous rather than banded, better matching observational data.

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
| `wet_bulb_temperature()` | thermo | Wet-bulb T (icing classifier) |
| `equivalent_potential_temperature()` | thermo | θ_e (air mass tracer) |
| `relative_humidity_from_dewpoint()` | thermo | RH from T and Td |
| `potential_temperature()` | thermo | θ for stability/Ri calculation |
| `wind_components()` | wind | u, v from speed/direction |
| `vertical_velocity()` | thermo | ω (Pa/s) → w (m/s) conversion |
| `saturation_vapor_pressure()` | thermo | Saturation vapor pressure for water vapor density (Ogimet icing index) |

### MetPy Functions Available but Not Yet Used

| Function | What it provides | Potential use |
|----------|-----------------|---------------|
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
- Ogimet icing index: Autorouter GRAMET documentation
- MetPy documentation: https://unidata.github.io/MetPy/
- Sounding analysis plan: [sounding_analysis_plan.md](./sounding_analysis_plan.md)
- Vertical motion plan: [vertical-motion-plan.md](./vertical-motion-plan.md)
