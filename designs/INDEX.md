# Flyfun Weather

> Medium-range (D-7 to D-0) aviation weather assessment for cross-country GA flights

Install: `pip install -e ".[dev]"` (local development)

Modules are grouped by area below; the headings are presentational only — discovery
matches on each entry's description and doc link regardless of grouping.

## Core pipeline & data

### architecture
System overview: pipeline, API, web app, storage layout (briefing.json + forecasts.json split), dependencies, phase roadmap.
Key exports: `execute_briefing`, `create_app`, `run_fetch`, `run_analysis`, `run_advisories`
→ Full doc: architecture.md

### data-models
Pydantic v2 models for routes, forecasts, analysis, snapshots, cross-sections, elevation, flights, and briefing packs. Snapshots stored as split files: `briefing.json` (analyses/observations/metadata) + `forecasts.json` (raw forecasts).
Key exports: `ForecastSnapshot`, `RouteConfig`, `RoutePoint`, `RouteCrossSection`, `WaypointForecast`, `SoundingAnalysis`, `VerticalMotionAssessment`, `RoutePointAnalysis`, `ElevationProfile`, `InversionLayer`, `Flight`, `FlightProfile`, `BriefingPackMeta`, `RouteAlternates`, `RouteSunAnalysis`
→ Full doc: data-models.md

### fetch
Weather data retrieval: Open-Meteo multi-point client, route interpolation, route-aware text forecasts (NWS AFD US / DWD Europe), SRTM elevation, model freshness, and GRIB2 enrichment (GFS/HRRR + ICON-EU/D2 + ECMWF IFS) with two-phase sequential decode for memory safety.
Key exports: `OpenMeteoClient`, `interpolate_route`, `fetch_text_forecasts`, `get_elevation_profile`, `enrich_forecasts`
→ Full doc: fetch.md

### weather-engine-specs
GRIB2 enrichment engine and data-source registry: GFS S3 (CLWMR/ICMR + cloud diagnostics patch), HRRR S3 (full-sounding replacement of the gfs slot on CONUS, Lambert grid), ICON-EU/ICON-D2 DWD (model-level sounding replacement, D2 explicit-convection fields, cache-warming economics), ECMWF IFS via ECPDS (pressure-level replacement + a1 surface fields), plus per-model bucket paths, variable reference tables, gap-filling strategy and implementation gotchas.
Key exports: `plan_byte_ranges`, `find_best_ecmwf_run`, `IconVariant`, `build_hrrr_cloud_diagnostics`, `build_ecmwf_surface_snapshot`, `propagate_all`, `purge_old_runs`, `precache_icon_d2_flights`
→ Full doc: weather-engine-specs.md

### freshness-markers
Marker-based per-(model, source) staleness decision for `/packs/freshness` + auto-refresh: an in-memory `MarkerStore` (5-min refresh loop) feeds a pure-compute, horizon-aware lookup in the HTTP path. Admin endpoint surfaces observed delivery delays vs. registry expectation.
Key exports: `SOURCE_REGISTRY`, `MarkerStore`, `get_store`, `check_source`, `run_freshness_loop`, `_build_data_status`, `gated_data_status`, `decide_refresh`, `catalog.build`
→ Full doc: freshness-markers.md

### time-alignment
Time and spatial alignment in the data pipeline: aware-UTC datetime convention, the `TZDateTime` column type that centralises it (aware-UTC in Python, naive-UTC in the DB, naive writes rejected — read before adding a datetime column), per-hour GRIB enrichment across flight windows, spatial index consistency, hour-matching merge logic, old pack backward compatibility.
Key exports: `TZDateTime`, `compute_flight_window_hours`, `compute_icon_eu_flight_window_hours`, `_forecast_hour_to_utc`, `_merge_cloud_water_into_sections`
→ Full doc: time-alignment-audit.md

## Analysis & meteorology

### analysis
Aviation-specific analysis: wind components, MetPy sounding analysis (thermodynamics, DD/NWP cloud methods, four icing methods, inversions, convective, vertical motion/CAT), altitude advisories, model divergence scoring. Icing gated by `is_in_cloud_layer()`; icing/cloud methods user-selectable. Per-subsystem deep-dive audits (clouds, convective, icing, cross-cutting) and an explicit meteorological-decisions log (reasoning + rejected options) are linked from the doc.
Key exports: `compute_wind_components`, `analyze_sounding`, `compute_altitude_advisories`, `compare_models`, `assess_vertical_motion`, `detect_inversions`
→ Full doc: analysis.md

### meteorology-decisions
Dated log of explicit meteorological design decisions, each with context, reasoning, and rejected alternatives: ceiling DD-vs-NWP-adjusted, Ogimet icing-zone width / convective contribution at moderate CAPE, GFS cloud-diagnostics window-midpoint interp + RH/condensate gate, convective realizable-CAPE / regime discrimination / DD-stays-pure, NWP-cover vs CAPE-driven risk, ICON-D2 explicit-convection firing table (reflectivity × corroborators, §19), reconstructed-geopotential datum + virtual-temperature thicknesses (§20), freezing level is MSL everywhere with ECMWF `deg0l` normalized at decode (§21), Richardson CAT tiers scaled by each layer's own thickness Δz — not by altitude (§28, superseding §25(a)'s ramp) — + mixed-layer suppression + BL-severe through the route-percentage gate (§25), one route-extent convention — distance-based percentage, domain-scoped denominator, per-tier extent (§27). Read before changing any weather calibration or threshold — the choice was likely made deliberately.
→ Full doc: meteorology-decisions.md

### analysis-metrics
Comprehensive catalog of all ~85 weather metrics across the 6 model slots (plus HRRR/ICON-D2 GRIB sourcing): Open-Meteo API sources, GRIB2 enrichment, MetPy derivations, SFIP/Ogimet icing indices, per-model availability matrix, and known issues.
→ Full doc: analysis-metrics.md

### advisories
Route advisory system: 22 deterministic evaluators across 11 categories (icing incl. freezing precipitation, cloud, precipitation/en-route visibility, turbulence incl. wave-corroborated mountain wind, convective incl. convective-character, winds-aloft trip impact, airport conditions incl. density altitude, LLWS and terminal convective, feasibility incl. approach feasibility, model-quality, fronts, sun) with per-model severity grading, user-tunable params, registry auto-discovery, worst/majority aggregation, and recalculation without re-fetching.
Key exports: `evaluate_all`, `get_catalog`, `RouteContext`, `RouteAdvisoriesManifest`
→ Full doc: advisories.md

### alternates
Weather-based alternate airports (D-2 inward, gated by `compute_alternates` pref, default-on): for a marginal destination, surface the nearest divert candidates that fix a deficient axis (category, wind, crosswind), classified before/after with a detour pair. Per-airport assessment shares `analysis/airport_consensus.py` with the forecast map (consistency guarantee). Per-candidate approach gate + cross-border operational flags; rendered in web UI, iOS, text digest and via the MCP/agent alternates endpoint (not yet in the LLM prompt).
Key exports: `run_alternates`, `RouteAlternates`, `AlternateAirport`, `AlternateAxisPick`, `OperationalFlag`, `cross_border_flag`, `best_ceiling`, `flight_category`, `enrich_wind`, `consensus`, `compute_route_distances`
→ Full doc: alternates.md

### alternate-requirement
Regulatory "is a filed alternate required?" for the destination, computed two ways — FAA (14 CFR 91.169, binary) and EASA Part-NCO (Likely/Marginal/Unlikely band) — plus per-candidate alternate-minima qualification for each #210 divert candidate. Forecast ceiling/visibility are real (TAF at D-0, NWP consensus otherwise); the unknown plate minima are estimated as a per-approach-class range that sets the Marginal band width. Pure logic in `analysis/alternate_requirement.py`, wired as a pipeline post-step.
Key exports: `run_alternate_requirement`, `compute_faa_trigger`, `compute_easa_trigger`, `compute_faa_qual`, `compute_easa_qual`, `build_window`, `APPROACH_CLASS_PROXY`, `AlternateRequirement`, `AlternateQual`, `BandVerdict`, `TriggerVerdict`
→ Full doc: alternate-requirement.md

### timing-scenarios
"Is there a better departure time?" — opt-in per-flight Flexibility scan (none / alternate time / same day / ±day) that re-grades the route at other departure hours on the full advisory set. ECMWF-anchored coarse-to-fine: free in-window candidates graded multi-model, a background ECMWF-only daylight sweep for provisional ones, and an on-tap multi-model confirm. Enforces a hard honesty invariant — never grade an hour whose fields aren't decoded for the model being claimed (`compute_model_coverage` / `refused_times`), because `at_time()` clamps silently. Scans keyed by (flight, ECMWF run).
Key exports: `run_time_scan`, `compute_model_coverage`, `candidate_valid_times`, `extend_ecmwf_daylight`, `extend_openmeteo_adjacent_day`, `confirm_candidate`, `compute_daylight_window`, `TimeScanStatus`, `TimeCandidate`, `ModelCoverage`
→ Full doc: timing-scenarios.md

### vertical-profile-solver
Shared `(route-distance × altitude)` cost-field path-finder behind altitude-dependent advisories: one solver replaces the per-axis VFR mitigation gates and is reused by icing-escape, superseding the ad-hoc #328/#330 corridor logic. Produces the climb/descent profile a mitigation suggests.
Key exports: `vertical_profile.solve`, `CostModel`, `Profile`, `Blockage`, `floor_reachable_bins`, `build_cost_model`, `MitigationProfile`
→ Full doc: vertical-profile-solver.md

### synoptic-charts
DWD + Met Office surface-analysis charts as the Synoptic Forecast basemap, with the Hewson gridded overlay and gate-detected front polylines drawn on top: shared `ChartCache` disk/projection layer, source-gated manifest + serve endpoints, and a <1px Python↔TS projection-equivalence contract (generated constants + fixture) so the grid and fronts land where the chart says they do.
Key exports: `ChartCache`, `ChartCalibration`, `serve_chart_bytes`, `build_source_manifest`, `SOURCES`, `makeChartProjection`, `makeChartInverseProjection`, `HewsonGridLayer.setProjector`, `SynopticMap.setBasemap`
→ Full doc: synoptic-charts.md

### frontal-detection
Zone-scale frontal presence detection from 850hPa gridded fields. Two-pass anomaly filtering, dual T850+θe gradient thresholding, 20 European zones, cross-front wind classification, clearance timing. Integrated-but-experimental: per-leg Hewson path wired into pipeline/scheduler (gated by `auto_front_detection`); zone-aggregation path is CLI-only.
Key exports: `compute_frontal_zones_dual`, `classify_front_type`, `build_zone_timeseries`, `find_fronts_in_regions`, `find_frontal_clearance_time`, `compute_timing_spread`
→ Full doc: frontal-detection.md

## Visualization & briefing UI

### visualization
Four synchronized client visualizations: canvas cross-section (28 registered layers incl. advisory highlight + night shading), canvas route graph (12 scalar metrics), Leaflet route map (13 metrics, altitude slider, front + airport-forecast overlays), and dynamic canvas Skew-T (see skewt-canvas.md). Switchable themes with theme-aware legends; four layout modes (cross-section, compare, split, map); model-availability NWP fallback, shared color scales and hover sync. Compare mode renders one layer across all models with four band modes.
Key exports: `CrossSectionRenderer`, `CompareSectionRenderer`, `RouteGraphRenderer`, `RouteMapRenderer`, `SkewTRenderer`, `extractVizData`, `getAllLayers`, `getLayerLegend`, `getActiveTheme`, `setActiveTheme`
→ Full doc: visualization.md

### skewt-canvas
Dynamic client-rendered Skew-T log-P diagram (replaces static MetPy PNGs): canvas grid, T/Td/parcel curves, CAPE/CIN shading, overlay bands (clouds/icing/inversions/convective), dual-axis side panel (14 variables, theme-grouped), hover tooltip + cross-section-linked cursor, and a multi-model Compare mode. Sidecar-first: derived variables come from `sounding_profiles.json.gz` written at refresh, recompute only as fallback.
Key exports: `SkewTRenderer`, `SkewTTransform`, `attachSkewTInteraction`, `SkewTCompareRenderer`, `attachSkewTCompareInteraction`, `renderSkewtCompareControls`, `renderCompareSidePanel`, `VARIABLE_REGISTRY`, `VARIABLE_GROUPS`, `SKEWT_OVERLAYS`
→ Full doc: skewt-canvas.md

### route-graph
2D chart below the cross-section for scalar weather values along the route. Dual Y-axes, extensible metric registry (12 metrics incl. CIN, region-aware QNH/Altimeter, AGL ceilings with an above-scale state), line and bar render types, hover sync with the cross-section, and metrics driven by advisory presets.
Key exports: `RouteGraphRenderer`, `attachRouteGraphInteraction`, `ROUTE_GRAPH_METRICS`, `sampleMetric`, `getMetricById`, `renderRouteGraphControls`
→ Full doc: route-graph.md

### forecast-page
Pan-European weather overview map with per-airport forecast visualization (10 metrics incl. alternate-required, visibility and runway crosswind/headwind, server-baked consensus modes). Cache layer serves pre-computed JSON with staleness tracking, falling back to live queries.
Key exports: `get_forecast_map_data`, `WeatherMap`, `fetchForecastMap`
→ Full doc: forecast-page.md

### briefing-sidebar
Default, reversible layout for the briefing page: fixed left rail (route identity, derived glance summary, scroll-spy nav, freshness, controls) + scrollable main pane, resizable, with per-section focus mode; classic is a one-click opt-out. The rail owns no data — it derives its summary from already-rendered DOM and builds its nav from the `NAV_GROUPS` whitelist of `data-section` keys, so it adds/removes without touching other managers.
Key exports: `initBriefingLayout`, `getBriefingLayout`, `BriefingLayout`
→ Full doc: briefing-sidebar.md

## Output & D-0 weather

### digest
Output generation: plain-text digest, enhanced Skew-T plots (CAPE/CIN shading, hodograph, indices panel), LLM-powered weather briefing via LangGraph (short-range traffic-light + long-range outlook regimes), deterministic guardrails.
Key exports: `format_digest`, `generate_all_skewts`, `run_digest`, `WeatherDigest`, `LongRangeDigest`, `run_guardrails`
→ Full doc: digest.md

### metar-taf-route-weather
D-0 METAR/TAF integration: fetch route-corridor observations, compare vs NWP, wind advisory, TAF highlighting, observations refresh. Sibling D-0 **route SIGMET** integration (area hazards, no model comparison) shares the module + real-time refresh seam. Deterministic worsened-conditions banner computed on refresh via `compute_refresh_delta`.
Key exports: `run_route_weather`, `run_observation_comparison`, `run_realtime_refresh`, `compute_wind_advisory`, `compute_refresh_delta`, `RefreshDelta`, `RouteObservations`, `AirportObservation`, `run_route_sigmets`, `RouteSigmets`, `SigmetAlongRoute`, `RealtimeRefreshResult`
→ Full doc: metar-taf-route-weather.md

### current-conditions
Observed conditions along the route (#574, phase 1): OPERA radar reflectivity + rain rate, EUMETSAT MTG total lightning and satellite cloud tops, collected as local frames and sampled in 5/10/20 NM discs around every route point. Displays observations only — no verdict, no advisory wiring; the cross-check is visual, with `observed-tops` drawn over the NWP cloud bands. Load-bearing invariants: `nodata` (half the OPERA grid) never conflated with `undetect`; parallax applied before corridor membership (52 km median displacement vs a 37 km corridor); one windowed read, never per-station file access; no synthetic shared timestamp. Payload inline on `briefing.json`; imagery served from `/api/observed`. Gated on `WB_OBSERVED_ENABLED`.
Key exports: `build_observed_conditions`, `ObservedConditions`, `FrameStore`, `sample`, `sample_flashes`, `collect_once`, `run_observed_collect_loop`, `render_overlay`, `build_summary`
→ Full doc: current-conditions.md

## iOS app

### ios-app-overview
iOS/iPad companion app entry point — start here, links to all ios-app-* docs. Phase 1 + Phase 2 complete; Phase 3 M0/M1 (aircraft registry, PIREP submit/view) shipped. Briefing re-cut into four tabs (Advisory · Discussion · Cross-Section · Map, gated PIREPs) by #310; also live tracking, flight sharing, post-flight debrief, forecast map, offline auto-download + eviction, APNs push, App Intents, What's New.
Key exports: `AppState`, `BriefingViewModel`, `BriefingTab`, `CachingBriefingRepository`, `CrossSectionRenderer`, `PirepViewModel`, `PirepOfflineStore`, `HelpCatalogStore`, `WhatsNewStore`
→ Full doc: ios-app-overview.md

### ios-app-architecture
Tech stack (SwiftUI, no SwiftData, MapKit, iOS 26.2+), MVVM + Repository pattern, layer responsibilities, Google OAuth + Apple Sign In via FlyFunCommon, library reuse (FlyFunCommon, RZFlight, RZUtils, RZSkewT).
→ Full doc: ios-app-architecture.md

### ios-app-data-models
No SwiftData. Three plain-struct tiers under `Models/`: Codable API structs, `Viz*` domain structs (`VizData.swift`) for rendering, and the `Assessment` enum. Persistence is JSON-on-disk (`BriefingCacheStore` pack cache, `PirepOfflineStore` queue, help-catalog and what's-new stores) plus UserDefaults. Flat PIREP model with client UUIDs for idempotent offline sync.
Key exports: `FlightResponse`, `PackMetaResponse`, `RouteAnalysesResponse`, `AdvisoriesResponse`, `SnapshotResponse`, `VizRouteData`, `VizPoint`, `Assessment`, `BriefingCacheStore`, `PirepOfflineStore`
→ Full doc: ios-app-data-models.md

### ios-app-server-api
Server API contract for the iOS app: auth (Google `?platform=ios` + native Apple), flights CRUD and the PATCH-vs-move structural-edit rule, packs/snapshot/advisories/sounding-profile, queued vs streamed refresh, the gzipped offline `/bundle`, and PIREPs (`/api/pireps`) with their row model and lat/lon-box spatial queries.
Key exports: `/api/flights/{id}/move`, `/api/flights/{id}/packs/{ts}/bundle`, `/api/flights/{id}/packs/refresh`, `/api/refresh/active`, `/api/pireps`, `PirepRow`, `list_pireps`
→ Full doc: ios-app-server-api.md

### ios-app-features
End-state feature set + vision, largely NOT as-built — read the status banner first. Briefing sync (offline payload + on-demand artifacts), PIREP filing modes, voice PIREP via Siri, passive collection, observation timeline, community feed, live sharing, post-flight verification. Shipped: pack download, refresh push, Start Flight tracking, briefing-anchored PIREP filing with offline queue; unbuilt: prompting engine, standalone/community PIREP UI, voice, WebSocket. Authoritative status: ios-app-overview.md
→ Full doc: ios-app-features.md

### ios-app-ui
Cockpit UI design principles (one-handed, large tap targets, high-contrast, non-blocking) and screen layouts. As-built: briefing viewer + a single manual `PirepReportingView` sheet inside the existing tabs. The in-flight mode/map and report-card variants are original Phase-3 vision, NOT yet built.
→ Full doc: ios-app-ui.md

### ios-app-sync-prompting
Sync engine + forecast-driven prompting engine — Phase 3a/3b. Built: the JSON-file `PirepOfflineStore` queue (foreground + post-submit flush) and route-position projection via `FlightTrackingService`. Still spec-only: connectivity-triggered flush, WebSocket real-time, forecast look-ahead, 7 trigger types, priority queue, cross-section forecast lookup.
Key exports: `PirepOfflineStore`, `NetworkMonitor`, `FlightTrackingService`, `ProjectedPosition`, `AppState.syncPendingPireps()`
→ Full doc: ios-app-sync-prompting.md

### ios-app-roadmap
3-phase roadmap: Phase 1 (online viewer — done), Phase 2 (offline caching + APNs refresh push — done; `/companion` payload and offline map tiles never built), Phase 3 (3a PIREP filing + offline queue partly shipped, 3b prompting and 3c live sharing unbuilt). Records which planned designs the shipped code diverged from, plus decisions made and open questions.
→ Full doc: ios-app-roadmap.md

### ios-app-intents [phase-1 shipped]
App Intents / Siri / Apple Intelligence surface for the iOS app — the on-device sibling of the MCP tool catalog. Phase 1 (current iOS 26, shipped in #364): `FlightEntity`/`AirportEntity`, open/refresh/check intents, `AppShortcutsProvider` phrases, `EntityStringQuery` resolver for "the flight tomorrow to Fairoaks", Spotlight indexing. Phase 2 (WWDC26/iOS 27, not yet built): View Annotations ("explain this", "show the cross-section"). No SiriKit to migrate. MCP⇆intent parity table.
Key exports: `FlightEntity`, `AirportEntity`, `OpenFlightListIntent`, `OpenBriefingIntent`, `CheckBriefingIntent`, `FlightsOverviewIntent`, `RefreshBriefingIntent`, `AirportWeatherIntent`, `FlyFunShortcuts`, `FlightResolver`, `IntentDialogs`, `PendingNavigation`
→ Full doc: ios-app-intents.md

### ios-app-briefing-notifications [implemented]
APNs push when a briefing finishes refreshing — closes the loop for the Siri refresh intent. **Server half implemented (#366):** `notify/push.py` (token-based APNs, httpx HTTP/2 + PyJWT), `notify/dispatch.py` (single notify gate emitted once from `api/packs.py::_notify_refresh_complete`, called after each refresh path commits its pack, covering auto/in-app/Siri/MCP; email moved here from the scheduler), `notify/badge.py` + `api/notifications.py` (server-derived cross-surface badge, `flight_briefing_seen` table, `/flights/badge` + `/flights/{id}/seen`), `api/devices.py` (register/unregister). Channel + scope + change-only prefs in `app_prefs_json`; per-flight `notify_override`; migration `075`. **iOS client (on #364's app-shell):** `Services/PushNotifications.swift` (`AppDelegate` + `UNUserNotificationCenterDelegate` — token upload, foreground suppression, silent badge-sync, tap→`PendingNavigation` deep-link), `AppState` push/badge/mark-seen methods, Settings push toggle, `aps-environment` entitlement + `remote-notification` background mode. **Prefs UI + semantics (#371):** full Account › Notifications on web + iOS (Briefing-updates 3-stop folding scope+change-only, Email toggle, device-conditional Push) and a per-flight Default/Always/Mute bell; a clean WHEN/HOW split — one channel-agnostic decision (`notify_qualifies` AND not `present`) then pure-preference delivery per channel — with **presence** ("was the user watching this refresh finish?") recorded server-side in the refresh registry (`touch_watch`/`is_watched`, from SSE keepalive or `/packs/refresh/status` poll, same signal web+iOS, robust to navigate-away-and-back; `/refresh/active` list poll excluded); `?source=` is usage-attribution only; last-device decay fail-safe (`apply_last_device_decay`) + `notify_decay_notice`/`push_device_count` in the prefs response; retired the per-refresh "Email me when done" (`force_email`/`?notify_email=` gone).
Key exports: `send_briefing_push`, `send_silent_badge_push`, `notify_briefing_refresh` (takes `present`), `notify_qualifies`, `_RefreshRegistry.touch_watch`/`is_watched`, `apply_last_device_decay`, `compute_badge_count`, `mark_flight_seen`; iOS `PushSupport`, `AppDelegate`, `AppState.uploadDeviceToken/reconcileBadge/markBriefingSeen`
→ Full doc: ios-app-briefing-notifications.md

### rzskewt
Swift package for Skew-T log-P diagrams. Extracted to own repo: `github.com/roznet/rztskew`. Full atmospheric thermodynamics, Canvas rendering, 47 unit tests. Design docs live in that repo's `designs/` directory.
Key exports: `SkewTView`, `SkewTRenderer`, `SoundingProfile`, `Thermodynamics`

## Verification, evaluation & pilot feedback

### metar-taf-accuracy [project]
Dual-track METAR/TAF verification: flight-based collection (10-min poll during active flights) + standalone pan-European monitoring (~620 airports) via three decoupled loops (METAR ingest, forecast+sounding fetch, scoring). Daily/monthly rollups, dashboard cache with staleness tracking, graceful degradation. Storage is tiered (#522): raw rows in MySQL for a bounded online window, aggregates forever in rollup tables, row-level history forever as Parquet under `DATA_DIR/archive/` — every phase behind an env gate, rollout runbook in `plans/verification-data-tiering.md`.
Key exports: `collect_and_store`, `run_standalone_cycle`, `score_completed_flights`, `backfill_scores`, `get_digest_data`, `send_verification_digest`, `rollup_day`, `run_monthly_rollup`, `run_archive`, `verify_archives`, `prune_raw_observations`, `rebuild_all`, `is_stale`, `VerificationDigestData`, `VerificationObservation`
→ Full doc: metar-taf-accuracy.md
→ Rollout plan: plans/verification-data-tiering.md

### eval-digest-workbench [project]
Dev-only golden-labelling workbench for the LLM digest eval (#254): renders the standard briefing view for a curated corpus of pulled prod packs, with an in-view panel to record golden GREEN/AMBER/RED labels per guidance. File-based corpus in a separate eval-set repo (staging scratch + committed corpus), served through existing endpoints via a "virtual-flight" resolver (`eval-<corpus_id>` ids), runtime-gated by `WEATHERBRIEF_EVAL_WORKBENCH` + admin (never in prod). The labelled corpus is now the eval set `run_digest_eval.py` scores against.
Key exports: `eval_workbench_enabled`, `resolve_eval_flight`, `CorpusMeta`, `CorpusLabel`, `CorpusPack`, `promote`, `coverage_report`, `select_candidates`, `ingest_pack`, `rerun_diff`
→ Full doc: eval-digest-workbench.md

### debrief
Pilot post-flight judgement (flown/cancelled/monitoring) on past flights — Phase 1 of #92. Sidecar `flight_debriefs` table with a shared 8-tag taxonomy served to clients via `/api/help/catalog`, hybrid chips+text entry (web) and a form on iOS, per-user summary stats, three-section flight list (future/recent/past). Debriefed flights' packs are exempt from T2 retention so calibration can re-analyse against ERA5 later.
Key exports: `FlightDebrief`, `Decision`, `ConditionTag`, `OutcomeValue`, `build_taxonomy_catalog`, `compute_stats`, `upsert_debrief`, `list_debriefed_flight_ids`
→ Full doc: debrief.md

### pireps
Crowdsourced pilot weather reports for European airspace (as-built M0/M1): flat `PirepRow` with client UUIDs for idempotent offline submit, optional flight/aircraft links, submit/batch/query API, per-user view/publish flags in `app_prefs_json` (both default-off during beta), burst + daily rate limits where the batch route charges per item, server-enforced European-bounds gate, and anonymise-on-account-deletion. PIREP-linked packs are exempt from *all* retention tiers so the forecast the pilot saw survives with the report.
Key exports: `PirepRow`, `validate_european_bounds`, `create_pirep`, `list_pireps`, `can_view_pireps`, `can_publish_pireps`, `api/pireps.py` router
→ Full doc: pireps.md

## Connectors (agent integrations)

### chatgpt-connector [project]
Native ChatGPT support (Custom GPT + OpenAPI Action) as the sibling of the Claude MCP connector: two thin front-doors over one core. Shared response shaping + meteorological guardrails in `connectors/views.py`; the ChatGPT REST router (`api/agent.py`, mounted `/agent/v1`) reuses upstream logic in-process (read endpoints via the same helpers, write endpoints by calling the existing route handlers directly). Isolated OpenAPI at `/agent/v1/openapi.json`; OAuth reuses the `mcp` scope with a pre-provisioned (DCR-registered) confidential client. No CORS change (server-to-server).
Key exports: `summarize_advisories`, `summarize_altitude_table`, `advisory_detail`, `convective_detail`, `summarize_alternates`, `alternates_hook`, `briefing_freshness_status`, `agent.router`
→ Full doc: chatgpt-connector.md

## Infrastructure & operations

### grib-decode-dispatcher [project]
Priority-aware, fault-tolerant admission layer in front of the GRIB decode process pool. `DecodePriority` (INTERACTIVE/SCHEDULED/BACKGROUND) propagated via a ContextVar orders jobs and bounds in-flight to worker count; on a pool fault it keeps completed work, reschedules interrupted jobs, and dead-letters poison jobs. Idempotency-only invariant. Bypass via `GRIB_DECODE_WORKERS=0` or `GRIB_DECODE_PRIORITY_ENABLED=0`.
Key exports: `DecodePriority`, `PriorityDecodeDispatcher`, `set_decode_priority`, `enrich_forecasts(priority=...)`, `_dispatch_decode`, `_dispatch_decode_parallel`, `decode_pool_enabled`, `shutdown_decode_pool`, `decode_dead_letter_counts`
→ Full doc: grib-decode-dispatcher.md

### refresh-durability
Durable briefing-refresh tracking (`briefing_refresh_jobs`, migration 082) as a best-effort write-through mirror of the in-memory `_RefreshRegistry`, plus a one-shot boot-time pass that abandons or resumes refreshes interrupted by a container restart (OOM/deploy/crash). Single uvicorn worker ⇒ any non-terminal row at boot is an orphan; `WB_REFRESH_MAX_ATTEMPTS` (default 2) caps retries; `/refresh/status` falls back to the row so a reload reports "interrupted"/"gave up" instead of nothing.
Key exports: `BriefingRefreshJobRow`, `record_queued`, `record_running`, `record_heartbeat`, `record_finished`, `list_orphans`, `latest_job_for_flight`, `_RefreshRegistry(durable=True)`, `mark_outcome`, `note_pack_path`, `decide_resume`, `run_refresh_resume`, `resume_max_attempts`
→ Full doc: refresh-durability.md

### multi-user-deployment
Deployment architecture for weather.flyfun.aero: Docker on DigitalOcean, auth via flyfun-common (OAuth, JWT, cross-subdomain SSO), MySQL/SQLite DB schema, rate limiting, encrypted credentials, account deletion + GDPR data export, admin hub, Resend email, deploy commands, env vars.
→ Full doc: multi-user-deployment.md

### cost-attribution
Per-briefing cost computation in USD (LLM tokens incl. prompt-cache tiers + infrastructure + storage + margin), program-wide cost report, shared cross-app cost_ledger via flyfun-common, versioned admin rate card, public transparency endpoint, and the Stripe donation + impact-framing layer. No credits abstraction — all values in positive USD.
Key exports: `compute_cost`, `compute_program_cost`, `CostBreakdown`, `CostConfig`, `charge_briefing`, `get_active_cost_config`, `build_program_report`, `economics_from_report`, `personal_impact`
→ Full doc: cost-attribution-design.md
