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
    altitude_table=table,           # optional AltitudeTableResult → OPTIONS TO IMPROVE (Altitude)
    confidence_note=note,           # optional long-range FORECAST CONFIDENCE note
    flight_rules="vfr_only",        # → pilot-capability line
    units_region="europe",          # visibility unit formatting
    dwd_translated=blocks,          # optional list[(DWDDayBlock, english)]
    dwd_is_synoptic_extract=False,  # framing for non-German routes
    longrange=False,                # True → coarse quant + drop raw text forecasts
)
```

Builds structured text. Sections are appended only when their data is present:
1. Header: `ROUTE / DATE (with day-of-week) / BRIEFING ISSUED / ALTITUDE / PILOT CAPABILITY`
2. `=== FORECAST CONFIDENCE ===` — long-range only: the code-computed `confidence_note` (when the high-res GRIB first covers this flight). See *Long-Range Outlook* below.
3. `=== OPTIONS TO IMPROVE (advice only — do NOT change the assessment) ===` — deterministic, consolidated block (`_format_options_to_improve_context`, #330). Two sub-parts: an **Altitude** sub-block (`_format_altitude_options_context`) that states the planned-altitude advisory picture and what the best-below / best-above-cruise alternatives improve/worsen via the shared `diff_altitude_rows` primitive — it OWNS the altitude axis because only it shows the cross-advisory trade-off; and an optional **Tactical** sub-block (`_format_tactical_mitigations_context`) listing each advisory's non-altitude `aggregate_mitigations` (ROUTE_POSITION/TIMING, `kind != ALTITUDE`) by advisory name. Per-advisory ALTITUDE mitigations are dropped from the digest (they'd double-narrate the altitude block and hide its worsens-Y trade-off; they stay on UI/MCP). The Tactical sub-part is omitted when no advisory has a non-altitude mitigation, and the whole section is omitted when both sub-parts are empty. The LLM only *phrases* the structure — it never invents the numbers and these are advice only (a RED advisory with a mitigation is still RED). See [advisories.md](./advisories.md) for the table and the `Mitigation` model.
4. `=== QUANTITATIVE DATA ===` — per-waypoint surface, weather, cruise-level, wind components, then the per-waypoint sounding analysis (`_format_sounding_context`: thermo indices, icing zones type/SLD/risk, cloud layers, convective risk) and model divergence (`_format_divergence_context`: only moderate/poor agreement, "variable level(spread=...)") inline. There is no separate model-comparison or altitude-band-comparison section. **Long-range** replaces this with a coarser `_build_coarse_quant` block and drops the raw text-forecast section (NWS AFD / non-covering DWD describe a different day at that range).
5. `=== ROUTE ADVISORIES ===` — deterministic hazard assessments (`_format_route_advisories_context`)
6. `=== METAR/TAF OBSERVATIONS ===` — D-0 only: corridor airports, flight categories, raw METARs, TAF trends
7. `=== SIGMETs ALONG ROUTE ===` — D-0 only, when `route_sigmets.count` > 0
8. `=== TEXT FORECASTS (...) ===` — translated DWD blocks (`_format_dwd_translated_context`, full or synoptic-extract framing) if available, else NWS AFD / raw DWD entries
9. `=== PREVIOUS DIGEST (for trend comparison) ===` — prior assessment/reason/synoptic/trend (if available)

### LangGraph Pipeline (`digest/llm_digest.py`)

Single-node graph — the heavy data prep happens *outside* the graph so only a
lightweight context string enters the traced state (keeps LangSmith payloads
~100 KB instead of ~30 MB):

```
START → briefer → END
```

`run_digest()` orchestrates three stages:
- **pre-graph (not traced)**: `_fetch_and_translate_text()` calls `fetch_text_forecasts(route)` (dispatches NWS vs DWD by route, None on failure) and, for European routes, translates DWD day-blocks via `translate_dwd_blocks()` (synoptic-extract mode when the route doesn't cross Germany). Then `build_digest_context()` assembles the context string.
- **graph (traced — lightweight state)**: `briefer_node` calls the LLM with `with_structured_output(schema, include_raw=True)` — `schema`/prompt/model switch by regime (`WeatherDigest` + `briefer` vs `LongRangeDigest` + `briefer_longrange`; see *Long-Range Outlook*) — loading the system prompt via `config.load_prompt(prompt_key, locale=, guidance_key=)`, and captures token usage from the raw `AIMessage`. The invocation runs with a **pre-generated `run_id`** (`config={"run_id": uuid4()}`) so we own the LangSmith trace id rather than letting LangChain auto-generate-and-discard it. The id is returned as `result["digest_trace_id"]` and persisted on the pack (see below).
- **post-graph (not traced)**: formats markdown; re-attaches `dwd_translated` to the result.

```python
result = run_digest(snapshot, target_time, config,
                    locale=, units_region=, guidance_key=, flight_rules=,
                    route_advisories=, previous_digest=)
result["digest"]            # → WeatherDigest (Pydantic model)
result["digest_text"]       # → formatted markdown string
result["llm_input_tokens"]  # → token usage (or None); INCLUDES cached tokens
result["llm_output_tokens"]
result["llm_cache_read_tokens"]   # → prompt-cache split, subsets of
result["llm_cache_write_tokens"]  #   llm_input_tokens — never add them to it
result["dwd_translated"]    # → translated DWD blocks (attached post-graph)
result["digest_trace_id"]   # → controlled LangSmith root run id (str UUID)
result["diagnostic"]        # → typed Diagnostic if the LLM call failed, else None
                            #   (see weatherbrief.models.Diagnostic +
                            #    weatherbrief.digest.exceptions.classify_llm_exception)
```

### Digest run id → thumb feedback (LangSmith, issue #244)

The digest's LangSmith run id is threaded out and persisted so a later pilot
rating can be attached to the run that produced the digest:

`run_digest` (generates `run_id`) → `run_llm_digest` (`DigestResult.digest_trace_id`)
→ `pipeline.BriefingResult.digest_trace_id` → `_build_pack_meta`
(`BriefingPackMeta.digest_trace_id`) → `BriefingPackRow.digest_trace_id`
(migration `066`, nullable `String(36)`). NULL on the provisional pack row
(digest hasn't run yet) and for legacy packs created before #244.

When a 👍/👎 lands at `POST /api/feedback` with `category="digest_rating"`,
`submit_feedback` looks up the pack by `(flight_id, pack_timestamp)`, reads its
`digest_trace_id`, and calls `digest/langsmith_feedback.py:push_digest_thumb_feedback`
→ `langsmith.Client().create_feedback(run_id, key="user_thumb", score=1.0/0.0,
comment=…)`. **Fire-and-forget**: a no-op when LangSmith isn't configured
(`LANGCHAIN_API_KEY`/`LANGSMITH_API_KEY` unset — local/dev) or the pack has no
trace id, and it never raises into the user's POST (the DB `feedback` row is
written regardless).

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

### Long-Range Outlook (dual horizons)

Beyond the **ECMWF GRIB horizon** (`ecmwf_grib_horizon_days()` — 168h → 7 days, read live from `fetch/freshness/registry.py:max_horizon`, *not* hard-coded) the high-resolution GRIB and ICON soundings no longer reach the flight date; only global models (ECMWF/GFS via Open-Meteo, GEM) remain. `is_long_range(snapshot)` (`snapshot.days_out > horizon`) flips the digest into a **trimmed, cheaper long-range regime**:

- A distinct structured model, `LongRangeDigest`, replaces the GREEN/AMBER/RED verdict with a **soft tendency** — never a go/no-go. Fields: `outlook` (`TRENDING_SETTLED` / `MIXED_SIGNALS` / `TRENDING_UNSETTLED`), `outlook_reason`, `synoptic`, `model_agreement`, `trend`, `watch_items`. At this range confidence is driven by how well the remaining global models *agree*, not any single value — hence the explicit `model_agreement` field instead of an assessment.
- A cheaper model + a different prompt: `briefer_node` picks `create_llm(config, longrange=…)` and the `briefer_longrange` prompt (`configs/weather_digest/prompts/briefer_longrange_v1.md`).
- `build_confidence_note()` is **code-computed, not asked of the LLM** (which is unreliable at date arithmetic): it derives from the registry the date the first full-horizon ECMWF GRIB run will cover the flight and phrases the `=== FORECAST CONFIDENCE ===` block ("high-resolution guidance first covers this flight from <date>").
- `run_digest(..., longrange=None)` auto-detects via `is_long_range`; pass an explicit bool to force a regime (used by `scripts/run_longrange_eval.py`).
- Output: `format_longrange_markdown()` renders the outlook icon/label (see below) instead of the traffic-light icon.

**Canonical outlook labels live in `digest/outlook.py`** (a deliberately import-light module: `OUTLOOK_LABELS` plain-text + `OUTLOOK_ICONS` emoji) so `notify/email.py` can render the same strings without importing the LangGraph-laden `llm_digest`. The web UI keeps its own localised labels in the i18n catalogs.

**Pack persistence:** migration `068` adds nullable `outlook` / `outlook_reason` columns to `briefing_packs`, **mutually exclusive with `assessment`** — NULL for short-range packs (which use the traffic light) and legacy packs. The flight list and briefing page show the outlook tendency in place of a verdict.

> **Three distinct horizons — keep them separate.** Since the pending-coverage
> feature (PR #362) the old "booking gate == dual-model horizon" equivalence no
> longer holds. There are now three boundaries:
> 1. **Booking cap = `MAX_BOOKING_LEAD_DAYS` (180 days)** — the only save-time
>    gate (`api/flights.py:_reject_if_beyond_booking_cap`); an absurdity guard,
>    not a forecast boundary.
> 2. **Forecast-coverage horizon = `dual_model_horizon_days()` (~9 days)** — last
>    lead day both global models reach. A flight saved beyond this is allowed but
>    **pending coverage** (`is_beyond_forecast_horizon` / `coverage_start_date`
>    in `fetch/variables.py`); it briefs automatically once it crosses in.
> 3. **Full-GRIB-briefing horizon = `ecmwf_grib_horizon_days()` (~7 days)** — the
>    outlook-regime boundary described in this section (168h ECMWF GRIB).
>
> This section's outlook boundary is #3. See the project memory note
> "Long-range outlook + dual horizons" and "Pending-coverage future flights".

### Deterministic Guardrails (`digest/guardrails.py`)

A **prompt/approach-independent safety layer** (`run_guardrails(context, output) → list[Violation]`, pure, no LLM) that verifies promises the briefer prompt already makes, by checking the structured output against the raw context string it was generated from. Checks: **coordinate leak** (raw `58°N`/`8°W` that should have been converted to plain geography), **fabricated sources** (citing DWD/NWS/AFD when no `=== TEXT FORECASTS` section was provided), **number traceability** (every hPa/ft/FL figure in the output must fuzzily trace to a number in the context), and **structure** (enum assessment, all fields present, per-field sentence/length bounds). Currently used to **gate CI on recorded eval output** (`tests/test_digest_assertions.py`); designed to also run against a live subset or in front of user-facing output. Bounds were recalibrated to real output in #253.

### Markdown Output

`format_digest_markdown(digest, snapshot)` produces output with assessment icon, labeled sections (SYNOPTIC / SPECIFIC CONCERNS / TREND / WATCH), and separator lines. Saved as `digest.md` inside the pack dir (`tasks/outputs.py`).

### DWD Synoptic Overview Side-Output

For Europe routes, the digest pipeline also fetches DWD German weather text, translates/extracts it via a cheap LLM (`digest/dwd_translate.py`, content-hash cached), and uses the result as briefer context. The translated entries are also persisted as `pack_dir/dwd_overview.json` (`tasks/outputs.py:_save_dwd_overview`) and exposed via `GET /packs/{ts}/dwd-overview`.

**Plumbing:** `dwd_translated` is intentionally kept out of the LangGraph state (commit 2589691d, to keep LangSmith traces small). `run_digest()` attaches it to the returned dict post-graph so `outputs.py` can save it — this contract was silently broken from 2589691d (Mar 2026) until commit 6d722099 (May 2026) restored it. If 404s reappear, that's the wire to check first.

**UI gating:** `renderDWDOverview` in `web/ts/managers/briefing-ui.ts` is admin-only (`isAdmin` flag from `/auth/me`). Non-admins never see the section to keep the briefing UI focused; admins see it for debugging/inspection.

### System Prompt

The shipped configs (`default.json`, `openai.json`) point `briefer` at `configs/weather_digest/prompts/briefer_v2.md` (the `DigestConfig.prompts.briefer` code default is still `briefer_v1.md`, but the JSON overrides it). It sets an aviation weather briefer persona addressing a competent pilot (do NOT over-simplify), instructs the LLM to handle both NWS AFD (English — synthesize synoptic/aviation sections) and DWD text (German — translate), use aviation terminology, be direct about uncertainty. Avoids exposing internal section names (e.g. "ALTITUDE OPTIONS") in prose and renders numbered `watch_items` as a list. `briefer_longrange_v1.md` is the trimmed long-range counterpart (outlook tendency + model-agreement framing; see *Long-Range Outlook*).

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
- **Config files, not code** — switching providers (Anthropic <-> OpenAI) is a JSON change.
  `_system_content()` is gated on the resolved provider for exactly this reason: it attaches
  an Anthropic-only `cache_control` breakpoint, which must not reach `ChatOpenAI`.
- **Prompt caching on the system block** — the tool schema plus system prompt (~4.7k tokens,
  ~40% of a request) are identical across digests sharing a (locale, guidance) pair, and
  Anthropic renders `tools` → `system` → `messages`, so one breakpoint covers both. 1-hour
  TTL, chosen against measured inter-digest gaps; skipped for long-range, whose Haiku 4.5
  prompt is below that model's 4096-token cacheable minimum. The TTL is **not** a local
  constant — it is `costs.DIGEST_CACHE_TTL`, and the rate card's write multiplier is derived
  from it, so the two cannot drift. See `designs/cost-attribution-design.md` for how the
  read/write split is priced.
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
