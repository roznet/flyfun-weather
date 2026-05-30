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

**Sounding analysis formatting** — for each waypoint with sounding data:
- Thermodynamic indices: freezing level, CAPE, LCL, K-index, total totals, precipitable water, lifted index, bulk shear
- Convective risk banner with severe modifiers
- Icing zones with type (RIME/MIXED/CLEAR), SLD flag, and altitude range
- Cloud layers with coverage category (SCT/BKN/OVC)
- Band comparisons: per-band worst-case icing/cloud across models with agreement indicators

## Skew-T Plots (`digest/skewt.py`)

MetPy-based Skew-T log-P diagrams per waypoint/model combination, plus a
*separate* companion hodograph PNG (the hodograph is its own plot now, not a
Skew-T inset).

```python
paths = generate_all_skewts(snapshot, target_time, output_dir)  # also emits companion hodographs
```

**Skew-T plot:**
- Temperature (red), dewpoint (green), wind barbs, parcel profile (dashed), LCL/LFC/EL markers
- CAPE/CIN shading (`skew.shade_cape` / `skew.shade_cin`) between parcel and environment profiles
- Analysis overlays: cloud layers, icing zones, and inversion layers drawn from the sounding analysis (`_draw_cloud_layers` / `_draw_icing_zones` / `_draw_inversion_layers`); fallback generic icing band (0 to -20°C) when no analysis zones
- Altitude labels on the right edge, cruise-altitude line, standard adiabats/mixing lines

**Companion hodograph** (`generate_hodograph`, standalone PNG via `_draw_hodograph_on_axes`): altitude-colored wind trace; requires wind data.

**Output:** PNG at 150 DPI. Skew-T figsize `(9, 5)`; hodograph `(6, 6)`.

- Requires >= 3 pressure levels with temperature; dewpoint and wind optional
- Uses `matplotlib.use("agg")` — required for worker thread compatibility (macOS backend crashes on non-main threads)

## LLM Digest

Three-file architecture following a config-driven pattern:

### Config (`digest/llm_config.py`)

```python
config = load_digest_config("default")  # or "openai", or from env
llm = create_llm(config)  # → BaseChatModel via init_chat_model
prompt = config.load_prompt("briefer", locale="fr", guidance_key="balanced")  # → markdown string
```

- JSON configs in `configs/weather_digest/{name}.json`
- Resolution: explicit name → `WEATHERBRIEF_DIGEST_CONFIG` env → `"default"`
- `create_llm()` uses LangChain `init_chat_model(model, model_provider, temperature)` — no custom provider logic
- `load_prompt()` injects locale content (from `prompts/locales/{locale}.md` frontmatter + body, replacing `{locale}` and vocabulary tokens) and the `{guidance}` placeholder (from `digest_guidance/{key}.md`). `DigestConfig` also carries a separate `translator` LLMConfig (default `claude-haiku-4-5`) used by DWD translation.

### Context Assembly (`digest/prompt_builder.py`)

```python
context = build_digest_context(
    snapshot, target_time,
    text_forecasts=text_fcsts,      # optional TextForecasts (NWS or DWD)
    previous_digest=prev_digest,    # optional, for trend
    route_advisories=manifest,      # optional RouteAdvisoriesManifest
    flight_rules="vfr_only",        # → pilot-capability line
    units_region="europe",          # visibility unit formatting
    dwd_translated=blocks,          # optional list[(DWDDayBlock, english)]
    dwd_is_synoptic_extract=False,  # framing for non-German routes
)
```

Builds structured text. Sections are appended only when their data is present:
1. Header: `ROUTE / DATE (with day-of-week) / BRIEFING ISSUED / ALTITUDE / PILOT CAPABILITY`
2. `=== QUANTITATIVE DATA ===` — per-waypoint surface, weather, cruise-level, wind components, then the per-waypoint sounding analysis (`_format_sounding_context`: thermo indices, icing zones type/SLD/risk, cloud layers, convective risk) and model divergence (`_format_divergence_context`: only moderate/poor agreement, "variable level(spread=...)") inline. There is no separate model-comparison or altitude-band-comparison section.
3. `=== ROUTE ADVISORIES ===` — deterministic hazard assessments (`_format_route_advisories_context`)
4. `=== METAR/TAF OBSERVATIONS ===` — D-0 only: corridor airports, flight categories, raw METARs, TAF trends
5. `=== SIGMETs ALONG ROUTE ===` — D-0 only, when `route_sigmets.count` > 0
6. `=== TEXT FORECASTS (...) ===` — translated DWD blocks (`_format_dwd_translated_context`, full or synoptic-extract framing) if available, else NWS AFD / raw DWD entries
7. `=== PREVIOUS DIGEST (for trend comparison) ===` — prior assessment/reason/synoptic/trend (if available)

### LangGraph Pipeline (`digest/llm_digest.py`)

Single-node graph — the heavy data prep happens *outside* the graph so only a
lightweight context string enters the traced state (keeps LangSmith payloads
~100 KB instead of ~30 MB):

```
START → briefer → END
```

`run_digest()` orchestrates three stages:
- **pre-graph (not traced)**: `_fetch_and_translate_text()` calls `fetch_text_forecasts(route)` (dispatches NWS vs DWD by route, None on failure) and, for European routes, translates DWD day-blocks via `translate_dwd_blocks()` (synoptic-extract mode when the route doesn't cross Germany). Then `build_digest_context()` assembles the context string.
- **graph (traced — lightweight state)**: `briefer_node` calls the LLM with `with_structured_output(WeatherDigest, include_raw=True)`, loading the system prompt via `config.load_prompt("briefer", locale=, guidance_key=)`, and captures token usage from the raw `AIMessage`.
- **post-graph (not traced)**: formats markdown; re-attaches `dwd_translated` to the result.

```python
result = run_digest(snapshot, target_time, config,
                    locale=, units_region=, guidance_key=, flight_rules=,
                    route_advisories=, previous_digest=)
result["digest"]            # → WeatherDigest (Pydantic model)
result["digest_text"]       # → formatted markdown string
result["llm_input_tokens"]  # → token usage (or None)
result["llm_output_tokens"]
result["dwd_translated"]    # → translated DWD blocks (attached post-graph)
result["diagnostic"]        # → typed Diagnostic if the LLM call failed, else None
                            #   (see weatherbrief.models.Diagnostic +
                            #    weatherbrief.digest.exceptions.classify_llm_exception)
```

`briefer_node` gates its failure log level by `diagnostic.level` so retryable
transients (rate-limited / overloaded) log at warning, not error.

### WeatherDigest Model

Structured output with 6 fields (the per-hazard fields were consolidated into
`synoptic`, which now carries winds/cloud/precip/icing inline):

| Field | Type | Content |
|-------|------|---------|
| `assessment` | `GREEN`/`AMBER`/`RED` | Go/no-go traffic light |
| `assessment_reason` | str | One sentence justification |
| `synoptic` | str | Large-scale pattern + the key hazards (winds, cloud/vis, precip, icing) that matter for this flight |
| `specific_concerns` | str | Route-specific: Alpine, foehn, valley fog, Channel crossings |
| `trend` | str | How outlook compares to yesterday |
| `watch_items` | str | What to monitor next 24h |

### Markdown Output

`format_digest_markdown(digest, snapshot)` produces output with assessment icon, labeled sections (SYNOPTIC / SPECIFIC CONCERNS / TREND / WATCH), and separator lines. Saved as `digest.md` inside the pack dir (`tasks/outputs.py`).

### DWD Synoptic Overview Side-Output

For Europe routes, the digest pipeline also fetches DWD German weather text, translates/extracts it via a cheap LLM (`digest/dwd_translate.py`, content-hash cached), and uses the result as briefer context. The translated entries are also persisted as `pack_dir/dwd_overview.json` (`tasks/outputs.py:_save_dwd_overview`) and exposed via `GET /packs/{ts}/dwd-overview`.

**Plumbing:** `dwd_translated` is intentionally kept out of the LangGraph state (commit 2589691d, to keep LangSmith traces small). `run_digest()` attaches it to the returned dict post-graph so `outputs.py` can save it — this contract was silently broken from 2589691d (Mar 2026) until commit 6d722099 (May 2026) restored it. If 404s reappear, that's the wire to check first.

**UI gating:** `renderDWDOverview` in `web/ts/managers/briefing-ui.ts` is admin-only (`isAdmin` flag from `/auth/me`). Non-admins never see the section to keep the briefing UI focused; admins see it for debugging/inspection.

### System Prompt

`configs/weather_digest/prompts/briefer_v1.md`: aviation weather briefer persona, instructs the LLM to handle both NWS AFD (English — synthesize synoptic/aviation sections) and DWD text (German — translate), use aviation terminology, be direct about uncertainty.

The prompt contains a `{guidance}` placeholder that is replaced at runtime with a guidance preset. This controls how the LLM interprets advisory severity when producing the GREEN/AMBER/RED assessment.

### Digest Guidance Presets (`configs/digest_guidance/`)

Three preset files control assessment calibration:

| Preset | File | Philosophy |
|--------|------|------------|
| **Conservative** | `conservative.md` | Single RED = strong signal toward RED. Multiple AMBERs push toward RED. Favours cautious interpretation. |
| **Balanced** | `balanced.md` | Single RED requires investigation, not auto no-go. Meteorological judgment weighs the combination. (Default) |
| **Tolerant** | `tolerant.md` | For IFR/FIKI-equipped. AMBER icing/IMC is routine. RED reserved for genuinely unmanageable conditions. |

`index.json` provides localised names and descriptions (en/fr/de/es) for the frontend.

Presets are stored per-profile in `settings_json.digest_guidance`. System templates map: VFR Only → conservative, IFR Conservative → balanced, IFR FIKI → tolerant.

The `WEATHERBRIEF_GUIDANCE_DIR` env var can override the guidance directory to allow updates without redeploying.

## Key Choices

- **LangGraph over plain function** — provides structured state management, easy node-level testing, and future extensibility (e.g., parallel text fetch + quant assembly)
- **Structured output via `with_structured_output()`** — Pydantic model enforced by the LLM provider, no manual JSON parsing
- **Config files, not code** — switching providers (Anthropic <-> OpenAI) is a JSON change
- **Versioned prompts** — `briefer_v1.md` allows prompt iteration without code changes

## Gotchas

- LLM providers need API keys in environment (loaded via `.env` by `python-dotenv`)
- Text forecasts are region-dependent: NWS AFD (English) for US routes, DWD (German) for European routes — prompt handles both
- `with_structured_output()` behavior varies by provider (tool-calling vs JSON mode)
- `DigestState` uses `total=False` TypedDict — all keys optional, access via `.get()`
- `matplotlib.use("agg")` must be called before `import matplotlib.pyplot` in skewt.py

## Email Delivery (`notify/`)

The `notify/` package handles email delivery of digest outputs:

- **`notify/email.py`** — `send_briefing_email(recipients, flight, pack, pack_dir, base_url="", smtp_config=None)`: sends a lightweight plain+HTML alternative email (assessment summary from `digest.json` + `route_advisories.json`) with a link to the full web briefing — no PDF attachment. SMTP settings from `SmtpConfig.from_env()` (reads `SMTP_*` env vars).
- **`notify/admin_email.py`** — `send_new_user_notification(user, db)`: sends admin notification on new user signup with HMAC-signed one-click approval URL (7-day expiry). Targets `ADMIN_EMAILS` env var; logs URL in dev mode.

**Email recipients**: the `/api/.../email` endpoint sends to the logged-in user's email (from the `users` table), not a global env var. This changed from the original `WEATHERBRIEF_EMAIL_RECIPIENTS` approach when multi-user support was added.

## References

- Pipeline orchestration: `pipeline.py` `execute_briefing()`
- Config files: `configs/weather_digest/`
- Report rendering: `report/render.py` — `render_html()`, `render_pdf()` via Jinja2 + WeasyPrint. GRAMET PDFs converted to PNG via PyMuPDF (`fitz`) for embedding as base64 data URIs in HTML reports.
- Data models: [data-models.md](./data-models.md)
- Fetch sources: [fetch.md](./fetch.md)
- Analysis inputs: [analysis.md](./analysis.md)
- Multi-user deployment: [multi-user-deployment.md](./multi-user-deployment.md)
