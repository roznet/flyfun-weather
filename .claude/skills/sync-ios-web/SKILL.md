---
name: sync-ios-web
description: Audit web↔iOS consistency for hand-copied surfaces (preset tables, metrics-catalog, debrief taxonomy, API DTOs) and surface divergences as an actionable task list. Run after a feature that touched one platform, before merge, or any time the two clients may have drifted.
---

# Sync iOS ↔ Web

Web (`web/ts/`) and iOS (`app/flyfun-weather/`) are **two independent clients of
the same Python backend**. Anything the backend *serves* (advisory severity,
meteorology, the help catalog) stays in sync automatically. The drift risk is the
handful of surfaces that are **copied by hand** between the two clients. This
skill audits those surfaces and produces a grouped task list of divergences — it
does **not** edit code itself.

Scope note: advisory severity / weather math is server-computed and rendered by
both clients. Do **not** audit it here — it cannot drift. Focus only on the
copied surfaces below.

## Step 0 — Choose scope

Ask the user which scope to run (default suggestion: **branch-diff** for the
common post-feature case):

- **Branch-diff scoped** — audit only the surfaces the current branch changed on
  either platform, then report which counterpart needs mirroring. Get the changed
  files with `git diff --name-only main...HEAD` (fall back to `git diff --name-only`
  for uncommitted work) and intersect against the surfaces below.
- **Full audit** — check every surface regardless of branch.

State the chosen scope before proceeding.

## Surfaces to check

### 1. `metrics-catalog.json` — detect-only

Byte-compare the two copies:

```
diff web/ts/data/metrics-catalog.json \
     app/flyfun-weather/flyfun-weather/Resources/metrics-catalog.json
```

- **Identical** → no task.
- **Differ** → emit one task: "regenerate the iOS copy from the web source"
  (the web copy is the source of truth). This pass is **detect-only** — do not
  propose or build a generation/build step; a manual copy is the fix for now.

### 2. Cross-section preset tables

These carry reciprocal `SYNC` comments. The iOS port lives in
`app/flyfun-weather/flyfun-weather/Views/CrossSection/Layers/CrossSectionPresets.swift`.
Compare each web source against it:

| Web source | Symbols to compare | iOS mirror |
|---|---|---|
| `web/ts/visualization/cross-section/layer-registry.ts` | `PRESETS`, `*_ENABLED` (GRAMET / Windy / ForeFlight) | `LayerPreset` table |
| `web/ts/visualization/cross-section/advisory-presets.ts` | `ADVISORY_PRESETS`, `ADVISORY_TO_PRESET`, `getPresetForAdvisory` | `AdvisoryPreset` table (Basic / Icing / Clouds / Convective / Turbulence / VFR / IFR) |
| `web/ts/visualization/cross-section/layers/cloud-bands-factory.ts` | `CLOUD_LAYER_BY_AXES`, `parseCloudLayerId` | cloud source×style axes |

Report added / removed / renamed presets, lenses, or cloud source×style
combinations. **Account for the documented iOS-missing layer IDs** — iOS
intentionally lacks `ieng-icing-bands`, `e-shear-bands`, `sld-bands`,
`surface-obscuration-bands`; a web preset referencing only those is *expected* to
drop them on iOS, so don't flag that as drift (the `CrossSectionPresets.swift`
header documents this).

### 3. API DTO contracts

iOS `Models/API/*Response.swift` Codable types mirror backend JSON. A server
shape change breaks iOS decode **at runtime** with no compile-time signal. For
each changed endpoint, cross-check the three mirrors:

- Python response model in `src/` (source of truth — what the server emits)
- `web/ts/types/*.ts` (the web's view of the same shape)
- `app/flyfun-weather/flyfun-weather/Models/API/*Response.swift`

Report field name / optionality / nesting mismatches. Prioritise fields present
in the backend (or web) but **missing or non-optional in Swift** — those are the
runtime-decode hazards. A field the server may omit must be `Optional` on iOS.

### 4. Debrief taxonomy — three-way copy

The debrief vocabulary (condition tags + labels/descriptions, decisions, outcome
values, advisory→tag map, note limit) is a **three-way** copy. Python is the
source of truth; it is *served* to iOS in the `/api/help/catalog` `debrief`
section, but iOS also ships a hand-kept offline baseline and web keeps a
build-time mirror:

| Role | File | Symbols |
|---|---|---|
| Source of truth | `src/weatherbrief/debriefs/taxonomy.py` | `ConditionTag`, `TAG_LABELS`, `TAG_DESCRIPTIONS`, `DECISION_LABELS`, `OUTCOME_LABELS`, `ADVISORY_TAG_MAP`, `NOTE_MAX_LENGTH`, `build_taxonomy_catalog()` |
| Web mirror (build-time) | `web/ts/components/debrief-taxonomy.ts` | `ALL_TAGS`, `TAG_LABELS`, `TAG_DESCRIPTIONS`, `OUTCOME_LABELS`, `KEYWORD_MAP`, `ADVISORY_TAG_MAP` |
| iOS offline baseline | `app/flyfun-weather/flyfun-weather/Models/API/DebriefTaxonomy.swift` | `DebriefTaxonomy.bundledBaseline` |

The live iOS path reads the *served* catalog, so the baseline only backstops a
cold first launch — but it must still match. Cross-check the tag set, labels,
descriptions, decision order, advisory→tag map, and the note limit across all
three. Report any tag/label/mapping that exists in Python but is missing or
different in the web mirror **or** the iOS baseline. Web is not served, so a
Python edit needs a manual TS + Swift-baseline update (source of truth = Python).
The `KEYWORD_MAP` matcher is web-only by design (iOS dropped `matchTagsInText` for
v1) — don't flag its absence on iOS.

### 5. Known parity gaps (informational)

List these so they are **not** re-flagged as new divergences, and note any *new*
gap the branch introduced:

- iOS-missing cross-section layers: `ieng-icing-bands`, `e-shear-bands`,
  `sld-bands`, `surface-obscuration-bands`.
- Cross-section color themes (web-only; tracked in #320).

### 6. SYNC-comment integrity

Verify reciprocity for each `SYNC`-commented file pair:

```
grep -rl "SYNC" web/ts --include="*.ts"
grep -rl "SYNC" app/flyfun-weather --include="*.swift"
```

Each side should name a counterpart that still exists and still carries the
reciprocal note. Flag a one-sided SYNC comment (e.g. the web file points at an
iOS file that no longer carries the reciprocal comment, or vice versa).

## Output

Produce a **task list grouped by surface**. Each task is:

- **What diverged** — the concrete symbol/field/file.
- **Which side to change** — and why that's the source of truth (web is source
  of truth for preset tables and the metrics catalog; the Python backend is
  source of truth for DTO shapes).
- **One-line why** — the user-facing or runtime consequence if left unsynced
  (e.g. "iOS will fail to decode this endpoint on device").

If every surface is in sync, say so plainly (e.g. "metrics-catalog IDENTICAL,
presets in sync, DTOs aligned; known parity gaps unchanged") rather than padding.

## Then offer next steps

End by offering the user to:

1. **Plan** — hand the task list to plan mode for sign-off before implementing.
2. **Implement** — apply the syncing changes directly (small, mechanical ones).
3. **Stop** — just keep the audit as a record.

Do not start editing without the user picking (2).
