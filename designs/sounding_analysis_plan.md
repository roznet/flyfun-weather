# Sounding Analysis Module — Design Document

## Overview

This module processes atmospheric sounding profiles (from ECMWF, ICON, GFS) using MetPy to produce aviation-relevant derived metrics. The goal is to enrich a flight briefing app with thermodynamic indices, cloud layer detection, icing risk assessment, turbulence indicators, and convective analysis — computed per-model for cross-model comparison.

## Input Data

Each model sounding provides a vertical profile at a given lat/lon and forecast time:

- **Pressure levels** (hPa) — the vertical axis
- **Temperature** (°C or K) at each level
- **Dewpoint temperature** (°C or K) at each level
- **Wind speed** (kt or m/s) and **wind direction** (°) at each level
- **Geopotential height** (m) at each level (to map pressure → altitude)

All MetPy functions expect `pint`-wrapped `Quantity` arrays. Ensure units are attached before any computation.

```python
import metpy.calc as mpcalc
from metpy.units import units
import numpy as np

# Example: wrapping raw data
pressure = np.array([1013, 950, 850, 700, 500, 300]) * units.hPa
temperature = np.array([15, 12, 5, -5, -20, -45]) * units.degC
dewpoint = np.array([10, 8, 2, -10, -30, -50]) * units.degC
wind_speed = np.array([5, 10, 15, 25, 30, 40]) * units.knot
wind_direction = np.array([270, 260, 250, 240, 230, 220]) * units.degree
height = np.array([0, 540, 1500, 3010, 5570, 9160]) * units.meter
```

---

## Part 1: Direct MetPy Computations

These are values and indices computed directly from MetPy functions. They form the raw building blocks.

### 1.1 Per-Level Derived Values

Compute these for every pressure level in the profile:

| Metric | MetPy Function | Aviation Relevance |
|--------|---------------|-------------------|
| Relative Humidity | `mpcalc.relative_humidity_from_dewpoint(T, Td)` | Moisture at level, cloud/fog indicator |
| Mixing Ratio | `mpcalc.mixing_ratio_from_relative_humidity(pressure, T, RH)` | Moisture content |
| Wet-Bulb Temperature | `mpcalc.wet_bulb_temperature(pressure, T, Td)` | Icing assessment (see Part 2) |
| Virtual Temperature | `mpcalc.virtual_temperature(T, mixing_ratio)` | Density altitude corrections |
| Theta (Potential Temperature) | `mpcalc.potential_temperature(pressure, T)` | Stability analysis |
| Theta-E (Equiv. Potential Temp) | `mpcalc.equivalent_potential_temperature(pressure, T, Td)` | Airmass identification, frontal zones |
| Dewpoint Depression | `T - Td` (direct subtraction) | Cloud probability at level |
| Lapse Rate (between levels) | `(T[i] - T[i+1]) / (height[i+1] - height[i])` | Stability, turbulence hint |

### 1.2 Profile-Level Thermodynamic Indices

Compute once per sounding profile:

| Index | MetPy Function | What It Tells You |
|-------|---------------|-------------------|
| LCL (Lifted Condensation Level) | `mpcalc.lcl(pressure[0], temperature[0], dewpoint[0])` | Estimated cloud base altitude |
| LFC (Level of Free Convection) | `mpcalc.lfc(pressure, temperature, dewpoint)` | Altitude where free convection starts |
| EL (Equilibrium Level) | `mpcalc.el(pressure, temperature, dewpoint)` | Approximate CB top / convective cloud top |
| CAPE (surface-based) | `mpcalc.surface_based_cape_cin(pressure, temperature, dewpoint)` | Convective energy — thunderstorm potential |
| CAPE (most-unstable) | `mpcalc.most_unstable_cape_cin(pressure, temperature, dewpoint)` | Worst-case convective energy |
| CAPE (mixed-layer) | `mpcalc.mixed_layer_cape_cin(pressure, temperature, dewpoint)` | Average boundary layer convective energy |
| CIN | Returned alongside CAPE from above functions | Convective inhibition — "cap" strength |
| Lifted Index | `mpcalc.lifted_index(pressure, temperature, parcel_profile)` | Quick stability check |
| Showalter Index | `mpcalc.showalter_index(pressure, temperature, dewpoint)` | Mid-level stability |
| K-Index | `mpcalc.k_index(pressure, temperature, dewpoint)` | Thunderstorm potential |
| Total Totals | `mpcalc.total_totals_index(pressure, temperature, dewpoint)` | Severe weather potential |
| Precipitable Water | `mpcalc.precipitable_water(pressure, dewpoint)` | Total column moisture |

**Parcel profile** (needed for some indices):
```python
parcel_profile = mpcalc.parcel_profile(pressure, temperature[0], dewpoint[0])
```

### 1.3 Wind-Derived Values

| Metric | MetPy Function | Aviation Relevance |
|--------|---------------|-------------------|
| Wind Components (u, v) | `mpcalc.wind_components(wind_speed, wind_direction)` | Headwind/crosswind at level |
| Bulk Wind Shear (0-6 km) | `mpcalc.bulk_shear(pressure, u, v, height=height, depth=6000*units.meter)` | Convective severity, turbulence |
| Bulk Wind Shear (0-1 km) | `mpcalc.bulk_shear(pressure, u, v, height=height, depth=1000*units.meter)` | Low-level shear, approach hazards |
| Storm-Relative Helicity | `mpcalc.storm_relative_helicity(height, u, v, depth=3000*units.meter)` | Severe convection rotation potential |

### 1.4 Key Altitude Markers

Compute these by interpolating through the profile:

| Marker | How to Compute | Use |
|--------|---------------|-----|
| Freezing Level (0°C) | Interpolate where T crosses 0°C | Icing boundary, rain/snow transition |
| -10°C Level | Interpolate where T crosses -10°C | SLD icing zone lower bound |
| -20°C Level | Interpolate where T crosses -20°C | Upper icing zone limit (approx) |
| Tropopause (approx) | Where lapse rate drops below 2°C/km sustained | Max convective extent |

Use `mpcalc.pressure_to_height_std(pressure)` or the model's geopotential height for pressure-to-altitude conversion.

---

## Part 2: Aviation-Derived Analysis

These are heuristics and assessments built on top of the Part 1 computations. MetPy does not provide these directly — they must be implemented as analysis logic.

### 2.1 Cloud Layer Detection

**Method:** Walk up the profile and identify layers where dewpoint depression (T - Td) is below a threshold.

```
Algorithm:
1. Compute dewpoint_depression = T - Td at each level
2. Flag levels where dewpoint_depression < CLOUD_THRESHOLD (use 3°C as default, optionally 2.5°C)
3. Group consecutive flagged levels into cloud layers
4. For each cloud layer, record:
   - cloud_base: lowest level pressure and altitude (ft AMSL)
   - cloud_top: highest level pressure and altitude (ft AMSL)
   - thickness: cloud_top_alt - cloud_base_alt
   - mean_RH: average relative humidity within the layer
   - mean_temperature: average temperature within the layer (feeds icing assessment)
   - estimated_coverage: map mean dewpoint depression to coverage:
       - < 1°C → OVC (overcast)
       - 1-2°C → BKN (broken)
       - 2-3°C → SCT (scattered)
```

**Additional cloud base estimate:** The LCL from Part 1.2 provides a thermodynamic cloud base estimate (convective cloud base). Compare it against the dewpoint-depression method — the LCL applies for convective clouds, the profile scan catches stratiform layers too.

**Output per cloud layer:**
```python
@dataclass
class CloudLayer:
    base_pressure: float  # hPa
    base_altitude_ft: float  # ft AMSL
    top_pressure: float  # hPa
    top_altitude_ft: float  # ft AMSL
    thickness_ft: float
    mean_temperature_c: float
    estimated_coverage: str  # SCT / BKN / OVC
    icing_risk: str  # from section 2.2
```

### 2.2 Icing Risk Assessment

Icing occurs when flying through visible moisture (cloud) at subfreezing temperatures. Severity depends on temperature, moisture content, and droplet characteristics.

**Step 1: Identify icing-possible zones**

A level has icing potential if ALL of:
- It is within or near a cloud layer (dewpoint depression < 3°C)
- Wet-bulb temperature is between -20°C and 0°C
- Temperature is ≤ 0°C

**Step 2: Classify severity by temperature band**

| Wet-Bulb Temperature Range | Typical Icing Type | Severity |
|---------------------------|-------------------|----------|
| -3°C to 0°C | Clear ice (large droplets, dangerous) | MODERATE to SEVERE |
| -10°C to -3°C | Mixed ice | MODERATE |
| -15°C to -10°C | Rime ice | LIGHT to MODERATE |
| -20°C to -15°C | Light rime (less supercooled water) | LIGHT |
| Below -20°C | Rare (most moisture frozen out) | NEGLIGIBLE |

**Step 3: Enhance with moisture content**

Increase severity one step if:
- Relative humidity > 95% at the level
- Precipitable water is high (> 25mm)
- Large dewpoint depression gradient exists below the layer (warm moist air being lifted)

**Step 4: Supercooled Large Droplet (SLD) flag**

Flag SLD risk if:
- Temperature between -15°C and 0°C
- A warm layer (T > 0°C) exists above the icing layer (classic freezing rain setup — warm nose aloft)
- OR thick cloud layer (> 3000 ft) with tops warmer than -12°C

**Output per level in icing zone:**
```python
@dataclass
class IcingAssessment:
    pressure: float  # hPa
    altitude_ft: float
    temperature_c: float
    wet_bulb_c: float
    severity: str  # NONE / LIGHT / MODERATE / SEVERE
    icing_type: str  # RIME / MIXED / CLEAR / NONE
    sld_risk: bool
```

### 2.3 Convective Assessment

Synthesize Part 1 thermodynamic indices into an actionable convective risk summary.

```
Convective Risk Level:
  - NONE: CAPE < 100 J/kg OR CIN < -200 J/kg (strong cap)
  - LOW: CAPE 100-500 J/kg, moderate CIN
  - MODERATE: CAPE 500-1500 J/kg, CIN > -50 J/kg
  - HIGH: CAPE 1500-3000 J/kg, CIN > -25 J/kg
  - EXTREME: CAPE > 3000 J/kg, weak or no CIN

Severity modifiers (if convection fires):
  - Bulk shear 0-6km > 40 kt → organized storms, possible supercells
  - Bulk shear 0-6km > 25 kt → multicell potential
  - SRH 0-3km > 150 m²/s² → rotation risk
  - Freezing level > 3500m AND CAPE > 1000 → large hail potential

Convective layer (where to avoid):
  - Base: LFC altitude (if LFC exists)
  - Top: EL altitude (if EL exists)
  - If LFC/EL don't exist: no free convection expected
```

**Output:**
```python
@dataclass
class ConvectiveAssessment:
    risk_level: str  # NONE / LOW / MODERATE / HIGH / EXTREME
    cape_surface: float  # J/kg
    cape_most_unstable: float
    cin: float
    lcl_ft: float  # cloud base
    lfc_ft: float | None  # convection base (None if no LFC)
    el_ft: float | None  # convection top
    bulk_shear_0_6km_kt: float
    severe_modifiers: list[str]  # e.g. ["organized storms", "large hail potential"]
    lifted_index: float
    k_index: float
    total_totals: float
```

### 2.4 Turbulence Indicators

MetPy doesn't directly assess turbulence. Use these proxy indicators:

**Mechanical turbulence (wind shear based):**
```
For each pair of adjacent levels:
  wind_shear_kt_per_1000ft = |wind_change| / altitude_difference * 1000
  
  Thresholds:
    < 4 kt/1000ft → SMOOTH
    4-8 kt/1000ft → LIGHT turbulence
    8-12 kt/1000ft → MODERATE turbulence
    > 12 kt/1000ft → SEVERE turbulence
```

**Thermal turbulence (lapse rate based):**
```
Dry adiabatic lapse rate (DALR) ≈ 9.8°C/km
Environmental lapse rate at level = computed in Part 1

If lapse_rate > 8°C/km → unstable, expect convective turbulence (thermals)
If lapse_rate > DALR → absolutely unstable, strong turbulence
```

**Temperature inversion detection:**
```
If temperature INCREASES with altitude between two levels:
  - Flag as inversion layer
  - Record inversion base and top altitude
  - Aviation relevance: turbulence at inversion boundary, visibility trapping below
```

**Mountain wave hint (if terrain elevation is known):**
- Strong wind (>25kt) perpendicular to terrain at mountain-top level
- Stable layer (low lapse rate or inversion) just above mountain tops
- Flag as mountain wave risk

**Output:**
```python
@dataclass
class TurbulenceIndicator:
    altitude_ft: float
    wind_shear_severity: str  # SMOOTH / LIGHT / MODERATE / SEVERE
    thermal_instability: str  # STABLE / NEUTRAL / UNSTABLE / STRONGLY_UNSTABLE
    inversion: bool
    notes: list[str]
```

### 2.5 Freezing Level and Icing Altitude Bands

Summarize as altitude bands for quick pilot reference:

```python
@dataclass
class FreezingInfo:
    freezing_level_ft: float  # 0°C crossing
    minus_10_level_ft: float  # -10°C crossing
    minus_20_level_ft: float  # -20°C crossing
    icing_band_base_ft: float | None  # lowest altitude with icing risk
    icing_band_top_ft: float | None  # highest altitude with icing risk
    sld_risk_band: tuple[float, float] | None  # (base_ft, top_ft) if SLD flagged
```

### 2.6 Flight Level Summary

For a given requested flight level (or set of levels), produce a consolidated per-level snapshot:

```python
@dataclass
class FlightLevelBrief:
    altitude_ft: float
    pressure_hpa: float
    temperature_c: float
    dewpoint_c: float
    dewpoint_depression_c: float
    relative_humidity_pct: float
    wind_speed_kt: float
    wind_direction_deg: float
    wet_bulb_c: float
    in_cloud: bool
    cloud_coverage: str | None  # SCT/BKN/OVC if in cloud
    icing_severity: str  # NONE / LIGHT / MODERATE / SEVERE
    icing_type: str  # NONE / RIME / MIXED / CLEAR
    sld_risk: bool
    turbulence_shear: str
    turbulence_thermal: str
    density_altitude_ft: float  # for performance
```

---

## Part 3: Cross-Model Comparison

### 3.1 Data Structure

Each model (ECMWF, ICON, GFS) produces one `SoundingAnalysis` object per location/time.

```python
@dataclass
class SoundingAnalysis:
    model: str  # "ECMWF" / "ICON" / "GFS"
    valid_time: datetime
    location: tuple[float, float]  # lat, lon
    
    # Part 1 raw
    pressure_levels: np.ndarray
    temperature: np.ndarray
    dewpoint: np.ndarray
    wind_speed: np.ndarray
    wind_direction: np.ndarray
    height: np.ndarray
    
    # Part 1 computed
    lcl_pressure: float
    lcl_altitude_ft: float
    lfc_pressure: float | None
    lfc_altitude_ft: float | None
    el_pressure: float | None
    el_altitude_ft: float | None
    cape_surface: float
    cape_most_unstable: float
    cin: float
    lifted_index: float
    k_index: float
    total_totals: float
    precipitable_water_mm: float
    freezing_level_ft: float
    
    # Part 2 derived
    cloud_layers: list[CloudLayer]
    icing_zones: list[IcingAssessment]
    convective: ConvectiveAssessment
    turbulence: list[TurbulenceIndicator]
    freezing_info: FreezingInfo
```

### 3.2 Comparison Metrics

For the briefing, highlight model agreement and divergence on key parameters:

```
For each metric, compute across models:
  - mean, min, max, spread (max - min)
  - agreement flag: spread < threshold → "models agree" vs "models diverge"

Key comparison thresholds:
  - Freezing level: spread > 500ft → divergence flag
  - CAPE: spread > 500 J/kg → divergence flag
  - Cloud base: spread > 1000ft → divergence flag
  - Icing severity: any model differs by 2+ categories → divergence flag

Present as:
  "Freezing level: 5500-6200ft (models diverge — ECMWF lowest, GFS highest)"
  "CAPE: 800-950 J/kg (models agree — moderate convective risk)"
  "Cloud base: FL045-FL050 (good agreement)"
```

### 3.3 Confidence Indicators

When models agree, confidence is higher. Flag for the pilot:

```
confidence = HIGH if all models within tight thresholds
confidence = MEDIUM if 2 of 3 agree, one outlier
confidence = LOW if all models diverge significantly
```

---

## Part 4: Implementation Notes

### 4.1 MetPy Gotchas

- **Data must be sorted by descending pressure** (surface first, top of atmosphere last). MetPy expects this. Sort before passing.
- **Units are mandatory.** Every array must be wrapped with `pint` units via `metpy.units`. Failing to do so produces silent wrong results or crashes.
- **NaN handling:** Model data may have NaN at some levels. Interpolate or mask before passing to MetPy functions. Many MetPy functions do not handle NaN gracefully.
- **LFC/EL may not exist** if the atmosphere is stable. Handle `None` returns.
- **Pressure levels vary by model.** ECMWF, ICON, and GFS may provide different pressure level sets. Interpolate to a common grid if needed for direct comparison, or compare derived outputs only.

### 4.2 Dependencies

```
metpy>=1.5
pint
numpy
```

### 4.3 Suggested Module Structure

```
sounding/
├── __init__.py
├── compute.py          # Part 1: direct MetPy computations
├── cloud.py            # Part 2.1: cloud layer detection
├── icing.py            # Part 2.2: icing risk assessment
├── convective.py       # Part 2.3: convective assessment
├── turbulence.py       # Part 2.4: turbulence indicators
├── freezing.py         # Part 2.5: freezing level analysis
├── flight_level.py     # Part 2.6: per-level summary
├── comparison.py       # Part 3: cross-model comparison
├── models.py           # dataclass definitions (all the dataclasses above)
└── utils.py            # unit handling, interpolation, data prep
```

### 4.4 Integration with Briefing App

The briefing app should:
1. Fetch model sounding data for the route waypoints (departure, enroute, destination)
2. For each waypoint × model, instantiate a `SoundingAnalysis`
3. Run cross-model comparison
4. For the planned flight level(s), generate `FlightLevelBrief` per model
5. Feed results into the briefing report alongside existing METAR/TAF/SIGMET data

### 4.5 Performance Considerations

- MetPy computations are fast (milliseconds per profile). The bottleneck will be data fetching, not analysis.
- For a route with ~5 waypoints × 3 models, expect ~15 sounding analyses — trivially fast.
- Cache results by model run time + forecast hour to avoid recomputation.

---

## Part 5: Next Steps — Potential Extensions

This section documents areas to investigate for future improvement. These are research tasks — verify what's actually available before implementing.

### 5.1 Vertical Resolution: What Levels Do We Actually Have?

The quality of cloud layer detection and icing analysis depends heavily on vertical resolution. Standard pressure levels (1000, 925, 850, 700, 500, 300, 200) are too coarse — they'll miss thin cloud layers and smooth out inversions.

**Investigate for each model:**

- **ECMWF (IFS):** The full model has 137 model levels (L137). The publicly available data via ECMWF Open Data or CDS API may offer:
  - Standard pressure levels (coarse, ~15-25 levels)
  - A denser set of pressure levels (37 or 90 levels) if requested explicitly
  - Native model levels (best resolution, but requires conversion from hybrid sigma-pressure coordinates to geometric altitude — MetPy won't do this natively, you'd need to implement the ECMWF formula or use `cfgrib`/`earthkit`)
  - **Action:** Check what the current data pipeline is pulling. If it's standard pressure levels, investigate switching to the 90-level pressure level product or native model levels.

- **GFS:** Available on 26 standard pressure levels via NOMADS/NCEP. Also available on hybrid levels but these are less commonly used.
  - **Action:** Check if the 0.25° GFS product provides more levels than what we currently ingest. The HRRR (for US coverage) has 50+ levels and much better resolution — worth considering for US flights.

- **ICON (DWD):** Available via DWD Open Data. ICON-EU has ~60 model levels. Pressure-level products may be sparser.
  - **Action:** Check DWD Open Data for what vertical level sets are available for ICON-EU.

- **AROME/ARPEGE (Météo-France):** Relevant for European flights. ARPEGE is global (coarser), AROME is high-res over France/Western Europe.
  - **Action:** Investigate if Météo-France open data provides sounding-suitable vertical profiles. Could be a valuable addition for France/UK route planning.

**General rule:** Aim for at least 30+ levels between surface and 200 hPa for meaningful cloud/icing analysis. Below that, the analysis will have significant blind spots.

### 5.2 Cloud Liquid Water Content and Ice Water Content

The current icing analysis uses temperature + humidity proxies, which over-predicts (it flags any cloud below 0°C). Real icing forecast algorithms (NOAA CIP/FIP) use model-predicted hydrometeor fields.

**Fields to look for in model output:**

| Field | GRIB shortName | What It Provides |
|-------|---------------|-----------------|
| Cloud Liquid Water Content (CLWC) | `clwc` | Mass of liquid water per unit volume — directly indicates supercooled water |
| Cloud Ice Water Content (CIWC) | `ciwc` | Mass of ice per unit volume — ice means no icing risk at that level |
| Specific Cloud Liquid Water | `clwc` or `ql` | Same concept, different naming |
| Total Cloud Cover (per level) | `cc` | Model's own cloud fraction at each level |
| Vertical Velocity (omega) | `w` or `omega` | Upward motion sustains supercooled water — critical for icing severity |

**Model availability (to verify):**

- **ECMWF:** CLWC and CIWC are standard prognostic variables in IFS. They *should* be available on model levels via CDS API. Check if they're available on pressure levels too, or only on native model levels.
- **GFS:** GFS does output cloud water mixing ratio (`CLWMR`) and ice water mixing ratio (`ICMR`) on pressure levels. Check NOMADS availability.
- **ICON:** DWD ICON outputs `QC` (cloud water) and `QI` (cloud ice). Check if these are in the open data products.

**If we can get CLWC:**
- Replace the dewpoint-depression cloud detection with actual cloud water > threshold (e.g., > 0.01 g/kg)
- Icing severity becomes: CLWC at subfreezing temperatures directly indicates supercooled liquid water amount
  - CLWC > 0.1 g/m³ at T < 0°C → MODERATE to SEVERE icing
  - CLWC 0.01-0.1 g/m³ at T < 0°C → LIGHT to MODERATE icing
  - CIWC dominant (low CLWC/CIWC ratio) → glaciated cloud, low icing risk
- This is a major accuracy improvement over temperature-band heuristics

### 5.3 Additional Model Fields Worth Investigating

Beyond cloud water, other fields could enrich the briefing:

| Field | Why It Matters | Models Likely to Have It |
|-------|---------------|------------------------|
| **Vertical velocity (w / omega)** | Updraft/downdraft strength → turbulence, convective intensity, icing severity | All (ECMWF, GFS, ICON) |
| **Boundary layer height (PBLH)** | Top of turbulent mixing layer — expect bumps below this altitude | GFS, ECMWF, ICON |
| **Visibility (if available)** | Direct fog/low vis indication at surface | Some models at surface only |
| **Precipitation rate / type** | Rain, snow, freezing rain at surface and aloft | All |
| **Convective precipitation** | Distinguishes convective vs stratiform — helps thunder risk | All |
| **CAT (Clear Air Turbulence) indices** | Some models output Ellrod index or similar CAT predictors | ECMWF (via derived products), GFS (some) |
| **Eddy Dissipation Rate (EDR)** | Direct turbulence metric if available | GTG (Graphical Turbulence Guidance) — US only, may not be open |
| **Richardson number** | Ratio of stability to shear — low Ri = turbulence likely. Can be computed from what we already have (lapse rate + wind shear). | Compute from existing data |

**Action:** Richardson number is computable right now from the existing profile data — add it to the turbulence indicators in Part 2.4:

```
Ri = (g / theta) * (d_theta/dz) / (du/dz)² + (dv/dz)²

Ri < 0.25 → dynamic instability, turbulence likely
Ri 0.25-1.0 → marginal, turbulence possible
Ri > 1.0 → stable, turbulence unlikely
```

### 5.4 Terrain-Aware Analysis

Currently the analysis is purely column-based (one vertical profile). For a route briefing, terrain awareness would improve turbulence and wave analysis:

- **Terrain elevation along route:** If known, flag when planned altitude is within 2000-3000ft of terrain with strong winds (mountain wave / rotor risk)
- **Lee wave indicators:** Stable layer near mountain-top level + strong cross-ridge wind → wave activity. Requires knowing terrain orientation relative to wind.
- **Valley fog / low cloud trapping:** If an inversion exists near or below ridge-top level, valleys may have trapped low cloud/fog even if the sounding above looks clear.

**Action:** Investigate SRTM or similar elevation data for terrain profiles along route. This is a bigger feature but would significantly improve the briefing for European mountain crossings (Alps, Pyrenees, Massif Central).

### 5.5 Temporal Trend Analysis

For the D-7 to D-0 weather trend tracker, track how these sounding-derived metrics evolve across forecast runs:

- Plot CAPE, freezing level, cloud base trends over successive model runs
- Detect if models are converging or diverging as the flight date approaches
- Flag significant forecast changes (e.g., "CAPE jumped from 200 to 1500 J/kg in latest ECMWF run")
- Compare same-model successive runs (ECMWF 00Z vs 12Z vs next day) for consistency

This ties the sounding analysis into the existing trend tracker concept. The derived metrics from this module provide meaningful scalar values to trend over time, which is much more informative than trying to trend raw soundings.

### 5.6 SIGMET/AIRMET Cross-Reference

Once we have model-derived icing, turbulence, and convective assessments, compare them against issued SIGMETs and AIRMETs:

- If our analysis flags moderate icing at FL080-FL120 and there's a matching SIGMET, confidence increases
- If our analysis flags risk but no SIGMET is issued, still worth noting ("model indicates risk, no current SIGMET")
- If a SIGMET exists but our models don't support it, flag the discrepancy

This is a later-stage integration but adds significant value to the briefing.
