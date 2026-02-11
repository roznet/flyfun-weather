# Architecture

> System overview, data pipeline, CLI, storage layout, and phase roadmap

## Intent

WeatherBrief produces daily aviation weather assessments for a planned European GA cross-country flight, tracking conditions from D-7 through D-0. It fetches quantitative data from multiple NWP models, performs aviation-specific analysis, and generates both human-readable text digests and LLM-powered briefings.

## Pipeline

```
CLI args → RouteConfig (from YAML or inline ICAOs)
    ↓
OpenMeteoClient.fetch_all_models()  (per waypoint, per model)
    ↓
list[WaypointForecast]
    ↓
_analyze_waypoint()  (per waypoint)
├→ compute_wind_components()
├→ assess_icing_profile()
├→ estimate_cloud_layers()
└→ compare_models()
    ↓
ForecastSnapshot  (root object, saved as JSON)
    ↓
Optional outputs:
├→ GRAMET cross-section (Autorouter API → PNG)
├→ Skew-T plots (MetPy → PNG per waypoint/model)
├→ LLM digest (LangGraph: DWD text + quant → WeatherDigest → Markdown)
    ↓
format_digest() → plain-text console output
```

## Package Layout

```
src/weatherbrief/
├── models.py          # All Pydantic v2 data models
├── config.py          # Route YAML loading
├── airports.py        # ICAO → lat/lon via euro_aip
├── cli.py             # Entry point, pipeline orchestration
├── fetch/
│   ├── variables.py   # Model endpoints, API parameters
│   ├── open_meteo.py  # Open-Meteo client
│   ├── dwd_text.py    # DWD synoptic text forecasts
│   └── gramet.py      # Autorouter GRAMET
├── analysis/
│   ├── wind.py        # Headwind/crosswind decomposition
│   ├── icing.py       # Icing risk by pressure level
│   ├── clouds.py      # Cloud layer estimation from RH
│   └── comparison.py  # Multi-model divergence scoring
├── digest/
│   ├── text.py        # Plain-text digest formatter
│   ├── skewt.py       # Skew-T diagram generation
│   ├── llm_config.py  # LLM config schema + factory
│   ├── llm_digest.py  # LangGraph digest pipeline
│   └── prompt_builder.py  # Context assembly for LLM
└── storage/
    └── snapshots.py   # JSON save/load/list
```

## Storage Layout

```
data/
├── forecasts/{target_date}/d-{N}_{fetch_date}/snapshot.json
├── gramet/{target_date}/d-{N}_{fetch_date}/gramet.png
├── skewt/{target_date}/d-{N}_{fetch_date}/{ICAO}_{model}.png
└── digests/{target_date}/d-{N}_{fetch_date}/digest.md

config/routes.yaml                          # Named route definitions
configs/weather_digest/                     # LLM digest configs
├── default.json                            # Anthropic config
├── openai.json                             # OpenAI variant
└── prompts/briefer_v1.md                   # System prompt
```

## CLI

```bash
# Fetch with analysis + text digest
python -m weatherbrief fetch EGTK LFPB LSGS --db $DB --date 2026-02-17

# Named route with all outputs
python -m weatherbrief fetch --route egtk_lsgs --db $DB --date 2026-02-17 \
  --gramet --skewt --llm-digest

# Config switching
python -m weatherbrief fetch ... --llm-digest --digest-config openai

# List available routes
python -m weatherbrief routes
```

Key flags: `--alt` (cruise ft), `--time` (target hour UTC), `--duration` (hours), `--models` (csv), `--gramet`, `--skewt`, `--llm-digest`, `--digest-config`.

## Key Choices

- **Pydantic v2 throughout** — validation, serialization, JSON round-trip all free.
- **Graceful degradation** — GRAMET/Skew-T/LLM/DWD failures logged but don't halt pipeline. Missing model data doesn't block other models.
- **Per-waypoint track** — wind components use circular mean of incoming/outgoing leg bearings, not a single route heading.
- **LangChain `init_chat_model`** — single factory for any provider (Anthropic, OpenAI). No custom registry.
- **`python-dotenv`** — `.env` loaded in CLI entry point for API keys.

## Dependencies

| Package | Purpose |
|---------|---------|
| `pydantic>=2.0` | Data models |
| `requests` | HTTP API calls |
| `pyyaml` | Route config |
| `metpy`, `matplotlib`, `numpy` | Skew-T plots |
| `langchain`, `langgraph` | LLM digest orchestration |
| `langchain-anthropic`, `langchain-openai` | LLM providers |
| `python-dotenv` | Environment loading |
| `euro-aip` (local) | Airport DB, Autorouter credentials |

## Phase Roadmap

| Phase | Status | Summary |
|-------|--------|---------|
| 1 | Done | Open-Meteo fetch, wind/icing/cloud analysis, JSON snapshots, text digest |
| 2 | Done | Route rework (YAML, per-waypoint track), GRAMET, Skew-T plots |
| 3 | Done | DWD text forecasts, LLM digest (LangGraph + structured output) |
| 4 | Planned | Ensemble & model comparison refinement |
| 5 | Planned | Polish, Autorouter METAR/TAF, MCP server, notifications |

## References

- Spec: [flight-weather-tracker-spec.md](./flight-weather-tracker-spec.md)
- Data models: [data-models.md](./data-models.md)
- Fetch: [fetch.md](./fetch.md)
- Analysis: [analysis.md](./analysis.md)
- Digest: [digest.md](./digest.md)
