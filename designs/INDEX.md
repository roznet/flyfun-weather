# WeatherBrief

> Medium-range (D-7 to D-0) aviation weather assessment for cross-country GA flights in Europe

Install: `pip install -e ".[dev]"` (local development)

## Modules

### architecture
System overview: pipeline, API, web app, storage layout, dependencies, phase roadmap.
Key exports: `execute_briefing`, `create_app`
→ Full doc: architecture.md

### data-models
Pydantic v2 models for routes, forecasts, analysis, snapshots, cross-sections, elevation, flights, and briefing packs. Models split across `models/analysis.py` and `models/storage.py`.
Key exports: `ForecastSnapshot`, `RouteConfig`, `RoutePoint`, `RouteCrossSection`, `WaypointForecast`, `SoundingAnalysis`, `VerticalMotionAssessment`, `RoutePointAnalysis`, `ElevationProfile`, `InversionLayer`, `Flight`, `BriefingPackMeta`
→ Full doc: data-models.md

### fetch
Weather data retrieval: Open-Meteo multi-point client, route interpolation, DWD text forecasts, Autorouter GRAMET, SRTM elevation, model freshness.
Key exports: `OpenMeteoClient`, `interpolate_route`, `fetch_dwd_text_forecasts`, `AutorouterGramet`, `get_elevation_profile`, `check_freshness`
→ Full doc: fetch.md

### analysis
Aviation-specific analysis: wind components, MetPy sounding analysis (thermodynamics, clouds, icing, inversions, convective, vertical motion/CAT), altitude advisories, model divergence scoring.
Key exports: `compute_wind_components`, `analyze_sounding`, `compute_altitude_advisories`, `compare_models`, `assess_vertical_motion`, `detect_inversions`
→ Full doc: analysis.md

### analysis-metrics
Comprehensive catalog of all ~40 weather metrics: API sources, derivation formulas, per-model availability matrix, physical interpretation, aviation relevance, known issues, and MetPy function inventory.
→ Full doc: analysis-metrics.md

### visualization
Canvas-rendered interactive cross-section visualization: layer registry, data extraction, terrain fill, cloud/icing/CAT/inversion bands, convective towers, temperature/stability lines, hover interaction, metrics UI system.
Key exports: `CrossSectionRenderer`, `extractVizData`, `getAllLayers`
→ Full doc: visualization.md

### digest
Output generation: plain-text digest, enhanced Skew-T plots (CAPE/CIN shading, hodograph, indices panel), LLM-powered weather briefing via LangGraph.
Key exports: `format_digest`, `generate_all_skewts`, `run_digest`, `WeatherDigest`
→ Full doc: digest.md

### multi-user-deployment
Deployment architecture for weather.flyfun.aero: Docker on DigitalOcean, Google OAuth, MySQL/SQLite DB, per-user flights and credentials, usage tracking, rate limiting, admin approval, shareable briefing links, model freshness.
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
