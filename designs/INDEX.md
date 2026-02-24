# WeatherBrief

> Medium-range (D-7 to D-0) aviation weather assessment for cross-country GA flights

Install: `pip install -e ".[dev]"` (local development)

## Modules

### architecture
System overview: pipeline (with extracted `tasks/` modules), API, web app, storage layout, dependencies, phase roadmap.
Key exports: `execute_briefing`, `create_app`, `run_fetch`, `run_analysis`, `run_advisories`
→ Full doc: architecture.md

### data-models
Pydantic v2 models for routes, forecasts, analysis, snapshots, cross-sections, elevation, flights, and briefing packs. Models split across `models/analysis.py` and `models/storage.py`.
Key exports: `ForecastSnapshot`, `RouteConfig`, `RoutePoint`, `RouteCrossSection`, `WaypointForecast`, `SoundingAnalysis`, `VerticalMotionAssessment`, `RoutePointAnalysis`, `ElevationProfile`, `InversionLayer`, `Flight`, `FlightProfile`, `BriefingPackMeta`
→ Full doc: data-models.md

### fetch
Weather data retrieval: Open-Meteo multi-point client, route interpolation, route-aware text forecasts (NWS AFD for US, DWD for Europe), Autorouter GRAMET, SRTM elevation, model freshness (GFS/ECMWF/ICON/UKMO/MeteoFrance), GRIB2 enrichment (GFS + ICON-EU cloud water, diagnostics, cloud cover override, init time tracking).
Key exports: `OpenMeteoClient`, `interpolate_route`, `fetch_text_forecasts`, `detect_region`, `fetch_nws_afd`, `fetch_dwd_text_forecasts`, `AutorouterGramet`, `get_elevation_profile`, `check_freshness`, `enrich_forecasts`
→ Full doc: fetch.md

### analysis
Aviation-specific analysis: wind components, MetPy sounding analysis (thermodynamics, clouds, icing, inversions, convective, vertical motion/CAT), altitude advisories, model divergence scoring.
Key exports: `compute_wind_components`, `analyze_sounding`, `compute_altitude_advisories`, `compare_models`, `assess_vertical_motion`, `detect_inversions`
→ Full doc: analysis.md

### advisories
Route advisory system: 13 deterministic evaluators across 5 categories (icing, cloud, turbulence, airport conditions, feasibility) with per-model severity grading, user-tunable parameters, registry auto-discovery, aggregation modes (worst/majority), and recalculation without re-fetching.
Key exports: `evaluate_all`, `get_catalog`, `RouteContext`, `RouteAdvisoriesManifest`
→ Full doc: advisories.md

### analysis-metrics
Comprehensive catalog of all ~85 weather metrics across 7 models: Open-Meteo API sources, GRIB2 enrichment (GFS cloud water/diagnostics, ICON-EU CLW/ICE/cloud cover/ceiling), MetPy derivations, SFIP/Ogimet icing indices, precipitation assessment, per-model availability matrix, and known issues.
→ Full doc: analysis-metrics.md

### visualization
Canvas-rendered interactive cross-section visualization: layer registry, data extraction, terrain fill, cloud/icing/CAT/inversion/NWP-cloud bands (per-band hybrid with sounding-corroborated collapse), convective towers (marginal risk skipped), temperature/stability lines, hover interaction, shared interaction helpers, route graph (scalar metrics chart), metrics UI system with info popups, layer legends, "Discuss with AI" integration, unified atmospheric profile table, ceiling metrics, Windy meteogram links, and freshness bar with GRIB init time annotations.
Key exports: `CrossSectionRenderer`, `RouteGraphRenderer`, `extractVizData`, `getAllLayers`, `getLayerLegend`
→ Full doc: visualization.md

### digest
Output generation: plain-text digest, enhanced Skew-T plots (CAPE/CIN shading, hodograph, indices panel), LLM-powered weather briefing via LangGraph.
Key exports: `format_digest`, `generate_all_skewts`, `run_digest`, `WeatherDigest`
→ Full doc: digest.md

### multi-user-deployment
Deployment architecture for weather.flyfun.aero: Docker on DigitalOcean, Google OAuth + API token auth (bot/agent users), MySQL/SQLite DB, per-user flights/profiles/credentials, usage tracking, rate limiting, auto-approve + welcome email, admin management, shareable briefing links, flight parameter profiles.
→ Full doc: multi-user-deployment.md

### flight-weather-tracker-spec
Original requirements specification with phase roadmap, data source descriptions, algorithm details, and output format definitions.
→ Full doc: flight-weather-tracker-spec.md

## Implementation Plans

### visualization-plan
Detailed plan for cross-section plot and route map visualizations (Phase 7). Cross-section is implemented; route map is planned.
→ Full doc: visualization-plan.md

### elevation-profile-plan
Plan for SRTM terrain elevation profile along route. Fully implemented.
→ Full doc: elevation-profile-plan.md

### deferred-analysis-plan
Plan for background refresh and progressive analysis: per-model route analysis files, on-demand endpoints, digest change detection, background worker. Model freshness implemented; deferred analysis partially implemented.
→ Full doc: deferred-analysis-plan.md

### plan-briefing-architecture
Implementation plan for the API-first architecture, web UI, PDF reports, and email delivery (Steps 1-10). Fully implemented.
→ Full doc: plan-briefing-architecture.md

### sounding_analysis_plan
Implementation plan for Phase 4a: MetPy sounding analysis pipeline. Fully implemented.
→ Full doc: sounding_analysis_plan.md

### vertical-motion-plan
Implementation plan for vertical motion & turbulence analysis. Fully implemented.
→ Full doc: vertical-motion-plan.md

### weather-engine-specs
Raw GRIB2 engine: what's implemented (GFS CLWMR/ICMR/cloud diagnostics, ICON-EU QC/QI enrichment), data source registry (GFS/ECMWF/ICON/ARPEGE/UKMO with bucket paths and metadata URLs), future extensions (additional variables, Ellrod index, ECMWF enrichment), and implementation gotchas.
→ Full doc: weather-engine-specs.md

### sfip-implementation-design
SFIP (Simplified Forecast Icing Potential) algorithm design: fuzzy-logic membership functions (temperature, RH, CLW, vertical velocity), two variants (full with GRIB2 CLW, proxy with DD+cloud cover), glaciation factor from ICMR, severity mapping, per-model behavior. Based on Belo-Pereira (2015) and Morcrette et al. (2019).
→ Full doc: sfip-implementation-design.md

### route-graph
2D chart below the cross-section for plotting scalar weather values (head/tailwind, temperature, precipitation, cloud cover, CAPE, freezing level) along the route. X-axis aligned with cross-section, dual Y-axes, extensible metric registry, line and bar render types, hover sync.
→ Full doc: route-graph.md

### metar-taf-route-weather
D-0 METAR/TAF integration: fetch real observations from airports along route corridor via euro_aip RouteWeatherService, compare against NWP model predictions (CONFIRMING/SIGNIFICANT/CONFLICTING), wind advisory computation with runway crosswind, TAF line highlighting, sounding ceiling for model category, observations refresh endpoint, surface in digest, HTML report, and web UI.
Key exports: `run_route_weather`, `run_observation_comparison`, `compute_wind_advisory`, `RouteObservations`, `AirportObservation`
→ Full doc: metar-taf-route-weather.md
