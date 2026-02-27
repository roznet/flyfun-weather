# Time & Spatial Alignment Audit

> Audit findings for time/location mapping when merging GRIB and Open-Meteo data, and looking up data along routes.

## Context

Full audit of the data pipeline from route interpolation through Open-Meteo fetch, GRIB enrichment, and route-point analysis. Goal: verify correctness of time and spatial mapping at every stage.

## Findings: What's Correct

### Spatial mapping chain — all correct

| Step | Logic | Verified |
|------|-------|----------|
| Route interpolation | `walk_route()` with haversine great-circle via `NavPoint` | Correct |
| Open-Meteo multi-point | Comma-separated lat/lon; response order matches input | Correct, tested |
| Cross-section storage | `point_forecasts[i]` ↔ `route_points[i]` by array index | Correct |
| GFS GRIB spatial interp | xarray bilinear `interp()` with `lon % 360` (GFS 0-360°) | Correct |
| ICON-EU GRIB spatial interp | xarray bilinear `interp()`, no lon conversion (-180/180 match) | Correct |
| GRIB merge ordering | `decoded_points[point_idx]` ↔ `cs.point_forecasts[point_idx]` — same `route_points` list | Correct |
| ICON-EU domain check | `route_in_icon_eu_domain()` validates before fetch | Correct |
| GFS pressure levels | Extracted from existing Open-Meteo data, so exact match guaranteed | Correct |
| ICON-EU log-pressure interp | Ascending sort, NaN filter, no extrapolation, clamp ≥0 | Correct |

### Route-point time interpolation — correct

`interpolated_time = departure + (distance / total_distance) × duration` — linear time mapping based on constant ground speed. `at_time()` picks closest hourly forecast (≤30 min error). Standard for aviation NWP.

### GFS/ICON-EU priority — correct

GFS enriches first; ICON-EU checks `hourly.nwp_cloud_diagnostics is None` before writing, preventing contradictory overrides.

---

## Issue A: GRIB Data Applied to All Hours (CONFIRMED PROBLEM)

### The bug

**Location:** `fetch/grib/__init__.py`, `_merge_cloud_water_into_sections()` line 629 and `_enrich_cloud_diagnostics()` line 359.

GRIB enrichment fetches data for **one** forecast hour (the one closest to `target_hour`, typically the departure time). It then applies the same CLWMR, ICMR, and cloud diagnostics to **all 24 hourly entries** in each `WaypointForecast`.

```python
# __init__.py:629 — applies same decoded_points to ALL hours
for hourly in wf.hourly:          # ← iterates all 24 hours
    for pl in hourly.pressure_levels:
        level_data = point_data.get(pl.pressure_hpa)  # ← same data regardless of hour
```

### Why it causes wrong icing predictions

For a 3-hour flight departing 09:00 UTC, a route point near the destination is analyzed at ~12:00 UTC. The analysis combines:

| Variable | Source | Time | Correct? |
|----------|--------|------|----------|
| Temperature | Open-Meteo | 12:00 UTC | Yes |
| Humidity/RH | Open-Meteo | 12:00 UTC | Yes |
| Wind | Open-Meteo | 12:00 UTC | Yes |
| **CLWMR (cloud liquid water)** | **GRIB** | **09:00 UTC** | **No — 3h stale** |
| **ICMR (ice mixing ratio)** | **GRIB** | **09:00 UTC** | **No — 3h stale** |
| **Cloud cover (low/mid/high)** | **GRIB override** | **09:00 UTC** | **No — overrides correct Open-Meteo 12:00 values** |

**Consequences:**
- **Icing zones don't match cloud areas:** Cloud cover at 12:00 (from overridden GRIB 09:00 snapshot) doesn't align with temperature/dewpoint depression at 12:00 (from Open-Meteo). Clouds may have moved, formed, or dissipated.
- **Conservative icing predictions:** CLWMR from 09:00 persists in areas where clouds have already cleared by 12:00, triggering icing where none exists.
- **Missed icing:** Conversely, new cloud formations at 12:00 won't have CLWMR data (it shows zero from the 09:00 snapshot).
- **Cloud diagnostics frozen at departure time:** The `_apply_cloud_diagnostics()` override replaces Open-Meteo's time-varying cloud cover with a static GRIB snapshot, hiding the actual temporal evolution.

### Proposed fix

Fetch GRIB data for **multiple forecast hours** covering the flight window, and merge each forecast hour's data only into the **matching hourly entry**.

**Option 1: Per-hour GRIB fetch + merge (preferred)**
1. Compute the flight time window: `[target_hour, target_hour + ceil(flight_duration_hours)]`
2. For each forecast hour in the window, fetch GRIB data (GFS hourly data is available for f000–f120)
3. In `_merge_cloud_water_into_sections()`, match `hourly.time` to the closest GRIB forecast hour
4. Same for `_apply_cloud_diagnostics()` — apply per-hour diagnostics

**Option 2: Bracket + interpolate**
1. Fetch GRIB for the two hours bracketing `target_hour` (already done) AND the two hours bracketing `target_hour + duration`
2. Linearly interpolate CLWMR/ICMR between the two nearest GRIB hours for each hourly entry

Option 1 is simpler and more correct (no interpolation artifacts for discrete cloud fields). The extra GRIB fetches are cheap — GFS .idx byte-range downloads are ~50 KB per hour, and we're adding at most 4-5 extra hours.

**Memory note for ICON-EU:** The current code fetches only one forecast hour to limit memory (~800 MB per decoded hour). For ICON-EU, we may need to decode one hour at a time and merge immediately before loading the next, rather than holding all hours in memory simultaneously.

---

## Issue B: Naive vs Aware Datetimes (INCONSISTENCY)

### The problem

**Open-Meteo layer** (`fetch/open_meteo.py:299`):
```python
time=datetime.fromisoformat(timestamp)  # → naive datetime (no tzinfo)
```

**Analysis layer** (`tasks/analyze.py:345`):
```python
target_dt = datetime(*map(int, target_date.split("-")), target_hour)  # → naive
```

**GRIB layer** (`fetch/grib/__init__.py:137-139`):
```python
target_time = datetime.strptime(...).replace(tzinfo=timezone.utc)  # → aware (UTC)
```

The Open-Meteo timestamps are naive. The GRIB timestamps are aware. The analysis times are naive. These never get directly compared today (GRIB uses its datetimes only for forecast hour bracketing), but it's fragile:

- `at_time()` compares `h.time - target` — if one is naive and the other aware, Python raises `TypeError`
- The convention "all naive datetimes mean UTC" is undocumented and easy to violate

### Proposed fix

Standardize on **timezone-aware UTC datetimes** throughout:

1. `open_meteo.py:299`: `time=datetime.fromisoformat(timestamp).replace(tzinfo=timezone.utc)`
2. `analyze.py:345`: `target_dt = datetime(..., target_hour, tzinfo=timezone.utc)`
3. `compute_interpolated_time()`: already returns whatever type `departure` is, so it propagates
4. `at_time()`: no change needed (comparison works if both sides are aware)
5. Update `HourlyForecast.time` model field to document it's always UTC-aware
6. Audit serialization — Pydantic v2 serializes aware datetimes with `+00:00` suffix; verify downstream consumers (frontend, snapshot loading) handle this

---

## Other Minor Findings (No Action Needed)

### `forecast_hour` takes last model's value (cosmetic)
`analyze.py:292` sets `forecast_hour = hourly.time` inside a loop over models. Stores whichever model was processed last. Not used in computation. Comment says "they should agree."

### GFS has no domain bounds check
Not needed — GFS is global (0.25°). xarray returns NaN for out-of-bounds, gracefully skipped.

### Silent GRIB decode failures
Empty `decoded_points` arrays are skipped silently. Logging exists at aggregate level (`"GRIB2 enrichment: %d pressure levels enriched"`). Acceptable.

## References

- GRIB enrichment entry point: `src/weatherbrief/fetch/grib/__init__.py`
- Open-Meteo client: `src/weatherbrief/fetch/open_meteo.py`
- Route-point analysis: `src/weatherbrief/tasks/analyze.py`
- Cross-section fetch: `src/weatherbrief/tasks/fetch.py`
- GRIB decode/interpolation: `src/weatherbrief/fetch/grib/decode.py`
- ICON-EU level interpolation: `src/weatherbrief/fetch/grib/icon_eu_levels.py`
- Data models: `src/weatherbrief/models/analysis.py`
