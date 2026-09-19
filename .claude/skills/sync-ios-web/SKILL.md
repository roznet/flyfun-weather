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

### 3. Route-graph metric registry

The two clients draw the same scalar-metric graph below the cross-section from
hand-copied registries. Compare `web/ts/visualization/route-graph/metrics.ts`
(`ROUTE_GRAPH_METRICS`, `CEILING_AGL_CAP_FT`, `MetricSample`, `sampleMetric`,
`formatSample`) against
`app/flyfun-weather/flyfun-weather/Views/RouteGraph/RouteGraphMetrics.swift`, and
the axis rules in `route-graph/axes.ts` (`computeYScale`, `niceTickInterval`)
against `.../RouteGraph/RouteGraphView.swift` (`RouteGraphScale`).

Check, in this order of consequence:

1. **The metric set.** A metric present on only one client is not cosmetic — the
   advisory lenses in `advisory-presets.ts` name metric ids in their `routeGraph`
   directives, so a missing id silently breaks that lens on that client.
2. **`suggestedRange` per metric**, and that the client *honours* it. A pinned
   range that is declared but never read is the worst case: the same numbers
   render at different magnitudes with nothing on screen saying so.
3. **The `aboveScale` / no-coverage states.** Both must distinguish "above the
   display cap" and "the sensor does not look here" from "no data" (#384, #574).
   Collapsing either back to a gap makes good news and absent data identical.
4. **`formatValue` precision and units** (decimal places, thousands separators,
   direction suffixes) — a silent per-client difference in the same reading.

Web is the source of truth for the registry; iOS copies the English `graph.<id>`
strings from `web/ts/i18n/locales/en.json` because it is not localized.

**Known iOS divergence:** `qnh` is hPa-only on iOS (the web switches to
Altimeter/inHg for the US region). `UnitsRegion` is not plumbed into iOS — don't
re-flag it until it is.

### 4. Skew-T side-panel variable registry

Compare `web/ts/visualization/skewt/variable-panel.ts` (`VARIABLE_REGISTRY`,
`VARIABLE_GROUPS`) against
`app/flyfun-weather/flyfun-weather/Views/CrossSection/SkewTVariableCatalog.swift`:
the variable set, group membership and within-group order, `fixedRange`,
`zeroLine`, colours, `shortLabel`, and the `metricId` → `helpMetricId` pointers.

The help pointers deserve their own pass: both ids can exist in the catalog, so a
wrong one renders an (i) button with plausible but wrong text rather than failing
visibly. Check that a per-level variable points at a per-level metric.

**Known iOS divergence (do not re-flag):** the iOS variable ids are shorter than
the web's (`rh` / `relative_humidity`, `w` / `vertical_velocity`, `ri` /
`richardson`, `lapse` / `lapse_rate`, `thetae` / `theta_e`, `cloud` /
`cloud_area_fraction`, `clw` / `cloud_liquid_water`, `ice` / `ice_mixing_ratio`,
`icing-dd` / `icing_index`, `icing-nwp` / `icing_index_nwp`). This is why the web
`skewtSidePanel` preset directive cannot be ported as-is.

### 5. API DTO contracts

iOS `Models/API/*Response.swift` Codable types mirror backend JSON. A server
shape change breaks iOS decode **at runtime** with no compile-time signal. For
each changed endpoint, cross-check the three mirrors:

- Python response model in `src/` (source of truth — what the server emits)
- `web/ts/types/*.ts` (the web's view of the same shape)
- `app/flyfun-weather/flyfun-weather/Models/API/*Response.swift`

Report field name / optionality / nesting mismatches. Prioritise fields present
in the backend (or web) but **missing or non-optional in Swift** — those are the
runtime-decode hazards. A field the server may omit must be `Optional` on iOS.

### 6. Debrief taxonomy — three-way copy

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

### 7. Release-stream category rendering

Both clients render the same `/api/messages` stream, and both decide *per client*
how a category is presented. The category set is server-owned
(`MessageCategory` in `src/weatherbrief/api/messages.py`, mirrored by
`VALID_CATEGORIES` in `src/weatherbrief/release/__main__.py`); the chip labels are
hand-copied:

| Role | File | Symbols |
|---|---|---|
| Source of truth (set) | `src/weatherbrief/api/messages.py` | `MessageCategory` |
| Web labels | `web/ts/i18n/locales/*.json` | `messages.category.*` |
| iOS labels | `app/flyfun-weather/.../Models/API/SystemMessageResponse.swift` | `SystemMessage.categoryLabel` |

Check that every category in the Literal has a label on both clients, and that the
English strings match. **The install call to action is web-only by design** — the
web emits it under `app_release` entries from `APP_STORE_URL` (`web/ts/utils.ts`);
a reader already inside the app has nothing to install, so its absence on iOS is
not a divergence.

### 8. Shared form logic ports

Small pure-logic helpers ported by hand between the clients because the server
has no say in them — they shape what a *form* does with a stored value, not what
the value means. Compare the symbols, the constants, and the rounding/clamping
rules:

| Web source | Symbols to compare | iOS mirror |
|---|---|---|
| `web/ts/utils/duration.ts` | `DURATION_MINUTE_OPTIONS`, `MAX_DURATION_HOURS`, `splitDurationCeil` (ceil-to-quarter + epsilon), `combineDuration`, `formatDurationHM` | `Models/Domain/FlightDuration.swift` — `minuteOptions`, `maxHours`, `split`, `combine`, `label`, `clampToPickerRange` |

Report a divergence in any option list, ceiling, rounding direction, epsilon, or
label format. A drift here is silent and pilot-visible: the same stored
`flight_duration_hours` renders as a different flight window on the two clients,
and whichever form the pilot saves from writes its own reading back.

`clampToPickerRange` is **iOS-only by design** — the web reaches the same 12h45
ceiling through its dropdown markup (built from `splitDurationCeil`, read back on
Save), so its absence from the TS module is not a gap.

### 9. Known parity gaps (informational)

List these so they are **not** re-flagged as new divergences, and note any *new*
gap the branch introduced:

- iOS-missing cross-section layers: `ieng-icing-bands`, `e-shear-bands`,
  `sld-bands`, `surface-obscuration-bands`. Also web-only and equally expected:
  `current-conditions` (D-0 METAR/SIGMET overlay), `fronts-markers`,
  `night-shading` — iOS has no `obscuration` or `fronts` layer group, and
  `CrossSectionTheme.swift` documents the theme-level omissions.
- Cross-section color themes (web-only; tracked in #320).
- Skew-T overlay bands: the web has seven (`clouds-nwp`/`clouds-dd`,
  `icing-nwp`/`icing-dd`/`icing-sfip`, `inversions`, `convective`); iOS collapses
  them into three chips (Cloud / Icing / Inversion) with no method choice and no
  convective band. Defaults for the three iOS has match the web.
- Lens directives not wired on iOS: the web `AdvisoryPreset`'s `routeGraph`, `map`,
  `skewtOverlays` and `skewtSidePanel` fields, and its `interpretation` blurb.
  `CrossSectionPresets.swift` ports the cross-section directives only, so tapping
  a lens on iOS leaves the route graph and Skew-T on the pilot's last choice.

### 10. SYNC-comment integrity

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
  of truth for preset tables, the metrics catalog, and the route-graph and
  Skew-T variable registries; the Python backend is source of truth for DTO
  shapes).
- **One-line why** — the user-facing or runtime consequence if left unsynced
  (e.g. "iOS will fail to decode this endpoint on device").

If every surface is in sync, say so plainly (e.g. "metrics-catalog IDENTICAL,
presets in sync, route-graph + Skew-T registries in sync, DTOs aligned; known
parity gaps unchanged") rather than padding.

## Then offer next steps

End by offering the user to:

1. **Plan** — hand the task list to plan mode for sign-off before implementing.
2. **Implement** — apply the syncing changes directly (small, mechanical ones).
3. **Stop** — just keep the audit as a record.

Do not start editing without the user picking (2).
