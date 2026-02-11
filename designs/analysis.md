# Analysis Layer

> Aviation-specific analysis: wind, icing, clouds, model comparison

All modules in `src/weatherbrief/analysis/`. Pure computation — no I/O.

## Intent

Transform raw NWP data into aviation-relevant assessments. Each module is independent, testable, and stateless.

## Wind Components (`analysis/wind.py`)

Decompose wind vector relative to flight track.

```python
wc = compute_wind_components(wind_speed_kt=25, wind_direction_deg=290, track_deg=135)
wc.headwind_kt   # positive = headwind, negative = tailwind
wc.crosswind_kt  # positive = from right, negative = from left
```

- Uses `cos(relative_wind)` for head/tail, `sin(relative_wind)` for crosswind
- Track per waypoint comes from `RouteConfig.waypoint_track()` (circular mean of leg bearings)

## Icing Assessment (`analysis/icing.py`)

Assess icing risk at each pressure level using temperature + relative humidity.

```python
bands = assess_icing_profile(hourly.pressure_levels)
# Returns list[IcingBand] with risk per level
```

**Icing logic:**
- Outside [0, -20°C] or RH < 60%: **NONE**
- T ∈ [-10, 0°C], RH > 90%: **SEVERE** (SLD risk zone)
- T ∈ [-10, 0°C], RH > 80%: **MODERATE**
- T ∈ [-20, -10°C), RH > 90%: **MODERATE**
- T ∈ [-20, -10°C), RH > 80%: **LIGHT**
- Otherwise in band: **LIGHT**

Converts geopotential height (m) to feet if available.

## Cloud Estimation (`analysis/clouds.py`)

Estimate cloud layers from RH profile at pressure levels.

```python
layers = estimate_cloud_layers(hourly.pressure_levels)
# Returns list[CloudLayer] with base_ft, top_ft
```

- **Threshold**: RH ≥ 80% = "in cloud"
- State machine tracks cloud entry/exit across pressure levels
- Top is `None` (noted "top unknown") if still in cloud at highest level
- **Coarse resolution** — pressure level spacing (~1500m) means estimates are approximate. Skew-T LCL is better for convective bases.

## Model Comparison (`analysis/comparison.py`)

Score agreement across 2+ models for a given variable.

```python
div = compare_models("temperature_c", {"gfs": 5.0, "ecmwf": 6.0, "icon": 5.5})
div.agreement  # → AgreementLevel.GOOD
div.spread     # → 1.0
```

**Thresholds** (good, poor):

| Variable | Good ≤ | Poor > |
|----------|--------|--------|
| temperature_c | 2.0 | 5.0 |
| wind_speed_kt | 5.0 | 15.0 |
| wind_direction_deg | 20° | 60° |
| cloud_cover_pct | 15.0 | 40.0 |
| precipitation_mm | 1.0 | 5.0 |
| freezing_level_m | 300.0 | 800.0 |

**Circular statistics** for `wind_direction_deg` — uses sin/cos sum for mean, max angular difference for spread. Prevents averaging 350° and 10° as 180°.

## Pipeline Integration

In `pipeline._analyze_waypoint()`, for each waypoint:
1. Find closest pressure level to cruise altitude for wind analysis
2. Run all four analyses per model
3. Collect per-model values, then compute cross-model divergence

Results stored in `WaypointAnalysis` keyed by model name string.

## Gotchas

- Analysis requires at least one model with data at the target time — `pipeline._analyze_waypoint` raises `ValueError` if no forecasts
- Missing pressure level data silently skipped (None checks throughout)
- Comparison needs ≥ 2 models — single-model fetch produces no `ModelDivergence`

## References

- Input models: [data-models.md](./data-models.md)
- Fetch layer: [fetch.md](./fetch.md)
- Output consumers: [digest.md](./digest.md)
