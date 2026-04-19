# Time-Shift Table

> Evaluate advisory severity at multiple candidate departure times across the day (sunrise–sunset), helping pilots pick the best time window — analogous to the altitude table but varying time instead of altitude.

## Motivation

The altitude table already answers "what altitude is safest?" by sweeping advisories across an altitude range. Pilots ask the same question about departure time: would leaving 3 hours earlier or later avoid the worst weather? Today the system supports a single alternative departure time (`alt_departure_time`), but this requires the pilot to guess which time to try. A systematic sweep across the day — every 3h slot between sunrise and sunset — would surface the optimal window automatically.

## Existing Infrastructure

The architecture is well-suited for this. Key pieces already in place:

| Component | Status | Notes |
|-----------|--------|-------|
| Open-Meteo hourly data | Full day fetched | `start_date`/`end_date` cover 24h+ — all sunrise-to-sunset hours available |
| `analyze_all_route_points()` | Works at any departure time | Calls `at_time(interp_time)` to pick the right hourly data |
| `evaluate_all()` / `RouteContext` | Stateless, re-runnable | Frozen dataclass, pure evaluation |
| `run_alt_from_pack()` | Single alt time | Already does load → re-analyze → evaluate for one time |
| `compute_altitude_table()` | Pattern to follow | Loops altitudes, builds result rows — time table mirrors this |

## Design

### Candidate Time Generation

Generate candidate departure times at 3h intervals between sunrise and sunset, constrained to the fetched date window:

1. Compute sunrise/sunset for the route midpoint on the target date
2. Round sunrise up and sunset down to nearest hour
3. Generate slots: every 3h from rounded-sunrise to rounded-sunset
4. Always include the actual departure time (even if it doesn't fall on a 3h boundary)
5. Discard any candidate outside the Open-Meteo `start_date`/`end_date` window

**Sunrise/sunset source:** Open-Meteo's daily parameters include `sunrise`/`sunset` — can be added to the existing API call at negligible cost. Alternatively the `astral` Python library computes sun times from lat/lon without an API call.

Example for a mid-June European flight (EGTK → LSGS):
- Sunrise ~04:50 UTC, sunset ~20:10 UTC
- At 1h step: 05:00, 06:00, ..., 09:00 (actual), ..., 20:00 — ~16 candidates
- At 3h step: 05:00, 08:00, 09:00 (actual), 11:00, 14:00, 17:00, 20:00 — ~7 candidates

### Result Model

Mirrors `AltitudeTableResult`:

```
TimeShiftTableResult:
  rows: list[TimeShiftRow]
  advisory_ids: list[str]
  advisory_names: dict[str, str]
  departure_time: datetime        # actual planned departure
  sunrise: datetime
  sunset: datetime
  approximate: bool               # True until GRIB-enriched version ready

TimeShiftRow:
  departure_time: datetime
  statuses: dict[str, AdvisoryStatus]  # advisory_id → status
  red_count: int
  amber_count: int
  green_count: int
  is_planned: bool                # marks the actual departure time
```

### Computation

```
compute_time_table(cross_sections, route_points, elevation, route, candidates, ...):
    for each candidate departure_time:
        analyses = analyze_all_route_points(cross_sections, route_points,
                                           departure_time=candidate, ...)
        ctx = RouteContext(analyses, cross_sections, elevation, ...)
        results = evaluate_all(ctx, ...)
        rows.append(summarize(results))
    return TimeShiftTableResult(rows, best_time=..., ...)
```

### Computation Cost — Unlike the Altitude Table

The altitude table runs `analyze_all_route_points()` **once**, then loops `evaluate_all()` (cheap — just iterates existing results) at each altitude. The time table is different: it must re-run `analyze_all_route_points()` **per candidate time**, because different departure times select different hourly forecasts via `at_time()`. Each call runs MetPy sounding analysis (~30 points × ~6 models = ~180 sounding analyses).

Since the data is already fetched, the cost is purely CPU — no I/O. The dominant cost is `analyze_sounding()` (MetPy parcel lifting, cloud/icing detection, thermodynamic indices). Rough estimates:

| Step | Candidates | `analyze_all_route_points` calls | Estimated wall time |
|------|------------|----------------------------------|---------------------|
| 3h | ~7 | 7 | ~7–14s |
| 1h | ~16 | 16 | ~16–32s |

Since the fetch cost is identical either way (Open-Meteo has every hour, GRIB window covers sunrise-sunset once), **1h steps cost nothing extra in I/O** — only more CPU. Whether 1h or 3h is the right default depends on acceptable computation time. 1h gives much better resolution for convective timing (thermals build over ~2h windows), but doubles the compute. A reasonable approach: default to 1h, but if this proves too slow on production, make it configurable or fall back to 2h.

### Which Advisories Are Time-Dependent?

All of them, effectively — weather changes through the day. Unlike the altitude table (which only evaluates altitude-dependent advisories), the time table should evaluate **all enabled advisories** since icing, convection, cloud, turbulence, and airport conditions all vary with time of day. Convective advisories in particular are strongly time-dependent (afternoon heating).

## GRIB Enrichment Strategy

### The Problem

GRIB enrichment currently covers only the flight window (departure → arrival). Shifting departure by ±6h means those hours lack GRIB data, causing:

- **Cloud diagnostics** (`nwp_cloud_diagnostics`) fall back to bulk NWP percentages → false icing alerts, inflated SFIP scores
- **CLWMR/ICMR** fall back to Ogimet-DD method → acceptable degradation
- **Cloud cover overrides** fall back to Open-Meteo values → may be from a stale model run

### Options Considered

| Option | GRIB Coverage | Extra Fetch Cost | Accuracy |
|--------|--------------|------------------|----------|
| **A — Expand all models** | GFS + ICON-EU + ECMWF for sunrise–sunset | +2–4 min (ICON-EU: ~2880 bz2 files for 12 extra hours) | Full |
| **B — Open-Meteo only** | No extra GRIB | Zero | Approximate — acceptable for relative comparison |
| **C — GFS + ECMWF only** | GFS + ECMWF expanded, ICON-EU at flight window only | +30–60s | Good — cloud diagnostics from GFS/ECMWF cover most models |

### Recommended: Option C (GFS + ECMWF expanded)

**GFS:** Each additional hour = 1 byte-range HTTP request (~1–3 MB). Expanding from ~5h to ~16h ≈ +11 requests, +15–35 MB, +30–60s. Cheap.

**ECMWF:** Files already on disk (ECPDS delivery). Cost is decode-only — cfgrib + xarray interpolation per step file. No download at all. Moderately cheap.

**ICON-EU:** Each additional hour = ~240 individually bz2-compressed files from DWD. Expanding by 12h = ~2880 extra files. This is the expensive one. Keep at flight-window only.

For the ICON-EU cross-sections at off-window hours, the analysis falls back to Open-Meteo data — same as it does today when GRIB enrichment is disabled. GFS and ECMWF cloud diagnostics cover the other model cross-sections.

### Expanding the GRIB Window

Currently `enrich_forecasts()` receives `flight_duration_hours` and derives the window. Two approaches:

1. **New parameter:** `enrichment_window: tuple[datetime, datetime] | None` — when set, overrides the departure-based window for GFS and ECMWF. ICON-EU ignores it and uses the original flight window.

2. **Wider duration:** Pass an inflated `flight_duration_hours` that covers sunrise-to-sunset. Simpler but less explicit.

Option 1 is cleaner — makes the intent explicit and allows per-model window decisions.

## Progressive Pipeline (Two-Phase)

Rather than blocking the briefing on expanded GRIB fetch, split into immediate and background phases:

### Phase 1 — Immediate (in current pipeline, ~1s)

After the main briefing completes:

1. Compute sunrise/sunset for route midpoint
2. Run `compute_time_table()` using existing cross-sections (Open-Meteo data only for off-window hours, GRIB-enriched for the actual flight window)
3. Save as `time_table.json` with `approximate: true`
4. Pilot sees the time comparison immediately

### Phase 2 — Background (async, +30–60s)

Triggered after Phase 1 completes:

1. Expand GFS + ECMWF GRIB enrichment to sunrise–sunset window
2. Re-run `compute_time_table()` with enriched data
3. Overwrite `time_table.json` with `approximate: false`
4. Frontend polls or receives notification, updates display

This mirrors how the system already handles Skew-T (on-demand, not pre-generated) and keeps briefing latency unchanged.

### Background Task Options

- **In-process async:** `asyncio.create_task()` in the API handler after returning the briefing. Simple but ties to the request lifecycle.
- **Task queue:** If a task system exists, enqueue the enrichment job. More robust for long-running work.
- **Lazy on-demand:** Don't run Phase 2 until the pilot opens the time table in the UI. Saves work if they never look at it. But adds latency when they do.

## Constraints and Edge Cases

### Same-day validity

The time table must constrain candidates to the fetched Open-Meteo date window. For a flight fetched with `start_date=2026-06-15, end_date=2026-06-15`, candidates must fall on June 15. Shifting to June 14 or 16 would need different forecast data.

### Cross-midnight flights

If sunset extends past midnight (unlikely in aviation VFR context, but possible for IFR), or if `end_date` spans two days: candidates should still be bounded by sunrise/sunset of the departure day.

### Model run consistency

All candidate times are evaluated against the same model run. This is correct: the question is "given today's forecast, what time is best?" — not "what would a different model run say?" Reusing the same cross-sections ensures a fair comparison.

### Airport conditions

Airport conditions (wind, ceiling, visibility) vary by time. The `airport_conditions_recompute` callback used by `run_alt_from_pack` handles this — each candidate time gets fresh airport conditions from the time-shifted analysis.

### Duration consistency

All candidates use the same `flight_duration_hours`. The question is "same flight, different start time" — not "different flight." The interpolated times shift proportionally.

## Frontend

### Display

A table similar to the altitude table:

| Departure | Icing | Cloud | Turbulence | Convective | Airport | Feasibility |
|-----------|-------|-------|------------|------------|---------|-------------|
| 05:00 | 🟢 | 🟡 | 🟢 | 🟢 | 🟢 | 🟢 |
| **08:00** ← planned | 🟡 | 🟡 | 🟢 | 🟢 | 🟡 | 🟡 |
| 11:00 | 🟡 | 🟢 | 🟢 | 🟡 | 🟢 | 🟡 |
| 14:00 | 🟢 | 🟢 | 🟡 | 🔴 | 🟢 | 🔴 |
| 17:00 | 🟢 | 🟡 | 🟢 | 🟡 | 🟢 | 🟡 |

- Planned departure row highlighted
- Best time row indicated (fewest reds, then fewest ambers)
- `approximate` badge shown until Phase 2 completes
- Clicking a row could set it as the alt departure time

### Integration with existing alt departure

The time table subsumes the single `alt_departure_time` feature. Options:
- Keep `alt_departure_time` as a separate concept (pilot's explicit choice) — time table is informational
- Let clicking a time-table row set `alt_departure_time` and trigger the full alt advisory evaluation
- Eventually deprecate the single alt field in favor of the table

## Open Questions

1. **Step size:** Since data fetch is the same regardless of step, the only cost of 1h vs 3h is CPU (~16 vs ~7 `analyze_all_route_points` calls). 1h gives much better convective timing resolution. Default to 1h; fall back to 2h or 3h if production compute is too slow. Worth benchmarking one `analyze_all_route_points` call to decide.

2. **Sunrise/sunset source:** Open-Meteo daily API vs `astral` library. Open-Meteo requires no new dependency but needs an extra API parameter. `astral` is pure Python, no API call, and handles edge cases (polar regions).

3. **Which route point for sun times?** Route midpoint is a reasonable choice. For very long routes spanning significant longitude, departure and arrival could have notably different sunrise/sunset — but 3h steps are coarse enough that this doesn't matter much.

4. **Phase 2 trigger:** Automatic after briefing, or on-demand when pilot opens the time table? On-demand saves compute but adds latency.

5. **iOS app:** The companion app would need to render the time table. The data model is simple (mirrors altitude table), but it's additional UI work. Could be deferred to a later iOS release.

## Implementation Sequence

1. **Data model:** `TimeShiftTableResult`, `TimeShiftRow` in `models/`
2. **Sun times:** Add sunrise/sunset computation (choose source)
3. **Core computation:** `compute_time_table()` in `analysis/advisories/time_table.py` (mirrors `altitude_table.py`)
4. **Task wrapper:** `run_time_table_from_pack()` in `tasks/advise.py` (mirrors `run_altitude_table_from_pack`)
5. **Phase 1 integration:** Call from `execute_briefing()` after advisories complete
6. **API endpoint:** `GET .../packs/{ts}/time-table`
7. **Frontend:** Time table component in the advisory dashboard
8. **Phase 2 (GRIB expansion):** Modify `enrich_forecasts()` to accept wider window for GFS + ECMWF
9. **Background runner:** Async Phase 2 with `approximate` flag update
