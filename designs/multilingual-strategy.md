# Multilingual Strategy

> Making WeatherBrief available in French, German, and Spanish alongside English

## Scope

Target languages: **English** (existing), **French** (fr), **German** (de), **Spanish** (es).

This document covers: the web app, the iOS companion app, the backend (LLM digests, advisories, reports, emails), and static documentation. It identifies the content categories, evaluates i18n approaches for each, and proposes an implementation strategy.

---

## Content Inventory

The app has **five fundamentally different types** of translatable content, each with different constraints:

| Category | Volume | Source | Update Frequency | Translation Approach |
|----------|--------|--------|-------------------|---------------------|
| **A. UI chrome** | ~800 strings (web) + ~50 (iOS) | Hardcoded in TS/HTML/Swift | Changes with releases | Standard i18n framework |
| **B. Structured metadata** | ~400 strings | JSON catalogs + Python code | Occasional | Translated data files |
| **C. LLM-generated prose** | ~6 fields per briefing | LLM output | Every briefing refresh | LLM generates in target language |
| **D. Deterministic detail text** | ~50 template strings | Python advisory evaluators | Occasional | Server-side gettext/i18n |
| **E. Documents & reports** | HTML template + emails + help page | Jinja2/HTML | Rare | Translated templates |

---

## Category A: UI Chrome (Web + iOS)

### Web App — Current State

- **No i18n framework** — all ~800 strings are hardcoded across 7 HTML files and ~74 TypeScript files
- Rendering via `innerHTML` template literals (no React/Vue)
- Date formatting hardcoded: `toLocaleDateString('en-GB', ...)`
- `<html lang="en">` hardcoded

### Web App — Recommended Approach

**Option 1: Lightweight key-value i18n (Recommended)**

Use a minimal i18n library (or build a ~50-line helper) that loads JSON translation files:

```
web/ts/i18n/
├── i18n.ts          # t('key'), setLocale(), getLocale()
├── locales/
│   ├── en.json      # { "nav.flights": "Flights", "advisory.noData": "No data", ... }
│   ├── fr.json
│   ├── de.json
│   └── es.json
```

```typescript
// i18n.ts — core (~40 lines)
let locale = 'en';
let messages: Record<string, string> = {};

export function t(key: string, params?: Record<string, string | number>): string {
  let msg = messages[key] ?? key;
  if (params) {
    for (const [k, v] of Object.entries(params)) {
      msg = msg.replace(`{${k}}`, String(v));
    }
  }
  return msg;
}

export async function setLocale(lang: string) { ... }
```

Then replace hardcoded strings:
```typescript
// Before
el.innerHTML = 'No assessment available';
// After
el.innerHTML = t('briefing.noAssessment');
```

**Why not i18next or similar?** The app is vanilla TS with no framework. A heavyweight library adds complexity for marginal benefit. A simple key-value system with parameter interpolation covers all needs. If the project later moves to React/Vue, migration to i18next is trivial.

**Option 2: Build-time locale bundles**

Generate per-locale JS bundles at build time (esbuild can inline JSON). Eliminates runtime fetch but means page reload to switch language. Reasonable trade-off for this app since language switching mid-session is rare.

**Recommendation:** Option 1 (runtime JSON loading). It's simpler to implement incrementally, allows language switching without reload, and the JSON files are easy to hand off to translators.

### HTML Files

The 7 HTML files contain significant hardcoded text (especially `help.html` at ~315 lines of documentation). Two approaches:

**Option A: Move to server-side rendered templates (Recommended)**

FastAPI already serves static files. Add Jinja2 rendering for HTML pages with `{{ t('key') }}` calls, detecting locale from cookie/header. This handles the `help.html` documentation naturally.

**Option B: DOM-based replacement at page load**

Keep HTML as-is, add `data-i18n="key"` attributes, and have JS replace text on load. Works but causes a flash of untranslated content and doesn't handle the help page well.

**Recommendation:** Option A for the help page and settings page (heavy static text); DOM `t()` calls for dynamically generated content in TypeScript.

### iOS App — Current State

- MVVM architecture with SwiftUI
- ~50 hardcoded strings across Views, Models, Services
- **Already configured for String Catalogs** (`LOCALIZATION_PREFERS_STRING_CATALOGS = YES`) but none created
- No `.strings` or `.xcstrings` files exist

### iOS App — Recommended Approach

Use **Xcode String Catalogs** (`.xcstrings`) — this is Apple's modern localization system (Xcode 15+):

1. Create `Localizable.xcstrings`
2. SwiftUI `Text("key")` views already participate in localization automatically
3. For non-Text contexts, use `String(localized:)`
4. Xcode's export/import workflow generates XLIFF files for translators

This is the standard Apple approach and the project is already configured for it. The ~50 strings make this a manageable task.

### Language Selection & Persistence

**Where to store the user's language preference:**

| Option | Pros | Cons |
|--------|------|------|
| **Browser `Accept-Language` header** | Zero config, respects OS setting | Can't override, proxies may strip |
| **User preference in DB** | Persists across devices, backend can use it | Requires login to take effect |
| **localStorage + cookie** | Works before login, immediate | Lost on clear, doesn't sync across devices |
| **DB preference + localStorage fallback** | Best of both | Slightly more complex |

**Recommendation:** DB preference (in existing `user_preferences` table) + localStorage fallback for pre-login pages. The locale is sent to the backend via `Accept-Language` header (set by the frontend based on preference) so the API can return localized content.

---

## Category B: Structured Metadata

### Metrics Catalog (`metrics-catalog.json`)

~50-60 metrics with 5-8 translatable fields each (name, vibe, primary_goal, best_used_for, limitations, theory, threshold labels+meanings). Total: ~300-400 strings.

**Options:**

1. **Separate locale files** (`metrics-catalog.fr.json`, etc.) — complete copies per language. Simple but high duplication of non-text fields (units, IDs, numeric values).

2. **Translation overlay** — keep one canonical file with English, load a `metrics-catalog.translations.json` with just the translatable fields per locale. Merge at load time.

3. **Inline multi-language** — each translatable field becomes `{ "en": "...", "fr": "...", "de": "...", "es": "..." }`. Bloats the file but keeps everything together.

**Recommendation:** Option 2 (translation overlay). The canonical file stays clean, translators work with a focused file, and the merge is trivial.

### Advisory Catalog Entries

13 advisories with `name`, `short_description`, `description`, plus ~100 parameter `label`/`description` strings. Currently hardcoded in Python evaluator classes.

**Options:**

1. **Python gettext** — wrap strings in `_()`, extract with `xgettext`, translate in `.po` files. Standard but heavyweight for ~150 strings.

2. **JSON translation file** — `advisory-translations/{locale}.json` keyed by advisory ID. Load at API response time, merge into catalog entries before sending to clients.

3. **Translate in evaluator code** — `catalog_entry(locale)` returns locale-specific metadata. Requires passing locale through the pipeline.

**Recommendation:** Option 2 (JSON translation file). Advisory catalog entries are API responses — the backend can merge translations before sending. This keeps evaluator code clean and translations manageable.

```json
// advisory-translations/fr.json
{
  "airport_wind": {
    "name": "Vent Aéroport",
    "short_description": "Vent traversier et rafales aux aéroports",
    "description": "Évalue le vent traversier..."
  }
}
```

---

## Category C: LLM-Generated Prose (Digests)

This is the most interesting and impactful category. The LLM digest produces ~6 prose fields per briefing (synoptic, specific_concerns, trend, watch_items, etc.).

### Options

**Option 1: Generate directly in target language (Recommended)**

Modify the LLM system prompt to include a language instruction:

```markdown
## Output Language
Produce all text fields in {language}. Use standard aviation terminology
in {language} where established terms exist. For terms with no standard
translation (e.g., METAR, TAF, SIGMET, NOTAM), keep the English/ICAO term.
```

**Pros:**
- Single LLM call per briefing (no extra cost)
- Native-quality output — LLMs excel at generating in European languages
- Aviation terminology in French/German/Spanish is well-established (ICAO standards)
- No post-processing translation step

**Cons:**
- Need locale-specific prompt variants (or parameterized prompt)
- Output quality should be validated per language
- Some aviation terms are universally English (METAR, TAF, NOTAM, ATIS) — prompt must clarify

**Option 2: Generate in English, then translate**

Use a secondary LLM call (cheap model like Haiku) to translate the English digest. Similar to the existing DWD translation pipeline.

**Pros:**
- English generation is well-tested
- Translation is a simpler task for the LLM
- Can cache translations

**Cons:**
- Double LLM cost per briefing
- Added latency (~2-5s per translation)
- Translation may lose aviation nuance
- More complex pipeline

**Option 3: Generate both English + target language**

Ask the LLM to output both languages in one call (structured output with `synoptic_en`, `synoptic_fr`, etc.).

**Pros:**
- Single call, both languages available
- English always available as reference

**Cons:**
- Doubles output token count (cost)
- May degrade quality by splitting attention
- Rigid — can't add languages without schema change

**Recommendation:** Option 1 (direct generation). The prompt already handles DWD German text input gracefully. Adding a language parameter is minimal work. A single base prompt template with per-locale snippets:

```
configs/weather_digest/prompts/
├── briefer_v1.md              # Shared base template with {locale} placeholder
└── locales/
    ├── en.md                  # EN defaults (vocabulary only, no body)
    ├── fr.md                  # French: language instruction + vocabulary
    ├── de.md                  # German: language instruction + vocabulary
    └── es.md                  # Spanish: language instruction + vocabulary
```

Each locale file has YAML-like frontmatter for vocabulary tokens (`none_word`, `uncertainty_phrase`, `dwd_label`, `aviation_terms_note`) and an optional body for the language instruction paragraph. The base template uses `{locale}` and token placeholders, resolved at load time alongside `{guidance}`.

### Aviation Terminology Considerations

| Term | French | German | Spanish | Keep English? |
|------|--------|--------|---------|---------------|
| METAR | METAR | METAR | METAR | Yes (ICAO) |
| TAF | TAF | TAF | TAF | Yes (ICAO) |
| CAPE | CAPE | CAPE | CAPE | Yes (standard) |
| Icing | Givrage | Vereisung | Engelamiento | Translate |
| Freezing level | Isotherme 0°C | Nullgradgrenze | Nivel de congelación | Translate |
| Crosswind | Vent traversier | Seitenwind | Viento cruzado | Translate |
| Cloud base | Base des nuages | Wolkenuntergrenze | Base de nubes | Translate |
| VFR/IFR | VFR/IFR | VFR/IFR | VFR/IFR | Yes (ICAO) |
| Flight level | Niveau de vol | Flugfläche | Nivel de vuelo | Translate |

General rule: ICAO standard abbreviations stay in English. Descriptive terms get translated.

---

## Category D: Deterministic Detail Text

Advisory evaluators generate ~50 template strings like:
- `"No significant convective activity"`
- `"Freezing level above terrain"`
- `"Cloud tops reachable (max {top}ft, ceiling {ceiling}ft)"`
- `"Smooth ride expected"`

### Options

**Option 1: Python gettext with .po files**

Standard approach. Wrap strings in `_()`, extract, translate.

```python
# Before
detail = "No significant convective activity"
# After
detail = _("No significant convective activity")
```

**Pros:** Industry standard, good tooling, handles plurals
**Cons:** Heavyweight for ~50 strings, .po files are developer-unfriendly

**Option 2: JSON translation lookup (Recommended)**

Similar to advisory catalog translations. A helper function looks up the string by key:

```python
# translations.py
def t(key: str, locale: str, **kwargs) -> str:
    msg = TRANSLATIONS[locale].get(key, TRANSLATIONS['en'][key])
    return msg.format(**kwargs) if kwargs else msg

# In evaluator
detail = t("convective.no_activity", locale)
# or with params
detail = t("cloud_top.reachable", locale, top=max_top, ceiling=ceiling)
```

**Pros:** Simple, JSON-based, easy for translators, no build step
**Cons:** Need to pass `locale` through the evaluation pipeline

**Option 3: Post-hoc translation of detail strings**

Keep English detail text, translate at the API response level.

**Pros:** No changes to evaluator code
**Cons:** Fragile (free-text translation), slower, costly

**Recommendation:** Option 2. The advisory detail strings are short, template-based, and finite. A JSON file per locale with format-string patterns is clean and maintainable. The locale needs to be threaded through `evaluate_all()` → each evaluator.

### Implementation Detail

```python
# src/weatherbrief/i18n/
├── __init__.py          # t(key, locale, **kwargs), load_translations()
└── locales/
    ├── en.json          # { "advisory.convective.no_activity": "No significant convective activity", ... }
    ├── fr.json
    ├── de.json
    └── es.json
```

The `RouteContext` (currently frozen dataclass) gains a `locale: str = "en"` field, threaded from the user's preference through the API.

---

## Category E: Documents, Reports, and Emails

### HTML/PDF Reports (`report/templates/briefing.html`)

The Jinja2 template has ~15 hardcoded section headers ("METAR/TAF Observations", "Route Advisories", "Skew-T Soundings", "Model Comparison").

**Approach:** Pass a locale-specific translation dict to the Jinja2 context. The template uses `{{ t.section_advisories }}` instead of hardcoded strings.

### Email Templates (`notify/email.py`, `notify/admin_email.py`)

- Briefing email: subject line + HTML body with section headers
- Admin email: new user notification + welcome email

**Approach:** Email locale comes from the recipient's preference. Create locale-specific email template strings (same JSON approach as Category D). Admin emails can stay English-only (internal).

### Help Page (`web/help.html`)

~315 lines of user documentation. This is the single largest block of translatable prose.

**Options:**

1. **Separate HTML files** (`help.fr.html`, `help.de.html`, `help.es.html`) — simple, full control, but 4x maintenance burden.

2. **Markdown source + build step** — write help in Markdown per locale, render to HTML at build time. Easier for translators.

3. **Server-side Jinja2 with i18n blocks** — single template with `{% if locale == 'fr' %}...{% endif %}` blocks. Gets messy fast.

4. **CMS / external docs** — host documentation externally (Notion, GitBook). Over-engineered for this scale.

**Recommendation:** Option 1 (separate HTML files). The help page is essentially a static document. Having separate files per locale is the simplest approach and gives translators full context. The help content changes rarely.

---

## Architecture: How Locale Flows Through the System

```
User sets language preference
  → Stored in user_preferences table (DB) + localStorage
  → Frontend sends Accept-Language header on API calls
  → Cookie/header also used for pre-login pages

API receives request with locale
  → parse_locale(request) → "fr" | "de" | "es" | "en"

For briefing generation:
  → BriefingOptions gains locale field
  → Pipeline passes locale to:
      → LLM digest (selects prompt variant)
      → Advisory evaluation (detail text translation)
      → Report rendering (template translations)

For API responses:
  → Advisory catalog entries: merge translations from JSON
  → Digest JSON: already in target language (generated that way)
  → Progress stage labels: translated from JSON

For web app:
  → t() function loads locale JSON, replaces UI strings
  → HTML lang attribute set dynamically
  → Date/number formatting uses Intl API with locale

For iOS app:
  → String Catalog provides UI translations
  → API responses already come in the right language
```

---

## Key Design Decisions

### 1. Where does translation happen?

| Content | Translated where | Rationale |
|---------|-----------------|-----------|
| UI chrome | Client (browser/iOS) | Standard i18n pattern, no server round-trip |
| Advisory catalog metadata | Server (API response) | Merged before sending, client doesn't need translation files |
| Advisory detail text | Server (at evaluation time) | Generated server-side, locale must be available |
| LLM digest | Server (at generation time) | LLM generates in target language directly |
| Metrics catalog | Client (overlay merge) | Already loaded client-side, overlay is small |
| Reports/emails | Server (at render time) | Jinja2 already used |

### 2. What about existing briefings?

Briefings already generated and stored on disk are in English. Options:
- **Re-generate on demand:** Re-run LLM digest with new locale. Expensive.
- **Translate on read:** Use a cheap LLM to translate stored digests. Adds latency.
- **Accept English for historical data:** Simplest. New briefings use the user's locale.

**Recommendation:** Accept English for historical briefings. New briefings use the user's locale. Optionally, add a "Translate" button that triggers on-demand translation for old digests.

### 3. What if the LLM generates poor translations?

Risk: LLM may use incorrect aviation terminology in the target language.

**Mitigation:**
- Include a terminology glossary in the prompt (key aviation terms per language)
- Have native-speaking pilots review initial outputs
- Keep English available as a fallback (toggle in UI)
- The LLM already handles German DWD text well — evidence it can work across languages

### 4. Data formats and numbers

| Element | Approach |
|---------|----------|
| Dates | Use `Intl.DateTimeFormat(locale)` on web, `DateFormatter` on iOS |
| Numbers | Use `Intl.NumberFormat(locale)` — e.g., `1.234,5` in German/French/Spanish |
| Units | Keep aviation units (ft, kt, nm, hPa) — these are ICAO standard and universal |
| Wind | Keep `230@15G25` format — METAR standard, universal |
| Flight levels | Keep FL090 — ICAO standard |
| Times | Keep UTC (`1430Z`) — aviation standard |

**Important:** Do NOT localize aviation units. Pilots worldwide use feet, knots, nautical miles. Localizing these would be dangerous and confusing.

---

## Implementation Phases

### Phase 1: Infrastructure + Web UI Chrome (Smallest useful increment)

1. Add `locale` to `user_preferences` table (DB migration)
2. Build `web/ts/i18n/i18n.ts` helper module
3. Extract all web UI strings to `en.json` (~800 keys)
4. Add language selector to settings page
5. Wire locale through frontend (localStorage + API header)
6. Translate `en.json` → `fr.json`, `de.json`, `es.json`
7. Update `<html lang>` and date/number formatting

**Effort estimate scope:** ~800 string extractions + 3 translation files + i18n module + settings UI.

### Phase 2: LLM Digests in Target Language

1. Parameterize prompt selection by locale in `llm_config.py`
2. Create locale-specific prompt variants (mostly shared base + language block)
3. Add locale to `BriefingOptions` and thread through pipeline
4. Add aviation terminology glossary per language to prompts
5. Validate output quality with native speakers

**Effort estimate scope:** ~4 prompt files + pipeline locale threading + glossary.

### Phase 3: Advisory Detail Text + Catalog

1. Create `src/weatherbrief/i18n/` module with `t()` function
2. Extract ~50 advisory detail strings to locale JSON files
3. Thread locale through `RouteContext` → evaluators
4. Create advisory catalog translation files (13 advisories x 4 fields)
5. Merge translations in API response layer

**Effort estimate scope:** ~200 strings to extract + locale threading + 3 translation files.

### Phase 4: Reports, Emails, Help

1. Add translation dict to Jinja2 report context
2. Create locale-specific email template strings
3. Create translated help pages (`help.fr.html`, etc.)
4. Add locale detection for pre-login pages (Accept-Language header)

**Effort estimate scope:** ~50 template strings + 3 help page translations.

### Phase 5: iOS App

1. Create `Localizable.xcstrings` String Catalog
2. Extract ~50 strings from Swift views
3. Add translations via Xcode export/import workflow
4. Wire locale from API preference to iOS settings

**Effort estimate scope:** ~50 strings + Xcode localization workflow.

### Phase 6: Metrics Catalog + Polish

1. Create metrics catalog translation overlays
2. Translate ~300-400 metric strings per language
3. Polish date/number formatting edge cases
4. End-to-end testing in each language

**Effort estimate scope:** ~400 strings x 3 languages (heaviest translation volume).

---

## Translation Workflow

### Who translates?

**Options:**

1. **Professional translators** — high quality, expensive (~$0.10-0.15/word), slow turnaround
2. **LLM-assisted + human review** — use Claude/GPT to generate initial translations, have native speakers review. Fast, cheap, good for technical content
3. **Community / pilot volunteers** — free, domain expertise, but unreliable availability
4. **Translation platforms** (Crowdin, Lokalise, Transifex) — manages workflow, integrates with Git, costs $40-200/month

**Recommendation:** LLM-assisted initial translation + pilot review. The content is technical (aviation weather), and having pilots who speak the target language review ensures correct terminology. Use a translation platform only if the volume grows significantly.

### Translation file management

Keep translation files in the repo under version control. Use a simple JSON format that any translator can edit. Consider a CI check that flags missing keys (English has a key that French doesn't).

```bash
# CI check: find missing translation keys
python scripts/check_translations.py --base en.json --target fr.json de.json es.json
```

---

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| LLM generates incorrect aviation terms | Safety concern — pilots may misunderstand | Terminology glossary in prompts + pilot review |
| Translation quality degrades over time | Poor UX | CI check for missing keys, periodic review |
| Locale threading breaks existing API | Regression | Default to English when locale missing, extensive testing |
| Help page translations fall out of sync | Users see outdated docs | Accept this trade-off — help changes rarely |
| Performance impact of loading translations | Slower page load | Translation JSON is small (~20-50KB), cache aggressively |
| Aviation units get accidentally localized | Safety concern | Never localize aviation units — enforce in code review |

---

## What NOT to Translate

- ICAO codes and abbreviations (METAR, TAF, SIGMET, NOTAM, ATIS, VFR, IFR, VMC, IMC)
- Aviation units (ft, kt, nm, hPa, FL)
- Wind format (`230@15G25`)
- Time format (`1430Z`)
- Model names (GFS, ECMWF, ICON-EU)
- ICAO airport codes (LFPG, EGLL, EDDF)
- Cloud coverage codes (SCT, BKN, OVC, FEW)
- Status values in data (GREEN, AMBER, RED as enum values — though display labels can be translated)
- Admin-only pages and emails (low ROI, English is fine)

---

## Summary

The multilingual strategy is built on a key insight: **different content types need different translation strategies**. UI chrome uses standard client-side i18n. LLM-generated prose is generated directly in the target language (cheapest and highest quality). Deterministic backend text uses server-side translation lookups. Documents use per-locale static files.

The approach is incremental — Phase 1 (web UI chrome) delivers visible value quickly and establishes the infrastructure for subsequent phases. The LLM digest translation (Phase 2) is the highest-impact change for users, as the briefing narrative is the core product.

Total translatable content across all phases: ~1,500-2,000 unique strings (including the metrics catalog), plus the help documentation prose and LLM prompt variants.
