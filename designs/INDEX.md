# Flyfun Weather

> Medium-range (D-7 to D-0) aviation weather assessment for cross-country GA flights

Install: `pip install -e ".[dev]"` (local development)

## Modules

### architecture
System overview: pipeline, API, web app, storage layout (briefing.json + forecasts.json split), dependencies, phase roadmap.
Key exports: `execute_briefing`, `create_app`, `run_fetch`, `run_analysis`, `run_advisories`
→ Full doc: architecture.md

### data-models
Pydantic v2 models for routes, forecasts, analysis, snapshots, cross-sections, elevation, flights, and briefing packs. Snapshots stored as split files: `briefing.json` (analyses/observations/metadata) + `forecasts.json` (raw forecasts).
Key exports: `ForecastSnapshot`, `RouteConfig`, `RoutePoint`, `RouteCrossSection`, `WaypointForecast`, `SoundingAnalysis`, `VerticalMotionAssessment`, `RoutePointAnalysis`, `ElevationProfile`, `InversionLayer`, `Flight`, `FlightProfile`, `BriefingPackMeta`, `UserAircraftRow`
→ Full doc: data-models.md

### fetch
Weather data retrieval: Open-Meteo multi-point client, route interpolation, route-aware text forecasts (NWS AFD for US, DWD for Europe), SRTM elevation, model freshness, GRIB2 enrichment (GFS + ICON-EU + ECMWF IFS via ECPDS) with two-phase sequential decode for memory safety. (GRAMET now runs in `tasks/outputs.py:run_gramet()` via euro_aip's `AutorouterGrametSource`.)
Key exports: `OpenMeteoClient`, `interpolate_route`, `fetch_text_forecasts`, `get_elevation_profile`, `enrich_forecasts`
→ Full doc: fetch.md

### freshness-markers
Marker-based per-(model, source) staleness decision used by `/packs/freshness` + auto-refresh. In-memory `MarkerStore` populated by a 5-min loop; pure-compute lookup in the HTTP path. Horizon-aware comparison against pack `model_sources`. Admin endpoint surfaces observed delivery delays vs. registry expectation for ongoing calibration.
Key exports: `SOURCE_REGISTRY`, `MarkerStore`, `get_store`, `check_source`, `run_freshness_loop`, `_build_data_status`
→ Full doc: freshness-markers.md

### analysis
Aviation-specific analysis: wind components, MetPy sounding analysis (thermodynamics, DD/NWP cloud methods, four icing methods — Ogimet-DD/Ogimet-NWP/SFIP-NWP/IENG-NWP, inversions, convective, vertical motion/CAT), altitude advisories, model divergence scoring. Icing gated by `is_in_cloud_layer()` consistent with cloud display layers. Icing and cloud methods selectable via user preferences.
Key exports: `compute_wind_components`, `analyze_sounding`, `compute_altitude_advisories`, `compare_models`, `assess_vertical_motion`, `detect_inversions`
→ Full doc: analysis.md

### advisories
Route advisory system: 13 deterministic evaluators across 6 categories (icing, cloud, turbulence, convective, airport conditions, feasibility, model quality incl. DD-vs-NWP within-model agreement) with per-model severity grading, user-tunable parameters, registry auto-discovery, aggregation modes (worst/majority), icing/cloud method swapping, altitude-aware convective filtering, and recalculation without re-fetching.
Key exports: `evaluate_all`, `get_catalog`, `RouteContext`, `RouteAdvisoriesManifest`
→ Full doc: advisories.md

### analysis-metrics
Comprehensive catalog of all ~85 weather metrics across 7 models: Open-Meteo API sources, GRIB2 enrichment, MetPy derivations, SFIP/Ogimet icing indices, per-model availability matrix, and known issues.
→ Full doc: analysis-metrics.md

### visualization
Four synchronized visualizations: canvas cross-section (~25 weather layers across 9 groups), canvas route graph (11 scalar metrics incl. CIN and region-aware QNH/Altimeter), Leaflet route map (13 metric-colored segment types with altitude slider and width variation), and dynamic canvas Skew-T (see skewt-canvas.md). Switchable cross-section themes (standard, high-contrast, gramet) with cloud hatch patterns, theme preview, and theme-aware legends. Four layout modes (cross-section, compare, split, map), shared color scales, hover sync, compact/full layer mode, icing/cloud method groups. Compare mode renders one layer across all models with four band modes (overlay, overlay-soft, consensus, consensus+outlines).
Key exports: `CrossSectionRenderer`, `CompareSectionRenderer`, `RouteGraphRenderer`, `RouteMapRenderer`, `SkewTRenderer`, `extractVizData`, `getAllLayers`, `getLayerLegend`, `getActiveTheme`, `setActiveTheme`
→ Full doc: visualization.md

### skewt-canvas
Dynamic client-rendered Skew-T log-P diagram replacing static MetPy PNGs. Canvas-based with background grid (isotherms, adiabats, mixing ratios), T/Td/parcel path curves, CAPE/CIN shading, overlay bands (clouds, icing, inversions, convective), dual-axis side panel (14 variables incl. HW/XW and CC, grouped by theme via `<optgroup>`), hover tooltip, linked cursor with cross-section, and a multi-model Compare mode. Sidecar-first analysis: derived variables come from `sounding_profiles.json.gz` written at refresh, with on-the-fly recompute only as fallback.
Key exports: `SkewTRenderer`, `SkewTTransform`, `attachSkewTInteraction`, `SkewTCompareRenderer`, `attachSkewTCompareInteraction`, `renderSkewtCompareControls`, `renderCompareSidePanel`, `VARIABLE_REGISTRY`, `VARIABLE_GROUPS`, `SKEWT_OVERLAYS`
→ Full doc: skewt-canvas.md

### route-graph
2D chart below cross-section for scalar weather values along route. Dual Y-axes, extensible metric registry (11 metrics incl. CIN and region-aware QNH/Altimeter), line and bar render types, hover sync with cross-section.
→ Full doc: route-graph.md

### digest
Output generation: plain-text digest, enhanced Skew-T plots (CAPE/CIN shading, hodograph, indices panel), LLM-powered weather briefing via LangGraph.
Key exports: `format_digest`, `generate_all_skewts`, `run_digest`, `WeatherDigest`
→ Full doc: digest.md

### metar-taf-route-weather
D-0 METAR/TAF integration: fetch observations from route corridor airports, compare against NWP predictions, wind advisory computation, TAF highlighting, observations refresh endpoint. Sibling D-0 **route SIGMET** integration (area hazards, no model comparison) shares the same module + real-time refresh seam; model retains polygon/enroute-span/vertical-band for a future cross-section overlay. Deterministic worsened-conditions banner computed on refresh via `compute_refresh_delta`.
Key exports: `run_route_weather`, `run_observation_comparison`, `compute_wind_advisory`, `compute_refresh_delta`, `RefreshDelta`, `RouteObservations`, `AirportObservation`, `run_route_sigmets`, `RouteSigmets`, `SigmetAlongRoute`, `RealtimeRefreshResult`
→ Full doc: metar-taf-route-weather.md

### alternates
Weather-based alternate airports (D-2 inward, gated by `compute_alternates` pref): for a marginal destination, surface the nearest divert candidates that fix a deficient axis (flight category, wind, best-runway crosswind), classified before/after along the route with a detour pair. Per-airport assessment shares `analysis/airport_consensus.py` with the forecast map (consistency guarantee). Per-candidate instrument-approach gate with graceful relaxation; ranked table + nearest-improving picks rendered in briefing UI and text digest (not yet in LLM prompt).
Key exports: `run_alternates`, `RouteAlternates`, `AlternateAirport`, `AlternateAxisPick`, `best_ceiling`, `flight_category`, `enrich_wind`, `consensus`, `compute_route_distances`
→ Full doc: alternates.md

### time-alignment
Time and spatial alignment in the data pipeline: aware-UTC datetime convention, per-hour GRIB enrichment across flight windows, spatial index consistency, hour-matching merge logic, old pack backward compatibility.
Key exports: `compute_flight_window_hours`, `compute_icon_eu_flight_window_hours`, `_forecast_hour_to_utc`, `_merge_cloud_water_into_sections`
→ Full doc: time-alignment-audit.md

### weather-engine-specs
GRIB2 enrichment engine: GFS S3 (CLWMR/ICMR/cloud diagnostics), ICON-EU DWD (QC/QI/cloud cover/ceiling), ECMWF IFS ECPDS (clwc/ciwc/cc/surface diagnostics), data source registry with bucket paths, variable reference tables, implementation gotchas, future extensions.
→ Full doc: weather-engine-specs.md

### grib-decode-dispatcher [project]
Priority-aware, fault-tolerant admission layer in front of the GRIB decode process pool. `DecodePriority` (INTERACTIVE/SCHEDULED/BACKGROUND, lower=higher) propagated via a `_DECODE_PRIORITY` ContextVar; `PriorityDecodeDispatcher` orders pending jobs by priority (FIFO within a level), bounds in-flight to the worker count, and on a pool fault keeps completed work, reschedules interrupted work (crash=jittered backoff, timeout=immediate), and dead-letters poison jobs (timeout victim / retry cap / retry-rate budget). Idempotency-only invariant. Bypass via `GRIB_DECODE_WORKERS=0` or `GRIB_DECODE_PRIORITY_ENABLED=0`.
Key exports: `DecodePriority`, `PriorityDecodeDispatcher`, `set_decode_priority`, `enrich_forecasts(priority=...)`, `_dispatch_decode`, `_dispatch_decode_parallel`, `decode_dead_letter_counts`
→ Full doc: grib-decode-dispatcher.md

### multi-user-deployment
Deployment architecture for weather.flyfun.aero: Docker on DigitalOcean, auth via flyfun-common (OAuth, JWT, cross-subdomain SSO), MySQL/SQLite DB schema, rate limiting, encrypted credentials, account deletion, admin hub, Resend email, deploy commands, env vars.
→ Full doc: multi-user-deployment.md

### cost-attribution
Per-briefing cost computation in USD (LLM tokens + infrastructure + storage + margin), shared cross-app cost_ledger via flyfun-common, versioned admin cost config, transparency endpoint. No credits abstraction — all values in positive USD.
Key exports: `compute_cost`, `CostBreakdown`, `CostConfig`, `charge_briefing`, `get_active_cost_config`
→ Full doc: cost-attribution-design.md

### ios-app-overview
iOS/iPad companion app entry point: Phase 1 (online viewer) complete, Phase 2 (offline + resilience) complete, Phase 3 M0+M1 (aircraft registry, PIREP submit/view) implemented. Auth (Apple + Google + dev login), advisory dashboard, cross-section Canvas renderer, native Skew-T (RZSkewT package), multi-tier offline fallback, cached flight indicators, PIREP offline queue with auto-flush on connectivity. Start here — links to all other ios-app-* docs.
Key exports: `AppState`, `BriefingViewModel`, `CachingBriefingRepository`, `CrossSectionRenderer`, `PirepViewModel`, `PirepOfflineStore`
→ Full doc: ios-app-overview.md

### ios-app-architecture
Tech stack (SwiftUI, SwiftData, MapKit, iOS 18+), MVVM + Repository pattern, layer responsibilities, Google OAuth + Apple Sign In flow, library reuse (RZFlight, RZUtils, RZSkewT).
→ Full doc: ios-app-architecture.md

### ios-app-data-models
No SwiftData. Three plain-struct tiers under `Models/`: Codable API structs (`FlightResponse`, `PackMetaResponse`, `SnapshotResponse`, `RouteAnalysesResponse`, `AdvisoriesResponse`, `PirepResponse`, …), `Viz*` domain structs (`VizData.swift`) for cross-section/route-graph rendering, and an `Assessment` enum. Persistence via two actors: `BriefingCacheStore` (JSON-on-disk pack cache, 5 required endpoints) and `PirepOfflineStore` (pending-PIREP queue). Flat PIREP model with client UUIDs for idempotent offline sync.
→ Full doc: ios-app-data-models.md

### ios-app-server-api
Server API contract: existing endpoints consumed (auth, flights, packs, snapshot, advisories, SSE refresh, sounding profiles), Phase 2 `/companion` lightweight sync endpoint, Phase 3 top-level `/api/observations` + flight-scoped accessors + WebSocket, server data model (`FlightSession`, `Observation`), spatial query design.
→ Full doc: ios-app-server-api.md

### ios-app-features
End-state feature set + vision: briefing sync (lightweight offline payload + on-demand artifacts), PIREP filing modes (proactive prompts, in-flight manual, standalone), voice PIREP via Siri, passive data collection, observation timeline, community PIREP feed, live online sharing, post-flight verification.
→ Full doc: ios-app-features.md

### ios-app-ui
Cockpit UI design principles (one-handed, large tap targets, high-contrast, non-blocking) and screen layouts. As-built: briefing viewer + a single manual `PirepReportingView` sheet inside the existing tabs. The in-flight mode/map and report-card variants (prompted side-card, full bottom sheet with "All correct" shortcut) are original Phase-3 vision, NOT yet built.
→ Full doc: ios-app-ui.md

### ios-app-sync-prompting
Sync engine + forecast-driven prompting engine — largely Phase 3a/3b design intent. Implemented today: the JSON-file `PirepOfflineStore` queue. Still spec-only (absent from Swift code): `NWPathMonitor` flush, WebSocket real-time, route progress tracker, 7 trigger types with entry/exit/cooldown, priority queue, forecast lookup from cross-section data.
→ Full doc: ios-app-sync-prompting.md

### ios-app-roadmap
3-phase roadmap: Phase 1 (online viewer — DONE), Phase 2 (offline + push — resilience done, push pending), Phase 3 (3a manual + sync, 3b prompting, 3c live sharing). Cross-section renderer layer waves. Decisions made and open questions.
→ Full doc: ios-app-roadmap.md

### forecast-page
Pan-European weather overview map with per-airport forecast visualization (9 metrics incl. visibility and runway crosswind/headwind, consensus modes). Cache layer serves pre-computed JSON with staleness tracking; falls back to live queries. (The model accuracy heatmap was removed in #154; the replacement view consumes ``get_optimistic_bias_leaderboard`` from the verification stats module — see `metar-taf-accuracy.md`.)
Key exports: `get_forecast_map_data`, `WeatherMap`, `fetchForecastMap`
→ Full doc: forecast-page.md

### briefing-sidebar
Opt-in, fully reversible alternative layout for the briefing page: fixed left rail (route identity, derived glance summary, scroll-spy section nav, freshness, controls) beside a scrollable main pane, with resizable rail and per-section focus mode. The rail owns no data — it derives its summary by reading the already-rendered DOM and is generated from `data-section` tags, so it adds/removes without touching any other manager.
Key exports: `initBriefingLayout`, `getBriefingLayout`, `BriefingLayout`
→ Full doc: briefing-sidebar.md

### metar-taf-accuracy [project]
Dual-track METAR/TAF verification: flight-based collection (10-min poll during active flights) + standalone monitoring (~830 pan-European airports) via three decoupled loops — METAR ingest (every 30 min), forecast fetch + sounding enrichment (07/19 UTC), and scoring of existing snapshots (06/09/12/15/18 UTC). Monthly rollup aggregation, dashboard cache layer with staleness tracking, chunk-level retry, error recording, graceful degradation.
Key exports: `collect_and_store`, `run_standalone_cycle`, `score_completed_flights`, `backfill_scores`, `get_digest_data`, `send_verification_digest`, `run_monthly_rollup`, `rebuild_all`, `is_stale`, `VerificationDigestData`, `VerificationObservation`
→ Full doc: metar-taf-accuracy.md

### frontal-detection
Zone-scale frontal presence detection from 850hPa gridded fields. Two-pass anomaly filtering, dual T850+θe gradient thresholding, 18 European zones, cross-front wind classification, clearance timing. CLI-only (not yet in pipeline).
Key exports: `compute_frontal_zones_dual`, `classify_front_type`, `build_zone_timeseries`, `find_fronts_in_regions`, `find_frontal_clearance_time`, `compute_timing_spread`
→ Full doc: frontal-detection.md

### rzskewt
Swift package for Skew-T log-P diagrams. Extracted to own repo: `github.com/roznet/rztskew`. Full atmospheric thermodynamics, Canvas rendering, 47 unit tests. Design docs live in that repo's `designs/` directory.
Key exports: `SkewTView`, `SkewTRenderer`, `SoundingProfile`, `Thermodynamics`

### debrief
Pilot post-flight judgement (cancelled/flown) on past flights — Phase 1 of #92. Sidecar `flight_debriefs` table with shared 8-tag taxonomy, hybrid chips+text entry with auto-toggle, per-user summary stats panel, three-section flight list (future/recent/past). Debriefed flights' packs are exempt from T2 retention so calibration can re-analyse against ERA5 later.
Key exports: `FlightDebrief`, `Decision`, `ConditionTag`, `OutcomeValue`, `compute_stats`, `upsert_debrief`, `list_debriefed_flight_ids`
→ Full doc: debrief.md
