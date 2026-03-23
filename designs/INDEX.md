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
Key exports: `ForecastSnapshot`, `RouteConfig`, `RoutePoint`, `RouteCrossSection`, `WaypointForecast`, `SoundingAnalysis`, `VerticalMotionAssessment`, `RoutePointAnalysis`, `ElevationProfile`, `InversionLayer`, `Flight`, `FlightProfile`, `BriefingPackMeta`
→ Full doc: data-models.md

### fetch
Weather data retrieval: Open-Meteo multi-point client, route interpolation, route-aware text forecasts (NWS AFD for US, DWD for Europe), Autorouter GRAMET, SRTM elevation, model freshness, GRIB2 enrichment (GFS + ICON-EU) with two-phase sequential decode for memory safety.
Key exports: `OpenMeteoClient`, `interpolate_route`, `fetch_text_forecasts`, `AutorouterGramet`, `get_elevation_profile`, `check_freshness`, `enrich_forecasts`
→ Full doc: fetch.md

### analysis
Aviation-specific analysis: wind components, MetPy sounding analysis (thermodynamics, DD/NWP cloud methods, three icing methods — Ogimet-DD/Ogimet-NWP/SFIP-NWP, inversions, convective, vertical motion/CAT), altitude advisories, model divergence scoring. Icing and cloud methods selectable via user preferences.
Key exports: `compute_wind_components`, `analyze_sounding`, `compute_altitude_advisories`, `compare_models`, `assess_vertical_motion`, `detect_inversions`
→ Full doc: analysis.md

### advisories
Route advisory system: 13 deterministic evaluators across 5 categories (icing, cloud, turbulence, airport conditions, feasibility) with per-model severity grading, user-tunable parameters, registry auto-discovery, aggregation modes (worst/majority), icing/cloud method swapping, altitude-aware convective filtering, and recalculation without re-fetching.
Key exports: `evaluate_all`, `get_catalog`, `RouteContext`, `RouteAdvisoriesManifest`
→ Full doc: advisories.md

### analysis-metrics
Comprehensive catalog of all ~85 weather metrics across 7 models: Open-Meteo API sources, GRIB2 enrichment, MetPy derivations, SFIP/Ogimet icing indices, per-model availability matrix, and known issues.
→ Full doc: analysis-metrics.md

### visualization
Three synchronized visualizations: canvas cross-section (16 weather layers across 8 groups), canvas route graph (9 scalar metrics incl. ceiling-DD/NWP), and Leaflet route map (17 metric-colored segment types with altitude slider and width variation). Switchable cross-section themes (standard, high-contrast) with cloud hatch patterns, theme preview, and theme-aware legends. Three layout modes, shared color scales, hover sync, compact/full layer mode, icing/cloud method groups.
Key exports: `CrossSectionRenderer`, `RouteGraphRenderer`, `RouteMapRenderer`, `extractVizData`, `getAllLayers`, `getLayerLegend`, `getActiveTheme`, `setActiveTheme`
→ Full doc: visualization.md

### route-graph
2D chart below cross-section for scalar weather values along route. Dual Y-axes, extensible metric registry (9 metrics incl. ceiling-DD/NWP AGL), line and bar render types, hover sync with cross-section.
→ Full doc: route-graph.md

### digest
Output generation: plain-text digest, enhanced Skew-T plots (CAPE/CIN shading, hodograph, indices panel), LLM-powered weather briefing via LangGraph.
Key exports: `format_digest`, `generate_all_skewts`, `run_digest`, `WeatherDigest`
→ Full doc: digest.md

### metar-taf-route-weather
D-0 METAR/TAF integration: fetch observations from route corridor airports, compare against NWP predictions, wind advisory computation, TAF highlighting, observations refresh endpoint.
Key exports: `run_route_weather`, `run_observation_comparison`, `compute_wind_advisory`, `RouteObservations`, `AirportObservation`
→ Full doc: metar-taf-route-weather.md

### time-alignment
Time and spatial alignment in the data pipeline: aware-UTC datetime convention, per-hour GRIB enrichment across flight windows, spatial index consistency, hour-matching merge logic, old pack backward compatibility.
Key exports: `compute_flight_window_hours`, `compute_icon_eu_flight_window_hours`, `_forecast_hour_to_utc`, `_merge_cloud_water_into_sections`
→ Full doc: time-alignment-audit.md

### weather-engine-specs
GRIB2 enrichment engine: GFS S3 (CLWMR/ICMR/cloud diagnostics), ICON-EU DWD (QC/QI/cloud cover/ceiling), data source registry with bucket paths, variable reference tables, implementation gotchas, future extensions.
→ Full doc: weather-engine-specs.md

### multi-user-deployment
Deployment architecture for weather.flyfun.aero: Docker on DigitalOcean, auth via flyfun-common (OAuth, JWT, cross-subdomain SSO), MySQL/SQLite DB schema, rate limiting, encrypted credentials, account deletion, admin hub, Resend email, deploy commands, env vars.
→ Full doc: multi-user-deployment.md

### cost-attribution
Per-briefing cost computation in USD (LLM tokens + infrastructure + storage + margin), shared cross-app cost_ledger via flyfun-common, versioned admin cost config, transparency endpoint. No credits abstraction — all values in positive USD.
Key exports: `compute_cost`, `CostBreakdown`, `CostConfig`, `charge_briefing`, `get_active_cost_config`
→ Full doc: cost-attribution-design.md

### companion-app
iOS/iPad companion app: Phase 1 (online viewer) complete, Phase 2 (offline) mostly done. Auth (Apple + Google), NavigationSplitView, advisory dashboard, cross-section Canvas renderer, native Skew-T (RZSkewT package), pack history picker, SSE refresh streaming, offline caching. Phase 3 (PIREPs) not started.
Key exports: `AppState`, `BriefingViewModel`, `CachingBriefingRepository`, `CrossSectionRenderer`, `SkewTDetailView`
→ Full doc: companion-app.md

### rzskewt
Swift package for Skew-T log-P diagrams. Extracted to own repo: `github.com/roznet/rztskew`. Full atmospheric thermodynamics, Canvas rendering, 47 unit tests. Design docs live in that repo's `designs/` directory.
Key exports: `SkewTView`, `SkewTRenderer`, `SoundingProfile`, `Thermodynamics`
