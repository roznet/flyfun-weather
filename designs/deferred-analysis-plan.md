# Deferred Analysis & Background Refresh

## Problem

The current refresh pipeline runs **fetch + analysis + digest** synchronously. At 10nm spacing (~48 route points), analyzing all models takes ~12-15 seconds, which the user must wait through during an interactive refresh. Meanwhile, the planned background refresh process should pre-build everything so users get instant results.

## Current Pipeline

```
User clicks Refresh
  -> Fetch raw data from Open-Meteo (3 API calls, ~3s)
  -> Analyze ALL route points x ALL models (~12s at 10nm)
  -> Run LLM digest (~5-10s)
  -> Save everything
  -> Return to user (~20-25s total)
```

**What gets saved today:**
- `cross_section.json` — raw Open-Meteo response (all points, all models, all hours) ~6-12MB
- `route_analyses.json` — pre-computed analysis for all points ~400-800KB
- `snapshot.json` — waypoint forecasts + analyses ~850KB
- `digest.json` / `digest.md` — LLM-generated briefing

Key insight: **raw forecast data is already preserved** in `cross_section.json`. Analysis can be re-run from it at any time.

## Design

Two distinct refresh modes with different analysis strategies:

### Mode 1: Interactive Refresh (user-triggered)

Goal: minimize perceived wait time. The user sees results progressively as they become available.

```
User clicks Refresh
  -> Fetch raw data (3 API calls, ~3s)
  -> Save cross_section.json immediately
  -> Analyze waypoints only (3 pts x 3 models, ~1s)
  -> Save snapshot.json + start streaming results to UI
  -> Analyze route points PER MODEL as needed:
       - Selected model first (e.g., ECMWF, ~4s for 48 pts)
       - Cache to route_analyses_{model}.json
       - Other models analyzed on-demand when user switches
  -> LLM digest runs in parallel (uses waypoint analysis only)
```

**User experience:**
1. Click Refresh → 4-5s → briefing loads with waypoint data + selected model cross-section
2. Switch to GFS → 4s analysis → cross-section updates
3. Switch to ICON → 4s analysis → cross-section updates (ECMWF cached from step 1)

**Progressive streaming (existing SSE infrastructure):**
- Stage `fetching` → `analyzing_waypoints` → `analyzing_route:ecmwf` → `digest` → `complete`
- The UI already handles `refreshStage` + `refreshDetail` in the store

### Mode 2: Background Refresh (automated process)

Goal: everything pre-built, instant load when user opens briefing. Cost doesn't matter.

```
Background scheduler (cron / worker process)
  -> Check model freshness for all active flights
  -> For each stale flight:
       -> Fetch raw data (all models)
       -> Analyze ALL route points x ALL models (full ~12s, no rush)
       -> Run LLM digest (with change detection — see below)
       -> Save everything pre-built
       -> Notify user (push notification / email / in-app badge)
```

**Pre-build everything:**
- `cross_section.json` — raw data
- `route_analyses.json` — all models, all points, fully analyzed
- `snapshot.json` — waypoint data + analysis
- `digest.json` — LLM briefing (only if input changed)
- Skew-T PNGs for waypoints (pre-render)

When the user opens the briefing after notification, everything is cached and loads instantly.

## Implementation Steps

### Phase 1: Per-Model Route Analysis (enables deferred analysis)

Split `route_analyses.json` into per-model files:

```
pack_dir/
  route_analyses_ecmwf.json   # Only ECMWF analysis for all route points
  route_analyses_gfs.json     # Only GFS analysis
  route_analyses_icon.json    # Only ICON analysis
  route_analyses.json         # Combined manifest (lazy-merge on read)
```

**API changes:**
- `GET /packs/{ts}/route-analyses?model=ecmwf` — return single model (fast)
- `GET /packs/{ts}/route-analyses` — merge all available models (backwards compatible)

**Pipeline changes:**
- `analyze_route_for_model(cross_section_data, model, route_points)` — new function
- Interactive refresh calls this for the selected model first
- Background refresh calls it for all models

### Phase 2: On-Demand Analysis Endpoint

New API endpoint for interactive mode:

```
POST /packs/{ts}/analyze-route/{model}
```

- Loads raw data from `cross_section.json`
- Runs sounding analysis for the requested model only
- Saves result to `route_analyses_{model}.json`
- Returns the analysis (or 200 if already cached)

**Frontend changes:**
- When switching models in the cross-section, check if route analysis exists for that model
- If not, call the analyze endpoint and show a brief loading indicator
- Cache in the store once loaded

### Phase 3: LLM Digest Change Detection

Before calling the LLM, hash the assembled context string:

```python
import hashlib

context = build_digest_context(snapshot, dwd_text, previous_digest)
context_hash = hashlib.sha256(context.encode()).hexdigest()

# Check if previous digest used the same input
prev_hash = load_previous_context_hash(pack_dir)
if context_hash == prev_hash:
    logger.info("Digest input unchanged, reusing previous digest")
    return load_previous_digest(pack_dir)

# Otherwise run LLM
digest = run_digest(context, ...)
save_context_hash(pack_dir, context_hash)
```

This avoids expensive LLM calls when only model init times changed but the actual data at the target time is identical (common for distant forecast dates).

### Phase 4: Background Refresh Worker

Separate process (not in the web server):

```python
# background_refresh.py
async def refresh_loop():
    while True:
        flights = get_active_flights()  # flights with target_date in next 3 days
        for flight in flights:
            freshness = check_freshness(flight)
            if not freshness.fresh:
                await execute_briefing(
                    flight.route,
                    flight.target_date,
                    options=BriefingOptions(
                        generate_llm_digest=True,
                        generate_skewt=True,  # pre-render everything
                        mode="background",     # full analysis, no shortcuts
                    ),
                )
                notify_user(flight.user_id, flight.id)
        await asyncio.sleep(1800)  # Check every 30 minutes
```

**Notification options:**
- In-app: badge on flight card ("Updated 5m ago"), freshness bar shows "New data available"
- Email: optional per-user preference (already have email infrastructure)
- Push: future consideration (requires service worker)

## Data Flow Summary

```
                    Interactive Refresh          Background Refresh
                    ──────────────────          ──────────────────
Fetch raw data      Yes (3 API calls)           Yes (3 API calls)
Save raw data       Immediately                 Immediately
Waypoint analysis   Yes (~1s)                   Yes (~1s)
Route analysis      Selected model only (~4s)   All models (~12s)
Other models        On-demand when viewed        Pre-computed
LLM digest          Parallel, with hash check   Full, with hash check
Skew-T generation   On-demand (existing)        Pre-rendered
User wait time      ~5s initial                 0s (pre-built)
```

## Migration

- No breaking changes — `route_analyses.json` format stays the same
- Per-model files are an addition, not a replacement
- The combined endpoint merges per-model files transparently
- Old packs without per-model files continue to work (read from combined file)

## Dependencies

- Existing SSE streaming infrastructure (for progressive loading feedback)
- Existing freshness check system (for background refresh triggering)
- User notification system (for background refresh alerts) — needs design
