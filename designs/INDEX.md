# WeatherBrief

> Medium-range (D-7 to D-0) aviation weather assessment for cross-country GA flights in Europe

Install: `pip install -e ".[dev]"` (local development)

## Modules

### architecture
System overview: pipeline, API, web app, storage layout, dependencies, phase roadmap.
Key exports: `execute_briefing`, `create_app`
→ Full doc: architecture.md

### data-models
Pydantic v2 models for routes, forecasts, analysis, snapshots, flights, and briefing packs.
Key exports: `ForecastSnapshot`, `RouteConfig`, `WaypointForecast`, `SoundingAnalysis`, `Flight`, `BriefingPackMeta`
→ Full doc: data-models.md

### fetch
Weather data retrieval: Open-Meteo multi-model client, DWD text forecasts, Autorouter GRAMET.
Key exports: `OpenMeteoClient`, `fetch_dwd_text_forecasts`, `AutorouterGramet`
→ Full doc: fetch.md

### analysis
Aviation-specific analysis: wind components, MetPy sounding analysis (thermodynamics, clouds, icing, convective), altitude band comparison, model divergence scoring.
Key exports: `compute_wind_components`, `analyze_sounding`, `summarize_by_bands`, `compare_models`
→ Full doc: analysis.md

### digest
Output generation: plain-text digest, Skew-T plots, LLM-powered weather briefing via LangGraph.
Key exports: `format_digest`, `generate_all_skewts`, `run_digest`, `WeatherDigest`
→ Full doc: digest.md

### flight-weather-tracker-spec
Original requirements specification with phase roadmap, data source descriptions, algorithm details, and output format definitions.
→ Full doc: flight-weather-tracker-spec.md

### plan-briefing-architecture
Detailed implementation plan for the API-first architecture, web UI, PDF reports, and email delivery (Steps 1-10).
→ Full doc: plan-briefing-architecture.md

### sounding_analysis_plan
Implementation plan for Phase 4a: MetPy sounding analysis pipeline replacing simple T+RH heuristics with thermodynamic indices, enhanced cloud/icing/convective assessment, and altitude band comparison.
→ Full doc: sounding_analysis_plan.md
