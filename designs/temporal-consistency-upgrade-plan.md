# Temporal Consistency Upgrade Plan (Interpolation + HMM + Skill Blending)

## Summary
Implement a backend-only upgrade with three coordinated changes:
1. Replace nearest-hour sampling with deterministic time interpolation.
2. Add constrained HMM (Viterbi) smoothing on advisory state sequences to reduce flicker.
3. Add static-prior skill-weighted blending with optional offline calibration.

Chosen defaults:
- Scope: backend core only
- Smoother: constrained HMM Viterbi
- Blending: static priors + optional calibration

## Public Interface / Type Changes
1. No breaking API response changes for `route_analyses.json` or `route_advisories.json`.
2. Add internal method: `WaypointForecast.at_time_interpolated(target: datetime) -> HourlyForecast | None`.
3. Extend internal GRIB enrichment call to include `flight_duration_hours`.
4. Extend model comparison helper to support optional weights for weighted mean.

## Implementation Plan

### 1) Time interpolation core
1. Add `analysis/time_interpolation.py`:
- `bracket_hourlies(hourly_list, target)` -> `(h0, h1, alpha)`
- `interpolate_hourly(h0, h1, alpha)`
- `interpolate_pressure_levels(levels0, levels1, alpha)`
2. Use linear interpolation for continuous fields.
3. Interpolate wind direction via `u/v` vectors, then convert back to degrees.
4. Keep categorical fields (e.g. `weather_code`) nearest-sample.
5. Missing handling: one-side available -> use it; both missing -> `None`.
6. Gap guard: if bracket gap > 3h, fallback to nearest-hour behavior.

### 2) Wire interpolation into analysis paths
1. Keep existing `at_time()` unchanged; add and use `at_time_interpolated()`.
2. Replace `.at_time(...)` with `.at_time_interpolated(...)` in:
- `tasks/analyze.py`
- `tasks/route_weather.py`
- `analysis/airport_conditions.py`
- `api/packs.py` (Skew-T/hodograph point extraction)
3. Keep `RoutePointAnalysis.forecast_hour` as nearest real model hour for compatibility.

### 3) Fix GRIB temporal leakage + align fetch with flight window
1. Pass `route.flight_duration_hours` into GRIB enrichment from fetch stage.
2. In GRIB enrichment:
- Build enrichment window: departure to `departure + flight_duration_hours`
- Compute required forecast hours across the window (+1h margin)
- Fetch/decode required `fhour`s once
- Apply per-hour merge using bracketing `fhour`s and interpolation alpha
3. Apply same logic to cloud diagnostics path.
4. Keep ICON model-level nearest-hour strategy for memory safety; improve single-level handling where feasible.
5. Missing `fhour` fallback: nearest available decoded hour, else no-op.

### 4) HMM smoothing for advisory state flicker
1. Add `analysis/advisories/temporal_hmm.py`:
- States: GREEN/AMBER/RED
- Viterbi in log space
- Transition defaults:
  - G->G/A/R: 0.90/0.09/0.01
  - A->G/A/R: 0.12/0.76/0.12
  - R->G/A/R: 0.02/0.12/0.86
- Emission defaults centered on raw state
- Post-pass `min_run_length=2` for AMBER/RED (except safety overrides)
2. Integrate smoothing into:
- `advisories/icing_escape.py`
- `advisories/fiki_icing.py`
- `advisories/turbulence.py`
- `advisories/model_agreement.py`
3. Hard safety RED triggers remain unsmoothed and dominant.
4. Phase-1 icing-band smoothing applies to route sequence state counts/details (not full 2D vertical reconstruction).

### 5) Skill-weighted model blending (low risk)
1. Add config `configs/model_skill/default.json` with per-variable priors.
2. Add `analysis/blending.py`:
- `blend_scalar`
- `blend_direction` (vector-based)
- `effective_weights`
3. Apply blending in:
- `analysis/comparison.py` for weighted mean
- `tasks/route_weather.py` to replace first-model observation reference with blended reference
4. Add optional offline script: `scripts/calibrate_skill_weights.py`
- Writes `configs/model_skill/calibrated.json`
- Runtime uses calibrated file if present, else default priors

### 6) Rollout safeguards
1. Add feature flags in `pipeline.py` options:
- `time_interpolation_enabled=True`
- `advisory_hmm_enabled=True`
- `skill_blending_enabled=True`
2. Add debug logging:
- raw vs smoothed flip counts
- interpolation fallbacks
- blend weights used
3. Keep advisory aggregation behavior (`worst` / `majority`) unchanged.

## Tests and Acceptance Criteria

### Unit/Integration tests
1. `tests/test_models.py`:
- midpoint interpolation
- unequal gaps
- missing-side behavior
- wind wrap-around (350 deg vs 10 deg)
- pressure-level interpolation
2. `tests/test_grib.py`:
- verify no single-`fhour` broadcast
- verify per-hour temporal interpolation behavior + fallback
3. Add `tests/analysis/advisories/test_temporal_hmm.py`:
- suppress isolated spikes
- preserve sustained transitions
- preserve hard-RED overrides
4. `tests/analysis/advisories/test_evaluators.py`:
- verify smoothed affected counts and more stable aggregate states
5. `tests/test_route_weather.py`:
- verify blended reference is used for obs-model comparison

### Acceptance gates
1. Advisory state flips along route reduced by >= 30% on noisy fixtures.
2. No missed hard-RED cases vs baseline.
3. Runtime overhead increase < 10% on standard route pack.

## Assumptions and Defaults
1. No frontend/schema changes in this phase.
2. HMM smoothing only for selected evaluators listed above.
3. ICON model-level interpolation remains memory-constrained in phase 1.
4. Static priors are default; calibrated weights are optional.
5. If weights/config are missing, system falls back deterministically to current behavior.
