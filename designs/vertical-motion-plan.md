# Vertical Motion & Energy Analysis — Design Document

## Overview

Add vertical motion diagnostics and turbulence indicators to the sounding analysis pipeline. This extends the existing Phase 4a sounding analysis (thermodynamics, clouds, icing, convective) with NWP-predicted vertical velocity profiles and derived stability indicators.

**Goal**: Provide a GRAMET-like understanding of "where the air is going up/down and how hard" at each waypoint, plus turbulence risk from dynamic instability.

## Current State

### Already Computed (in `analysis/sounding/`)
- CAPE (surface-based, most-unstable, mixed-layer), CIN
- LCL, LFC, EL altitudes
- Lifted Index, Showalter Index, K-Index, Total Totals
- Lapse rates per level (°C/km)
- Bulk wind shear (0–1 km, 0–6 km)
- Wet-bulb temperature per level
- Equivalent potential temperature (θe) per level
- Convective risk classification with severity modifiers

### Missing
- NWP model-predicted vertical motion (omega / w)
- Richardson Number (dynamic turbulence indicator)
- Brunt-Väisälä frequency (static stability measure)
- Integrated vertical motion profile interpretation

---

## Data Source: Open-Meteo `vertical_velocity`

### Availability by Model

| Model | `vertical_velocity` at pressure levels | Notes |
|-------|---------------------------------------|-------|
| **GFS** | YES | All 8 standard levels |
| **ECMWF** | YES | Fixed Oct 2025 ([issue #1539](https://github.com/open-meteo/open-meteo/issues/1539)) |
| **ICON** | NO | Not in pressure level vars |
| **UKMO** | NO | Not available |
| **Météo-France** | NO | Not available |

### API Parameter

```
vertical_velocity_{level}hPa
```

For levels: 1000, 925, 850, 700, 600, 500, 400, 300 hPa.

### Units & Sign Convention

The Open-Meteo field is **omega (dp/dt)** in **Pa/s**, despite the `vertical_velocity` name:
- **Negative omega → upward motion (ascent)**
- **Positive omega → downward motion (subsidence)**

This follows standard meteorological convention. Convert to w (m/s) via:

```
w = -omega / (ρ · g)
```

MetPy provides `metpy.calc.vertical_velocity(omega, pressure, temperature)` for this conversion.

---

## Vertical Motion Magnitude Reference

| Scale | Omega (Pa/s) | w (m/s) | w (ft/min) | Aviation Impact |
|-------|-------------|---------|------------|-----------------|
| Quiescent | < 0.5 | < 0.07 | < 15 | Smooth air |
| Weak synoptic | 0.5–1 | 0.07–0.15 | 15–30 | Negligible |
| Moderate synoptic (fronts) | 1–5 | 0.15–0.8 | 30–160 | Light–moderate bumps |
| Strong forcing | 5–10 | 0.8–1.5 | 160–300 | Moderate turbulence |
| Convective | 10–100+ | 1.5–15+ | 300–3000+ | Severe / avoid |
| Mountain waves | oscillating | 1–10+ | 200–2000+ | Dangerous for GA |

**Convective contamination**: When |omega| > ~5 Pa/s at mid-troposphere, the model grid cell may be experiencing resolved convection — the sounding is "contaminated" and traditional parcel theory becomes less meaningful. (SHARPpy uses dashed purple bounds for this.)

---

## Implementation Plan

### Tier 1 — Fetch & Store Omega Profile

**Files to modify:**
- `src/weatherbrief/fetch/variables.py`: Add `"vertical_velocity"` to `PRESSURE_LEVEL_VARIABLES`; add to `unavailable_pressure` for ICON, UKMO, Météo-France
- `src/weatherbrief/models.py`: Add `vertical_velocity` field to `PressureLevelData`
- `src/weatherbrief/fetch/open_meteo.py`: Parse the new field (follows existing pattern)

**New data in sounding:**
- `omega_pa_s: float | None` per `DerivedLevel`
- `w_ms: float | None` (converted via MetPy) per `DerivedLevel`
- `w_fpm: float | None` (ft/min, for display) per `DerivedLevel`

### Tier 2 — Derive Turbulence Indicators

#### 2a. Richardson Number (per layer)

The Richardson Number relates static stability to wind shear:

```
Ri = N² / S²
```

Where:
- **N²** = Brunt-Väisälä frequency squared = (g/θ) · (dθ/dz) — static stability
- **S²** = (dU/dz)² + (dV/dz)² — squared wind shear

**Interpretation:**
| Ri | Meaning |
|----|---------|
| > 1.0 | Stable, laminar flow |
| 0.25–1.0 | Turbulence possible (marginal) |
| < 0.25 | Kelvin-Helmholtz instability → CAT likely |
| < 0 | Convectively unstable (N² < 0) |

**Implementation**: Compute between each pair of adjacent pressure levels using existing temperature and wind data. No new data fetch needed.

**MetPy functions available:**
- `metpy.calc.brunt_vaisala_frequency(height, potential_temperature)` — returns N
- `metpy.calc.brunt_vaisala_frequency_squared(height, potential_temperature)` — returns N²

#### 2b. Brunt-Väisälä Frequency (per layer)

Already needed for Richardson Number. Store separately as it's a useful stability indicator:
- High N → strongly stable (resistant to vertical displacement)
- Low N → weakly stable
- N² < 0 → statically unstable

#### 2c. Vertical Motion Profile Classification

Classify the omega profile shape at each waypoint:

| Pattern | Detection | Meaning |
|---------|-----------|---------|
| **Quiescent** | All levels |omega| < 1 Pa/s | No significant vertical motion |
| **Synoptic ascent** | Coherent negative omega, max at mid-levels | Frontal/low-pressure lifting |
| **Synoptic subsidence** | Coherent positive omega | High-pressure sinking |
| **Convective** | Very large |omega| (> 10 Pa/s), especially mid-upper levels | Active/resolved convection in model |
| **Oscillating** | Sign changes across levels, especially near terrain | Mountain wave signature |

### Tier 3 — Integration into Advisories & Display

**Extend `SoundingAnalysis` model:**
```python
class VerticalMotionAssessment(BaseModel):
    """Vertical motion profile summary for one model at one waypoint."""
    classification: VerticalMotionClass  # enum: QUIESCENT, SYNOPTIC_ASCENT, SYNOPTIC_SUBSIDENCE, CONVECTIVE, OSCILLATING
    max_omega_pa_s: float
    max_w_fpm: float
    max_w_level_ft: float  # altitude of strongest vertical motion
    cat_risk_layers: list[CATRiskLayer]  # layers with Ri < 1.0
```

**Extend `DerivedLevel` model:**
```python
# New fields per level
omega_pa_s: float | None       # raw model omega
w_fpm: float | None            # converted to ft/min
richardson_number: float | None # Ri for layer above
bv_frequency_squared: float | None  # N² for layer above
```

**Feed into altitude advisories:**
- Flag layers with strong vertical motion (|w| > 200 ft/min)
- Flag layers with low Richardson Number (Ri < 0.25 for CAT)
- Include in `VerticalRegime` labels (e.g., "Strong ascent", "CAT risk")

**Web UI:**
- Show omega/w profile alongside temperature/wind on sounding display
- Color-code vertical motion layers in the regime table
- Add CAT risk indicator

---

## MetPy Functions Reference

| Function | Purpose | Input | Output |
|----------|---------|-------|--------|
| `vertical_velocity(omega, p, T)` | omega → w conversion | Pa/s, hPa, °C | m/s |
| `brunt_vaisala_frequency(h, θ)` | Static stability | m, K | 1/s |
| `brunt_vaisala_frequency_squared(h, θ)` | Static stability squared | m, K | 1/s² |
| `potential_temperature(p, T)` | θ from p, T | hPa, K | K |

Note: We already compute potential temperature (θ) in `thermodynamics.py` via `equivalent_potential_temperature`. We'd need plain `potential_temperature` for BV frequency.

---

## CAPE → Maximum Updraft Speed

For reference, CAPE relates to theoretical maximum updraft speed:

```
w_max = sqrt(2 × CAPE)
```

| CAPE (J/kg) | w_max (m/s) | w_max (ft/min) | Category |
|-------------|-------------|----------------|----------|
| 0–300 | 0–24 | 0–4700 | Weak instability |
| 300–1000 | 24–45 | 4700–8800 | Moderate instability |
| 1000–2500 | 45–71 | 8800–14000 | Strong instability |
| 2500+ | 71+ | 14000+ | Extreme instability |

Real-world updrafts are typically 50–70% of w_max due to entrainment and water loading. Already computed in `convective.py` — this section is for reference only.

---

## Out of Scope (Requires Gridded 2D Data)

These diagnostics require horizontal gridded data, not available from single-point Open-Meteo queries:
- **Q-vector analysis** (MetPy `q_vector`): Requires 2D horizontal wind + temperature grids
- **Vorticity advection**: Requires gridded vorticity field
- **Temperature advection**: Requires gridded temperature + wind
- **Continuity equation**: Requires horizontal divergence field
- **Mountain wave specific diagnostics**: Requires high-resolution model + terrain data

---

## References

### Open-Meteo API
- [GFS API docs](https://open-meteo.com/en/docs) — `vertical_velocity` at pressure levels
- [ECMWF API docs](https://open-meteo.com/en/docs/ecmwf-api) — `vertical_velocity` added Oct 2025
- [GitHub issue #1539](https://github.com/open-meteo/open-meteo/issues/1539) — ECMWF vertical velocity fix

### MetPy
- [vertical_velocity](https://unidata.github.io/MetPy/latest/api/generated/metpy.calc.vertical_velocity.html) — omega → w conversion
- [brunt_vaisala_frequency](https://unidata.github.io/MetPy/latest/api/generated/metpy.calc.brunt_vaisala_frequency.html)
- [Sounding Calculations Example](https://unidata.github.io/MetPy/latest/examples/calculations/Sounding_Calculations.html)

### Meteorological Theory
- [NOAA Omega Equation Training](https://www.weather.gov/source/zhu/ZHU_Training_Page/Miscellaneous/omega/omega.html)
- [ECMWF Parameter Database — Vertical Velocity (param 135)](https://codes.ecmf.int/grib/param-db/135)
- [Millersville ESCI 342 — Vertical Motion](https://blogs.millersville.edu/adecaria/files/2021/11/esci342_lesson10_vertical_motion.pdf)
- [SHARPpy — Interpreting the GUI](https://sharppy.github.io/SHARPpy/interpreting_gui.html)

### Aviation Turbulence
- [GTG-3 Turbulence Product (NCAR/RAL)](https://ral.ucar.edu/solutions/products/graphical-turbulence-product-gtg-3)
- [SKYbrary — Mountain Waves](https://skybrary.aero/articles/mountain-waves)
- [FAA AC 00-57 — Hazardous Mountain Winds](https://www.faa.gov/documentLibrary/media/Advisory_Circular/00-57.pdf)
- [Richardson Number and CAT](https://en.wikipedia.org/wiki/Richardson_number)
