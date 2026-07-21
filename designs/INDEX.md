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
Weather data retrieval: Open-Meteo multi-point client, route interpolation, route-aware text forecasts (NWS AFD US / DWD Europe), SRTM elevation, model freshness, and GRIB2 enrichment (GFS + ICON-EU + ECMWF IFS) with two-phase sequential decode for memory safety.
Key exports: `OpenMeteoClient`, `interpolate_route`, `fetch_text_forecasts`, `get_elevation_profile`, `enrich_forecasts`
→ Full doc: fetch.md

### weather-engine-specs
GRIB2 enrichment engine: GFS S3 (CLWMR/ICMR/cloud diagnostics), ICON-EU DWD (QC/QI/cloud cover/ceiling), ECMWF IFS ECPDS (clwc/ciwc/cc/surface diagnostics), data source registry with bucket paths, variable reference tables, implementation gotchas, future extensions.
→ Full doc: weather-engine-specs.md

### freshness-markers
Marker-based per-(model, source) staleness decision for `/packs/freshness` + auto-refresh: an in-memory `MarkerStore` (5-min refresh loop) feeds a pure-compute, horizon-aware lookup in the HTTP path. Admin endpoint surfaces observed delivery delays vs. registry expectation.
Key exports: `SOURCE_REGISTRY`, `MarkerStore`, `get_store`, `check_source`, `run_freshness_loop`, `_build_data_status`
→ Full doc: freshness-markers.md

### time-alignment
Time and spatial alignment in the data pipeline: aware-UTC datetime convention, per-hour GRIB enrichment across flight windows, spatial index consistency, hour-matching merge logic, old pack backward compatibility.
Key exports: `compute_flight_window_hours`, `compute_icon_eu_flight_window_hours`, `_forecast_hour_to_utc`, `_merge_cloud_water_into_sections`
→ Full doc: time-alignment-audit.md

## Analysis & meteorology

### analysis
Aviation-specific analysis: wind components, MetPy sounding analysis (thermodynamics, DD/NWP cloud methods, four icing methods, inversions, convective, vertical motion/CAT), altitude advisories, model divergence scoring. Icing gated by `is_in_cloud_layer()`; icing/cloud methods user-selectable. Per-subsystem deep-dive audits (clouds, convective, icing, cross-cutting) and an explicit meteorological-decisions log (reasoning + rejected options) are linked from the doc.
Key exports: `compute_wind_components`, `analyze_sounding`, `compute_altitude_advisories`, `compare_models`, `assess_vertical_motion`, `detect_inversions`
→ Full doc: analysis.md

### meteorology-decisions
Dated log of explicit meteorological design decisions, each with context, reasoning, and rejected alternatives: ceiling DD-vs-NWP-adjusted, Ogimet icing-zone width / convective contribution at moderate CAPE, GFS cloud-diagnostics window-midpoint interp + RH/condensate gate, convective realizable-CAPE / regime discrimination / DD-stays-pure, NWP-cover vs CAPE-driven risk, ICON-D2 explicit-convection firing table (reflectivity × corroborators, §19). Read before changing any weather calibration or threshold — the choice was likely made deliberately.
→ Full doc: meteorology-decisions.md

### analysis-metrics
Comprehensive catalog of all ~85 weather metrics across 7 models: Open-Meteo API sources, GRIB2 enrichment, MetPy derivations, SFIP/Ogimet icing indices, per-model availability matrix, and known issues.
→ Full doc: analysis-metrics.md

### advisories
Route advisory system: 21 deterministic evaluators across 11 categories (icing incl. freezing precipitation, cloud, precipitation/en-route visibility, turbulence incl. wave-corroborated mountain wind, convective incl. convective-character, winds-aloft trip impact, airport conditions incl. density altitude, LLWS and terminal convective, feasibility, model-quality, fronts, sun) with per-model severity grading, user-tunable params, registry auto-discovery, worst/majority aggregation, and recalculation without re-fetching.
Key exports: `evaluate_all`, `get_catalog`, `RouteContext`, `RouteAdvisoriesManifest`
→ Full doc: advisories.md

### alternates
Weather-based alternate airports (D-2 inward, gated by `compute_alternates` pref): for a marginal destination, surface the nearest divert candidates that fix a deficient axis (category, wind, crosswind), classified before/after with a detour pair. Per-airport assessment shares `analysis/airport_consensus.py` with the forecast map (consistency guarantee). Per-candidate approach gate; rendered in briefing UI + text digest (not yet in LLM prompt).
Key exports: `run_alternates`, `RouteAlternates`, `AlternateAirport`, `AlternateAxisPick`, `best_ceiling`, `flight_category`, `enrich_wind`, `consensus`, `compute_route_distances`
→ Full doc: alternates.md

### alternate-requirement
Regulatory "is a filed alternate required?" for the destination, computed two ways — FAA (14 CFR 91.169, binary) and EASA Part-NCO (Likely/Marginal/Unlikely band) — plus per-candidate alternate-minima qualification for each #210 divert candidate. Forecast ceiling/visibility are real (TAF at D-0, NWP consensus otherwise); the unknown plate minima are estimated as a per-approach-class range that sets the Marginal band width. Pure logic in `analysis/alternate_requirement.py`, wired as a pipeline post-step.
Key exports: `run_alternate_requirement`, `compute_faa_trigger`, `compute_easa_trigger`, `compute_faa_qual`, `compute_easa_qual`, `build_window`, `APPROACH_CLASS_PROXY`, `AlternateRequirement`, `AlternateQual`, `BandVerdict`, `TriggerVerdict`
→ Full doc: alternate-requirement.md

### frontal-detection
Zone-scale frontal presence detection from 850hPa gridded fields. Two-pass anomaly filtering, dual T850+θe gradient thresholding, 20 European zones, cross-front wind classification, clearance timing. Integrated-but-experimental: per-leg Hewson path wired into pipeline/scheduler (gated by `auto_front_detection`); zone-aggregation path is CLI-only.
Key exports: `compute_frontal_zones_dual`, `classify_front_type`, `build_zone_timeseries`, `find_fronts_in_regions`, `find_frontal_clearance_time`, `compute_timing_spread`
→ Full doc: frontal-detection.md

## Visualization & briefing UI

### visualization
Four synchronized client visualizations: canvas cross-section (~25 weather layers), canvas route graph (12 scalar metrics), Leaflet route map (altitude slider, metric-colored segments), and dynamic canvas Skew-T (see skewt-canvas.md). Switchable themes with theme-aware legends; four layout modes (cross-section, compare, split, map); shared color scales and hover sync. Compare mode renders one layer across all models with four band modes.
Key exports: `CrossSectionRenderer`, `CompareSectionRenderer`, `RouteGraphRenderer`, `RouteMapRenderer`, `SkewTRenderer`, `extractVizData`, `getAllLayers`, `getLayerLegend`, `getActiveTheme`, `setActiveTheme`
→ Full doc: visualization.md

### skewt-canvas
Dynamic client-rendered Skew-T log-P diagram (replaces static MetPy PNGs): canvas grid, T/Td/parcel curves, CAPE/CIN shading, overlay bands (clouds/icing/inversions/convective), dual-axis side panel (14 variables, theme-grouped), hover tooltip + cross-section-linked cursor, and a multi-model Compare mode. Sidecar-first: derived variables come from `sounding_profiles.json.gz` written at refresh, recompute only as fallback.
Key exports: `SkewTRenderer`, `SkewTTransform`, `attachSkewTInteraction`, `SkewTCompareRenderer`, `attachSkewTCompareInteraction`, `renderSkewtCompareControls`, `renderCompareSidePanel`, `VARIABLE_REGISTRY`, `VARIABLE_GROUPS`, `SKEWT_OVERLAYS`
→ Full doc: skewt-canvas.md

### route-graph
2D chart below cross-section for scalar weather values along route. Dual Y-axes, extensible metric registry (11 metrics incl. CIN and region-aware QNH/Altimeter), line and bar render types, hover sync with cross-section.
→ Full doc: route-graph.md

### forecast-page
Pan-European weather overview map with per-airport forecast visualization (9 metrics incl. visibility and runway crosswind/headwind, consensus modes). Cache layer serves pre-computed JSON with staleness tracking, falling back to live queries.
Key exports: `get_forecast_map_data`, `WeatherMap`, `fetchForecastMap`
→ Full doc: forecast-page.md

### briefing-sidebar
Default, reversible layout for the briefing page: fixed left rail (route identity, derived glance summary, scroll-spy nav, freshness, controls) + scrollable main pane, resizable, with per-section focus mode; classic is a one-click opt-out. The rail owns no data — it derives its summary from already-rendered DOM and builds nav from `data-section` tags, so it adds/removes without touching other managers.
Key exports: `initBriefingLayout`, `getBriefingLayout`, `BriefingLayout`
→ Full doc: briefing-sidebar.md

## Output & D-0 weather

### digest
Output generation: plain-text digest, enhanced Skew-T plots (CAPE/CIN shading, hodograph, indices panel), LLM-powered weather briefing via LangGraph (short-range traffic-light + long-range outlook regimes), deterministic guardrails.
Key exports: `format_digest`, `generate_all_skewts`, `run_digest`, `WeatherDigest`, `LongRangeDigest`, `run_guardrails`
→ Full doc: digest.md

### metar-taf-route-weather
D-0 METAR/TAF integration: fetch route-corridor observations, compare vs NWP, wind advisory, TAF highlighting, observations refresh. Sibling D-0 **route SIGMET** integration (area hazards, no model comparison) shares the module + real-time refresh seam. Deterministic worsened-conditions banner computed on refresh via `compute_refresh_delta`.
Key exports: `run_route_weather`, `run_observation_comparison`, `compute_wind_advisory`, `compute_refresh_delta`, `RefreshDelta`, `RouteObservations`, `AirportObservation`, `run_route_sigmets`, `RouteSigmets`, `SigmetAlongRoute`, `RealtimeRefreshResult`
→ Full doc: metar-taf-route-weather.md

## iOS app

### ios-app-overview
iOS/iPad companion app entry point — start here, links to all ios-app-* docs. Phase 1 (online viewer) + Phase 2 (offline/resilience) complete; Phase 3 M0+M1 (aircraft registry, PIREP submit/view) implemented. Auth (Apple/Google/dev), advisory dashboard, cross-section + native Skew-T renderers, multi-tier offline fallback, PIREP offline queue with auto-flush.
Key exports: `AppState`, `BriefingViewModel`, `CachingBriefingRepository`, `CrossSectionRenderer`, `PirepViewModel`, `PirepOfflineStore`
→ Full doc: ios-app-overview.md

### ios-app-architecture
Tech stack (SwiftUI, no SwiftData, MapKit, iOS 26.2+), MVVM + Repository pattern, layer responsibilities, Google OAuth + Apple Sign In via FlyFunCommon, library reuse (FlyFunCommon, RZFlight, RZUtils, RZSkewT).
→ Full doc: ios-app-architecture.md

### ios-app-data-models
No SwiftData. Three plain-struct tiers under `Models/`: Codable API structs, `Viz*` domain structs (`VizData.swift`) for rendering, and an `Assessment` enum. Persistence via two actors: `BriefingCacheStore` (JSON-on-disk pack cache) and `PirepOfflineStore` (pending-PIREP queue). Flat PIREP model with client UUIDs for idempotent offline sync.
→ Full doc: ios-app-data-models.md

### ios-app-server-api
Server API contract: existing endpoints consumed (auth, flights, packs, snapshot, advisories, SSE refresh, sounding profiles), Phase 2 `/companion` lightweight sync endpoint, Phase 3 top-level `/api/observations` + flight-scoped accessors + WebSocket, server data model (`FlightSession`, `Observation`), spatial query design.
→ Full doc: ios-app-server-api.md

### ios-app-features
End-state feature set + vision: briefing sync (lightweight offline payload + on-demand artifacts), PIREP filing modes (proactive prompts, in-flight manual, standalone), voice PIREP via Siri, passive data collection, observation timeline, community PIREP feed, live online sharing, post-flight verification.
→ Full doc: ios-app-features.md

### ios-app-ui
Cockpit UI design principles (one-handed, large tap targets, high-contrast, non-blocking) and screen layouts. As-built: briefing viewer + a single manual `PirepReportingView` sheet inside the existing tabs. The in-flight mode/map and report-card variants are original Phase-3 vision, NOT yet built.
→ Full doc: ios-app-ui.md

### ios-app-sync-prompting
Sync engine + forecast-driven prompting engine — largely Phase 3a/3b design intent. Built today: the JSON-file `PirepOfflineStore` queue. Still spec-only (absent from Swift): `NWPathMonitor` flush, WebSocket real-time, route progress tracker, 7 trigger types, priority queue, forecast lookup from cross-section.
→ Full doc: ios-app-sync-prompting.md

### ios-app-roadmap
3-phase roadmap: Phase 1 (online viewer — DONE), Phase 2 (offline + push — resilience done, push pending), Phase 3 (3a manual + sync, 3b prompting, 3c live sharing). Cross-section renderer layer waves. Decisions made and open questions.
→ Full doc: ios-app-roadmap.md

### ios-app-intents [phase-1 shipped]
App Intents / Siri / Apple Intelligence surface for the iOS app — the on-device sibling of the MCP tool catalog. Phase 1 (current iOS 26, shipped in #364): `FlightEntity`/`AirportEntity`, open/refresh/check intents, `AppShortcutsProvider` phrases, `EntityStringQuery` resolver for "the flight tomorrow to Fairoaks", Spotlight indexing. Phase 2 (WWDC26/iOS 27, not yet built): View Annotations ("explain this", "show the cross-section"), on-device Foundation Models narration of cached advisories. No SiriKit to migrate. MCP⇆intent parity table.
Key exports: `FlightEntity`, `AirportEntity`, `OpenFlightListIntent`, `OpenBriefingIntent`, `CheckBriefingIntent`, `RefreshBriefingIntent`, `AirportWeatherIntent`, `ExplainAdvisoryIntent`, `FlyFunShortcuts`
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
Dual-track METAR/TAF verification: flight-based collection (10-min poll during active flights) + standalone pan-European monitoring (~830 airports) via three decoupled loops (METAR ingest, forecast+sounding fetch, scoring). Monthly rollups, dashboard cache with staleness tracking, graceful degradation.
Key exports: `collect_and_store`, `run_standalone_cycle`, `score_completed_flights`, `backfill_scores`, `get_digest_data`, `send_verification_digest`, `run_monthly_rollup`, `rebuild_all`, `is_stale`, `VerificationDigestData`, `VerificationObservation`
→ Full doc: metar-taf-accuracy.md

### eval-digest-workbench [project]
Dev-only golden-labelling workbench for the LLM digest eval (#254): renders the standard briefing view for a curated corpus of pulled prod packs, with an in-view panel to record golden GREEN/AMBER/RED labels per guidance. File-based corpus served through existing endpoints via a "virtual-flight" resolver (`eval-<corpus_id>` ids), runtime-gated by `WEATHERBRIEF_EVAL_WORKBENCH` + admin (never in prod). Labels committed (`label.json`), pack payloads gitignored/re-pullable. Backend+scripts+tests done; frontend written, runtime verification pending.
Key exports: `eval_workbench_enabled`, `resolve_eval_flight`, `CorpusMeta`, `CorpusLabel`, `coverage_report`, `select_candidates`, `ingest_pack`
→ Full doc: eval-digest-workbench.md

### debrief
Pilot post-flight judgement (cancelled/flown) on past flights — Phase 1 of #92. Sidecar `flight_debriefs` table with shared 8-tag taxonomy, hybrid chips+text entry, per-user summary stats, three-section flight list (future/recent/past). Debriefed flights' packs are exempt from T2 retention so calibration can re-analyse against ERA5 later.
Key exports: `FlightDebrief`, `Decision`, `ConditionTag`, `OutcomeValue`, `compute_stats`, `upsert_debrief`, `list_debriefed_flight_ids`
→ Full doc: debrief.md

## Connectors (agent integrations)

### chatgpt-connector [project]
Native ChatGPT support (Custom GPT + OpenAPI Action) as the sibling of the Claude MCP connector: two thin front-doors over one core. Shared response shaping + meteorological guardrails in `connectors/views.py`; the ChatGPT REST router (`api/agent.py`, mounted `/agent/v1`) reuses upstream logic in-process (read endpoints via the same helpers, write endpoints by calling the existing route handlers directly). Isolated OpenAPI at `/agent/v1/openapi.json`; OAuth reuses the `mcp` scope with a pre-provisioned (DCR-registered) confidential client. No CORS change (server-to-server).
Key exports: `summarize_advisories`, `summarize_altitude_table`, `advisory_detail`, `convective_detail`, `briefing_freshness_status`, `agent.router`
→ Full doc: chatgpt-connector.md

## Infrastructure & operations

### grib-decode-dispatcher [project]
Priority-aware, fault-tolerant admission layer in front of the GRIB decode process pool. `DecodePriority` (INTERACTIVE/SCHEDULED/BACKGROUND) propagated via a ContextVar orders jobs and bounds in-flight to worker count; on a pool fault it keeps completed work, reschedules interrupted jobs, and dead-letters poison jobs. Idempotency-only invariant. Bypass via `GRIB_DECODE_WORKERS=0` or `GRIB_DECODE_PRIORITY_ENABLED=0`.
Key exports: `DecodePriority`, `PriorityDecodeDispatcher`, `set_decode_priority`, `enrich_forecasts(priority=...)`, `_dispatch_decode`, `_dispatch_decode_parallel`, `decode_dead_letter_counts`
→ Full doc: grib-decode-dispatcher.md

### multi-user-deployment
Deployment architecture for weather.flyfun.aero: Docker on DigitalOcean, auth via flyfun-common (OAuth, JWT, cross-subdomain SSO), MySQL/SQLite DB schema, rate limiting, encrypted credentials, account deletion + GDPR data export, admin hub, Resend email, deploy commands, env vars.
→ Full doc: multi-user-deployment.md

### cost-attribution
Per-briefing cost computation in USD (LLM tokens + infrastructure + storage + margin), shared cross-app cost_ledger via flyfun-common, versioned admin cost config, transparency endpoint. No credits abstraction — all values in positive USD.
Key exports: `compute_cost`, `CostBreakdown`, `CostConfig`, `charge_briefing`, `get_active_cost_config`
→ Full doc: cost-attribution-design.md
