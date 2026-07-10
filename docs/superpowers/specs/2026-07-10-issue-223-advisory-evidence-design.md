# Issue #223 Advisory Evidence and Focus Design

Date: 2026-07-10

Issue: #223 — advisory highlighting, emphasis, non-cross-section actions, and method badges

Delivery: web first, shared backend contract, iOS-compatible

Policy: audit first and evidence-gated

## 1. Purpose

Issue #223 extends the advisory-aware visualization presets shipped in Phase 1. A pilot should be able to move from an advisory grade to the exact route region and, where applicable, altitude band that caused it. The interaction must preserve model and method attribution and must never reconstruct meteorological meaning in the browser.

The work also completes useful actions for advisories whose evidence does not belong primarily on the route cross-section:

- cross-model confidence opens Compare mode;
- airport conditions open an airport-profile drawer;
- fronts open the route map with the fronts overlay;
- advisory chips identify the actual method that controlled the result.

The Skew-T preset linkage requested by the original issue has already shipped through #308/#309. This design reuses and verifies it; it does not implement a second Skew-T path.

## 2. Authoritative design and evidence policy

The repository design hierarchy is binding for this work:

1. `.claude/CLAUDE.md` and `designs/INDEX.md` define repository workflow and module boundaries.
2. `designs/meteorology-decisions.md` is authoritative for meteorological calibration decisions.
3. `designs/advisories.md`, `designs/analysis-metrics.md`, `designs/visualization.md`, and the cloud, icing, and convection analysis documents describe the current subsystem, except where they conflict with the authoritative decisions record.
4. Point-in-time audits and older plans are evidence and historical context, not authority for superseded behaviour.

The implementation begins with an audit of the core hazard stack:

- clouds;
- icing and SLD;
- turbulence;
- convection;
- airport wind;
- advisory aggregation;
- associated route-map, route-graph, and cross-section metrics.

Each audit finding records the affected evaluator or metric, current behaviour, intended contract, evidence, safety/display impact, regression test, and disposition.

Corrections are evidence-gated:

- Objective computation, units, schema, threshold mapping, aggregation, or missing-data errors can be corrected directly.
- Meteorological algorithms or thresholds can change only with authoritative literature, an independent implementation/oracle, or an appropriate observation corpus.
- Observation-dependent calibration remains separate from #223 unless the required evidence is available.

Only these findings block #223:

- high- or medium-safety-impact errors;
- missing data represented as clear or GREEN;
- backend-to-frontend metric, severity, method, or display-contract mismatches.

Lower-risk calibration opportunities are tracked without indefinitely delaying the feature.

## 3. Scope and non-goals

### In scope

- An additive backend contract for per-model data state, method provenance, representative-model attribution, and spatial evidence regions.
- Shared evaluator helpers that derive grades, evidence, and distance metrics from the same per-point assessments.
- Migration of the core hazard evaluators needed by #223.
- Ephemeral web advisory focus rendered on the cross-section, route graph, and route map.
- Layer emphasis driven by the existing advisory preset configuration.
- Cross-model, airport, and fronts actions.
- Method badges based on backend provenance.
- Backward compatibility with existing briefing packs.
- Documentation, regression tests, and a permanent independent meteorology-review gate.

### Not in scope

- Observation-dependent threshold recalibration without qualifying evidence.
- A new Skew-T linkage implementation.
- A general rewrite of the compare renderer.
- A full same-model DD-versus-NWP comparison renderer.
- iOS UI implementation. The shared schema remains additive and decodable by iOS; the iOS interaction is planned separately.
- Unrelated advisory or visualization refactoring.

## 4. Backend evidence contract

### 4.1 Per-model result additions

`ModelAdvisoryResult` gains additive fields:

```python
data_state: Literal["complete", "partial", "unavailable"] | None = None
primary_method_id: str | None = None
evidence_regions: list[AdvisoryEvidenceRegion] = Field(default_factory=list)
```

`None` for `data_state` means a legacy pack or an evaluator that has not migrated. It must not be interpreted as complete.

`primary_method_id` uses stable method identifiers already used by analysis preferences or method catalogs wherever possible, for example `sfip`, `ogimet_nwp`, `ogimet_dd`, `nwp`, and `dewpoint_depression`. Compound provenance uses an explicit stable identifier rather than pretending that one method acted alone, for example `nwp_with_dd_floor`.

### 4.2 Spatial evidence region

```python
class AdvisoryEvidenceRegion(BaseModel):
    start_point_index: int
    end_point_index: int
    lower_altitude_ft: int | None = None
    upper_altitude_ft: int | None = None
    severity: AdvisoryStatus
    reason_code: str
    metric_id: str | None = None
    method_id: str | None = None
```

Contract rules:

- Route point indices are inclusive and refer to stable `RoutePointAnalysis.point_index` values.
- Start must not exceed end.
- Altitude bounds are either both absent or both present, with lower not exceeding upper.
- `reason_code` is a stable, non-localized evaluator token. Existing localized `detail` remains the human explanation.
- `metric_id` references an existing metric identifier where one accurately describes the evidence. It is absent rather than guessed when no such metric exists.
- `method_id` overrides `primary_method_id` only when that particular region was controlled by another method.
- `severity` is a local GREEN/AMBER/RED tier or, for a binary predicate, the containing model result's tier. It is never UNAVAILABLE. Evaluators normally emit regions only for conditions that contributed to a concern.
- A region must not bridge a route gap, severity change, reason change, method change, or altitude discontinuity that would imply unsupported hazardous air.
- Evaluators emit multiple regions when necessary to preserve the evidence geometry.

The model is inherited from the containing `ModelAdvisoryResult`; it is not duplicated in every region.

### 4.3 Aggregate attribution

`RouteAdvisoryResult` gains:

```python
representative_model: str | None = None
```

The backend sets it to the same per-model result used for `aggregate_detail` and aggregate mitigations. The frontend does not repeat aggregation logic to choose a model.

Evidence is never geometrically merged across forecast models. An aggregate action uses the representative model. A model-specific action uses only that model's regions.

### 4.4 Data-state meaning

- `complete`: all inputs required by the evaluator's documented method were available for the evaluated domain.
- `partial`: some required route or entity inputs were missing, but valid evidence exists for the available subset.
- `unavailable`: there was no sufficient valid input to evaluate the model.
- absent metadata: legacy/unknown; existing grades may be displayed, but #223 highlighting is disabled.

For a migrated spatial evaluator, `complete` with no evidence regions means that no spatial region met its hazard predicate. For a non-spatial evaluator, an empty region list only means that route highlighting is not applicable; its advisory status remains authoritative.

## 5. Evaluator architecture and data flow

### 5.1 One source for grade and display evidence

Each migrated evaluator first creates internal assessment records. Spatial evaluators create per-point or exact-segment assessments containing:

- stable route point index and distance;
- whether required input is available;
- severity or contributing condition;
- reason code;
- metric and method attribution;
- optional vertical bounds.

Non-spatial evaluators create equivalent domain records for the entities they assess, such as departure and arrival airports. They use the same data-state and provenance contract but do not manufacture route regions.

The evaluator's status, counts, detail, evidence regions, and distance extent are all derived from these assessments. Highlighting must not be implemented by a second browser-side threshold calculation or by a duplicate backend predicate.

A focused shared helper will:

1. validate and sort assessments in route order;
2. coalesce only semantically and geometrically contiguous evidence;
3. create `AdvisoryEvidenceRegion` objects;
4. compute unique affected points;
5. compute affected distance from actual route geometry;
6. construct the per-model result with data state and provenance.

### 5.2 Distance convention

The current `total_distance_nm * affected_points / total_points` estimate is not authoritative geometry and can be wrong on unevenly spaced routes.

For point-based evidence, each route point owns the interval bounded by the midpoint to its previous and next route point. The first and last intervals are clipped to route start and route end. `affected_nm` is the length of the union of qualifying intervals. This convention:

- handles uneven point spacing;
- gives a non-zero, bounded extent to an isolated affected point;
- avoids double-counting adjacent affected points;
- supplies the same route intervals used by the visual overlays.

Evaluators with genuinely segment-based evidence may provide exact segment intervals through the same helper instead of forcing them into point cells.

### 5.3 Missing-data safety

- Missing required input is never treated as a clear condition.
- Complete data with no qualifying hazard may produce GREEN.
- Partial data may preserve AMBER or RED when available evidence independently supports that grade.
- Partial data that would otherwise appear GREEN becomes UNAVAILABLE; no arbitrary AMBER uncertainty penalty is introduced.
- Optional cross-check inputs do not make the primary method partial unless the authoritative method requires them.
- A fallback method is used only where the design documents define it, and provenance names the method actually used.
- Aggregation ignores unavailable models only when at least one valid model exists. Empty or all-unavailable input produces aggregate UNAVAILABLE, never GREEN.
- An evaluator exception is logged with diagnostics and produces an unavailable advisory result rather than silently removing the advisory.

This policy directly covers structural gaps such as a sounding existing while turbulence vertical-motion/CAT data is absent.

### 5.4 Compound provenance

When multiple tracks influence a grade, provenance records the controlling path. For example, if the convective DD safety floor raises a quiet NWP result, neither the evidence nor the badge may claim that NWP alone produced the grade. The region reason identifies the floor or guardrail that fired.

## 6. Frontend focus architecture

### 6.1 Ephemeral state

The briefing store gains a top-level, non-persisted state:

```ts
interface ActiveAdvisoryFocus {
  advisoryId: string;
  model: string;
  highlightSurfaces: Array<'cross-section' | 'route-graph' | 'route-map'>;
  emphasizeLayers: string[];
}
```

The state stores identifiers and resolved presentation directives, not a copy of the meteorological regions. A shared selector finds the current advisory and model in the current manifest and validates its data state and evidence before rendering.

`activeAdvisoryFocus` is not part of `VizSettings` and is never saved to local storage.

### 6.2 Preset directives

`AdvisoryPreset` gains optional directives without changing existing call-site signatures. Existing presets remain valid without them; the targeted hazard presets then opt in:

```ts
highlights?: Array<'cross-section' | 'route-graph' | 'route-map'>;
emphasize?: boolean;
```

When `emphasize` is true, the resolver derives a concrete allow-list from the preset's method-resolved groups, explicit lines, and always-required context layers. `ResolvedView` carries the resulting highlight surfaces and layer IDs. The advisory-card action binds them to `activeAdvisoryFocus`; selecting the same preset from the generic dropdown does not fabricate an advisory focus.

Emphasis changes opacity only. It does not disable layers or overwrite the user's enabled-layer settings.

### 6.3 Focus action

Activating an aggregate spatial advisory action:

1. reads `representative_model` from the backend result;
2. validates that the model exists in the current briefing;
3. selects that model;
4. resolves and applies the existing advisory preset;
5. sets `activeAdvisoryFocus`;
6. changes map-only layout to split, matching Phase 1;
7. scrolls the visualization into view.

The existing per-model detail popup gains a model-specific "Show on chart" action. It selects that model and focuses only that model's evidence.

### 6.4 Focus lifecycle

- Focusing another advisory replaces the current focus.
- An explicit close action, a generic preset selection, a briefing/pack change, or an unrelated manual model change clears focus.
- Recalculation retains the advisory/model identifiers and resolves the newly returned regions. It clears focus if the advisory or model disappears.
- Manual layer toggles may retain the evidence outline for exploration, but clearing the active preset removes emphasis.
- Legacy or unavailable evidence still applies the useful Phase-1 preset, but displays a restrained location-unavailable message instead of guessing geometry.

### 6.5 Rendering

A shared focus selector supplies validated regions to thin adapters for each surface:

- Cross-section: translucent route/altitude fill and severity boundary above meteorological layers but below hover/selection annotations.
- Route graph: affected route intervals behind the plotted series.
- Route map: segment halo or outline that preserves the configured metric colours.
- Region without altitude bounds: along-route span, not a fabricated full-depth meteorological layer.

Visual treatment combines colour with outline or pattern. Partial evidence includes an explicit partial-data indicator. The overlay label identifies the advisory and model so Compare layout cannot be mistaken for cross-model consensus evidence.

Coordinate conversion is presentation logic. Thresholding, severity assignment, method choice, and region existence remain backend responsibilities.

## 7. Non-cross-section actions

A typed action registry extends the current mapping:

```ts
type AdvisoryAction =
  | { kind: 'preset-focus' }
  | { kind: 'compare-models' }
  | { kind: 'method-context' }
  | { kind: 'airport-profile' }
  | { kind: 'fronts-map' };
```

Existing entries in `ADVISORY_TO_PRESET` implicitly receive `preset-focus`; they are not duplicated in the new registry.

### 7.1 Cross-model agreement

`model_agreement` opens the existing Compare layout and ensures available models are enabled. When the backend evidence metric maps to a supported comparable layer, the action selects that layer. Otherwise it preserves the user's current layer and explains that no direct compare layer exists. It does not invent a meteorological mapping.

### 7.2 DD-versus-NWP agreement

`dd_nwp_agreement` must not open current Compare mode as if it were a cross-model disagreement. It compares two derivations within one model.

Its `method-context` action applies a focused cross-section view, highlights affected route ranges, and enables both DD and NWP context where the current visualization already supports both, such as cloud bands. For freezing-level disagreement, the evidence range and detail are shown without fabricating a second unsupported line. A richer same-model comparison renderer is a separate feature.

### 7.3 Airport advisories

`airport_wind` and `flight_category` open a briefing-side drawer that reuses `AirportProfilePanel` rather than implementing a second sounding/profile UI.

The drawer provides departure and arrival tabs instead of guessing which endpoint is worst. Departure uses the planned departure time; arrival uses estimated arrival time. The selected advisory model is used when the panel supports it. Unsupported models fall back visibly to an available GRIB model rather than silently claiming equivalence.

The drawer cancels its SSE stream when closed or when the briefing changes and retains the panel's existing loading and error treatment.

### 7.4 Fronts

`fronts` switches to the route-map layout, enables the existing fronts overlay, fits the route/front context, and scrolls the map into view. If the experimental front artifact is absent, the action is disabled with an availability explanation.

## 8. Method badges

Method badges are sourced only from backend provenance:

- The aggregate action uses the representative model's `primary_method_id`.
- A model-specific action uses that model's method.
- A region-specific override is used when it controlled the focused evidence.
- Compound labels such as `NWP + DD floor` come from explicit compound method metadata.
- Legacy, unknown, or unavailable provenance produces no inferred badge.

The UI maps stable IDs to display labels and localized accessible descriptions. It must not derive a method from the selected preset or the user's preferred-method setting because either may differ from the method that actually controlled the grade.

## 9. Audit seeds and expected disposition

The audit starts with, but is not limited to, these known concerns:

| Concern | Initial classification |
| --- | --- |
| Turbulence may report GREEN when vertical-motion/CAT input is structurally absent | Missing-versus-clear blocker |
| Route-map SFIP colours use 20/50/80 while backend/catalog thresholds are 15/30/55 | Backend/display contract blocker |
| Empty or all-unavailable aggregation can collapse to GREEN | Missing-versus-clear blocker |
| `affected_nm` is estimated from point counts | Objective metric-contract correction used by evidence geometry |
| SFIP `_no_vv` variants may retain dead vertical-velocity weight | Objective computation audit; correct if confirmed by equations/tests |
| Airport wind grades mean crosswind and gust separately rather than gust-vector crosswind | Meteorological improvement requiring evidence before changing behaviour |
| Cloud DD/NWP Jaccard can exceed 1.0 | Objective computation correction; blocking status depends on demonstrated advisory impact |
| Okta cutpoints, virtual versus dry potential temperature in Ri, wind-profile gating, and resolution-sensitive SLD | Document and validate; calibration changes require qualifying evidence |

The audit does not assume that every concern belongs in #223. Findings are separated into prerequisite blockers, direct non-blocking corrections, and observation-dependent follow-up.

## 10. Compatibility and failure behaviour

- All backend fields are additive and have legacy-safe defaults.
- No database migration is required; evidence is stored in generated briefing/advisory artifacts.
- Old packs continue to display their existing advisories and Phase-1 presets but do not receive guessed highlights or method badges.
- Unknown reason, metric, or method IDs fall back to generic safe presentation and are logged for diagnosis.
- Backend model validation rejects invalid generated regions before an artifact is persisted. If a malformed external or legacy artifact reaches the browser, the focus selector ignores only the invalid region, logs the contract error, and keeps the rest of the briefing usable.
- Evidence size is bounded by route sampling and coalesced to avoid unnecessarily large pack payloads.
- iOS decoders must tolerate the additive fields even though iOS focus UI is deferred.

## 11. Testing strategy

Implementation follows test-driven development.

### 11.1 Backend contract and helper tests

- New and legacy Pydantic serialization/deserialization.
- Region index and altitude validation.
- Coalescing across contiguous points and splitting on gaps, severity, reason, method, and altitude changes.
- Midpoint-cell distance geometry on evenly and unevenly spaced routes.
- Duplicate/overlapping region union without double-counting.
- Complete, partial, unavailable, legacy, and all-unavailable semantics.
- Representative-model attribution under majority and worst aggregation.
- Evaluator exceptions becoming unavailable results.

### 11.2 Evaluator regression tests

Each migrated core evaluator receives focused synthetic tests proving that its grade and evidence originate from the same predicate. Mandatory cases include:

- turbulence sounding present but required vertical-motion/CAT structure absent;
- disconnected cloud, icing, turbulence, and convective regions;
- varying altitude bands without false bridging;
- SLD and severe single-point triggers;
- compound convective provenance when the DD safety floor controls the grade;
- airport and other non-spatial results with explicit data state;
- unchanged thresholds unless an evidence-gated correction is approved.

### 11.3 Frontend unit tests

- TypeScript mirrors of new backend fields.
- Focus creation, replacement, recalculation, and clearing lifecycle.
- Aggregate versus model-specific evidence selection.
- No cross-model geometry union.
- Cross-section, route-graph, and route-map coordinate adaptation.
- Partial, unavailable, invalid, and legacy presentation.
- Emphasis without mutation of enabled-layer settings.
- Action dispatch for model agreement, DD/NWP context, airport drawer, and fronts map.
- Backend-sourced method badges and compound provenance.
- Keyboard operation, ARIA labels, and non-colour visual cues.

### 11.4 Integration and manual acceptance

Add a representative fixture with multiple models, disconnected regions, partial data, all-unavailable data, airport actions, and fronts. Manual browser acceptance verifies:

- evidence overlays align with the underlying weather bands and route points;
- switching models never leaves another model's evidence visible;
- route-map metric colours remain intact under focus halos;
- legacy packs degrade gracefully;
- the PR includes screenshots of the principal states.

## 12. Documentation and permanent independent review

Implementation updates the authoritative module documents for advisories, data models, metrics, and visualization. Stale sections in `designs/analysis-metrics.md` and `designs/advisories.md` are reconciled wherever they affect this work. A calibration entry is added to `designs/meteorology-decisions.md` only when qualifying evidence supports a changed meteorological decision.

A permanent fresh-model meteorology-review gate is added to the review checklist. The independent reviewer receives:

- this approved design;
- the authoritative meteorology decisions;
- the audit ledger and supporting sources;
- the complete diff;
- tests and representative fixtures;
- explicitly deferred findings.

The reviewer independently checks equations, units, method assumptions, missing-data safety, severity/visual threshold consistency, model attribution, and whether any proposed improvement actually requires observational calibration. The review must report concrete evidence and findings rather than a bare approval.

High- or medium-safety findings and contract errors must be resolved before merge. Observation-dependent calibration findings are added to the accuracy tracker and do not get silently folded into #223.

## 13. Delivery and PR structure

The intended delivery is a tested PR linked to #223. Genuine audit blockers may be submitted as focused prerequisite PRs when separating them materially improves reviewability. Non-blocking calibration work remains separate.

The #223 PR contains:

- the shared evidence contract and evaluator migrations;
- web focus overlays and emphasis;
- non-cross-section actions;
- method badges;
- tests, fixtures, and synchronized design documentation;
- audit evidence and independent-review findings;
- screenshots and verification commands/results.

The merge gate is:

1. audit blockers resolved;
2. no unsupported threshold recalibration;
3. backend, frontend, and compatibility tests passing;
4. design documents synchronized;
5. independent meteorology review addressed.

## 14. Acceptance criteria

- Clicking a spatial advisory action shows the backend-identified regions for the same model that supplied the aggregate detail.
- A user can explicitly inspect another model without cross-model evidence merging.
- Missing required data cannot appear as clear/GREEN solely because the data structure was absent.
- Highlighted route distance follows actual route geometry rather than point-count proportion.
- Cross-section, graph, and map show consistent evidence without recalculating meteorology.
- Model agreement, DD/NWP agreement, airport, and fronts actions open semantically correct surfaces.
- Method badges identify the method that actually controlled the result.
- Old briefing packs remain usable without fabricated focus data.
- Relevant documentation, tests, audit evidence, and independent review accompany the PR.
