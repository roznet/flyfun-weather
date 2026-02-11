# Digest Layer

> Output generation: plain-text digest, Skew-T plots, and LLM-powered weather briefing

All modules in `src/weatherbrief/digest/`.

## Intent

Transform analysis results into actionable outputs. Three output types exist: plain-text (always), Skew-T plots (optional), and LLM digest (optional). Each is independent and failure-tolerant.

## Plain-Text Digest (`digest/text.py`)

Always generated. Formats `ForecastSnapshot` into console-readable text.

```python
text = format_digest(snapshot, target_time, output_paths=["data/..."])
```

Sections: header (route/date/alt), per-waypoint forecasts + analysis, model agreement summary, output files footer. Handles missing data gracefully throughout.

## Skew-T Plots (`digest/skewt.py`)

MetPy-based Skew-T log-P diagrams per waypoint/model combination.

```python
paths = generate_all_skewts(snapshot, target_time, output_dir)
```

- Plots: temperature (red), dewpoint (green), wind barbs, parcel profile, LCL marker
- Icing zone (0 to -20°C) shaded light blue
- Requires ≥ 3 pressure levels with temperature; dewpoint and wind optional
- Output: PNG at 150 DPI, 9×9 inches

## LLM Digest

Three-file architecture following a config-driven pattern:

### Config (`digest/llm_config.py`)

```python
config = load_digest_config("default")  # or "openai", or from env
llm = create_llm(config)  # → BaseChatModel via init_chat_model
prompt = config.load_prompt("briefer")  # → markdown string
```

- JSON configs in `configs/weather_digest/{name}.json`
- Resolution: explicit name → `WEATHERBRIEF_DIGEST_CONFIG` env → `"default"`
- `create_llm()` uses LangChain `init_chat_model(model, model_provider, temperature)` — no custom provider logic

### Context Assembly (`digest/prompt_builder.py`)

```python
context = build_digest_context(
    snapshot, target_time,
    text_forecasts=dwd_fcsts,      # optional
    previous_digest=prev_digest,    # optional, for trend
)
```

Builds structured text with sections:
1. `ROUTE / DATE / ALTITUDE` — route metadata
2. `=== QUANTITATIVE DATA ===` — per-waypoint surface, cruise-level, wind components, icing, clouds
3. `=== MODEL COMPARISON ===` — divergence per variable
4. `=== TEXT FORECASTS ===` — DWD German text (if available)
5. `=== PREVIOUS DIGEST ===` — prior assessment for trend (if available)

### LangGraph Pipeline (`digest/llm_digest.py`)

3-node sequential graph:

```
START → fetch_text → assemble → briefer → END
```

- **fetch_text**: calls `fetch_dwd_text_forecasts()`, returns None on failure
- **assemble**: calls `build_digest_context()` combining quant + text
- **briefer**: calls LLM with `with_structured_output(WeatherDigest)`, formats markdown

```python
result = run_digest(snapshot, target_time, config)
result["digest"]       # → WeatherDigest (Pydantic model)
result["digest_text"]  # → formatted markdown string
result["error"]        # → error message if LLM failed, else None
```

### WeatherDigest Model

Structured output with 11 fields:

| Field | Type | Content |
|-------|------|---------|
| `assessment` | `GREEN`/`AMBER`/`RED` | Go/no-go traffic light |
| `assessment_reason` | str | One sentence justification |
| `synoptic` | str | Large-scale pattern summary |
| `winds` | str | Wind at cruise + significant levels |
| `cloud_visibility` | str | Bases, tops, IMC risk |
| `precipitation_convection` | str | Rain/snow/CB risk |
| `icing` | str | Altitude bands + severity |
| `specific_concerns` | str | Route-specific: Alpine, foehn, valley fog |
| `model_agreement` | str | Where models agree/disagree |
| `trend` | str | How outlook compares to yesterday |
| `watch_items` | str | What to monitor next 24h |

### Markdown Output

`format_digest_markdown()` produces spec-compliant output with assessment icon, labeled sections, and separator lines. Saved to `data/digests/{target_date}/d-{N}_{fetch_date}/digest.md`.

### System Prompt

`configs/weather_digest/prompts/briefer_v1.md`: aviation weather briefer persona, instructs the LLM to translate German DWD text, use aviation terminology, be direct about uncertainty.

## Key Choices

- **LangGraph over plain function** — provides structured state management, easy node-level testing, and future extensibility (e.g., parallel text fetch + quant assembly)
- **Structured output via `with_structured_output()`** — Pydantic model enforced by the LLM provider, no manual JSON parsing
- **Config files, not code** — switching providers (Anthropic ↔ OpenAI) is a JSON change
- **Versioned prompts** — `briefer_v1.md` allows prompt iteration without code changes

## Gotchas

- LLM providers need API keys in environment (loaded via `.env` by `python-dotenv`)
- DWD text is in German — prompt instructs LLM to translate during synthesis
- `with_structured_output()` behavior varies by provider (tool-calling vs JSON mode)
- `DigestState` uses `total=False` TypedDict — all keys optional, access via `.get()`

## References

- Pipeline orchestration: `cli.py` `_run_llm_digest()`
- Config files: `configs/weather_digest/`
- Data models: [data-models.md](./data-models.md)
- Fetch sources: [fetch.md](./fetch.md)
- Analysis inputs: [analysis.md](./analysis.md)
