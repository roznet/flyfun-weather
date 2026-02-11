# WeatherBrief

> Medium-range (D-7 to D-0) aviation weather assessment for cross-country GA flights in Europe

Install: `pip install -e ".[dev]"` (local development)

## Modules

### architecture
System overview: data pipeline, CLI, storage layout, phase roadmap.
Key exports: `cli.main`, `cli.run_fetch`
→ Full doc: architecture.md

### data-models
Pydantic v2 models for routes, forecasts, analysis results, and snapshots.
Key exports: `ForecastSnapshot`, `RouteConfig`, `WaypointForecast`, `WaypointAnalysis`
→ Full doc: data-models.md

### fetch
Weather data retrieval: Open-Meteo multi-model client, DWD text forecasts, Autorouter GRAMET.
Key exports: `OpenMeteoClient`, `fetch_dwd_text_forecasts`, `AutorouterGramet`
→ Full doc: fetch.md

### analysis
Aviation-specific analysis: wind components, icing assessment, cloud estimation, model comparison.
Key exports: `compute_wind_components`, `assess_icing_profile`, `estimate_cloud_layers`, `compare_models`
→ Full doc: analysis.md

### digest
Output generation: plain-text digest, Skew-T plots, LLM-powered weather briefing via LangGraph.
Key exports: `format_digest`, `generate_all_skewts`, `run_digest`, `WeatherDigest`
→ Full doc: digest.md

### flight-weather-tracker-spec
Original requirements specification with phase roadmap, data source descriptions, algorithm details, and output format definitions.
→ Full doc: flight-weather-tracker-spec.md
