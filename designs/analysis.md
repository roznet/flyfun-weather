# Analysis Layer

> Aviation-specific analysis: wind components, MetPy sounding analysis, model comparison

All modules in `src/weatherbrief/analysis/`. Pure computation — no I/O.

## Intent

Transform raw NWP data into aviation-relevant assessments. Each module is independent, testable, and stateless. The sounding subpackage uses MetPy for physically-based thermodynamic analysis.

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
# Returns SoundingAnalysis | None (None if <3 valid levels)
```

Pipeline: `prepare → thermodynamics → clouds → icing → convective`

### Prepare (`sounding/prepare.py`)

Pint boundary — converts `list[PressureLevelData]` to `PreparedProfile` with pint-wrapped numpy arrays for MetPy. Derives dewpoint from RH via Magnus formula when not directly available. Filters levels missing temperature, sorts by descending pressure (surface first). Returns `None` if <3 valid levels.

Pint arrays **never leak** beyond the sounding subpackage.

### Thermodynamics (`sounding/thermodynamics.py`)

All MetPy calls live here. Two functions:

**`compute_indices(profile) → ThermodynamicIndices`** — profile-level values:
- Parcel profile, LCL, LFC, EL (via MetPy, handles None for stable profiles)
- CAPE/CIN: surface-based, most-unstable, mixed-layer
- Lifted index, Showalter index, K-index, Total Totals
- Precipitable water
- Bulk wind shear: 0-6km and 0-1km
- Temperature crossings: freezing level (0°C), -10°C, -20°C (linear interpolation)

**`compute_derived_levels(profile) → list[DerivedLevel]`** — per pressure level:
- Wet-bulb temperature (`mpcalc.wet_bulb_temperature`)
- Dewpoint depression (T - Td)
- Theta-E (`mpcalc.equivalent_potential_temperature`)
- Lapse rate between adjacent levels (°C/km)
- Relative humidity (from `mpcalc.relative_humidity_from_dewpoint`)

Every MetPy call is wrapped in try/except — returns None for fields that fail. All pint magnitudes extracted via `.magnitude` before storing in Pydantic models.

### Clouds (`sounding/clouds.py`)

Enhanced cloud detection from dewpoint depression profiles.

```python
layers = detect_cloud_layers(derived_levels, lcl_altitude_ft=idx.lcl_altitude_ft)
```

- Threshold: dewpoint depression < 3°C = "in cloud" (configurable)
- Groups consecutive levels into `EnhancedCloudLayer`
- Coverage from mean dewpoint depression: < 1°C → OVC, 1-2°C → BKN, 2-3°C → SCT
- Records base/top altitudes, thickness, mean temperature

### Icing (`sounding/icing.py`)

Wet-bulb temperature based icing with cloud awareness.

```python
zones = assess_icing_zones(derived_levels, cloud_layers, precipitable_water_mm=pw)
```

**Only assesses levels near/in cloud** (DD < 3°C or within 500ft of a cloud layer).

**Wet-bulb bands:**

| Tw range | Type | Base risk |
|----------|------|-----------|
| -3°C to 0°C | CLEAR | SEVERE |
| -10°C to -3°C | MIXED | MODERATE |
| -15°C to -10°C | RIME | MODERATE |
| -20°C to -15°C | RIME | LIGHT |

- Severity enhanced if RH > 95% or precipitable water > 25mm
- SLD detection: thick cloud (>3000ft) with warm tops (>-12°C), or temperature inversion in icing zone
- Adjacent levels grouped into `IcingZone` bands (gap ≤ 100hPa)

### Convective (`sounding/convective.py`)

Pure threshold logic from `ThermodynamicIndices` — no MetPy dependency.

| CAPE (J/kg) | Risk |
|-------------|------|
| < 100 | NONE |
| 100-500 | LOW |
| 500-1500 | MODERATE |
| 1500-3000 | HIGH |
| > 3000 | EXTREME |

- CIN < -200 J/kg suppresses risk by one level
- Severe modifiers: bulk shear >40kt (supercell), >25kt (multicell), high freezing level + CAPE >1000 (hail), K-index >35, Total Totals >55, LI < -6

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
- `descend_below_icing`: Per model, escape = min(freezing level, lowest icing-overlapping cloud base) - 500ft. Aggregate: min() across models.
- `climb_above_icing`: Per model, max(highest icing top, highest cloud top in icing temps) + 500ft. Aggregate: max() across models. `feasible` if ≤ flight_ceiling_ft.
- Cruise icing status: any model showing icing at cruise altitude → `cruise_in_icing=True`, worst risk across models.

## Model Comparison (`analysis/comparison.py`)

Score agreement across 2+ models for a given variable.

```python
div = compare_models("temperature_c", {"gfs": 5.0, "ecmwf": 6.0, "icon": 5.5})
div.agreement  # → AgreementLevel.GOOD
```

**Thresholds** (good, poor) — 14 total:

| Variable | Good ≤ | Poor > |
|----------|--------|--------|
| temperature_c | 2.0 | 5.0 |
| wind_speed_kt | 5.0 | 15.0 |
| wind_direction_deg | 20° | 60° |
| cloud_cover_pct | 15.0 | 40.0 |
| precipitation_mm | 1.0 | 5.0 |
| freezing_level_m | 200.0 | 600.0 |
| freezing_level_ft | 500.0 | 1500.0 |
| cape_surface_jkg | 200.0 | 500.0 |
| lcl_altitude_ft | 500.0 | 1500.0 |
| k_index | 5.0 | 15.0 |
| total_totals | 3.0 | 8.0 |
| precipitable_water_mm | 5.0 | 15.0 |
| lifted_index | 2.0 | 5.0 |
| bulk_shear_0_6km_kt | 5.0 | 15.0 |

**Circular statistics** for `wind_direction_deg` — uses sin/cos sum for mean, max angular difference for spread.

## Pipeline Integration

In `pipeline.analyze_waypoint()`, for each waypoint:
1. Find closest pressure level to cruise altitude for wind analysis
2. Run `analyze_sounding()` per model → store in `analysis.sounding[model_key]`
3. Extract indices for cross-model comparison (8 sounding-derived metrics)
4. After all models: `compute_altitude_advisories()` → `analysis.altitude_advisories`
5. Compute cross-model divergence for all 14 metrics

## Gotchas

- `analyze_sounding()` returns None if <3 pressure levels with valid temperature + dewpoint
- MetPy LFC/EL return None/NaN for stable profiles — all checked
- Pressure levels not guaranteed sorted by API — `prepare_profile()` sorts them
- Pint units must not leak beyond sounding subpackage (causes Pydantic serialization issues)
- `matplotlib.use("agg")` required in skewt.py for worker thread compatibility

## References

- Input models: [data-models.md](./data-models.md)
- Fetch layer: [fetch.md](./fetch.md)
- Output consumers: [digest.md](./digest.md)
- Implementation plan: [sounding_analysis_plan.md](./sounding_analysis_plan.md)
