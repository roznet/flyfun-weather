# Icing Accuracy and Consistency Upgrade (False-Alarm Reduction)

## Summary
1. Fix three confirmed bugs that explain current icing issues: temporal leakage in GRIB enrichment, unsorted two-pass icing zone assembly, and inconsistent cloud gating between icing and advisory cloud logic.
2. Apply a stricter, corroborated NWP fallback policy so icing is not generated from cloud-cover percentages alone when cloud boundaries are missing.
3. Keep external API payloads stable while improving internal correctness, reproducibility, and test coverage.

## Public Interfaces and Type Changes
1. Extend internal enrichment API to be flight-window aware:
   enrich_forecasts(..., departure_time_utc: datetime, flight_duration_hours: float)
   in src/weatherbrief/fetch/grib/__init__.py.
2. Extend internal call chain from src/weatherbrief/tasks/fetch.py to pass departure_time_utc and flight_duration_hours.
3. Add optional internal flag in icing assessment for controlled rollout and debug:
   assess_icing_zones(..., corroborate_nwp_fallback: bool = True)
   in src/weatherbrief/analysis/sounding/icing.py.
4. No breaking schema changes to route_analyses.json, route_advisories.json, or public REST responses.

## Implementation Plan
1. Remove temporal leakage in GRIB enrichment.
   Files: src/weatherbrief/fetch/grib/__init__.py, src/weatherbrief/tasks/fetch.py, src/weatherbrief/fetch/grib/grib_fetch.py.
   Change: fetch and decode all required forecast hours across [departure, departure + duration] (+1h margin), then apply per-hour mapping instead of broadcasting one primary forecast hour to all hourly entries.
2. Fix icing zone assembly ordering bug in two-pass logic.
   File: src/weatherbrief/analysis/sounding/icing.py.
   Change: sort icing_levels by pressure (descending) before grouping; derive zone base and top from min and max altitude (not insertion order), then apply min-thickness expansion.
3. Fix missing high-cloud input in zone enhancement path.
   File: src/weatherbrief/analysis/sounding/icing.py.
   Change: thread nwp_cloud_high_pct through _nwp_cloud_for_zone() and _build_zone() so high-band zones are not artificially under-enhanced.
4. Enforce corroborated fallback policy for no-boundary cloud diagnostics.
   Files: src/weatherbrief/analysis/sounding/icing.py, src/weatherbrief/analysis/sounding/advisories.py.
   Change: when layer cover exists but base and top bounds are missing, require corroboration (DD, RH, near-cloud evidence) before fallback icing is allowed; do not accept cover percentage alone.
5. Unify cloud-at-altitude semantics across modules.
   Files: src/weatherbrief/analysis/sounding/icing.py, src/weatherbrief/analysis/sounding/advisories.py.
   Change: share one helper for cloud present at altitude to eliminate contradictions like clear cloud regime with icing fallback hit.
6. Add deterministic debug trace for investigation workflows.
   File: src/weatherbrief/analysis/sounding/icing.py.
   Change: optional per-level decision trace (source, gate_reason, nwp_cloud_used, corroboration_flags) to support point-level investigation.

## Test Cases and Scenarios
1. Add regression test: interleaved pass1 and pass2 levels must produce correct zone top and base after sorting.
   File: tests/test_icing.py.
2. Add regression test: fallback must not trigger when diagnostics have cover percentage but no boundaries and corroboration fails.
   File: tests/test_icing.py.
3. Add regression test: high-altitude enhancement path uses nwp_cloud_high_pct.
   File: tests/test_icing.py.
4. Add regression test: GRIB enrichment must not broadcast one hour's CLW and cloud diagnostics to all hourly slots.
   File: tests/test_grib.py.
5. Update advisory cloud tests for no-boundary diagnostics behavior under corroboration policy.
   File: tests/test_nwp_cloud_and_ceiling.py.

## Acceptance Criteria
1. No-cloud false positives: dry profile plus no sounding cloud plus no corroboration yields zero icing zones.
2. Zone geometry correctness: base and top are always monotonic and consistent with contributing levels.
3. Temporal correctness: per-hour enriched values differ when source forecast hours differ; no all-hours clone behavior.
4. Operational stability: advisory flicker and contradictory clear plus icing fallback cases are materially reduced on noisy routes.

## Assumptions and Defaults
1. Optimization bias is reduce false alarms.
2. ICON no-boundary policy is require corroboration.
3. Public API payloads remain unchanged.
4. Full test execution requires local dependency bootstrap if sqlalchemy and related test dependencies are missing.
