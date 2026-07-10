# Issue #223 Advisory Evidence and Focus Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver a tested pull request for issue #223 that exposes model-specific advisory evidence, fixes confirmed objective metric and missing-data errors, and lets the web briefing focus the exact backend-identified route/altitude regions without recalculating meteorology in the browser.

**Architecture:** The backend remains authoritative. Migrated evaluators derive status, detail, evidence regions, data state, provenance, and distance metrics from one set of assessments, while an additive Pydantic contract preserves legacy packs. The web app resolves one representative or explicitly selected model into ephemeral focus state, adapts those regions to the cross-section, route graph, route map, Compare view, airport drawer, and fronts action, and never unions evidence across forecast models.

**Tech Stack:** Python 3.11+, Pydantic v2, pytest, TypeScript 5.9, Zustand vanilla store, Canvas 2D, Leaflet, Vitest, Playwright, HTML/CSS, GitHub CLI.

---

## Authoritative inputs and policy

Read these before executing any task, in this order:

1. .claude/CLAUDE.md
2. designs/INDEX.md
3. designs/meteorology-decisions.md
4. designs/advisories.md
5. designs/analysis-metrics.md
6. designs/visualization.md
7. designs/cloud-layers-analysis.md
8. designs/icing-models-analysis.md
9. designs/convective-analysis.md
10. designs/cross-cutting-review.md
11. docs/superpowers/specs/2026-07-10-issue-223-advisory-evidence-design.md

The approved design is binding. In particular:

- Fix objective computation, units, missing-data, aggregation, schema, and display-contract errors.
- Do not recalibrate meteorological thresholds without authoritative literature, an independent implementation/oracle, or observations.
- Keep evidence model-specific. Never merge geometry from different models.
- Keep active focus ephemeral and outside persisted VizSettings.
- Do not open cross-model Compare mode for DD-versus-NWP agreement.
- Reuse the Skew-T linkage already shipped in #309.
- Do not run npm run build. Use targeted Vitest, npx tsc --noEmit, Playwright, and /devserver.
- A fresh-model meteorology review is a merge gate, not an optional polish step.

## File responsibility map

### Backend contract and shared logic

- Modify src/weatherbrief/models/advisories.py: add AdvisoryEvidenceRegion, additive result metadata, safe unavailable aggregation, and representative_model.
- Modify src/weatherbrief/models/__init__.py: re-export AdvisoryEvidenceRegion.
- Create src/weatherbrief/analysis/advisories/evidence.py: route-cell geometry, evidence coalescing, data-state calculation, distance summaries, spatial/non-spatial result builders, and method normalization helpers.
- Modify src/weatherbrief/analysis/advisories/__init__.py: carry resolved method choices on RouteContext.
- Modify src/weatherbrief/tasks/advise.py and src/weatherbrief/analysis/advisories/altitude_table.py: thread method context through every production RouteContext constructor.
- Modify src/weatherbrief/analysis/advisories/registry.py: turn evaluator exceptions into explicit unavailable results.
- Modify src/weatherbrief/analysis/advisories/strings.py: localized partial/failure messages.

### Objective audit corrections

- Modify src/weatherbrief/analysis/sounding/sfip.py: normalize remaining fuzzy weights only when omega is structurally absent.
- Modify src/weatherbrief/analysis/advisories/dd_nwp_agreement.py: compute interval Jaccard from merged unions/intersections.
- Modify web/ts/visualization/route-map/metrics.ts: align SFIP colours and legend with backend 15/30/55 thresholds.
- Create docs/superpowers/audits/2026-07-10-issue-223-meteorology-audit.md: evidence, impact, tests, disposition, and deferred calibration findings.

### Evaluator migrations

- Modify cloud_top.py and vmc_cruise.py.
- Modify icing_escape.py, fiki_icing.py, freezing_precip.py, and enroute_precip.py.
- Modify turbulence.py and mountain_wind.py.
- Modify convective.py.
- Modify vfr_feasibility.py and ifr_feasibility.py.
- Modify model_agreement.py, dd_nwp_agreement.py, airport_wind.py, flight_category.py, density_altitude.py, llws.py, and fronts.py.

### Web contract, focus, and actions

- Modify web/ts/types/advisories.ts and web/ts/visualization/types.ts.
- Modify web/ts/visualization/data-extract.ts to preserve stable pointIndex.
- Create web/ts/visualization/advisory-focus.ts: validation, selection, cell geometry, focus lifecycle reconciliation, and thin rendering adapters.
- Create web/ts/visualization/advisory-actions.ts: typed action registry and pure action planning.
- Create web/ts/visualization/advisory-methods.ts: backend method-id labels and accessible descriptions.
- Modify web/ts/visualization/cross-section/advisory-presets.ts: optional highlights and emphasize directives.
- Modify CrossSectionRenderer, CompareSectionRenderer, RouteGraphRenderer, and RouteMapRenderer to accept resolved focus.

### Web state and UI

- Modify web/ts/store/briefing-store.ts: non-persisted activeAdvisoryFocus and lifecycle actions.
- Modify web/ts/managers/advisories-ui.ts: typed buttons, method badges, per-model Show on chart, and disabled explanations.
- Modify web/ts/briefing-main.ts and web/briefing.html: dispatch actions, resolve focus, render the focus banner, and wire all surfaces.
- Create web/ts/components/briefing-airport-profile-drawer.ts: departure/arrival tabs around AirportProfilePanel.
- Modify web/ts/visualization/airport-profile-panel.ts only for reusable host messaging/model fallback hooks; do not duplicate its renderer or SSE lifecycle.
- Move reusable .ap-* styles from web/maps.html into web/css/style.css, then add focus/banner/action styles there.
- Modify all four locale files under web/ts/i18n/locales/.

### Tests, fixtures, docs, and review

- Add focused backend tests under tests/analysis/advisories/.
- Update tests/test_sfip.py and airport/evaluator tests.
- Add Vitest files under web/tests/unit/.
- Create web/tests/unit/fixtures/advisory-focus.ts for shared typed focus/action fixtures used by Tasks 10–13.
- Extend web/tests/fixtures/egtf_eglf/advisories.json with representative, disconnected, partial, legacy/unavailable, airport, and fronts cases.
- Extend web/tests/briefing.spec.ts with action, lifecycle, accessibility, and rendering acceptance tests.
- Modify app/flyfun-weather/flyfun-weatherTests/flyfun_weatherTests.swift to prove the existing Swift Codable models ignore the additive #223 keys.
- Synchronize designs/advisories.md, designs/data-models.md, designs/analysis-metrics.md, designs/visualization.md, and designs/route-graph.md.
- Add .github/PULL_REQUEST_TEMPLATE.md and docs/meteorology-review-checklist.md for the permanent fresh-model gate.

## Execution preflight

- [ ] **Step 1: Confirm the isolated branch and clean scope**

Run:

~~~bash
git status --short --branch
git log -2 --oneline
~~~

Expected: branch codex/issue-223-evidence-contract, design commit c70547a1 present, and no implementation changes.

- [ ] **Step 2: Initialize this worktree's own environments if absent**

Run:

~~~bash
test -x venv/bin/python || python3 -m venv venv
source venv/bin/activate
python -m pip install -e ".[dev]"
cd web
test -d node_modules || npm ci
~~~

Expected: imports resolve from this worktree, not a sibling worktree, and web/node_modules exists.

- [ ] **Step 3: Record baseline targeted test output**

Run:

~~~bash
source venv/bin/activate
pytest tests/analysis/advisories/test_aggregation.py tests/analysis/advisories/test_registry.py tests/test_sfip.py -q
cd web
npx vitest run tests/unit/advisory-presets.test.ts tests/unit/route-map-metrics.test.ts
npx tsc --noEmit
~~~

Expected: baseline passes. If it does not, stop and record the pre-existing failure in the audit ledger before changing code.

### Task 1: Create the audit ledger and fix confirmed objective metric errors

**Files:**

- Create: docs/superpowers/audits/2026-07-10-issue-223-meteorology-audit.md
- Modify: src/weatherbrief/analysis/sounding/sfip.py:31-40,203-272
- Modify: src/weatherbrief/analysis/advisories/dd_nwp_agreement.py:32-70
- Modify: web/ts/visualization/route-map/metrics.ts:267-292
- Test: tests/test_sfip.py
- Create: tests/analysis/advisories/test_dd_nwp_agreement.py
- Test: web/tests/unit/route-map-metrics.test.ts
- Test: tests/analysis/advisories/test_airport_advisories.py

- [ ] **Step 1: Seed the audit ledger with evidence-gated dispositions**

Create the document with this table and keep it updated as later tasks find evidence:

~~~markdown
# Issue #223 Meteorology and Metrics Audit

| ID | Area | Current behaviour | Intended contract | Evidence | Impact | Regression test | Disposition |
| --- | --- | --- | --- | --- | --- | --- | --- |
| A223-01 | Turbulence | Sounding exists but vertical_motion is absent; point votes clear | Missing CAT/vertical-motion structure is unavailable, never smooth | Code path plus designs/meteorology-approach-review-2026-06.md §3.6 | High safety | test_turbulence_missing_vertical_motion_is_unavailable | Blocker; fix in #223 |
| A223-02 | Aggregation | Empty/all-unavailable input becomes GREEN | Empty/all-unavailable becomes UNAVAILABLE | Direct model helper inspection | High safety | test_empty_and_all_unavailable_aggregate_unavailable | Blocker; fix in #223 |
| A223-03 | Registry | Evaluator exception is logged and result disappears | Preserve advisory as unavailable with diagnostics | Direct registry inspection | Medium contract | test_registry_exception_returns_unavailable_result | Blocker; fix in #223 |
| A223-04 | Airport | Missing airport domain returns empty per_model | Emit one unavailable result per requested model | Direct evaluator inspection | Medium contract | test_missing_airport_domain_is_explicitly_unavailable | Blocker; fix in #223 |
| A223-05 | Distance | affected_nm is point-count proportion | Use midpoint-owned route cells and union geometry | Objective geometry contract | Medium display | test_uneven_route_midpoint_cells | Fix in #223 |
| A223-06 | SFIP display | Map colours use 20/50/80 | Use backend/catalog 15/30/55 | sfip_to_risk and tests/test_sfip.py | Medium contract | route-map SFIP boundary tests | Blocker; fix in #223 |
| A223-07 | SFIP no-vv | Missing omega retains dead 10%/15% weight | Normalize remaining weights; do not alter thresholds | Fuzzy weights sum and existing design review | Medium model-vote bias | no-vv normalization tests | Fix in #223 |
| A223-08 | DD/NWP clouds | Pairwise intersection double-counts overlaps; Jaccard can exceed 1 | Merge each interval set and intersect the unions | Set geometry identity | Medium diagnostic | overlapping-layer Jaccard test | Fix in #223 |
| A223-09 | Airport gust vector | Crosswind and gust graded separately | No change without POH/standard/observation evidence | Meteorology approach review §3.8 | Calibration-dependent | characterization test | Defer; separate evidence issue |
| A223-10 | DD okta labels | Vertical saturation is treated as horizontal coverage | No threshold/label recalibration without observations | Meteorology decisions §1 and approach review §3.4 | Calibration-dependent | Existing cloud tests | Defer |
| A223-11 | Ri/SLD/resolution | Potential method and resolution improvements | Validate/document only unless an oracle or observations support a change | Design reviews | Calibration-dependent | N/A | Track separately |
| A223-12 | Convective character | `convective_character.py` is an explanatory characterization path, not the active advisory grade predicate | Verify units, inputs, and separation from `ConvectiveEvaluator`; make no calibration change without qualifying evidence | Direct code/design comparison | Review/no-change unless a contract defect is found | Record inspected symbols and existing characterization tests | Audited explicitly; disposition required |
| A223-13 | Ogimet-NWP icing availability | `_resolve_analyses` can place an empty Ogimet-NWP zone list in the active slot when `nwp_cloud_layers is None`; evaluators can count it as assessed clear | Native NWP cloud geometry is a required input for Ogimet-NWP; absent geometry is unavailable, while an available empty list is assessed clear | `designs/analysis-metrics.md` Ogimet-NWP requirements plus direct code path | High missing-versus-clear | test_ogimet_nwp_without_native_cloud_geometry_is_unavailable | Blocker; fix in #223 without threshold change |
| A223-14 | Convective aggregate attribution | `ConvectiveEvaluator` replaces the representative model's detail with a cross-model percentage range while aggregate mitigations still come from one model | `aggregate_detail`, `aggregate_mitigations`, and `representative_model` must identify the same per-model source | Approved #223 backend contract plus direct override block | Medium contract | test_convective_aggregate_detail_is_representative_model_owned | Blocker; remove the cross-model detail override in #223 |
| A223-15 | Scenario/alternate assessment | `derive_assessment_from_advisories` filters unavailable advisories and returns GREEN when nothing valid remains | An empty/all-unavailable advisory picture is UNAVAILABLE, not a clear scenario | Direct helper inspection and missing-data policy | High safety/display | test_derive_assessment_all_unavailable_is_unavailable | Blocker; fix string status and consumers in #223 |
~~~

Before moving to Step 2, add reviewed/no-change or new-finding rows for every
item in this audit matrix:

~~~text
cloud_top.py, vmc_cruise.py
icing_escape.py, fiki_icing.py, freezing_precip.py
turbulence.py, mountain_wind.py
convective.py, convective_character.py
airport_wind.py, flight_category.py, density_altitude.py, llws.py
models/advisories.py, registry.py
route-map/metrics.ts, route-graph/metrics.ts, cross-section layer thresholds
~~~

Each row must name the inspected predicate/equation, current and intended
contract, evidence source, safety/display impact, regression test or reason no
test is needed, and one of: blocker, direct correction, no change, or
evidence-dependent follow-up. Do not silently omit an audited component because
no defect was found.

- [ ] **Step 2: Write failing SFIP normalization tests**

Append:

~~~python
def test_full_no_vv_renormalizes_remaining_memberships():
    _, score_missing, _, variant_missing = compute_sfip_level(
        temperature_c=-7.0,
        rh_pct=100.0,
        dewpoint_depression_c=0.0,
        clw_g_kg=0.2,
        icmr_g_kg=None,
        omega_pa_s=None,
        cloud_cover_at_band=100.0,
    )
    _, score_quiescent, _, variant_quiescent = compute_sfip_level(
        temperature_c=-7.0,
        rh_pct=100.0,
        dewpoint_depression_c=0.0,
        clw_g_kg=0.2,
        icmr_g_kg=None,
        omega_pa_s=0.0,
        cloud_cover_at_band=100.0,
    )
    assert variant_missing == "full_no_vv"
    assert variant_quiescent == "full"
    assert score_missing == 100.0
    assert score_quiescent == 85.0


def test_proxy_no_vv_renormalizes_remaining_memberships():
    _, score_missing, _, variant_missing = compute_sfip_level(
        temperature_c=-7.0,
        rh_pct=100.0,
        dewpoint_depression_c=0.0,
        clw_g_kg=None,
        icmr_g_kg=None,
        omega_pa_s=None,
        cloud_cover_at_band=100.0,
    )
    _, score_quiescent, _, variant_quiescent = compute_sfip_level(
        temperature_c=-7.0,
        rh_pct=100.0,
        dewpoint_depression_c=0.0,
        clw_g_kg=None,
        icmr_g_kg=None,
        omega_pa_s=0.0,
        cloud_cover_at_band=100.0,
    )
    assert variant_missing == "proxy_no_vv"
    assert variant_quiescent == "proxy"
    assert score_missing == 100.0
    assert score_quiescent == 90.0
~~~

- [ ] **Step 3: Write the failing cloud-overlap regression**

Create:

~~~python
import pytest

from weatherbrief.analysis.advisories.dd_nwp_agreement import _cloud_overlap_fraction
from weatherbrief.models import EnhancedCloudLayer


def _layer(base: float, top: float) -> EnhancedCloudLayer:
    return EnhancedCloudLayer(base_ft=base, top_ft=top)


def test_cloud_overlap_merges_internal_overlaps_before_jaccard():
    dd = [_layer(0, 10_000), _layer(5_000, 15_000)]
    nwp = [_layer(0, 15_000)]
    overlap = _cloud_overlap_fraction(dd, nwp)
    assert overlap == 1.0
    assert 0.0 <= overlap <= 1.0


def test_cloud_overlap_handles_disjoint_unions():
    dd = [_layer(0, 5_000), _layer(10_000, 15_000)]
    nwp = [_layer(2_500, 12_500)]
    assert _cloud_overlap_fraction(dd, nwp) == pytest.approx(1 / 3)
~~~

- [ ] **Step 4: Write failing SFIP route-map boundary tests**

Replace the current 20/50/80 assertions with:

~~~typescript
it('uses the authoritative backend SFIP thresholds 15/30/55', () => {
  expect(m().getColor(14.9)).toBe('#22c55e');
  expect(m().getColor(15)).toBe('#facc15');
  expect(m().getColor(29.9)).toBe('#facc15');
  expect(m().getColor(30)).toBe('#f97316');
  expect(m().getColor(54.9)).toBe('#f97316');
  expect(m().getColor(55)).toBe('#ef4444');
});
~~~

- [ ] **Step 5: Run the new tests and confirm the defects**

Run:

~~~bash
source venv/bin/activate
pytest tests/test_sfip.py -k no_vv -v
pytest tests/analysis/advisories/test_dd_nwp_agreement.py -v
cd web
npx vitest run tests/unit/route-map-metrics.test.ts
~~~

Expected: SFIP missing-omega tests fail with 85/90 instead of 100, overlap can exceed 1 or disagrees with one-third, and map boundaries still use 20/50/80.

- [ ] **Step 6: Normalize only structurally absent SFIP members**

Add this helper and use it for both variants:

~~~python
def _weighted_membership(
    components: list[tuple[float, float]],
    *,
    normalize: bool,
) -> float:
    weighted = sum(weight * membership for weight, membership in components)
    if not normalize:
        return weighted
    denominator = sum(weight for weight, _ in components)
    return weighted / denominator if denominator > 0 else 0.0
~~~

The full branch must build:

~~~python
components = [
    (_W_T_FULL, m_t),
    (_W_RH_FULL, m_rh),
    (_W_CLW_FULL, m_clw),
]
if has_vv:
    components.append((_W_VV_FULL, m_vv))
sfip_value = _weighted_membership(components, normalize=not has_vv)
~~~

The proxy branch must use the same pattern with its proxy weights. Do not renormalize a real quiescent omega value of 0.0; only omega_pa_s is None is structural absence.

- [ ] **Step 7: Replace pairwise overlap with merged interval-set Jaccard**

Use:

~~~python
def _merge_spans(layers: list[EnhancedCloudLayer]) -> list[tuple[float, float]]:
    merged: list[list[float]] = []
    for base, top in sorted((cl.base_ft, cl.top_ft) for cl in layers):
        if top <= base:
            continue
        if merged and base <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], top)
        else:
            merged.append([base, top])
    return [(base, top) for base, top in merged]


def _span_length(spans: list[tuple[float, float]]) -> float:
    return sum(top - base for base, top in spans)


def _intersection_length(
    a: list[tuple[float, float]],
    b: list[tuple[float, float]],
) -> float:
    total = 0.0
    i = j = 0
    while i < len(a) and j < len(b):
        lo = max(a[i][0], b[j][0])
        hi = min(a[i][1], b[j][1])
        if hi > lo:
            total += hi - lo
        if a[i][1] <= b[j][1]:
            i += 1
        else:
            j += 1
    return total
~~~

Then calculate intersection once, union as len(A)+len(B)-intersection, and clamp the final ratio to [0, 1].

- [ ] **Step 8: Align the route-map display contract**

Implement:

~~~typescript
getColor: (v) => {
  if (v < 15) return '#22c55e';
  if (v < 30) return '#facc15';
  if (v < 55) return '#f97316';
  return '#ef4444';
},
legendStops: [
  { value: 0, label: 'None (<15)', color: '#22c55e' },
  { value: 15, label: 'Light (15–29)', color: '#facc15' },
  { value: 30, label: 'Moderate (30–54)', color: '#f97316' },
  { value: 55, label: 'Severe (55+)', color: '#ef4444' },
],
~~~

- [ ] **Step 9: Add a no-recalibration airport-wind characterization**

Add a test proving the current policy remains separate mean crosswind plus absolute gust:

~~~python
def test_gust_vector_crosswind_is_not_recalibrated_without_evidence():
    status = _wind_status(
        crosswind_kt=12.0,
        gust_kt=28.0,
        xwind_green=15.0,
        xwind_red=25.0,
        gust_green=25.0,
        gust_red=35.0,
    )
    assert status == AdvisoryStatus.AMBER
~~~

This is a characterization, not an endorsement. Link it to A223-09 in the audit ledger.

- [ ] **Step 10: Verify and commit the objective corrections**

Run:

~~~bash
source venv/bin/activate
pytest tests/test_sfip.py tests/analysis/advisories/test_dd_nwp_agreement.py tests/analysis/advisories/test_airport_advisories.py -q
cd web
npx vitest run tests/unit/route-map-metrics.test.ts
cd ..
git diff --check
git add docs/superpowers/audits/2026-07-10-issue-223-meteorology-audit.md src/weatherbrief/analysis/sounding/sfip.py src/weatherbrief/analysis/advisories/dd_nwp_agreement.py tests/test_sfip.py tests/analysis/advisories/test_dd_nwp_agreement.py tests/analysis/advisories/test_airport_advisories.py web/ts/visualization/route-map/metrics.ts web/tests/unit/route-map-metrics.test.ts
git commit -m "fix(meteorology): correct advisory metric contracts"
~~~

Expected: all targeted tests pass; no thresholds other than the display mapping changed.

### Task 2: Add the additive evidence contract, safe aggregation, registry failures, and method context

**Files:**

- Modify: src/weatherbrief/models/advisories.py:15-299
- Modify: src/weatherbrief/models/__init__.py:82-101
- Modify: src/weatherbrief/analysis/advisories/__init__.py:31-62
- Modify: src/weatherbrief/analysis/advisories/registry.py:97-158
- Modify: src/weatherbrief/analysis/advisories/strings.py
- Modify: src/weatherbrief/tasks/advise.py:280-390,416-550,560-640,735-870
- Modify: src/weatherbrief/analysis/advisories/altitude_table.py:92-190
- Create: tests/analysis/advisories/test_evidence_contract.py
- Modify: tests/analysis/advisories/test_aggregation.py
- Modify: tests/analysis/advisories/test_registry.py
- Create: tests/test_advise_method_context.py
- Modify: app/flyfun-weather/flyfun-weatherTests/flyfun_weatherTests.swift

- [ ] **Step 1: Write failing model and aggregation tests**

Create:

~~~python
import pytest
from pydantic import ValidationError

from weatherbrief.models import (
    AdvisoryAggregation,
    AdvisoryEvidenceRegion,
    AdvisoryStatus,
    Mitigation,
    MitigationKind,
    ModelAdvisoryResult,
    RouteAdvisoryResult,
    RouteAdvisoriesManifest,
)
from weatherbrief.tasks.advise import derive_assessment_from_advisories


def test_legacy_model_result_has_unknown_data_state_and_no_evidence():
    result = ModelAdvisoryResult.model_validate({
        "model": "gfs",
        "status": "amber",
        "detail": "legacy",
    })
    assert result.data_state is None
    assert result.primary_method_id is None
    assert result.evidence_regions == []


@pytest.mark.parametrize("payload", [
    {
        "start_point_index": 4,
        "end_point_index": 2,
        "severity": "amber",
        "reason_code": "bad_order",
    },
    {
        "start_point_index": 1,
        "end_point_index": 1,
        "lower_altitude_ft": 5000,
        "upper_altitude_ft": None,
        "severity": "amber",
        "reason_code": "half_bounds",
    },
    {
        "start_point_index": 1,
        "end_point_index": 1,
        "lower_altitude_ft": 9000,
        "upper_altitude_ft": 5000,
        "severity": "amber",
        "reason_code": "reversed_bounds",
    },
    {
        "start_point_index": 1,
        "end_point_index": 1,
        "severity": "unavailable",
        "reason_code": "bad_severity",
    },
    {
        "start_point_index": 1,
        "end_point_index": 1,
        "severity": "amber",
        "reason_code": "   ",
    },
])
def test_invalid_evidence_region_is_rejected(payload):
    with pytest.raises(ValidationError):
        AdvisoryEvidenceRegion.model_validate(payload)


def test_empty_and_all_unavailable_aggregate_unavailable():
    empty = RouteAdvisoryResult.from_per_model(
        "cloud_top", [], {}, AdvisoryAggregation.MAJORITY,
    )
    all_missing = RouteAdvisoryResult.from_per_model(
        "cloud_top",
        [
            ModelAdvisoryResult(model="gfs", status=AdvisoryStatus.UNAVAILABLE),
            ModelAdvisoryResult(model="ecmwf", status=AdvisoryStatus.UNAVAILABLE),
        ],
        {},
        AdvisoryAggregation.WORST,
    )
    assert empty.aggregate_status == AdvisoryStatus.UNAVAILABLE
    assert empty.representative_model is None
    assert all_missing.aggregate_status == AdvisoryStatus.UNAVAILABLE
    assert all_missing.representative_model == "gfs"


def test_representative_model_matches_detail_and_mitigations_source():
    red_mitigation = Mitigation(
        kind=MitigationKind.ALTITUDE,
        addresses="cloud_top",
        detail="descend",
        mitigated_status=AdvisoryStatus.AMBER,
        altitude_ft=6000,
    )
    result = RouteAdvisoryResult.from_per_model(
        "cloud_top",
        [
            ModelAdvisoryResult(model="gfs", status=AdvisoryStatus.GREEN, detail="g"),
            ModelAdvisoryResult(
                model="ecmwf",
                status=AdvisoryStatus.RED,
                detail="r",
                mitigations=[red_mitigation],
            ),
        ],
        {},
        AdvisoryAggregation.WORST,
    )
    assert result.aggregate_status == AdvisoryStatus.RED
    assert result.aggregate_detail == "r"
    assert result.representative_model == "ecmwf"
    assert result.aggregate_mitigations == [red_mitigation]


def test_majority_representative_model_matches_majority_detail():
    result = RouteAdvisoryResult.from_per_model(
        "cloud_top",
        [
            ModelAdvisoryResult(model="gfs", status=AdvisoryStatus.AMBER, detail="g"),
            ModelAdvisoryResult(model="ecmwf", status=AdvisoryStatus.GREEN, detail="e"),
            ModelAdvisoryResult(model="icon", status=AdvisoryStatus.AMBER, detail="i"),
        ],
        {},
        AdvisoryAggregation.MAJORITY,
    )
    assert result.aggregate_status == AdvisoryStatus.AMBER
    assert result.aggregate_detail == "g"
    assert result.representative_model == "gfs"


def test_new_evidence_contract_round_trips():
    original = ModelAdvisoryResult(
        model="gfs",
        status=AdvisoryStatus.AMBER,
        detail="evidence",
        data_state="partial",
        primary_method_id="nwp",
        evidence_regions=[AdvisoryEvidenceRegion(
            start_point_index=2,
            end_point_index=4,
            lower_altitude_ft=5000,
            upper_altitude_ft=9000,
            severity=AdvisoryStatus.AMBER,
            reason_code="cruise_in_bkn_cloud",
            metric_id="cloud_coverage",
            method_id="nwp",
        )],
    )
    decoded = ModelAdvisoryResult.model_validate_json(original.model_dump_json())
    assert decoded == original


def test_derive_assessment_all_unavailable_is_unavailable():
    manifest = RouteAdvisoriesManifest(advisories=[
        RouteAdvisoryResult.from_per_model(
            "cloud_top",
            [ModelAdvisoryResult(
                model="gfs",
                status=AdvisoryStatus.UNAVAILABLE,
                data_state="unavailable",
            )],
            {},
        ),
    ])
    assert derive_assessment_from_advisories(manifest) == (
        "UNAVAILABLE",
        "No advisory data available",
    )
~~~

- [ ] **Step 2: Write the failing registry-exception test**

Add:

~~~python
def test_registry_exception_returns_unavailable_result(monkeypatch, clear_context):
    class BrokenEvaluator:
        @staticmethod
        def catalog_entry():
            return AdvisoryCatalogEntry(
                id="broken_test",
                name="Broken",
                short_description="Broken",
                description="Broken",
                category="test",
            )

        @staticmethod
        def evaluate(ctx, params):
            raise RuntimeError("boom")

    monkeypatch.setattr(registry, "_EVALUATORS", {"broken_test": BrokenEvaluator})
    monkeypatch.setattr(registry, "_loaded", True)
    results = registry.evaluate_all(clear_context, enabled_ids={"broken_test"})
    assert len(results) == 1
    assert results[0].advisory_id == "broken_test"
    assert results[0].aggregate_status == AdvisoryStatus.UNAVAILABLE
    assert {m.model for m in results[0].per_model} == set(clear_context.models)
    assert all(m.data_state == "unavailable" for m in results[0].per_model)
~~~

- [ ] **Step 3: Run the tests and confirm contract failures**

Run:

~~~bash
source venv/bin/activate
pytest tests/analysis/advisories/test_evidence_contract.py tests/analysis/advisories/test_aggregation.py tests/analysis/advisories/test_registry.py -v
~~~

Expected: imports/fields fail, unavailable aggregation returns GREEN, and the broken evaluator is dropped.

- [ ] **Step 4: Add AdvisoryEvidenceRegion and additive result fields**

Implement:

~~~python
from pydantic import BaseModel, Field, model_validator


class AdvisoryEvidenceRegion(BaseModel):
    start_point_index: int
    end_point_index: int
    lower_altitude_ft: int | None = None
    upper_altitude_ft: int | None = None
    severity: AdvisoryStatus
    reason_code: str
    metric_id: str | None = None
    method_id: str | None = None

    @model_validator(mode="after")
    def _validate_geometry(self) -> "AdvisoryEvidenceRegion":
        if self.start_point_index > self.end_point_index:
            raise ValueError("start_point_index must not exceed end_point_index")
        has_lower = self.lower_altitude_ft is not None
        has_upper = self.upper_altitude_ft is not None
        if has_lower != has_upper:
            raise ValueError("altitude bounds must both be present or both absent")
        if has_lower and self.lower_altitude_ft > self.upper_altitude_ft:
            raise ValueError("lower_altitude_ft must not exceed upper_altitude_ft")
        if self.severity == AdvisoryStatus.UNAVAILABLE:
            raise ValueError("evidence severity cannot be unavailable")
        if not self.reason_code.strip():
            raise ValueError("reason_code must be non-empty")
        return self
~~~

Add to ModelAdvisoryResult:

~~~python
data_state: Literal["complete", "partial", "unavailable"] | None = None
primary_method_id: str | None = None
evidence_regions: list[AdvisoryEvidenceRegion] = Field(default_factory=list)
~~~

Add to RouteAdvisoryResult:

~~~python
representative_model: str | None = None
~~~

Re-export AdvisoryEvidenceRegion from models/__init__.py.

- [ ] **Step 5: Make both aggregation modes unavailable-safe**

Implement the common valid-status rule:

~~~python
@classmethod
def worst(cls, statuses: list["AdvisoryStatus"]) -> "AdvisoryStatus":
    order = [cls.GREEN, cls.AMBER, cls.RED]
    valid = [status for status in statuses if status in order]
    if not valid:
        return cls.UNAVAILABLE
    return max(valid, key=order.index)
~~~

majority must likewise return UNAVAILABLE when valid is empty. In from_per_model, set representative_model from the same representative object used for aggregate_detail and aggregate_mitigations.
Rename/update the existing `test_all_unavailable_returns_green` and
`test_empty_returns_green` unit cases in `test_aggregation.py` to expect
UNAVAILABLE, and add equivalent direct tests for `AdvisoryStatus.worst`.

Update `derive_assessment_from_advisories` so a manifest with no valid
GREEN/AMBER/RED advisory returns `("UNAVAILABLE", "No advisory data available")`.
Keep UNAVAILABLE excluded from better/worse timing comparisons; this changes
only the displayed no-data assessment, not scenario ranking.

In `registry.evaluate_all`, remove the obsolete guard that skips re-aggregation
when every per-model status is UNAVAILABLE, plus its GREEN-collapse comment. With the fixed aggregation
helpers, WORST re-aggregation of an all-unavailable evaluator must remain
UNAVAILABLE and retain the first unavailable model as its representative.

- [ ] **Step 6: Preserve evaluator failures as unavailable results**

In registry.py, append this result inside the exception handler:

~~~python
except Exception:
    logger.warning("Advisory %s evaluation failed", adv_id, exc_info=True)
    failed_models = ctx.models or ["all"]
    detail = adv_t("evaluation_failed", ctx.locale)
    per_model = [
        ModelAdvisoryResult(
            model=model,
            status=AdvisoryStatus.UNAVAILABLE,
            detail=detail,
            data_state="unavailable",
        )
        for model in failed_models
    ]
    results.append(RouteAdvisoryResult.from_per_model(adv_id, per_model, params))
~~~

Add evaluation_failed and partial_data strings in all four backend locales in strings.py. Do not expose the exception text to the pilot; the logger carries diagnostics.

- [ ] **Step 7: Add method fields to RouteContext**

Add:

~~~python
icing_method: str | None = None
cloud_method: str | None = None
convective_method: str | None = None
~~~

Thread the original resolved profile values through these four production `RouteContext` constructors:

1. run_advisories
2. run_advisories_from_pack
3. run_alt_from_pack
4. compute_altitude_table

Also add the three optional parameters to compute_altitude_table and pass them from both call sites: the altitude-table precompute inside run_advisories and run_altitude_table_from_pack. Test spies must assert the exact values survive to each RouteContext rather than merely checking that the new keyword arguments were accepted.

- [ ] **Step 8: Prove additive decoding remains iOS-compatible**

Extend `modelAdvisoryResultDecodesCrossCheckAndParameters` in
`app/flyfun-weather/flyfun-weatherTests/flyfun_weatherTests.swift` by adding
`representative_model` beside the route advisory's `aggregate_detail`, and the
other three server keys inside the GFS per-model object, while deliberately
leaving the Swift `RouteAdvisoryResult` and `ModelAdvisoryResult` structs
unchanged:

~~~json
"representative_model": "gfs",
"data_state": "partial",
"primary_method_id": "nwp_with_dd_floor",
"evidence_regions": [
  {
    "start_point_index": 2,
    "end_point_index": 4,
    "lower_altitude_ft": 5000,
    "upper_altitude_ft": 25000,
    "severity": "red",
    "reason_code": "convective_dd_floor",
    "metric_id": "convective_risk",
    "method_id": "nwp_with_dd_floor"
  }
]
~~~

Keep the existing decode assertions. Their successful execution proves the
app's synthesized Codable conformance ignores unknown additive keys. Do not add
iOS focus UI or mirror the new fields into Swift in #223.

- [ ] **Step 9: Verify contract, callers, and commit**

Run:

~~~bash
source venv/bin/activate
pytest tests/analysis/advisories/test_evidence_contract.py tests/analysis/advisories/test_aggregation.py tests/analysis/advisories/test_registry.py tests/test_advise_method_context.py -q
rg -n "RouteContext\\(" src/weatherbrief
git diff --check
git add src/weatherbrief/models/advisories.py src/weatherbrief/models/__init__.py src/weatherbrief/analysis/advisories/__init__.py src/weatherbrief/analysis/advisories/registry.py src/weatherbrief/analysis/advisories/strings.py src/weatherbrief/tasks/advise.py src/weatherbrief/analysis/advisories/altitude_table.py tests/analysis/advisories/test_evidence_contract.py tests/analysis/advisories/test_aggregation.py tests/analysis/advisories/test_registry.py tests/test_advise_method_context.py app/flyfun-weather/flyfun-weatherTests/flyfun_weatherTests.swift
git commit -m "feat(advisories): add evidence and provenance contract"
~~~

Expected: every production RouteContext constructor carries method context; empty/all-unavailable no longer becomes GREEN.

### Task 3: Build the shared evidence helper and midpoint-cell geometry

**Files:**

- Create: src/weatherbrief/analysis/advisories/evidence.py
- Create: tests/analysis/advisories/test_evidence.py
- Modify: src/weatherbrief/analysis/advisories/_helpers.py:106-123 only to deprecate point-count format_extent for migrated evaluators

- [ ] **Step 1: Write failing geometry, coalescing, and data-state tests**

Create `tests/analysis/advisories/test_evidence.py` with these imports and the
real-model fixture used by every test in the module:

~~~python
from datetime import datetime

import pytest

from weatherbrief.analysis.advisories.evidence import (
    EvidenceSample,
    build_non_spatial_result,
    cloud_method_id,
    icing_method_is_available,
    summarize_evidence,
)
from weatherbrief.models import AdvisoryStatus, RoutePointAnalysis, SoundingAnalysis


@pytest.fixture
def route_points():
    def build(
        distances: list[float],
        *,
        point_indices: list[int] | None = None,
    ) -> list[RoutePointAnalysis]:
        indices = point_indices or list(range(len(distances)))
        return [
            RoutePointAnalysis(
                point_index=point_index,
                lat=50.0 + position,
                lon=-1.0 + position,
                distance_from_origin_nm=distance,
                interpolated_time=datetime(2026, 7, 10, 10, 0),
                forecast_hour=datetime(2026, 7, 10, 9, 0),
                track_deg=90.0,
            )
            for position, (point_index, distance) in enumerate(zip(indices, distances))
        ]
    return build


def test_uneven_route_midpoint_cells(route_points):
    summary = summarize_evidence(
        route_points=route_points([0, 10, 50, 100]),
        total_distance_nm=100,
        evaluated_point_indices={0, 1, 2, 3},
        complete_point_indices={0, 1, 2, 3},
        affected_point_indices={1, 2},
        evidence_samples=[],
    )
    assert summary.affected_nm == 70.0
    assert summary.affected_pct == 50.0


def test_isolated_endpoint_owns_a_nonzero_bounded_cell(route_points):
    summary = summarize_evidence(
        route_points=route_points([0, 10, 50, 100]),
        total_distance_nm=100,
        evaluated_point_indices={0, 1, 2, 3},
        complete_point_indices={0, 1, 2, 3},
        affected_point_indices={0},
        evidence_samples=[],
    )
    assert summary.affected_nm == 5.0


def test_regions_split_on_gap_reason_method_severity_and_altitude(route_points):
    points = route_points([0, 10, 20, 30, 40])
    samples = [
        EvidenceSample(0, AdvisoryStatus.AMBER, "cloud", "cloud_coverage", "nwp", 4000, 8000),
        EvidenceSample(1, AdvisoryStatus.AMBER, "cloud", "cloud_coverage", "nwp", 4000, 8000),
        EvidenceSample(3, AdvisoryStatus.AMBER, "cloud", "cloud_coverage", "nwp", 4000, 8000),
        EvidenceSample(4, AdvisoryStatus.RED, "cloud", "cloud_coverage", "nwp", 4000, 8000),
    ]
    summary = summarize_evidence(
        route_points=points,
        total_distance_nm=40,
        evaluated_point_indices={0, 1, 2, 3, 4},
        complete_point_indices={0, 1, 2, 3, 4},
        affected_point_indices={0, 1, 3, 4},
        evidence_samples=samples,
    )
    assert [(r.start_point_index, r.end_point_index) for r in summary.evidence_regions] == [
        (0, 1), (3, 3), (4, 4),
    ]


def test_partial_green_is_guarded_to_unavailable(route_points):
    summary = summarize_evidence(
        route_points=route_points([0, 10, 20]),
        total_distance_nm=20,
        evaluated_point_indices={0, 1},
        complete_point_indices={0, 1},
        affected_point_indices=set(),
        evidence_samples=[],
    )
    result = summary.build_result(
        model="gfs",
        status=AdvisoryStatus.GREEN,
        detail="clear",
        unavailable_detail="partial data",
        primary_method_id="nwp",
    )
    assert result.status == AdvisoryStatus.UNAVAILABLE
    assert result.data_state == "partial"
    assert result.detail == "partial data"


def test_partial_red_preserves_supported_hazard(route_points):
    summary = summarize_evidence(
        route_points=route_points([0, 10, 20]),
        total_distance_nm=20,
        evaluated_point_indices={0, 1},
        complete_point_indices={0, 1},
        affected_point_indices={1},
        evidence_samples=[],
    )
    result = summary.build_result(
        model="gfs",
        status=AdvisoryStatus.RED,
        detail="severe evidence",
        unavailable_detail="partial data",
        primary_method_id="nwp",
    )
    assert result.status == AdvisoryStatus.RED
    assert result.data_state == "partial"


def test_complete_clear_stays_green(route_points):
    summary = summarize_evidence(
        route_points=route_points([0, 10, 20]),
        total_distance_nm=20,
        evaluated_point_indices={0, 1, 2},
        complete_point_indices={0, 1, 2},
        affected_point_indices=set(),
        evidence_samples=[],
    )
    result = summary.build_result(
        model="gfs",
        status=AdvisoryStatus.GREEN,
        detail="clear",
        unavailable_detail="missing",
        primary_method_id="nwp",
    )
    assert result.status == AdvisoryStatus.GREEN
    assert result.data_state == "complete"


def test_exact_duplicate_samples_create_one_region(route_points):
    sample = EvidenceSample(
        1, AdvisoryStatus.AMBER, "cloud", "cloud_coverage", "nwp", 4000, 8000,
    )
    summary = summarize_evidence(
        route_points=route_points([0, 10, 20]),
        total_distance_nm=20,
        evaluated_point_indices={0, 1, 2},
        complete_point_indices={0, 1, 2},
        affected_point_indices={1},
        evidence_samples=[sample, sample],
    )
    assert len(summary.evidence_regions) == 1
    assert summary.affected_nm == 10.0


def test_overlapping_reason_regions_do_not_double_count_distance(route_points):
    samples = [
        EvidenceSample(1, AdvisoryStatus.AMBER, "icing", "icing_risk", "ogimet_dd"),
        EvidenceSample(1, AdvisoryStatus.RED, "sld", "sld_risk", "ogimet_dd"),
    ]
    summary = summarize_evidence(
        route_points=route_points([0, 10, 20]),
        total_distance_nm=20,
        evaluated_point_indices={0, 1, 2},
        complete_point_indices={0, 1, 2},
        affected_point_indices={1},
        evidence_samples=samples,
    )
    assert len(summary.evidence_regions) == 2
    assert summary.affected_points == 1
    assert summary.affected_nm == 10.0


def test_regions_split_across_a_missing_stable_point_index(route_points):
    points = route_points([0, 10, 20], point_indices=[0, 2, 3])
    samples = [
        EvidenceSample(0, AdvisoryStatus.AMBER, "cloud", "cloud_coverage", "nwp"),
        EvidenceSample(2, AdvisoryStatus.AMBER, "cloud", "cloud_coverage", "nwp"),
    ]
    summary = summarize_evidence(
        route_points=points,
        total_distance_nm=20,
        evaluated_point_indices={0, 2, 3},
        complete_point_indices={0, 2, 3},
        affected_point_indices={0, 2},
        evidence_samples=samples,
    )
    assert [(r.start_point_index, r.end_point_index) for r in summary.evidence_regions] == [
        (0, 0), (2, 2),
    ]


def test_non_spatial_partial_hazard_keeps_supported_grade():
    result = build_non_spatial_result(
        model="gfs",
        status=AdvisoryStatus.AMBER,
        detail="departure affected",
        unavailable_detail="partial airport data",
        expected_entities={"departure", "arrival"},
        evaluated_entities={"departure"},
        complete_entities={"departure"},
        affected_entities={"departure"},
        primary_method_id="airport_conditions",
    )
    assert result.status == AdvisoryStatus.AMBER
    assert result.data_state == "partial"
    assert result.affected_points == 1


def test_method_provenance_does_not_guess_native_nwp():
    assert cloud_method_id("nwp_synthesized", "square_nwp") == "nwp_synthesized"
    assert cloud_method_id(None, "square_nwp") is None


def test_ogimet_nwp_distinguishes_missing_from_available_clear_geometry():
    missing = SoundingAnalysis(nwp_cloud_layers=None)
    available_clear = SoundingAnalysis(nwp_cloud_layers=[])
    assert not icing_method_is_available(missing, "ogimet_nwp")
    assert icing_method_is_available(available_clear, "ogimet_nwp")
~~~

- [ ] **Step 2: Run the tests and confirm the helper is absent**

Run:

~~~bash
source venv/bin/activate
pytest tests/analysis/advisories/test_evidence.py -v
~~~

Expected: collection fails because evidence.py and its API do not exist.

- [ ] **Step 3: Implement the helper API**

Use these public types and signatures:

~~~python
from __future__ import annotations

from dataclasses import dataclass
from typing import Collection, Literal, Sequence

from weatherbrief.models import (
    AdvisoryEvidenceRegion,
    AdvisoryStatus,
    Mitigation,
    ModelAdvisoryResult,
    RoutePointAnalysis,
    SoundingAnalysis,
)

DataState = Literal["complete", "partial", "unavailable"]


@dataclass(frozen=True)
class EvidenceSample:
    point_index: int
    severity: AdvisoryStatus
    reason_code: str
    metric_id: str | None = None
    method_id: str | None = None
    lower_altitude_ft: int | None = None
    upper_altitude_ft: int | None = None


@dataclass(frozen=True)
class EvidenceSummary:
    data_state: DataState
    affected_points: int
    total_points: int
    affected_pct: float
    affected_nm: float
    total_nm: float
    affected_mod_points: int
    affected_mod_pct: float
    affected_mod_nm: float
    evidence_regions: list[AdvisoryEvidenceRegion]

    def format_extent(self) -> str:
        return (
            f"{round(self.affected_nm)}nm/{round(self.total_nm)}nm "
            f"({self.affected_pct:.0f}%)"
        )

    def format_mod_extent(self) -> str:
        return (
            f"{round(self.affected_mod_nm)}nm/{round(self.total_nm)}nm "
            f"({self.affected_mod_pct:.0f}%)"
        )

    def build_result(
        self,
        *,
        model: str,
        status: AdvisoryStatus,
        detail: str,
        unavailable_detail: str,
        primary_method_id: str | None,
        cross_check: str | None = None,
        mitigations: list[Mitigation] | None = None,
    ) -> ModelAdvisoryResult:
        guarded = guard_status_for_data_state(status, self.data_state)
        return ModelAdvisoryResult(
            model=model,
            status=guarded,
            detail=unavailable_detail if guarded == AdvisoryStatus.UNAVAILABLE else detail,
            affected_points=self.affected_points,
            total_points=self.total_points,
            affected_pct=self.affected_pct,
            affected_nm=self.affected_nm,
            total_nm=self.total_nm,
            affected_mod_points=self.affected_mod_points,
            affected_mod_pct=self.affected_mod_pct,
            affected_mod_nm=self.affected_mod_nm,
            cross_check=cross_check,
            mitigations=mitigations or [],
            data_state=self.data_state,
            primary_method_id=primary_method_id,
            evidence_regions=self.evidence_regions,
        )
~~~

Implement these functions:

~~~python
def data_state_from_domains(
    *,
    expected: Collection[object],
    evaluated: Collection[object],
    complete: Collection[object],
) -> DataState:
    expected_set = set(expected)
    evaluated_set = set(evaluated) & expected_set
    complete_set = set(complete) & evaluated_set
    if not expected_set or not evaluated_set:
        return "unavailable"
    if complete_set == expected_set:
        return "complete"
    return "partial"


def combine_data_states(*states: DataState) -> DataState:
    if states and all(state == "complete" for state in states):
        return "complete"
    if any(state in ("complete", "partial") for state in states):
        return "partial"
    return "unavailable"


def guard_status_for_data_state(
    status: AdvisoryStatus,
    data_state: DataState,
) -> AdvisoryStatus:
    if data_state == "unavailable":
        return AdvisoryStatus.UNAVAILABLE
    if data_state == "partial" and status == AdvisoryStatus.GREEN:
        return AdvisoryStatus.UNAVAILABLE
    return status
~~~

summarize_evidence must accept:

~~~python
def summarize_evidence(
    *,
    route_points: Sequence[RoutePointAnalysis],
    total_distance_nm: float,
    evaluated_point_indices: Collection[int],
    complete_point_indices: Collection[int],
    affected_point_indices: Collection[int],
    evidence_samples: Sequence[EvidenceSample],
    moderate_point_indices: Collection[int] = (),
) -> EvidenceSummary:
    ordered = sorted(
        route_points,
        key=lambda point: (point.distance_from_origin_nm, point.point_index),
    )
    indices = [point.point_index for point in ordered]
    if len(indices) != len(set(indices)):
        raise ValueError("route point indices must be unique")
    if any(a >= b for a, b in zip(indices, indices[1:])):
        raise ValueError("route point indices must increase in route order")

    expected = set(indices)
    evaluated = set(evaluated_point_indices) & expected
    complete = set(complete_point_indices) & evaluated
    affected = set(affected_point_indices) & evaluated
    moderate = set(moderate_point_indices) & evaluated
    position_by_index = {
        point.point_index: position for position, point in enumerate(ordered)
    }

    cells: dict[int, tuple[float, float]] = {}
    for position, point in enumerate(ordered):
        distance = point.distance_from_origin_nm
        start = (
            0.0
            if position == 0
            else (
                ordered[position - 1].distance_from_origin_nm + distance
            ) / 2.0
        )
        end = (
            total_distance_nm
            if position == len(ordered) - 1
            else (
                distance + ordered[position + 1].distance_from_origin_nm
            ) / 2.0
        )
        cells[point.point_index] = (
            max(0.0, min(total_distance_nm, start)),
            max(0.0, min(total_distance_nm, end)),
        )

    def distance_for(point_indices: set[int]) -> float:
        return round(
            sum(cells[index][1] - cells[index][0] for index in point_indices),
            1,
        )

    grouped: dict[
        tuple[
            AdvisoryStatus,
            str,
            str | None,
            str | None,
            int | None,
            int | None,
        ],
        set[int],
    ] = {}
    for sample in set(evidence_samples):
        if (
            sample.point_index not in evaluated
            or sample.severity == AdvisoryStatus.UNAVAILABLE
        ):
            continue
        key = (
            sample.severity,
            sample.reason_code,
            sample.metric_id,
            sample.method_id,
            sample.lower_altitude_ft,
            sample.upper_altitude_ft,
        )
        grouped.setdefault(key, set()).add(position_by_index[sample.point_index])

    regions_with_position: list[tuple[int, AdvisoryEvidenceRegion]] = []
    for key, positions in grouped.items():
        severity, reason, metric, method, lower, upper = key
        run_start: int | None = None
        run_end: int | None = None
        for position in sorted(positions):
            if run_start is None:
                run_start = run_end = position
                continue
            if (
                position == run_end + 1
                and ordered[position].point_index
                == ordered[run_end].point_index + 1
            ):
                run_end = position
                continue
            regions_with_position.append((
                run_start,
                AdvisoryEvidenceRegion(
                    start_point_index=ordered[run_start].point_index,
                    end_point_index=ordered[run_end].point_index,
                    lower_altitude_ft=lower,
                    upper_altitude_ft=upper,
                    severity=severity,
                    reason_code=reason,
                    metric_id=metric,
                    method_id=method,
                ),
            ))
            run_start = run_end = position
        if run_start is not None and run_end is not None:
            regions_with_position.append((
                run_start,
                AdvisoryEvidenceRegion(
                    start_point_index=ordered[run_start].point_index,
                    end_point_index=ordered[run_end].point_index,
                    lower_altitude_ft=lower,
                    upper_altitude_ft=upper,
                    severity=severity,
                    reason_code=reason,
                    metric_id=metric,
                    method_id=method,
                ),
            ))

    total_points = len(evaluated)
    affected_points = len(affected)
    affected_mod_points = len(moderate)
    return EvidenceSummary(
        data_state=data_state_from_domains(
            expected=expected,
            evaluated=evaluated,
            complete=complete,
        ),
        affected_points=affected_points,
        total_points=total_points,
        affected_pct=round(
            100 * affected_points / total_points, 1
        ) if total_points else 0.0,
        affected_nm=distance_for(affected),
        total_nm=round(total_distance_nm, 1),
        affected_mod_points=affected_mod_points,
        affected_mod_pct=round(
            100 * affected_mod_points / total_points, 1
        ) if total_points else 0.0,
        affected_mod_nm=distance_for(moderate),
        evidence_regions=[
            region
            for _, region in sorted(
                regions_with_position,
                key=lambda item: (
                    item[0],
                    item[1].reason_code,
                    item[1].lower_altitude_ft or -1,
                ),
            )
        ],
    )
~~~

Its implementation must:

1. Sort route points by distance, reject duplicate point_index values, and clip all cell boundaries to [0, total_distance_nm].
2. Give each point the interval between adjacent midpoints; endpoints own route start/end.
3. Sum unique affected cells for affected_nm and moderate cells for affected_mod_nm.
4. Calculate percentages over evaluated points, not expected points; data_state carries the missing-domain warning.
5. Drop evidence samples whose point index was not evaluated (including out-of-route samples), deduplicate exact samples, and coalesce only adjacent route positions with consecutive stable point indices plus identical severity, reason, metric, method, and exact altitude bounds.
6. Never create an UNAVAILABLE evidence region.

Add this non-spatial builder:

~~~python
def build_non_spatial_result(
    *,
    model: str,
    status: AdvisoryStatus,
    detail: str,
    unavailable_detail: str,
    expected_entities: Collection[str],
    evaluated_entities: Collection[str],
    complete_entities: Collection[str],
    affected_entities: Collection[str],
    primary_method_id: str | None,
) -> ModelAdvisoryResult:
    expected = set(expected_entities)
    evaluated = set(evaluated_entities) & expected
    affected = set(affected_entities) & evaluated
    state = data_state_from_domains(
        expected=expected,
        evaluated=evaluated,
        complete=set(complete_entities) & expected,
    )
    guarded = guard_status_for_data_state(status, state)
    return ModelAdvisoryResult(
        model=model,
        status=guarded,
        detail=unavailable_detail if guarded == AdvisoryStatus.UNAVAILABLE else detail,
        affected_points=len(affected),
        total_points=len(evaluated),
        affected_pct=round(
            100 * len(affected) / len(evaluated), 1
        ) if evaluated else 0.0,
        affected_nm=0.0,
        total_nm=0.0,
        data_state=state,
        primary_method_id=primary_method_id,
    )
~~~

- [ ] **Step 4: Add method-id normalization helpers**

Keep these mappings in evidence.py so evaluators and frontend use stable IDs:

~~~python
def cloud_method_id(
    effective: str | None,
    requested: str | None = None,
) -> str | None:
    if effective == "dd":
        return "dewpoint_depression"
    if effective == "nwp":
        return "nwp"
    if effective == "nwp_synthesized":
        return "nwp_synthesized"
    if effective is None and (
        requested is None
        or requested == "dd"
        or requested.endswith("_dd")
    ):
        return "dewpoint_depression"
    return None


def icing_method_id(value: str | None) -> str | None:
    if value is None:
        return "ogimet_dd"
    return {
        "ogimet_dd": "ogimet_dd",
        "ogimet_nwp": "ogimet_nwp",
        "sfip_nwp": "sfip",
        "ieng": "ieng",
    }.get(value)


def icing_method_is_available(
    sounding: SoundingAnalysis | None,
    value: str | None,
) -> bool:
    if sounding is None:
        return False
    if value in ("ogimet_nwp", "ieng"):
        return sounding.nwp_cloud_layers is not None
    return value in (None, "ogimet_dd", "sfip_nwp")


def convective_method_id(value: str | None) -> str | None:
    if value is None or value == "thermo":
        return "thermo"
    if value == "nwp":
        return "nwp"
    return None
~~~

Per-point evaluators may override these when the effective method or compound
path differs. Never convert an unknown/requested value into an asserted method
badge. In particular, `nwp_synthesized` is explicit provenance because it uses
NWP cloud percentages constrained by a DD envelope; it must not be presented as
pure native NWP.

- [ ] **Step 5: Verify geometry and commit**

Run:

~~~bash
source venv/bin/activate
pytest tests/analysis/advisories/test_evidence.py tests/analysis/advisories/test_evidence_contract.py -q
git diff --check
git add src/weatherbrief/analysis/advisories/evidence.py src/weatherbrief/analysis/advisories/_helpers.py tests/analysis/advisories/test_evidence.py
git commit -m "feat(advisories): derive evidence from route geometry"
~~~

Expected: uneven geometry, duplicate union, gap splitting, data-state guards, and non-spatial results all pass.

### Task 4: Migrate cloud evaluators to one grade/evidence assessment

**Files:**

- Modify: src/weatherbrief/analysis/advisories/cloud_top.py:64-123
- Modify: src/weatherbrief/analysis/advisories/vmc_cruise.py:62-124
- Create: tests/analysis/advisories/test_cloud_evidence.py

**Stable reason and metric IDs:**

| Evaluator | reason_code | metric_id |
| --- | --- | --- |
| cloud_top | cloud_top_exceeds_ceiling | cloud_coverage |
| vmc_cruise | cruise_in_bkn_cloud | cloud_coverage |
| vmc_cruise | cruise_in_ovc_cloud | cloud_coverage |

- [ ] **Step 1: Write failing disconnected-region and method-provenance tests**

Use the existing clear_context fixture, dataclasses.replace, and model_copy to create model-specific points:

~~~python
from dataclasses import replace

from weatherbrief.analysis.advisories.cloud_top import CloudTopEvaluator
from weatherbrief.analysis.advisories.vmc_cruise import VMCCruiseEvaluator
from weatherbrief.models import (
    AdvisoryStatus,
    CloudCoverage,
    EnhancedCloudLayer,
)


def _with_clouds(ctx, layers_by_index):
    analyses = []
    for rpa in ctx.analyses:
        sounding = rpa.sounding["gfs"].model_copy(update={
            "cloud_layers": layers_by_index.get(rpa.point_index, []),
            "cloud_method_effective": "nwp",
        })
        analyses.append(rpa.model_copy(update={"sounding": {"gfs": sounding}}))
    return replace(
        ctx,
        analyses=analyses,
        models=["gfs"],
        cloud_method="square_nwp",
    )


def test_cloud_top_emits_disconnected_model_specific_regions(clear_context):
    hazard = EnhancedCloudLayer(
        base_ft=5000,
        top_ft=15000,
        coverage=CloudCoverage.OVC,
        source="grib",
    )
    ctx = _with_clouds(clear_context, {1: [hazard], 2: [hazard], 4: [hazard]})
    ctx = replace(ctx, flight_ceiling_ft=12000)
    result = CloudTopEvaluator.evaluate(ctx, {"margin_ft": 1000, "pct_amber": 5})
    model = result.per_model[0]
    assert model.data_state == "complete"
    assert model.primary_method_id == "nwp"
    assert [(r.start_point_index, r.end_point_index) for r in model.evidence_regions] == [
        (1, 2), (4, 4),
    ]
    assert all(r.reason_code == "cloud_top_exceeds_ceiling" for r in model.evidence_regions)
    assert model.affected_nm > 0


def test_vmc_regions_split_when_vertical_bounds_change(clear_context):
    first = EnhancedCloudLayer(
        base_ft=5000, top_ft=9000, coverage=CloudCoverage.BKN, source="grib",
    )
    second = EnhancedCloudLayer(
        base_ft=4000, top_ft=10000, coverage=CloudCoverage.BKN, source="grib",
    )
    ctx = _with_clouds(clear_context, {1: [first], 2: [second]})
    result = VMCCruiseEvaluator.evaluate(
        ctx, {"bkn_pct_amber": 5, "ovc_pct_red": 50},
    )
    regions = result.per_model[0].evidence_regions
    assert [(r.start_point_index, r.end_point_index) for r in regions] == [
        (1, 1), (2, 2),
    ]
    assert [(r.lower_altitude_ft, r.upper_altitude_ft) for r in regions] == [
        (5000, 9000), (4000, 10000),
    ]


def test_synthesized_nwp_clouds_keep_explicit_compound_provenance(clear_context):
    hazard = EnhancedCloudLayer(
        base_ft=5000,
        top_ft=15000,
        coverage=CloudCoverage.OVC,
        source="synthesized",
    )
    ctx = _with_clouds(clear_context, {1: [hazard], 2: [hazard]})
    analyses = []
    for rpa in ctx.analyses:
        sounding = rpa.sounding["gfs"].model_copy(update={
            "cloud_method_effective": "nwp_synthesized",
        })
        analyses.append(rpa.model_copy(update={"sounding": {"gfs": sounding}}))
    result = CloudTopEvaluator.evaluate(
        replace(ctx, analyses=analyses, flight_ceiling_ft=12000),
        {"margin_ft": 1000, "pct_amber": 5},
    )
    assert result.per_model[0].primary_method_id == "nwp_synthesized"
~~~

- [ ] **Step 2: Add the missing-data safety tests**

~~~python
def test_cloud_partial_clear_becomes_unavailable(clear_context):
    ctx = _with_clouds(clear_context, {})
    analyses = [
        rpa if rpa.point_index != 3
        else rpa.model_copy(update={"sounding": {}})
        for rpa in ctx.analyses
    ]
    result = VMCCruiseEvaluator.evaluate(
        replace(ctx, analyses=analyses),
        {"bkn_pct_amber": 25, "ovc_pct_red": 50},
    )
    model = result.per_model[0]
    assert model.data_state == "partial"
    assert model.status == AdvisoryStatus.UNAVAILABLE


def test_cloud_partial_hazard_preserves_amber(clear_context):
    hazard = EnhancedCloudLayer(
        base_ft=5000, top_ft=9000, coverage=CloudCoverage.BKN, source="grib",
    )
    ctx = _with_clouds(clear_context, {0: [hazard], 1: [hazard]})
    analyses = [
        rpa if rpa.point_index != 3
        else rpa.model_copy(update={"sounding": {}})
        for rpa in ctx.analyses
    ]
    result = VMCCruiseEvaluator.evaluate(
        replace(ctx, analyses=analyses),
        {"bkn_pct_amber": 5, "ovc_pct_red": 50},
    )
    model = result.per_model[0]
    assert model.data_state == "partial"
    assert model.status == AdvisoryStatus.AMBER
~~~

- [ ] **Step 3: Run tests to verify the old count-only path fails**

Run:

~~~bash
source venv/bin/activate
pytest tests/analysis/advisories/test_cloud_evidence.py -v
~~~

Expected: new fields/regions are empty and partial-clear remains GREEN.

- [ ] **Step 4: Refactor CloudTopEvaluator around EvidenceSummary**

Within each model loop:

~~~python
evaluated: set[int] = set()
complete: set[int] = set()
affected: set[int] = set()
samples: list[EvidenceSample] = []
methods: list[str] = []

for rpa in ctx.analyses:
    sounding = rpa.sounding.get(model)
    if sounding is None:
        continue
    evaluated.add(rpa.point_index)
    complete.add(rpa.point_index)
    method_id = cloud_method_id(
        sounding.cloud_method_effective,
        ctx.cloud_method,
    )
    if method_id is not None:
        methods.append(method_id)
    reachable = [
        layer for layer in sounding.cloud_layers
        if layer.base_ft <= cruise + margin_ft and layer.base_ft <= ceiling
    ]
    for layer in reachable:
        if layer.top_ft + margin_ft <= ceiling:
            continue
        affected.add(rpa.point_index)
        samples.append(EvidenceSample(
            point_index=rpa.point_index,
            severity=AdvisoryStatus.AMBER,
            reason_code="cloud_top_exceeds_ceiling",
            metric_id="cloud_coverage",
            method_id=method_id,
            lower_altitude_ft=round(layer.base_ft),
            upper_altitude_ft=round(layer.top_ft),
        ))
~~~

Build one summary, use `summary.affected_pct` for thresholds,
`summary.format_extent()` for detail, and `summary.build_result()` for the model
result. Because `cloud_top_exceeds_ceiling` is a binary predicate, retier every
candidate region to the containing model's raw GREEN/AMBER/RED result before
the missing-data guard is applied:

~~~python
summary = replace(
    summary,
    evidence_regions=[
        region.model_copy(update={"severity": status})
        for region in summary.evidence_regions
    ],
)
~~~

This prevents a RED-by-extent cloud-top result from carrying misleading AMBER
regions and prevents a below-threshold GREEN result from presenting AMBER
geometry. Use the first controlling sample method, falling back to the first
evaluated method. Supply `adv_t("partial_data", loc)` as `unavailable_detail`.

- [ ] **Step 5: Refactor VMCCruiseEvaluator from the same per-layer predicate**

For every BKN/OVC layer that contains cruise:

~~~python
local_severity = (
    AdvisoryStatus.RED
    if layer.coverage == CloudCoverage.OVC
    else AdvisoryStatus.AMBER
)
reason = (
    "cruise_in_ovc_cloud"
    if layer.coverage == CloudCoverage.OVC
    else "cruise_in_bkn_cloud"
)
samples.append(EvidenceSample(
    point_index=rpa.point_index,
    severity=local_severity,
    reason_code=reason,
    metric_id="cloud_coverage",
    method_id=method_id,
    lower_altitude_ft=round(layer.base_ft),
    upper_altitude_ft=round(layer.top_ft),
))
~~~

Count each point once in affected even if overlapping cloud layers exist. Derive bkn_count and ovc_count from point assessments, not a second pass.

- [ ] **Step 6: Verify thresholds stayed unchanged and commit**

Run:

~~~bash
source venv/bin/activate
pytest tests/analysis/advisories/test_cloud_evidence.py tests/analysis/advisories/test_evaluators.py -k "CloudTop or VMC" -q
git diff --check
git add src/weatherbrief/analysis/advisories/cloud_top.py src/weatherbrief/analysis/advisories/vmc_cruise.py tests/analysis/advisories/test_cloud_evidence.py
git commit -m "feat(advisories): add cloud evidence regions"
~~~

Expected: existing 25/50/60 percentage decisions remain unchanged; only geometry/data-state/provenance are additive.

### Task 5: Migrate icing, FIKI, freezing precipitation, and precipitation assessments

**Files:**

- Modify: src/weatherbrief/analysis/advisories/icing_escape.py:237-333
- Modify: src/weatherbrief/analysis/advisories/fiki_icing.py:169-321
- Modify: src/weatherbrief/analysis/advisories/freezing_precip.py:85-160
- Modify: src/weatherbrief/analysis/advisories/enroute_precip.py:45-217
- Create: tests/analysis/advisories/test_icing_evidence.py
- Modify: tests/analysis/advisories/test_freezing_precip.py
- Modify: tests/analysis/advisories/test_enroute_precip.py
- Modify: tests/test_icing_escape_mitigation.py

**Stable reason and metric IDs:**

| Condition | reason_code | metric_id | method_id |
| --- | --- | --- | --- |
| relevant icing | icing_exposure | method-specific icing metric | selected icing method |
| no warm escape | icing_no_warm_escape | freezing_level_ft | selected icing method |
| tight warm escape | icing_tight_warm_escape | freezing_level_ft | selected icing method |
| FIKI cruise | fiki_cruise_icing | method-specific icing metric | selected icing method |
| FIKI departure transit | fiki_departure_transit | method-specific icing metric | selected icing method |
| FIKI arrival transit | fiki_arrival_transit | method-specific icing metric | selected icing method |
| active FZRA/PL | active_freezing_precip | sld_risk | nwp_precipitation_profile |
| primed warm nose | primed_freezing_rain_profile | sld_risk | nwp_precipitation_profile |
| precipitation visibility | precip_visibility | precipitation_mm | nwp_precipitation_profile |

- [ ] **Step 1: Write failing icing geometry and provenance tests**

Add these tests:

~~~python
def test_icing_escape_regions_follow_actual_zones_and_route_cells(icing_context):
    result = IcingEscapeEvaluator.evaluate(
        replace(icing_context, icing_method="sfip_nwp"),
        {
            "terrain_margin_ft": 1000,
            "tight_margin_ft": 2000,
            "icing_altitude_buffer_ft": 2000,
            "icing_coverage_pct_amber": 5,
            "no_escape_pct_red": 15,
        },
    )
    model = result.per_model[0]
    assert model.primary_method_id == "sfip"
    assert model.data_state == "complete"
    assert model.evidence_regions
    assert all(region.metric_id == "sfip_risk" for region in model.evidence_regions)
    assert model.affected_nm <= model.total_nm


def test_fiki_single_point_sld_is_red_and_not_diluted(fiki_sld_context):
    result = FIKIIcingEvaluator.evaluate(fiki_sld_context, {})
    model = result.per_model[0]
    sld = [r for r in model.evidence_regions if r.reason_code in {
        "fiki_departure_transit", "fiki_arrival_transit",
    }]
    assert model.status == AdvisoryStatus.RED
    assert model.primary_method_id == "ogimet_dd"
    assert sld
    assert any(r.severity == AdvisoryStatus.RED for r in sld)


def test_ogimet_nwp_without_native_cloud_geometry_is_unavailable(clear_context):
    ctx = replace(clear_context, models=["gfs"], icing_method="ogimet_nwp")
    assert all(
        rpa.sounding["gfs"].nwp_cloud_layers is None
        for rpa in ctx.analyses
    )
    result = IcingEscapeEvaluator.evaluate(ctx, {
        "terrain_margin_ft": 1000,
        "tight_margin_ft": 2000,
        "icing_altitude_buffer_ft": 2000,
        "icing_coverage_pct_amber": 5,
        "no_escape_pct_red": 15,
    })
    model = result.per_model[0]
    assert model.status == AdvisoryStatus.UNAVAILABLE
    assert model.data_state == "unavailable"


def test_icing_regions_split_on_missing_points_and_changed_bounds(icing_context):
    analyses = []
    for rpa in icing_context.analyses:
        sounding = rpa.sounding["gfs"]
        if rpa.point_index in {2, 3}:
            zones = []
        elif rpa.point_index == 4:
            zones = [
                sounding.icing_zones[0].model_copy(update={
                    "base_ft": 5000,
                    "top_ft": 11000,
                }),
            ]
        else:
            zones = sounding.icing_zones
        analyses.append(rpa.model_copy(update={
            "sounding": {"gfs": sounding.model_copy(update={"icing_zones": zones})},
        }))

    ctx = replace(icing_context, analyses=analyses, models=["gfs"])
    result = IcingEscapeEvaluator.evaluate(ctx, {
        "terrain_margin_ft": 1000,
        "tight_margin_ft": 2000,
        "icing_altitude_buffer_ft": 2000,
        "icing_coverage_pct_amber": 5,
        "no_escape_pct_red": 15,
    })
    exposure = [
        region for region in result.per_model[0].evidence_regions
        if region.reason_code == "icing_exposure"
    ]
    assert [
        (r.start_point_index, r.end_point_index) for r in exposure
    ] == [(0, 1), (4, 4), (5, 9)]
    assert (exposure[1].lower_altitude_ft, exposure[1].upper_altitude_ft) == (
        5000, 11000,
    )
~~~

The final assertion proves the helper neither bridges the missing points 2–3
nor merges point 4 into point 5 across an altitude-bound change.

- [ ] **Step 2: Write failing freezing-precipitation evidence tests**

Add:

~~~python
def test_active_freezing_precip_uses_warm_nose_bounds():
    active = _active_fzra().model_copy(update={
        "precipitation": PrecipitationAssessment(
            surface_phase=PrecipPhase.FREEZING_RAIN,
            freezing_rain_risk=True,
            warm_nose_base_ft=3000,
            warm_nose_top_ft=5000,
            total_mm=1.2,
        ),
    })
    result = FreezingPrecipEvaluator.evaluate(
        _ctx([_dry()] * 9 + [active]), {"primed_pct_amber": 5},
    )
    model = result.per_model[0]
    active = [
        region for region in model.evidence_regions
        if region.reason_code == "active_freezing_precip"
    ]
    assert model.status == AdvisoryStatus.RED
    assert model.primary_method_id == "nwp_precipitation_profile"
    assert active
    assert all(region.metric_id == "sld_risk" for region in active)
    assert all(
        region.lower_altitude_ft is not None
        and region.upper_altitude_ft is not None
        for region in active
    )


def test_freezing_precip_missing_signal_is_unavailable_not_clear():
    result = FreezingPrecipEvaluator.evaluate(
        _ctx([SoundingAnalysis()] * 10), {},
    )
    model = result.per_model[0]
    assert model.status == AdvisoryStatus.UNAVAILABLE
    assert model.data_state == "unavailable"
~~~

- [ ] **Step 3: Refactor precipitation classification into a reusable assessment object**

Add:

~~~python
@dataclass(frozen=True)
class EnroutePrecipAssessment:
    status: AdvisoryStatus
    detail: str
    summary: EvidenceSummary
    has_signal: bool
    snow_point_indices: frozenset[int]
    moderate_snow_point_indices: frozenset[int]
    significant_rain_point_indices: frozenset[int]
    light_point_indices: frozenset[int]
~~~

Create assess_enroute_precip(ctx, model, params=None) -> EnroutePrecipAssessment. Preserve classify_enroute_precip as a compatibility wrapper that returns its existing five-tuple from the assessment. Both EnroutePrecipEvaluator and VFRFeasibilityEvaluator must consume assess_enroute_precip so the precipitation predicate exists once.
Every precipitation evidence sample and the standalone evaluator result use
`primary_method_id="nwp_precipitation_profile"`.

- [ ] **Step 4: Run failing tests**

Run:

~~~bash
source venv/bin/activate
pytest tests/analysis/advisories/test_icing_evidence.py tests/analysis/advisories/test_freezing_precip.py tests/analysis/advisories/test_enroute_precip.py -v
~~~

Expected: evidence/provenance fields are absent and old precipitation classification has no point sets.

- [ ] **Step 5: Migrate IcingEscapeEvaluator**

For each sounding:

1. Call `icing_method_is_available` before reading the active zone list. `ogimet_nwp` requires `sounding.nwp_cloud_layers is not None`; `None` is missing, while `[]` is an available clear native envelope. Ogimet-DD and SFIP are available when the heavy sounding analysis exists. Mark a point evaluated/complete only when the selected method's required source exists; an available empty icing list means assessed clear.
2. Select the method-specific metric:

~~~python
ICING_METRIC_BY_METHOD = {
    "ogimet_dd": "icing_risk",
    "ogimet_nwp": "icing_ogimet_nwp_risk",
    "sfip": "sfip_risk",
    "ieng": "ieng_icing_risk",
}
~~~

3. Add one icing_exposure sample for every relevant zone, preserving its exact base/top.
4. If freezing level or terrain is missing, leave the hazard evidence intact but do not mark the point fully complete; partial AMBER/RED may survive.
5. Add icing_no_warm_escape or icing_tight_warm_escape samples using the same point and zone bounds. Evidence may cover more sub-issues than headline affected_nm; headline affected points remain the relevant-icing set.
6. Keep the vertical-profile mitigation code unchanged and attach its output through EvidenceSummary.build_result.

- [ ] **Step 6: Migrate FIKIIcingEvaluator**

Create one point assessment per route point. Reuse _transit_icing and _min_icing_clearance; do not rewrite their meteorology.

- Departure samples apply only inside proximity_nm.
- Arrival samples apply only inside the arrival proximity.
- Cruise samples apply where clearance is below cruise_icing_buffer_ft.
- Apply the same selected-method availability rule as IcingEscapeEvaluator so missing native NWP cloud geometry cannot become a clear FIKI route.
- Each sample uses the actual icing zone bounds and method-specific metric.
- SLD samples use RED local severity.
- Headline affected points remain cruise_total - cruise_clear, matching the existing metric; transit evidence is allowed to exceed that headline set.

- [ ] **Step 7: Migrate FreezingPrecipEvaluator**

Call detect_warm_nose once per point and retain its returned base/top. Use precipitation.warm_nose_base_ft/top_ft when populated, falling back to the detected bounds. If the profile shape is known but bounds are absent, emit an along-route sample with no altitude bounds rather than inventing a full-depth zone.

Build active and primed point sets, derive detail from summary.format_extent, and keep the binary active-anywhere RED rule and primed_pct_amber unchanged.
Set `primary_method_id="nwp_precipitation_profile"` and the same method ID on
its regions; this names the existing precipitation-phase/warm-nose profile
calculation and does not imply a new calibration.

- [ ] **Step 8: Verify and commit**

Run:

~~~bash
source venv/bin/activate
pytest tests/analysis/advisories/test_icing_evidence.py tests/analysis/advisories/test_freezing_precip.py tests/analysis/advisories/test_enroute_precip.py tests/test_icing_escape_mitigation.py tests/analysis/advisories/test_evaluators.py -k "Icing or Freezing or Precip" -q
git diff --check
git add src/weatherbrief/analysis/advisories/icing_escape.py src/weatherbrief/analysis/advisories/fiki_icing.py src/weatherbrief/analysis/advisories/freezing_precip.py src/weatherbrief/analysis/advisories/enroute_precip.py tests/analysis/advisories/test_icing_evidence.py tests/analysis/advisories/test_freezing_precip.py tests/analysis/advisories/test_enroute_precip.py tests/test_icing_escape_mitigation.py
git commit -m "feat(advisories): add icing and SLD evidence"
~~~

Expected: no icing or precipitation threshold changes; affected distance is geometry-based for migrated evaluators.

### Task 6: Fix turbulence missing-data safety and migrate mountain-wind evidence

**Files:**

- Modify: src/weatherbrief/analysis/advisories/turbulence.py:61-123
- Modify: src/weatherbrief/analysis/advisories/mountain_wind.py:159-238
- Create: tests/analysis/advisories/test_turbulence_evidence.py
- Modify: tests/analysis/advisories/test_mountain_wind.py

**Stable reason and metric IDs:**

| Condition | reason_code | metric_id | method_id |
| --- | --- | --- | --- |
| CAT at cruise | cat_at_cruise | cat_risk | richardson_cat |
| strong vertical motion | strong_vertical_motion_near_cruise | absent (trigger is `max_w_fpm`, which has no catalog metric ID) | vertical_motion |
| terrain wind | mountain_wind | wind_speed_kt | terrain_wind |
| corroborated wave | mountain_wave_corroborated | wind_speed_kt | terrain_wind_wave |

- [ ] **Step 1: Write the missing-versus-smooth blocker tests**

~~~python
def test_turbulence_missing_vertical_motion_is_unavailable(clear_context):
    analyses = []
    for rpa in clear_context.analyses:
        sounding = rpa.sounding["gfs"].model_copy(update={"vertical_motion": None})
        analyses.append(rpa.model_copy(update={"sounding": {"gfs": sounding}}))
    ctx = replace(clear_context, analyses=analyses, models=["gfs"])
    result = TurbulenceEvaluator.evaluate(
        ctx, {"route_pct_amber": 20, "strong_w_fpm": 200},
    )
    model = result.per_model[0]
    assert model.status == AdvisoryStatus.UNAVAILABLE
    assert model.data_state == "unavailable"


def test_turbulence_partial_severe_evidence_remains_red(turbulent_context):
    analyses = []
    for rpa in turbulent_context.analyses:
        if rpa.point_index == 0:
            sounding = rpa.sounding["gfs"].model_copy(update={"vertical_motion": None})
            analyses.append(rpa.model_copy(update={"sounding": {"gfs": sounding}}))
        else:
            analyses.append(rpa.model_copy(update={"sounding": {"gfs": rpa.sounding["gfs"]}}))
    ctx = replace(turbulent_context, analyses=analyses, models=["gfs"])
    result = TurbulenceEvaluator.evaluate(
        ctx, {"route_pct_amber": 20, "strong_w_fpm": 200},
    )
    assert result.per_model[0].data_state == "partial"
    assert result.per_model[0].status in (AdvisoryStatus.AMBER, AdvisoryStatus.RED)
~~~

- [ ] **Step 2: Write evidence geometry tests**

Add to `test_turbulence_evidence.py`:

~~~python
def test_turbulence_keeps_cat_bounds_and_motion_level(turbulent_context):
    result = TurbulenceEvaluator.evaluate(
        turbulent_context, {"route_pct_amber": 20, "strong_w_fpm": 200},
    )
    regions = result.per_model[0].evidence_regions
    cat = [r for r in regions if r.reason_code == "cat_at_cruise"]
    motion = [
        r for r in regions
        if r.reason_code == "strong_vertical_motion_near_cruise"
    ]
    assert [(r.lower_altitude_ft, r.upper_altitude_ft) for r in cat] == [
        (7000, 10000),
    ]
    assert [(r.lower_altitude_ft, r.upper_altitude_ft) for r in motion] == [
        (8000, 8000),
    ]


def test_turbulence_regions_do_not_bridge_missing_assessments(turbulent_context):
    analyses = []
    for rpa in turbulent_context.analyses:
        sounding = rpa.sounding["gfs"]
        if rpa.point_index in {2, 3}:
            sounding = sounding.model_copy(update={"vertical_motion": None})
        analyses.append(rpa.model_copy(update={"sounding": {"gfs": sounding}}))
    ctx = replace(turbulent_context, analyses=analyses)
    result = TurbulenceEvaluator.evaluate(
        ctx, {"route_pct_amber": 20, "strong_w_fpm": 200},
    )
    cat = [
        r for r in result.per_model[0].evidence_regions
        if r.reason_code == "cat_at_cruise"
    ]
    assert [(r.start_point_index, r.end_point_index) for r in cat] == [
        (0, 1), (4, 9),
    ]
~~~

Add to `test_mountain_wind.py`, reusing its existing `_ctx` and `_evaluate`
helpers:

~~~python
def test_mountain_wind_evidence_is_route_only():
    result = _evaluate(_ctx(32.0, ridge_inversion=True))
    regions = result.per_model[0].evidence_regions
    assert {r.reason_code for r in regions} == {
        "mountain_wind", "mountain_wave_corroborated",
    }
    assert all(
        r.lower_altitude_ft is None and r.upper_altitude_ft is None
        for r in regions
    )
~~~

- [ ] **Step 3: Run tests to expose GREEN-by-absence**

Run:

~~~bash
source venv/bin/activate
pytest tests/analysis/advisories/test_turbulence_evidence.py tests/analysis/advisories/test_mountain_wind.py -v
~~~

Expected: missing vertical_motion currently increments total and returns GREEN.

- [ ] **Step 4: Migrate TurbulenceEvaluator**

Only mark a point evaluated when vertical_motion exists and classification is not VerticalMotionClass.UNAVAILABLE. A sounding alone is not sufficient.

For CAT:

~~~python
samples.append(EvidenceSample(
    point_index=rpa.point_index,
    severity=(
        AdvisoryStatus.RED
        if layer.risk == CATRiskLevel.SEVERE
        else AdvisoryStatus.AMBER
    ),
    reason_code="cat_at_cruise",
    metric_id="cat_risk",
    method_id="richardson_cat",
    lower_altitude_ft=round(layer.base_ft),
    upper_altitude_ft=round(layer.top_ft),
))
~~~

For strong motion, use
`lower_altitude_ft == upper_altitude_ft == round(max_w_level_ft)`,
`metric_id=None`, and `method_id="vertical_motion"`. Do not label a ft/min
trigger as the Pa/s `max_omega_pa_s` catalog metric. The renderer task must draw
a minimum-width line for zero-height evidence.

Choose primary_method_id as richardson_cat when severe CAT controls RED, vertical_motion when it is the sole trigger, or cat_with_vertical_motion when both contribute. Keep route_pct_amber, 50% RED, and strong_w_fpm unchanged.

- [ ] **Step 5: Migrate MountainWindEvaluator**

Expected domain is every route point because terrain data is required to decide applicability:

- terrain missing: incomplete point;
- terrain present below threshold: evaluated and complete clear/non-applicable;
- mountain point with target wind missing: incomplete point;
- wind available: evaluated and complete.

No qualifying terrain with complete elevation data remains GREEN. Missing elevation across the route becomes unavailable. Add mountain_wave_corroborated when _wave_signatures fires at the same strong-wind point. Do not add a cross-ridge component.

- [ ] **Step 6: Verify and commit**

Run:

~~~bash
source venv/bin/activate
pytest tests/analysis/advisories/test_turbulence_evidence.py tests/analysis/advisories/test_mountain_wind.py tests/analysis/advisories/test_evaluators.py -k "Turbulence or Mountain" -q
git diff --check
git add src/weatherbrief/analysis/advisories/turbulence.py src/weatherbrief/analysis/advisories/mountain_wind.py tests/analysis/advisories/test_turbulence_evidence.py tests/analysis/advisories/test_mountain_wind.py
git commit -m "fix(advisories): distinguish missing turbulence data"
~~~

Expected: A223-01 is closed with direct regression evidence; no CAT/Ri thresholds changed.

### Task 7: Migrate convection with explicit compound provenance

**Files:**

- Modify: src/weatherbrief/analysis/advisories/convective.py:115-342
- Create: tests/analysis/advisories/test_convective_evidence.py
- Modify: tests/analysis/advisories/test_evaluators.py
- Modify: tests/test_convective.py

**Stable reason and method IDs:**

| Path | reason_code | metric_id | method_id |
| --- | --- | --- | --- |
| active selected track | convective_active | convective_risk or nwp_convective_risk | thermo or nwp |
| DD floor raises selected NWP | convective_dd_floor | convective_risk | nwp_with_dd_floor |

- [ ] **Step 1: Write failing compound-provenance tests**

~~~python
from dataclasses import replace

from weatherbrief.models import AdvisoryStatus, ConvectiveAssessment, ConvectiveRisk


_CONV_PARAMS = {
    "min_risk": 2,
    "affected_pct_amber": 20,
    "affected_pct_red": 50,
    "top_clearance_ft": 2000,
}


def _with_tracks(ctx, active_by_index, thermo_by_index=None):
    thermo_by_index = thermo_by_index or {}
    analyses = []
    for rpa in ctx.analyses:
        sounding = rpa.sounding["gfs"].model_copy(update={
            "convective": active_by_index.get(rpa.point_index),
            "convective_thermo": thermo_by_index.get(rpa.point_index),
        })
        analyses.append(rpa.model_copy(update={"sounding": {"gfs": sounding}}))
    return replace(ctx, analyses=analyses, models=["gfs"])


def _nwp(risk, *, base=5000, top=25000):
    return ConvectiveAssessment(
        risk_level=risk,
        base_ft=base,
        top_ft=top,
        method="nwp",
    )


def test_dd_floor_records_compound_method_and_thermo_geometry(convective_context):
    active = {
        index: _nwp(ConvectiveRisk.NONE, base=None, top=None)
        for index in range(10)
    }
    thermo = {
        1: ConvectiveAssessment(
            risk_level=ConvectiveRisk.HIGH,
            base_ft=5000,
            top_ft=25000,
            method="thermo",
        ),
    }
    ctx = _with_tracks(convective_context, active, thermo)
    result = ConvectiveEvaluator.evaluate(ctx, _CONV_PARAMS)
    model = result.per_model[0]
    floor_regions = [
        region for region in model.evidence_regions
        if region.reason_code == "convective_dd_floor"
    ]
    assert model.primary_method_id == "nwp_with_dd_floor"
    assert floor_regions
    assert all(region.method_id == "nwp_with_dd_floor" for region in floor_regions)
    assert floor_regions[0].lower_altitude_ft == 5000
    assert floor_regions[0].upper_altitude_ft == 25000


def test_convective_regions_do_not_join_disconnected_cells(convective_context):
    active = {
        index: _nwp(ConvectiveRisk.NONE)
        for index in range(10)
    }
    for index in (1, 2, 4):
        active[index] = _nwp(ConvectiveRisk.MODERATE)
    ctx = _with_tracks(convective_context, active)
    result = ConvectiveEvaluator.evaluate(ctx, _CONV_PARAMS)
    regions = result.per_model[0].evidence_regions
    assert [(r.start_point_index, r.end_point_index) for r in regions] == [
        (1, 2), (4, 4),
    ]


def test_convective_missing_active_assessment_is_not_clear(convective_context):
    ctx = _with_tracks(convective_context, {
        0: _nwp(ConvectiveRisk.NONE),
        1: _nwp(ConvectiveRisk.NONE),
    })
    result = ConvectiveEvaluator.evaluate(ctx, _CONV_PARAMS)
    model = result.per_model[0]
    assert model.data_state == "partial"
    assert model.status == AdvisoryStatus.UNAVAILABLE


def test_convective_aggregate_detail_is_representative_model_owned(clear_context):
    analyses = []
    for rpa in clear_context.analyses:
        soundings = {}
        for model, affected_until in (("gfs", 1), ("ecmwf", 2)):
            risk = (
                ConvectiveRisk.MODERATE
                if rpa.point_index <= affected_until
                else ConvectiveRisk.NONE
            )
            soundings[model] = rpa.sounding[model].model_copy(update={
                "convective": _nwp(risk),
                "convective_thermo": None,
            })
        analyses.append(rpa.model_copy(update={"sounding": soundings}))
    ctx = replace(clear_context, analyses=analyses, models=["gfs", "ecmwf"])
    result = ConvectiveEvaluator.evaluate(ctx, _CONV_PARAMS)
    representative = next(
        model for model in result.per_model
        if model.model == result.representative_model
    )
    assert result.aggregate_status == AdvisoryStatus.AMBER
    assert result.aggregate_detail == representative.detail
~~~

- [ ] **Step 2: Run tests and confirm metadata is absent**

Run:

~~~bash
source venv/bin/activate
pytest tests/analysis/advisories/test_convective_evidence.py -v
~~~

Expected: regions/method IDs are absent and partial-clear remains GREEN.

- [ ] **Step 3: Create one per-point convective assessment path**

Inside the existing loop, record these values once:

~~~python
active = sounding.convective
thermo = sounding.convective_thermo
if active is None:
    continue

evaluated.add(rpa.point_index)
complete.add(rpa.point_index)
graded_risk = active.risk_level
floor_controls = (
    thermo is not None
    and _RISK_ORDER.index(thermo.risk_level) > _RISK_ORDER.index(graded_risk)
)
if floor_controls:
    graded_risk = thermo.risk_level

check_top_ft = active.top_ft
if floor_controls and thermo is not None:
    if check_top_ft is None or (
        thermo.top_ft is not None and thermo.top_ft > check_top_ft
    ):
        check_top_ft = thermo.top_ft
~~~

Keep the existing below-cruise filter, LOW cap, HIGH-anywhere override,
cross-check tally, and MODERATE+ per-model headline logic exactly as written.
Delete the post-aggregation block that synthesizes a cross-model percentage
range into `result.aggregate_detail`; `RouteAdvisoryResult.from_per_model` now
owns aggregate detail, mitigations, and representative attribution as one
indivisible policy.

- [ ] **Step 4: Build evidence from the controlling path**

When the point qualifies:

~~~python
source = thermo if floor_controls and thermo is not None else active
method_id = "nwp_with_dd_floor" if floor_controls else (
    "nwp" if active.method.startswith("nwp") else "thermo"
)
reason = "convective_dd_floor" if floor_controls else "convective_active"
metric_id = "nwp_convective_risk" if method_id == "nwp" else "convective_risk"
samples.append(EvidenceSample(
    point_index=rpa.point_index,
    severity=(
        AdvisoryStatus.RED
        if graded_risk in (ConvectiveRisk.HIGH, ConvectiveRisk.EXTREME)
        else AdvisoryStatus.AMBER
    ),
    reason_code=reason,
    metric_id=metric_id,
    method_id=method_id,
    lower_altitude_ft=round(source.base_ft) if source.base_ft is not None else None,
    upper_altitude_ft=round(source.top_ft) if source.top_ft is not None else None,
))
~~~

Only provide altitude bounds when both source bounds exist. Pass affected_mod point indices to summarize_evidence. Use summary.format_extent and summary.format_mod_extent in all detail strings so displayed nautical miles match the stored contract.

- [ ] **Step 5: Verify no meteorological recalibration and commit**

Run:

~~~bash
source venv/bin/activate
pytest tests/analysis/advisories/test_convective_evidence.py tests/analysis/advisories/test_evaluators.py tests/test_convective.py -q
git diff --check
git add src/weatherbrief/analysis/advisories/convective.py tests/analysis/advisories/test_convective_evidence.py tests/analysis/advisories/test_evaluators.py tests/test_convective.py
git commit -m "feat(advisories): expose convective evidence provenance"
~~~

Expected: all established #283 DD-floor and cross-check tests still pass;
tests that expected a synthesized #300 aggregate range are updated to expect
the representative model's existing per-model detail. No convective thresholds
or per-model grades change.

### Task 8: Migrate VFR and IFR composites without duplicate predicates

**Files:**

- Modify: src/weatherbrief/analysis/advisories/vfr_feasibility.py:48-310,684-803
- Modify: src/weatherbrief/analysis/advisories/ifr_feasibility.py:43-153,267-361
- Modify: src/weatherbrief/analysis/advisories/enroute_precip.py
- Create: tests/analysis/advisories/test_feasibility_evidence.py
- Modify: tests/test_vfr_mitigation.py
- Modify: tests/analysis/advisories/test_ifr_feasibility_defaults.py

**Stable reason IDs:**

- vfr_cruise_imc
- vfr_cloud_clearance
- vfr_climb_deck
- vfr_descent_deck
- vfr_precip_visibility
- ifr_icing_exposure
- ifr_convective_exposure

- [ ] **Step 1: Write failing completeness and no-double-count tests**

~~~python
def test_vfr_missing_airport_domain_and_clear_route_is_unavailable(clear_context):
    ctx = replace(clear_context, airport_conditions=None, models=["gfs"])
    result = VFRFeasibilityEvaluator.evaluate(ctx, {})
    model = result.per_model[0]
    assert model.data_state == "partial"
    assert model.status == AdvisoryStatus.UNAVAILABLE


def test_vfr_partial_red_route_evidence_is_preserved(vfr_imc_enroute_context):
    ctx = replace(
        vfr_imc_enroute_context,
        airport_conditions=None,
        models=["gfs"],
    )
    result = VFRFeasibilityEvaluator.evaluate(ctx, {})
    assert result.per_model[0].data_state == "partial"
    assert result.per_model[0].status == AdvisoryStatus.RED


def test_ifr_icing_and_convection_same_point_count_once(ifr_normal_context):
    first = ifr_normal_context.analyses[0]
    icing = IcingZone(
        base_ft=4000,
        top_ft=10000,
        risk=IcingRisk.MODERATE,
        icing_type=IcingType.MIXED,
    )
    sounding = first.sounding["gfs"].model_copy(update={
        "icing_zones": [icing],
        "convective": ConvectiveAssessment(
            risk_level=ConvectiveRisk.MODERATE,
            base_ft=4000,
            top_ft=18000,
        ),
    })
    analyses = [
        first.model_copy(update={"sounding": {"gfs": sounding}}),
        *[
            rpa.model_copy(update={"sounding": {"gfs": rpa.sounding["gfs"]}})
            for rpa in ifr_normal_context.analyses[1:]
        ],
    ]
    ctx = replace(
        ifr_normal_context,
        analyses=analyses,
        models=["gfs"],
    )
    result = IFRFeasibilityEvaluator.evaluate(ctx, {})
    model = result.per_model[0]
    assert model.affected_points == 1
    assert {r.reason_code for r in model.evidence_regions} == {
        "ifr_icing_exposure", "ifr_convective_exposure",
    }
~~~

- [ ] **Step 2: Replace tuple-only cloud checks with assessment records**

Add an internal immutable record:

~~~python
@dataclass(frozen=True)
class VFRPointAssessment:
    point_index: int
    available: bool
    complete: bool
    in_cloud: bool
    marginal: bool
    cloud_samples: tuple[EvidenceSample, ...]
~~~

Create _assess_enroute_vfr that returns these records. Make _check_enroute_vfr a compatibility/count wrapper over the records because mitigation tests call it. Grade, detail, evidence, and mitigation candidate status must all consume the same records/predicate.

- [ ] **Step 3: Make corridor_points carry stable point identity and exact layers**

Change its yield to a named record containing:

~~~python
@dataclass(frozen=True)
class CorridorPointAssessment:
    point_index: int
    distance_nm: float
    phase: Literal["climb", "descent"]
    has_ovc: bool
    has_bkn: bool
    base_agl_ft: float | None
    blocking_layers: tuple[EnhancedCloudLayer, ...]
~~~

Update both the corridor grade and mitigation helpers to use this record. This preserves the current terminal-deck predicate and prevents a second evidence-only implementation.

- [ ] **Step 4: Combine route, airport, and precipitation data states**

For VFR:

- route cloud state from VFRPointAssessment;
- airport state from two expected endpoints;
- precipitation state from assess_enroute_precip;
- corridor completeness follows the route cloud/elevation inputs.

Use combine_data_states. Missing required axes make the composite partial. A partial GREEN becomes UNAVAILABLE; a supported AMBER/RED survives.

Carry the underlying method ID on every region. Track the method IDs of the
sub-issues tied for the controlling VFR status: if exactly one method controls,
use it as `primary_method_id`; if multiple methods tie, use the explicit stable
ID `vfr_composite`.

For IFR, add IFRPointAssessment containing icing and convective samples from
the same single-pass predicate. Reuse the Task 5 selected-icing-method
availability rule, so a missing Ogimet-NWP cloud envelope makes the route axis
partial rather than clear. Combine its route state with the airport endpoint
state.

Apply the same controlling-method rule to IFR, using `ifr_composite` when more
than one method ties for the headline grade. Do not label a composite as icing
or convection alone when both controlled it.

- [ ] **Step 5: Keep headline metrics unique while retaining sub-issue evidence**

VFR headline affected points remain IMC plus marginal cloud points, matching today. Corridor and precipitation samples may exist outside that set.

IFR headline affected points are the union of icing and convective point indices:

~~~python
affected_points = icing_point_indices | convective_point_indices
~~~

Never add counts. This is the explicit no-double-count rule.

- [ ] **Step 6: Run tests and commit**

Run:

~~~bash
source venv/bin/activate
pytest tests/analysis/advisories/test_feasibility_evidence.py tests/test_vfr_mitigation.py tests/analysis/advisories/test_ifr_feasibility_defaults.py tests/analysis/advisories/test_evaluators.py -k "VFR or IFR" -q
git diff --check
git add src/weatherbrief/analysis/advisories/vfr_feasibility.py src/weatherbrief/analysis/advisories/ifr_feasibility.py src/weatherbrief/analysis/advisories/enroute_precip.py tests/analysis/advisories/test_feasibility_evidence.py tests/test_vfr_mitigation.py tests/analysis/advisories/test_ifr_feasibility_defaults.py
git commit -m "feat(advisories): add composite hazard evidence"
~~~

Expected: mitigation behaviour and existing thresholds remain unchanged; missing composite axes no longer masquerade as clear.

### Task 9: Add model-agreement, DD/NWP, airport, and fronts metadata

**Files:**

- Modify: src/weatherbrief/analysis/advisories/model_agreement.py:73-118
- Modify: src/weatherbrief/analysis/advisories/dd_nwp_agreement.py:140-233
- Modify: src/weatherbrief/analysis/advisories/airport_wind.py:117-172
- Modify: src/weatherbrief/analysis/advisories/flight_category.py:187-253
- Modify: src/weatherbrief/analysis/advisories/density_altitude.py:146-203
- Modify: src/weatherbrief/analysis/advisories/llws.py:130-214
- Modify: src/weatherbrief/analysis/advisories/fronts.py:268-365
- Create: tests/analysis/advisories/test_context_action_metadata.py
- Modify: tests/analysis/advisories/test_airport_advisories.py
- Modify: tests/analysis/advisories/test_fronts_advisory.py

- [ ] **Step 1: Write explicit unavailable airport-domain tests**

Replace assertions that expect an empty per_model list:

~~~python
@pytest.mark.parametrize("evaluator", [
    FlightCategoryEvaluator,
    AirportWindEvaluator,
    DensityAltitudeEvaluator,
])
def test_missing_airport_domain_is_explicitly_unavailable(evaluator):
    ctx = _make_ctx(None, models=["gfs", "ecmwf"])
    result = evaluator.evaluate(ctx, {})
    assert result.aggregate_status == AdvisoryStatus.UNAVAILABLE
    assert [m.model for m in result.per_model] == ["gfs", "ecmwf"]
    assert all(m.status == AdvisoryStatus.UNAVAILABLE for m in result.per_model)
    assert all(m.data_state == "unavailable" for m in result.per_model)
~~~

Add an LLWS no-analysis equivalent.

- [ ] **Step 2: Write model-context evidence tests**

Create `tests/analysis/advisories/test_context_action_metadata.py` with these
tests. The helper mutates only GFS and makes every route point comparable so a
single disagreement cannot be confused with partial input:

~~~python
from dataclasses import replace

from weatherbrief.analysis.advisories.dd_nwp_agreement import DDvsNWPAgreementEvaluator
from weatherbrief.analysis.advisories.model_agreement import ModelAgreementEvaluator
from weatherbrief.models import AdvisoryStatus, CloudCoverage, EnhancedCloudLayer


_DD_PARAMS = {
    "freezing_delta_ft": 2000,
    "cloud_overlap_min": 30,
    "amber_pct": 5,
    "red_pct": 60,
}


def _gfs_soundings(ctx, transform):
    analyses = []
    for rpa in ctx.analyses:
        analyses.append(rpa.model_copy(update={
            "sounding": {"gfs": transform(rpa.point_index, rpa.sounding["gfs"])},
        }))
    return replace(ctx, analyses=analyses, models=["gfs"])


def test_model_agreement_emits_cross_model_metadata(poor_agreement_context):
    result = ModelAgreementEvaluator.evaluate(poor_agreement_context, {
        "min_poor_vars": 3,
        "poor_pct_amber": 25,
        "poor_pct_red": 50,
    })
    model = result.per_model[0]
    assert model.model == "all"
    assert model.primary_method_id == "model_divergence"
    assert model.data_state == "complete"
    assert model.evidence_regions
    assert all(r.reason_code == "poor_model_agreement" for r in model.evidence_regions)


def test_dd_nwp_freezing_region_spans_both_levels(clear_context):
    def transform(index, sounding):
        indices = sounding.indices.model_copy(update={
            "freezing_level_ft": 5000,
            "nwp_freezing_level_ft": 9000 if index == 1 else 5000,
        })
        return sounding.model_copy(update={
            "indices": indices,
            "dd_cloud_layers": [],
            "nwp_cloud_layers": None,
        })

    result = DDvsNWPAgreementEvaluator.evaluate(
        _gfs_soundings(clear_context, transform), _DD_PARAMS,
    )
    freezing = [
        r for r in result.per_model[0].evidence_regions
        if r.reason_code == "freezing_level_disagreement"
    ]
    assert len(freezing) == 1
    assert (freezing[0].lower_altitude_ft, freezing[0].upper_altitude_ft) == (
        5000, 9000,
    )
    assert freezing[0].method_id == "dd_vs_nwp"


def test_dd_nwp_cloud_regions_keep_both_source_geometries(clear_context):
    common_dd = EnhancedCloudLayer(
        base_ft=3000, top_ft=7000, coverage=CloudCoverage.BKN, source="dd",
    )
    common_nwp = common_dd.model_copy(update={"source": "grib"})
    dd_only = EnhancedCloudLayer(
        base_ft=2000, top_ft=6000, coverage=CloudCoverage.BKN, source="dd",
    )
    nwp_only = EnhancedCloudLayer(
        base_ft=9000, top_ft=12000, coverage=CloudCoverage.BKN, source="grib",
    )

    def transform(index, sounding):
        indices = sounding.indices.model_copy(update={
            "freezing_level_ft": 5000,
            "nwp_freezing_level_ft": 5000,
        })
        return sounding.model_copy(update={
            "indices": indices,
            "dd_cloud_layers": [dd_only] if index == 1 else [common_dd],
            "nwp_cloud_layers": [nwp_only] if index == 1 else [common_nwp],
        })

    result = DDvsNWPAgreementEvaluator.evaluate(
        _gfs_soundings(clear_context, transform), _DD_PARAMS,
    )
    clouds = [
        r for r in result.per_model[0].evidence_regions
        if r.reason_code in {"dd_cloud_disagreement", "nwp_cloud_disagreement"}
    ]
    assert {
        (r.method_id, r.lower_altitude_ft, r.upper_altitude_ft) for r in clouds
    } == {
        ("dewpoint_depression", 2000, 6000),
        ("nwp", 9000, 12000),
    }
~~~

Extend `tests/analysis/advisories/test_fronts_advisory.py`:

~~~python
def test_no_artifact_is_unavailable():
    result = FrontsEvaluator.evaluate(_ctx(None), _PARAMS)
    assert result.advisory_id == FRONTS_ADVISORY_ID
    assert result.aggregate_status == AdvisoryStatus.UNAVAILABLE
    assert [m.model for m in result.per_model] == ["gfs"]
    assert result.per_model[0].data_state == "unavailable"
    assert result.per_model[0].primary_method_id == "hewson"


def test_sharp_crossing_is_red():
    manifest = _manifest(crossings=[_crossing(intensity="sharp", gradient=14.0)])
    result = FrontsEvaluator.evaluate(_ctx(manifest), _PARAMS)
    assert result.aggregate_status == AdvisoryStatus.RED
    assert result.per_model[0].data_state == "complete"
    assert result.per_model[0].primary_method_id == "hewson"
    assert result.per_model[0].evidence_regions == []
~~~

- [ ] **Step 3: Run tests**

Run:

~~~bash
source venv/bin/activate
pytest tests/analysis/advisories/test_context_action_metadata.py tests/analysis/advisories/test_airport_advisories.py tests/analysis/advisories/test_fronts_advisory.py -v
~~~

Expected: airport empty-list assertions fail and metadata is absent.

- [ ] **Step 4: Migrate model agreement**

For each route point, treat model_divergence absence as missing. For every POOR variable that participates in the point predicate, add:

~~~python
from weatherbrief.analysis.comparison import DIVERGENCE_THRESHOLDS


EvidenceSample(
    point_index=rpa.point_index,
    severity=AdvisoryStatus.RED,
    reason_code="poor_model_agreement",
    metric_id=(
        divergence.variable
        if divergence.variable in DIVERGENCE_THRESHOLDS
        else None
    ),
    method_id="model_divergence",
)
~~~

Moderate-only points may emit AMBER samples but remain outside the poor headline affected set. Keep model="all"; frontend actions must not interpret it as evidence belonging to a forecast model.

- [ ] **Step 5: Migrate DD/NWP agreement**

Use point sets from the same comparisons that grade:

- freezing bounds span the two levels;
- cloud disagreement samples use the actual DD and native-NWP layer bounds as separate samples: `dd_cloud_disagreement` with method_id `dewpoint_depression`, and `nwp_cloud_disagreement` with method_id `nwp`;
- primary_method_id is dd_vs_nwp;
- affected_nm uses disagreement point cells.

Freezing/cloud disagreement predicates are binary, so after calculating the
raw model GREEN/AMBER/RED status, retier all of that model's disagreement
regions to the containing status exactly as in Task 4. Never emit an AMBER
region for a below-threshold GREEN model or for a RED-by-extent model.

Do not add convective disagreement here.

- [ ] **Step 6: Migrate non-spatial airport evaluators**

Use build_non_spatial_result with expected endpoint keys {"departure", "arrival"}:

- Flight category: endpoint evaluated when a model condition exists.
- Airport wind: endpoint evaluated only when crosswind or gust input exists; a condition object with neither is missing, not calm.
- Density altitude: endpoint evaluated only when condition, temperature, and elevation exist.
- LLWS: endpoint evaluated only when shear or gust factor exists.

Set method IDs:

~~~text
flight_category -> airport_conditions
airport_wind -> runway_components
density_altitude -> density_altitude
llws -> bulk_shear
~~~

Keep airport wind mean-crosswind/gust policy unchanged.

- [ ] **Step 7: Migrate fronts metadata**

Use explicit per-model unavailable results for requested models when the artifact/model analysis is absent. Valid model results are complete because the front artifact is a precomputed domain. Set primary_method_id="hewson". Do not manufacture route evidence regions; the action opens the authoritative fronts overlay.

- [ ] **Step 8: Verify and commit**

Run:

~~~bash
source venv/bin/activate
pytest tests/analysis/advisories/test_context_action_metadata.py tests/analysis/advisories/test_airport_advisories.py tests/analysis/advisories/test_fronts_advisory.py tests/analysis/advisories/test_aggregation.py -q
git diff --check
git add src/weatherbrief/analysis/advisories/model_agreement.py src/weatherbrief/analysis/advisories/dd_nwp_agreement.py src/weatherbrief/analysis/advisories/airport_wind.py src/weatherbrief/analysis/advisories/flight_category.py src/weatherbrief/analysis/advisories/density_altitude.py src/weatherbrief/analysis/advisories/llws.py src/weatherbrief/analysis/advisories/fronts.py tests/analysis/advisories/test_context_action_metadata.py tests/analysis/advisories/test_airport_advisories.py tests/analysis/advisories/test_fronts_advisory.py
git commit -m "feat(advisories): add action metadata and safe airport states"
~~~

Expected: all non-spatial actions have provenance and explicit availability; no fake route geometry is emitted.

### Task 10: Add the TypeScript contract, pure focus selector, presets, actions, and method labels

**Files:**

- Modify: web/ts/types/advisories.ts:1-70
- Modify: web/ts/visualization/types.ts:1-180,220-320
- Modify: web/ts/visualization/data-extract.ts:30-90,390-450
- Create: web/ts/visualization/advisory-focus.ts
- Create: web/ts/visualization/advisory-actions.ts
- Create: web/ts/visualization/advisory-methods.ts
- Modify: web/ts/visualization/cross-section/advisory-presets.ts:25-382
- Create: web/tests/unit/advisory-focus.test.ts
- Create: web/tests/unit/advisory-actions.test.ts
- Create: web/tests/unit/advisory-methods.test.ts
- Modify: web/tests/unit/advisory-presets.test.ts
- Modify: web/tests/unit/fixtures/viz-point.ts
- Create: web/tests/unit/fixtures/advisory-focus.ts

- [ ] **Step 1: Mirror the additive backend contract**

Add:

~~~typescript
export type AdvisoryDataState = 'complete' | 'partial' | 'unavailable';

export interface AdvisoryEvidenceRegion {
  start_point_index: number;
  end_point_index: number;
  lower_altitude_ft?: number | null;
  upper_altitude_ft?: number | null;
  severity: Exclude<AdvisoryStatus, 'unavailable'>;
  reason_code: string;
  metric_id?: string | null;
  method_id?: string | null;
}
~~~

Extend ModelAdvisoryResult:

~~~typescript
data_state?: AdvisoryDataState | null;
primary_method_id?: string | null;
evidence_regions?: AdvisoryEvidenceRegion[];
~~~

Extend RouteAdvisoryResult:

~~~typescript
representative_model?: string | null;
~~~

Add pointIndex: number to VizPoint and populate it from rpa.point_index in both data-extract.ts and airport-profile-adapter.ts. Update makeVizPoint with pointIndex: 0.

- [ ] **Step 2: Write failing focus-selection tests**

First create `web/tests/unit/fixtures/advisory-focus.ts`; Tasks 10–13 import
these builders instead of referring to undeclared test globals:

~~~typescript
import type {
  AdvisoryEvidenceRegion,
  ModelAdvisoryResult,
  RouteAdvisoriesManifest,
  RouteAdvisoryResult,
} from '../../../ts/types/advisories';
import type { AdvisoryActionContext } from '../../../ts/visualization/advisory-actions';
import type {
  ActiveAdvisoryFocus,
  ResolvedFocusRegion,
} from '../../../ts/visualization/advisory-focus';
import type { VizRouteData } from '../../../ts/visualization/types';
import { makeVizPoint } from './viz-point';


function evidenceRegion(
  start: number,
  end: number,
  overrides: Partial<AdvisoryEvidenceRegion> = {},
): AdvisoryEvidenceRegion {
  return {
    start_point_index: start,
    end_point_index: end,
    lower_altitude_ft: 5000,
    upper_altitude_ft: 9000,
    severity: 'amber',
    reason_code: 'cloud_top_exceeds_ceiling',
    metric_id: 'cloud_coverage',
    method_id: 'nwp',
    ...overrides,
  };
}


function modelResult(
  model: string,
  overrides: Partial<ModelAdvisoryResult> = {},
): ModelAdvisoryResult {
  return {
    model,
    status: 'amber',
    detail: `${model} detail`,
    affected_points: 1,
    total_points: 3,
    affected_pct: 33.3,
    affected_nm: 15,
    total_nm: 60,
    ...overrides,
  };
}


function advisory(
  advisoryId: string,
  perModel: ModelAdvisoryResult[],
  overrides: Partial<RouteAdvisoryResult> = {},
): RouteAdvisoryResult {
  return {
    advisory_id: advisoryId,
    aggregate_status: 'amber',
    aggregate_detail: 'aggregate detail',
    per_model: perModel,
    parameters_used: {},
    ...overrides,
  };
}


function manifest(
  advisories: RouteAdvisoryResult[],
  models: string[] = ['gfs', 'ecmwf'],
): RouteAdvisoriesManifest {
  return {
    advisories,
    catalog: [],
    route_name: 'EGTF EGLF',
    cruise_altitude_ft: 8000,
    flight_ceiling_ft: 18000,
    total_distance_nm: 60,
    models,
    aggregation: 'majority',
    airport_conditions: null,
  };
}


export function routeData(distances: number[] = [0, 30, 60]): VizRouteData {
  return {
    points: distances.map((distanceNm, pointIndex) => makeVizPoint({
      pointIndex,
      distanceNm,
      lat: 50 + pointIndex,
      lon: -1 + pointIndex,
    })),
    cruiseAltitudeFt: 8000,
    ceilingAltitudeFt: 18000,
    flightCeilingFt: 23000,
    totalDistanceNm: distances.length > 0 ? distances[distances.length - 1] : 0,
    waypointMarkers: [],
    departureTime: '2026-07-10T10:00:00Z',
    flightDurationHours: 2,
    terrainProfile: null,
    currentConditions: null,
    fronts: null,
    nightIntervals: [],
    sunSide: null,
  };
}


export function activeFocus(
  model = 'gfs',
  advisoryId = 'cloud_top',
): ActiveAdvisoryFocus {
  return {
    advisoryId,
    model,
    highlightSurfaces: ['cross-section', 'route-graph', 'route-map'],
    emphasizeLayers: ['square-nwp-cloud-bands', 'terrain', 'cruise-altitude'],
  };
}


export function manifestWithTwoModels(): RouteAdvisoriesManifest {
  return manifest([advisory('cloud_top', [
    modelResult('gfs', {
      data_state: 'complete',
      primary_method_id: 'nwp',
      evidence_regions: [evidenceRegion(0, 0)],
    }),
    modelResult('ecmwf', {
      data_state: 'complete',
      primary_method_id: 'nwp',
      evidence_regions: [evidenceRegion(1, 1)],
    }),
  ], { representative_model: 'ecmwf' })]);
}


export function manifestWithDisjointGfsAndEcmwfRegions(): RouteAdvisoriesManifest {
  return manifest([advisory('cloud_top', [
    modelResult('gfs', {
      data_state: 'complete',
      evidence_regions: [evidenceRegion(0, 0)],
    }),
    modelResult('ecmwf', {
      data_state: 'complete',
      evidence_regions: [evidenceRegion(2, 2)],
    }),
  ], { representative_model: 'gfs' })]);
}


export function manifestWithOneValidAndOneInvalidRegion(): RouteAdvisoriesManifest {
  return manifest([advisory('cloud_top', [modelResult('gfs', {
    data_state: 'complete',
    evidence_regions: [evidenceRegion(0, 0), evidenceRegion(99, 99)],
  })], { representative_model: 'gfs' })], ['gfs']);
}


export function legacyManifestWithoutEvidenceMetadata(): RouteAdvisoriesManifest {
  return manifest([advisory('cloud_top', [modelResult('gfs')], {
    representative_model: 'gfs',
  })], ['gfs']);
}


export function refreshedManifest(): RouteAdvisoriesManifest {
  return manifestWithDisjointGfsAndEcmwfRegions();
}


export function manifestWithoutFocusedModel(): RouteAdvisoriesManifest {
  return manifest([advisory('cloud_top', [modelResult('ecmwf', {
    data_state: 'complete',
    evidence_regions: [evidenceRegion(2, 2)],
  })], { representative_model: 'ecmwf' })], ['ecmwf']);
}


export function focusRegion(
  overrides: Partial<ResolvedFocusRegion> = {},
): ResolvedFocusRegion {
  return {
    model: 'gfs',
    startPointIndex: 0,
    endPointIndex: 0,
    startNm: 0,
    endNm: 15,
    lowerAltitudeFt: 5000,
    upperAltitudeFt: 9000,
    severity: 'amber',
    reasonCode: 'cloud_top_exceeds_ceiling',
    metricId: 'cloud_coverage',
    methodId: 'nwp',
    mapPath: [],
    ...overrides,
  };
}


export function modelAgreement(metricId = 'unsupported_metric'): RouteAdvisoryResult {
  return advisory('model_agreement', [modelResult('all', {
    data_state: 'complete',
    primary_method_id: 'model_divergence',
    evidence_regions: [evidenceRegion(0, 0, {
      reason_code: 'poor_model_agreement',
      metric_id: metricId,
      method_id: 'model_divergence',
    })],
  })], { representative_model: 'all' });
}


export function ddNwpCloudAgreement(): RouteAdvisoryResult {
  return advisory('dd_nwp_agreement', [modelResult('gfs', {
    data_state: 'complete',
    primary_method_id: 'dd_vs_nwp',
    evidence_regions: [
      evidenceRegion(0, 0, {
        reason_code: 'dd_cloud_disagreement',
        method_id: 'dewpoint_depression',
      }),
      evidenceRegion(0, 0, {
        reason_code: 'nwp_cloud_disagreement',
        method_id: 'nwp',
      }),
    ],
  })], { representative_model: 'gfs' });
}


export function frontsAdvisory(): RouteAdvisoryResult {
  return advisory('fronts', [modelResult('gfs', {
    data_state: 'complete',
    primary_method_id: 'hewson',
    evidence_regions: [],
  })], { representative_model: 'gfs' });
}


export function airportAdvisory(model = 'gfs'): RouteAdvisoryResult {
  return advisory('airport_wind', [modelResult(model, {
    data_state: 'complete',
    primary_method_id: 'runway_components',
    evidence_regions: [],
  })], { representative_model: model });
}


export function actionContext(
  overrides: Partial<AdvisoryActionContext> = {},
): AdvisoryActionContext {
  return {
    selectedModel: 'gfs',
    availableModels: ['gfs', 'ecmwf'],
    layout: 'cross-section',
    compareLayer: 'freezing-level',
    hasFronts: true,
    supportedAirportProfileModels: ['ecmwf', 'gfs', 'icon'],
    ...overrides,
  };
}
~~~

Then add this exact focus-selection behaviour:

~~~typescript
it('selects only the representative model for an aggregate focus', () => {
  const resolved = resolveAdvisoryFocus(
    {
      advisoryId: 'cloud_top',
      model: 'ecmwf',
      highlightSurfaces: ['cross-section', 'route-graph', 'route-map'],
      emphasizeLayers: ['square-nwp-cloud-bands', 'terrain', 'cruise-altitude'],
    },
    manifestWithTwoModels(),
    routeData(),
  );
  expect(resolved?.modelResult.model).toBe('ecmwf');
  expect(resolved?.regions.every((region) => region.model === 'ecmwf')).toBe(true);
  expect(resolved?.regions).toHaveLength(1);
});


it('never unions geometry from another model', () => {
  const resolved = resolveAdvisoryFocus(
    activeFocus('gfs'),
    manifestWithDisjointGfsAndEcmwfRegions(),
    routeData(),
  );
  expect(resolved?.regions.map((region) => [region.startNm, region.endNm]))
    .toEqual([[0, 15]]);
});


it('turns inclusive point indices into midpoint-owned distances', () => {
  const points = routeData([0, 10, 50, 100]).points;
  expect(pointCellBounds(points, 1)).toEqual({ startNm: 5, endNm: 30 });
  expect(pointCellBounds(points, 2)).toEqual({ startNm: 30, endNm: 75 });
});


it('filters one malformed region without disabling valid regions', () => {
  const resolved = resolveAdvisoryFocus(
    activeFocus('gfs'),
    manifestWithOneValidAndOneInvalidRegion(),
    routeData(),
  );
  expect(resolved?.regions).toHaveLength(1);
});


it('keeps legacy focus but reports location unavailable', () => {
  const resolved = resolveAdvisoryFocus(
    activeFocus('gfs'),
    legacyManifestWithoutEvidenceMetadata(),
    routeData(),
  );
  expect(resolved?.locationState).toBe('legacy');
  expect(resolved?.regions).toEqual([]);
});
~~~

- [ ] **Step 3: Implement focus types and validation**

Create:

~~~typescript
export type AdvisoryHighlightSurface =
  | 'cross-section'
  | 'route-graph'
  | 'route-map';

export interface ActiveAdvisoryFocus {
  advisoryId: string;
  model: string;
  highlightSurfaces: AdvisoryHighlightSurface[];
  emphasizeLayers: string[];
}

export interface ResolvedFocusRegion {
  model: string;
  startPointIndex: number;
  endPointIndex: number;
  startNm: number;
  endNm: number;
  lowerAltitudeFt: number | null;
  upperAltitudeFt: number | null;
  severity: 'green' | 'amber' | 'red';
  reasonCode: string;
  metricId: string | null;
  methodId: string | null;
  mapPath: Array<{ lat: number; lon: number }>;
}

export interface ResolvedAdvisoryFocus {
  active: ActiveAdvisoryFocus;
  advisory: RouteAdvisoryResult;
  modelResult: ModelAdvisoryResult;
  regions: ResolvedFocusRegion[];
  locationState: 'available' | 'partial' | 'unavailable' | 'legacy';
}
~~~

Implement:

~~~typescript
export function pointCellBounds(
  points: readonly VizPoint[],
  position: number,
): { startNm: number; endNm: number } | null

export function routeCellPath(
  points: readonly VizPoint[],
  startPosition: number,
  endPosition: number,
): Array<{ lat: number; lon: number }>

export function resolveAdvisoryFocus(
  active: ActiveAdvisoryFocus | null,
  manifest: RouteAdvisoriesManifest | null,
  data: VizRouteData,
): ResolvedAdvisoryFocus | null

export function reconcileAdvisoryFocus(
  active: ActiveAdvisoryFocus | null,
  manifest: RouteAdvisoriesManifest | null,
): ActiveAdvisoryFocus | null

export function replaceAdvisoryFocus(
  current: ActiveAdvisoryFocus | null,
  next: ActiveAdvisoryFocus,
): ActiveAdvisoryFocus

export function effectiveEmphasis(
  active: ActiveAdvisoryFocus | null,
  activePreset: string | null,
): string[] | null

export function focusedMethodId(
  focus: ResolvedAdvisoryFocus | null,
): string | null
~~~

Validation rules must mirror the backend, additionally requiring every stable point index in the inclusive span to exist in current VizRouteData. Log unknown IDs/malformed regions with console.warn and skip only the bad region.

`replaceAdvisoryFocus` returns `next`. `effectiveEmphasis` returns a copy of
`active.emphasizeLayers` only while both an active focus and a non-null active
preset exist; otherwise it returns null. `focusedMethodId` computes each
region's effective method as `region.methodId ?? modelResult.primary_method_id`;
if every focused region has the same non-null effective method it returns that
ID, otherwise it returns the containing model's `primary_method_id`, otherwise
null. These definitions are reused
verbatim by the lifecycle and banner tasks.

- [ ] **Step 4: Add typed action planning tests**

~~~typescript
it('maps existing preset advisories to preset-focus', () => {
  expect(actionForAdvisory('cloud_top')).toEqual({ kind: 'preset-focus' });
});

it('opens model agreement in cross-model compare', () => {
  expect(actionForAdvisory('model_agreement')).toEqual({ kind: 'compare-models' });
});

it('never maps DD/NWP agreement to cross-model compare', () => {
  expect(actionForAdvisory('dd_nwp_agreement')).toEqual({ kind: 'method-context' });
});

it('maps airport and fronts actions to their semantic surfaces', () => {
  expect(actionForAdvisory('airport_wind')).toEqual({ kind: 'airport-profile' });
  expect(actionForAdvisory('flight_category')).toEqual({ kind: 'airport-profile' });
  expect(actionForAdvisory('fronts')).toEqual({ kind: 'fronts-map' });
});
~~~

- [ ] **Step 5: Implement the action registry**

Create:

~~~typescript
export type AdvisoryAction =
  | { kind: 'preset-focus' }
  | { kind: 'compare-models' }
  | { kind: 'method-context' }
  | { kind: 'airport-profile' }
  | { kind: 'fronts-map' };

const SPECIAL_ACTIONS: Readonly<Record<string, AdvisoryAction>> = {
  model_agreement: { kind: 'compare-models' },
  dd_nwp_agreement: { kind: 'method-context' },
  airport_wind: { kind: 'airport-profile' },
  flight_category: { kind: 'airport-profile' },
  fronts: { kind: 'fronts-map' },
};

export function actionForAdvisory(advisoryId: string): AdvisoryAction | null {
  if (ADVISORY_TO_PRESET[advisoryId]) return { kind: 'preset-focus' };
  return SPECIAL_ACTIONS[advisoryId] ?? null;
}
~~~

Also export:

~~~typescript
export const COMPARE_LAYER_BY_METRIC: Readonly<Record<string, string>> = {
  freezing_level_ft: 'freezing-level',
  cloud_coverage: 'square-nwp-cloud-bands',
  cloud_cover_pct: 'square-nwp-cloud-bands',
  icing_risk: 'icing-bands',
  icing_ogimet_nwp_risk: 'icing-ogimet-nwp-bands',
  sfip_risk: 'sfip-bands',
  cat_risk: 'cat-bands',
  convective_risk: 'thermo-convective-bg',
  nwp_convective_risk: 'nwp-convective-bg',
};
~~~

Define the pure planner contract now so Task 13 tests and handlers use one
stable shape:

~~~typescript
import type { RouteAdvisoryResult } from '../types/advisories';
import type { VizLayout } from './types';

export interface AdvisoryActionContext {
  selectedModel: string;
  availableModels: string[];
  layout: VizLayout;
  compareLayer: string;
  hasFronts: boolean;
  supportedAirportProfileModels: string[];
}

export interface AdvisoryActionPlan {
  kind: AdvisoryAction['kind'];
  model: string | null;
  layout: VizLayout | null;
  enableModels: string[];
  compareLayer: string | null;
  layerOverrides: Record<string, boolean>;
  airportProfileModel: string | null;
  noteKey: string | null;
  noteParams: Record<string, string>;
  disabledReasonKey: string | null;
}
~~~

Task 13 implements the planner, after its failing tests, with this exact public
signature:

~~~typescript
export function planAdvisoryAction(
  advisory: RouteAdvisoryResult,
  context: AdvisoryActionContext,
  requestedModel?: string,
): AdvisoryActionPlan
~~~

The planner selects `requestedModel` first, then `representative_model`, and
never guesses a different forecast model for spatial evidence. Compare may use
a supported evidence `metric_id`; otherwise `compareLayer` is null and
`noteKey` is `advisories.noDirectCompareLayer`. Airport-profile fallback is the
only permitted model substitution: choose the first model that appears in both
`supportedAirportProfileModels` and `availableModels`, and return the original
and fallback labels in `noteParams`. Front absence returns
`disabledReasonKey: 'advisories.frontsUnavailable'`.

- [ ] **Step 6: Add backend-sourced method labels**

Create a closed label map whose stable IDs come only from the backend. Short
technical labels stay canonical; accessible descriptions resolve through i18n
with an English fallback until Task 13 adds all locale keys:

~~~typescript
import { t } from '../i18n/i18n';

export interface AdvisoryMethodLabel {
  short: string;
  description: string;
}

interface AdvisoryMethodDefinition {
  short: string;
  descriptionKey: string;
  fallbackDescription: string;
}

const METHOD_LABELS: Readonly<Record<string, AdvisoryMethodDefinition>> = {
  dewpoint_depression: { short: 'DD', descriptionKey: 'advisories.methods.dewpoint_depression', fallbackDescription: 'Dewpoint-depression cloud method' },
  nwp: { short: 'NWP', descriptionKey: 'advisories.methods.nwp', fallbackDescription: 'Model-native numerical weather prediction method' },
  nwp_synthesized: { short: 'NWP + DD envelope', descriptionKey: 'advisories.methods.nwp_synthesized', fallbackDescription: 'NWP cloud coverage constrained by a dewpoint-depression envelope' },
  nwp_precipitation_profile: { short: 'NWP precip', descriptionKey: 'advisories.methods.nwp_precipitation_profile', fallbackDescription: 'NWP precipitation phase and warm-nose profile' },
  ogimet_dd: { short: 'Ogimet-DD', descriptionKey: 'advisories.methods.ogimet_dd', fallbackDescription: 'Ogimet icing index with DD cloud signal' },
  ogimet_nwp: { short: 'Ogimet-NWP', descriptionKey: 'advisories.methods.ogimet_nwp', fallbackDescription: 'Ogimet icing index with NWP cloud signal' },
  sfip: { short: 'SFIP', descriptionKey: 'advisories.methods.sfip', fallbackDescription: 'Simplified Forecast Icing Potential' },
  ieng: { short: 'IENG', descriptionKey: 'advisories.methods.ieng', fallbackDescription: 'IENG icing method' },
  thermo: { short: 'Thermo', descriptionKey: 'advisories.methods.thermo', fallbackDescription: 'Thermodynamic convective method' },
  nwp_with_dd_floor: { short: 'NWP + DD floor', descriptionKey: 'advisories.methods.nwp_with_dd_floor', fallbackDescription: 'NWP result raised by the thermodynamic safety floor' },
  richardson_cat: { short: 'Ri CAT', descriptionKey: 'advisories.methods.richardson_cat', fallbackDescription: 'Richardson-number clear-air turbulence method' },
  vertical_motion: { short: 'Vertical motion', descriptionKey: 'advisories.methods.vertical_motion', fallbackDescription: 'Model vertical-motion trigger' },
  cat_with_vertical_motion: { short: 'CAT + motion', descriptionKey: 'advisories.methods.cat_with_vertical_motion', fallbackDescription: 'CAT and vertical-motion triggers both contributed' },
  terrain_wind: { short: 'Terrain wind', descriptionKey: 'advisories.methods.terrain_wind', fallbackDescription: 'Wind near significant terrain' },
  terrain_wind_wave: { short: 'Wind + wave', descriptionKey: 'advisories.methods.terrain_wind_wave', fallbackDescription: 'Terrain wind corroborated by a wave signature' },
  model_divergence: { short: 'Model spread', descriptionKey: 'advisories.methods.model_divergence', fallbackDescription: 'Cross-model divergence assessment' },
  dd_vs_nwp: { short: 'DD ↔ NWP', descriptionKey: 'advisories.methods.dd_vs_nwp', fallbackDescription: 'Within-model comparison of two derivations' },
  airport_conditions: { short: 'Airport NWP', descriptionKey: 'advisories.methods.airport_conditions', fallbackDescription: 'Forecast airport conditions' },
  runway_components: { short: 'Runway wind', descriptionKey: 'advisories.methods.runway_components', fallbackDescription: 'Runway-relative wind components' },
  density_altitude: { short: 'Density altitude', descriptionKey: 'advisories.methods.density_altitude', fallbackDescription: 'Forecast density-altitude calculation' },
  bulk_shear: { short: 'Bulk shear', descriptionKey: 'advisories.methods.bulk_shear', fallbackDescription: 'Low-level bulk wind shear' },
  hewson: { short: 'Hewson', descriptionKey: 'advisories.methods.hewson', fallbackDescription: 'Hewson frontal-boundary diagnostics' },
  vfr_composite: { short: 'VFR composite', descriptionKey: 'advisories.methods.vfr_composite', fallbackDescription: 'Multiple VFR feasibility methods tied for the controlling grade' },
  ifr_composite: { short: 'IFR composite', descriptionKey: 'advisories.methods.ifr_composite', fallbackDescription: 'Multiple IFR feasibility methods tied for the controlling grade' },
};

export function advisoryMethodLabel(
  methodId: string | null | undefined,
): AdvisoryMethodLabel | null {
  if (!methodId) return null;
  const definition = METHOD_LABELS[methodId];
  if (!definition) {
    console.warn(`Unknown advisory method_id: ${methodId}`);
    return null;
  }
  const translated = t(definition.descriptionKey);
  return {
    short: definition.short,
    description: translated === definition.descriptionKey
      ? definition.fallbackDescription
      : translated,
  };
}
~~~

Add unit assertions that `nwp_with_dd_floor` renders the compound short label,
that its description is non-empty, and that an unknown ID logs one warning and
returns null. Unknown method IDs produce no inferred badge.

- [ ] **Step 7: Extend advisory presets additively**

Add:

~~~typescript
highlights?: AdvisoryHighlightSurface[];
emphasize?: boolean;
~~~

Add to ResolvedView:

~~~typescript
highlightSurfaces?: AdvisoryHighlightSurface[];
emphasizeLayers?: string[];
~~~

For icing, clouds, convective, turbulence, VFR, and IFR presets set:

~~~typescript
highlights: ['cross-section', 'route-graph', 'route-map'],
emphasize: true,
~~~

The resolver derives emphasizeLayers from enabledLayers entries set true plus terrain and cruise-altitude. It does not change enabled-layer settings beyond the existing preset behaviour.

Add freezing_precip to ADVISORY_TO_PRESET and give it an override that enables sld-bands plus freezing-level. Do not add model/airport/fronts to the preset mapping.

- [ ] **Step 8: Run unit tests and commit**

Run:

~~~bash
cd web
npx vitest run tests/unit/advisory-focus.test.ts tests/unit/advisory-actions.test.ts tests/unit/advisory-methods.test.ts tests/unit/advisory-presets.test.ts
npx tsc --noEmit
git diff --check
git add ts/types/advisories.ts ts/visualization/types.ts ts/visualization/data-extract.ts ts/adapters/airport-profile-adapter.ts ts/visualization/advisory-focus.ts ts/visualization/advisory-actions.ts ts/visualization/advisory-methods.ts ts/visualization/cross-section/advisory-presets.ts tests/unit/advisory-focus.test.ts tests/unit/advisory-actions.test.ts tests/unit/advisory-methods.test.ts tests/unit/advisory-presets.test.ts tests/unit/fixtures/viz-point.ts tests/unit/fixtures/advisory-focus.ts
git commit -m "feat(web): add advisory focus and action contracts"
~~~

Expected: pure tests prove model isolation, legacy handling, action semantics, and method-label non-inference.

### Task 11: Render evidence and emphasis on cross-section, Compare, graph, and map

**Files:**

- Modify: web/ts/visualization/advisory-focus.ts
- Modify: web/ts/visualization/cross-section/renderer.ts:1-124
- Modify: web/ts/visualization/cross-section/compare-renderer.ts:1-180
- Modify: web/ts/visualization/route-graph/renderer.ts:1-220
- Modify: web/ts/visualization/route-map/renderer.ts:1-360
- Create: web/tests/unit/advisory-focus-rendering.test.ts

- [ ] **Step 1: Write pure rendering-adapter tests**

Test:

~~~typescript
it('cross-section altitude evidence retains its vertical bounds', () => {
  const primitive = crossSectionPrimitive(focusRegion({
    startNm: 10,
    endNm: 30,
    lowerAltitudeFt: 5000,
    upperAltitudeFt: 9000,
  }));
  expect(primitive).toEqual({
    kind: 'band',
    startNm: 10,
    endNm: 30,
    lowerAltitudeFt: 5000,
    upperAltitudeFt: 9000,
    severity: 'amber',
  });
});

it('cross-section route-only evidence becomes a rail, not full-depth fill', () => {
  expect(crossSectionPrimitive(focusRegion({
    lowerAltitudeFt: null,
    upperAltitudeFt: null,
  })).kind).toBe('route-rail');
});

it('map path starts and ends at midpoint cell boundaries', () => {
  const path = routeCellPath(routeData([0, 10, 50]).points, 1, 1);
  expect(path).toEqual([
    { lat: 50.5, lon: -0.5 },
    { lat: 51, lon: 0 },
    { lat: 51.5, lon: 0.5 },
  ]);
});
~~~

- [ ] **Step 2: Implement shared visual primitives**

In advisory-focus.ts export:

~~~typescript
export type CrossSectionFocusPrimitive =
  | {
      kind: 'band';
      startNm: number;
      endNm: number;
      lowerAltitudeFt: number;
      upperAltitudeFt: number;
      severity: 'green' | 'amber' | 'red';
    }
  | {
      kind: 'route-rail';
      startNm: number;
      endNm: number;
      severity: 'green' | 'amber' | 'red';
    };

export function crossSectionPrimitive(
  region: ResolvedFocusRegion,
): CrossSectionFocusPrimitive

export function renderCrossSectionFocus(
  ctx: CanvasRenderingContext2D,
  transform: CoordTransform,
  focus: ResolvedAdvisoryFocus,
): void

export function renderRouteGraphFocus(
  ctx: CanvasRenderingContext2D,
  plotArea: PlotArea,
  distanceToX: (distanceNm: number) => number,
  focus: ResolvedAdvisoryFocus,
): void
~~~

Use severity colour plus dashed/solid outline and diagonal hatching so colour is never the only cue. A zero-height band renders as a minimum four-pixel stripe centred on its altitude. A route-only cross-section region renders as a 10-pixel rail along the plot bottom, never a full-height atmospheric fill.

- [ ] **Step 3: Add focus and opacity emphasis to CrossSectionRenderer**

Add:

~~~typescript
private advisoryFocus: ResolvedAdvisoryFocus | null = null;
private emphasizedLayerIds: ReadonlySet<string> | null = null;

setAdvisoryFocus(focus: ResolvedAdvisoryFocus | null): void {
  this.advisoryFocus = focus;
}

setLayerEmphasis(layerIds: readonly string[] | null): void {
  this.emphasizedLayerIds = layerIds ? new Set(layerIds) : null;
}
~~~

Wrap each layer render so opacity never leaks into the following layer or the
focus/hover annotations:

~~~typescript
ctx.save();
if (
  this.emphasizedLayerIds
  && !this.emphasizedLayerIds.has(layer.id)
) {
  ctx.globalAlpha = 0.22;
}
layer.render(ctx, transform, data);
ctx.restore();
~~~

After meteorological layers and before the hover overlay, call renderCrossSectionFocus when the focus includes cross-section.

- [ ] **Step 4: Add explicit model-labelled focus to CompareSectionRenderer**

Add setAdvisoryFocus. Render the model-specific focus after the comparable layer and before terrain/reference annotations. Draw a compact label inside the plot:

~~~text
Cloud Tops · ECMWF evidence
~~~

Append Partial data when locationState is partial. Never label it consensus and never draw another model's regions.

- [ ] **Step 5: Add route-interval background to RouteGraphRenderer**

Add setAdvisoryFocus. Draw translucent, hatched intervals after the plot background/grid and before zero lines/series. Evidence must stay behind the plotted metrics and selected/hover crosshair.

- [ ] **Step 6: Add a map evidence pane under metric-coloured route segments**

In RouteMapRenderer:

~~~typescript
private evidenceGroup: L.LayerGroup | null = null;
private advisoryFocus: ResolvedAdvisoryFocus | null = null;

setAdvisoryFocus(focus: ResolvedAdvisoryFocus | null): void {
  this.advisoryFocus = focus;
}
~~~

Create a Leaflet pane wb-advisory-evidence with z-index 390 and pointerEvents none; keep normal route segments in overlayPane at z-index 400. Draw each focus mapPath as a wider severity-coloured halo with reduced opacity and a dashArray for partial data. The original metric-coloured segment stays visible above it.

Add fitRouteAndFronts() as a public method. It may include route points, current focus paths, and front-axis coordinates, but it must only be called by an explicit action, never every render.

- [ ] **Step 7: Verify renderers and commit**

Run:

~~~bash
cd web
npx vitest run tests/unit/advisory-focus.test.ts tests/unit/advisory-focus-rendering.test.ts
npx tsc --noEmit
git diff --check
git add ts/visualization/advisory-focus.ts ts/visualization/cross-section/renderer.ts ts/visualization/cross-section/compare-renderer.ts ts/visualization/route-graph/renderer.ts ts/visualization/route-map/renderer.ts tests/unit/advisory-focus-rendering.test.ts
git commit -m "feat(web): render advisory evidence across visualizations"
~~~

Expected: no renderer computes hazard thresholds; all consume already validated backend regions.

### Task 12: Wire ephemeral focus state, lifecycle, method badges, and spatial actions

**Files:**

- Modify: web/ts/store/briefing-store.ts:1-220,292-430,608-680,990-1170
- Modify: web/ts/managers/advisories-ui.ts:1-180,200-260,340-760
- Modify: web/ts/briefing-main.ts:1-360,1215-1575,1660-1810
- Modify: web/briefing.html:146-166
- Modify: web/ts/visualization/advisory-focus.ts
- Create: web/tests/unit/advisory-focus-lifecycle.test.ts

- [ ] **Step 1: Write failing pure lifecycle tests**

~~~typescript
it('replaces focus when another advisory is selected', () => {
  expect(replaceAdvisoryFocus(activeFocus('gfs', 'cloud_top'), activeFocus('gfs', 'turbulence')))
    .toEqual(activeFocus('gfs', 'turbulence'));
});

it('recalculation retains identifiers when advisory and model still exist', () => {
  expect(reconcileAdvisoryFocus(activeFocus('gfs'), refreshedManifest()))
    .toEqual(activeFocus('gfs'));
});

it('recalculation clears focus when the advisory or model disappears', () => {
  expect(reconcileAdvisoryFocus(activeFocus('gfs'), manifestWithoutFocusedModel()))
    .toBeNull();
});

it('manual layer edits retain evidence identity but disable emphasis', () => {
  const focus = activeFocus('gfs');
  expect(effectiveEmphasis(focus, null)).toBeNull();
  expect(focus.advisoryId).toBe('cloud_top');
});
~~~

- [ ] **Step 2: Add non-persisted focus state and atomic actions**

Extend BriefingState:

~~~typescript
activeAdvisoryFocus: ActiveAdvisoryFocus | null;
focusAdvisory: (
  focus: ActiveAdvisoryFocus,
  presetId: string,
  view: ResolvedView,
) => void;
clearAdvisoryFocus: () => void;
~~~

Do not add activeAdvisoryFocus to VizSettings or saveVizSettings.

focusAdvisory must atomically:

1. set selectedModel to focus.model and persist only selectedModel;
2. apply the resolved preset directives;
3. set activeAdvisoryFocus.

Generic applyAdvisoryPreset and setVizPreset must clear focus. Manual setSelectedModel clears focus. selectPack clears focus. toggleVizLayer leaves focus identity intact but clears activePreset as today, which makes effectiveEmphasis return null.

After successful recalculateAdvisories/changeFlightProfile/reanchor, call reconcileAdvisoryFocus against the new manifest. Do not copy regions into state.

- [ ] **Step 3: Replace the advisory chip callback with a typed action callback**

Change renderAdvisories to accept:

~~~typescript
onAdvisoryAction?: (advisoryId: string, model?: string) => void;
~~~

Replace the `ADVISORY_TO_PRESET`-only chip condition with
`actionForAdvisory(adv.advisory_id)`. Render one aggregate action button for
every non-null action, using these localized label keys:

~~~typescript
const ACTION_LABEL_KEY: Readonly<Record<AdvisoryAction['kind'], string>> = {
  'preset-focus': 'advisories.showOnChart',
  'compare-models': 'advisories.openCompare',
  'method-context': 'advisories.showOnChart',
  'airport-profile': 'advisories.openAirportProfile',
  'fronts-map': 'advisories.openFrontsMap',
};
~~~

The aggregate button uses no model argument. The per-model popup button
supplies the exact model. A `fronts-map` action whose per-model results are all
unavailable renders with `disabled`, `aria-disabled="true"`, and the
`advisories.frontsUnavailable` explanation; it never invokes the callback.

Render the aggregate method badge only from:

~~~typescript
const representative = adv.per_model.find(
  (model) => model.model === adv.representative_model,
);
const method = advisoryMethodLabel(representative?.primary_method_id);
~~~

No representative/provenance means no badge.

- [ ] **Step 4: Add per-model Show on chart to the popup**

When a model result has a preset-focus or method-context action, append:

~~~html
<button
  class="btn btn-secondary btn-sm advisory-model-focus-btn"
  data-advisory-id="${escapeHtml(adv.advisory_id)}"
  data-model="${escapeHtml(m.model)}"
  aria-label="${escapeHtml(t('advisories.showOnChart'))}: ${escapeHtml(modelLabel(m.model))}"
>
  ${escapeHtml(t('advisories.showOnChart'))}
</button>
~~~

Wire click and Enter/Space through the existing popup/event delegation. This action must focus only that model's evidence.

- [ ] **Step 5: Dispatch aggregate and model-specific spatial actions**

For preset-focus:

1. Read representative_model for an aggregate action.
2. If absent on a legacy pack, keep the current selected model only as the model whose preset is being displayed. Set legacy location state with no regions, suppress the "MODEL evidence" wording and method badge, and show "Older briefing — evidence model unavailable". Never call that selected model representative attribution.
3. Resolve the existing preset and create ActiveAdvisoryFocus from ResolvedView.highlightSurfaces/emphasizeLayers.
4. Call focusAdvisory.
5. If layout is map, switch to split. If layout is compare, keep Compare and let CompareSectionRenderer label the focused model.
6. Scroll #viz-section into view.

For a model-specific action, use the supplied model and never representative_model.

- [ ] **Step 6: Resolve focus once per visualization render**

After extractVizData:

~~~typescript
const focus = resolveAdvisoryFocus(
  state.activeAdvisoryFocus,
  getEffectiveAdvisories(state),
  data,
);
const emphasis = effectiveEmphasis(
  state.activeAdvisoryFocus,
  state.vizSettings.activePreset ?? null,
);
~~~

Pass focus to all four renderers and emphasis only to CrossSectionRenderer. A focus with legacy/unavailable location still drives the banner but gives renderers an empty region list.

- [ ] **Step 7: Add a focus banner and close control**

Add above viz-layout-wrapper:

~~~html
<div
  id="advisory-focus-banner"
  class="advisory-focus-banner"
  role="region"
  aria-live="polite"
  hidden
></div>
~~~

Render:

- `aria-label` from `advisories.focusLabel`;
- advisory name;
- model label;
- method badge for the focused evidence method when known;
- Partial data, Location unavailable, or Older briefing copy;
- a keyboard-operable Close button that calls clearAdvisoryFocus.

The focused method is a single region method_id when all focused regions agree; otherwise use the model primary_method_id. Do not infer from preset or profile settings.

- [ ] **Step 8: Verify state and commit**

Run:

~~~bash
cd web
npx vitest run tests/unit/advisory-focus.test.ts tests/unit/advisory-focus-lifecycle.test.ts tests/unit/advisory-methods.test.ts
npx tsc --noEmit
git diff --check
git add ts/store/briefing-store.ts ts/managers/advisories-ui.ts ts/briefing-main.ts briefing.html ts/visualization/advisory-focus.ts tests/unit/advisory-focus-lifecycle.test.ts
git commit -m "feat(web): wire advisory focus lifecycle"
~~~

Expected: focus is never persisted, model changes cannot leave stale evidence, and generic preset selection cannot fabricate advisory focus.

### Task 13: Implement Compare, DD/NWP, airport, and fronts actions; finish accessibility and integration

**Files:**

- Create: web/ts/components/briefing-airport-profile-drawer.ts
- Modify: web/ts/visualization/advisory-actions.ts
- Modify: web/ts/visualization/airport-profile-panel.ts:90-210,450-555
- Modify: web/ts/briefing-main.ts
- Modify: web/ts/managers/advisories-ui.ts
- Modify: web/maps.html:105-280
- Modify: web/css/style.css:3270-3650,3764-4180,4909-5235
- Modify: web/ts/i18n/locales/en.json
- Modify: web/ts/i18n/locales/fr.json
- Modify: web/ts/i18n/locales/de.json
- Modify: web/ts/i18n/locales/es.json
- Modify: web/tests/fixtures/egtf_eglf/advisories.json
- Modify: web/tests/briefing.spec.ts
- Create: web/tests/unit/advisory-action-plans.test.ts
- Modify: web/tests/unit/advisory-methods.test.ts

- [ ] **Step 1: Write pure action-plan tests**

Add these tests, importing all builders from
`tests/unit/fixtures/advisory-focus.ts`:

~~~typescript
it('model agreement enables every available model and keeps current layer without a mapping', () => {
  const plan = planAdvisoryAction(modelAgreement(), actionContext());
  expect(plan.kind).toBe('compare-models');
  expect(plan.enableModels).toEqual(['gfs', 'ecmwf']);
  expect(plan.compareLayer).toBeNull();
  expect(plan.noteKey).toBe('advisories.noDirectCompareLayer');
});

it('model agreement selects a directly comparable evidence metric', () => {
  const plan = planAdvisoryAction(
    modelAgreement('freezing_level_ft'),
    actionContext(),
  );
  expect(plan.compareLayer).toBe('freezing-level');
  expect(plan.noteKey).toBeNull();
});

it('DD/NWP cloud disagreement enables both derivations in one model', () => {
  const plan = planAdvisoryAction(ddNwpCloudAgreement(), actionContext());
  expect(plan.kind).toBe('method-context');
  expect(plan.layout).not.toBe('compare');
  expect(plan.layerOverrides).toMatchObject({
    'square-cloud-bands': true,
    'square-nwp-cloud-bands': true,
  });
});

it('front action is disabled when the artifact is absent', () => {
  const plan = planAdvisoryAction(frontsAdvisory(), actionContext({ hasFronts: false }));
  expect(plan.disabledReasonKey).toBe('advisories.frontsUnavailable');
});

it('unsupported airport model chooses a visible GRIB fallback', () => {
  const plan = planAdvisoryAction(airportAdvisory('meteofrance'), actionContext({
    availableModels: ['meteofrance', 'ecmwf'],
  }));
  expect(plan.airportProfileModel).toBe('ecmwf');
  expect(plan.noteKey).toBe('advisories.airportProfileFallback');
  expect(plan.noteParams).toEqual({ advisoryModel: 'meteofrance', profileModel: 'ecmwf' });
});
~~~

- [ ] **Step 2: Implement the pure planner and model-agreement Compare action**

Implement the function declared in Task 10. The pure portion is complete in
this step; later steps consume its plans rather than re-deciding action
semantics in event handlers:

~~~typescript
function emptyPlan(
  kind: AdvisoryAction['kind'],
  model: string | null,
): AdvisoryActionPlan {
  return {
    kind,
    model,
    layout: null,
    enableModels: [],
    compareLayer: null,
    layerOverrides: {},
    airportProfileModel: null,
    noteKey: null,
    noteParams: {},
    disabledReasonKey: null,
  };
}


export function planAdvisoryAction(
  advisory: RouteAdvisoryResult,
  context: AdvisoryActionContext,
  requestedModel?: string,
): AdvisoryActionPlan {
  const action = actionForAdvisory(advisory.advisory_id);
  if (!action) throw new Error(`No action registered for ${advisory.advisory_id}`);

  const attributedModel = requestedModel
    ?? advisory.representative_model
    ?? context.selectedModel;
  const attributedResult = advisory.per_model.find(
    (result) => result.model === attributedModel,
  ) ?? null;

  if (action.kind === 'compare-models') {
    const comparisonResult = advisory.per_model.find(
      (result) => result.model === advisory.representative_model,
    ) ?? advisory.per_model[0];
    const compareLayer = comparisonResult?.evidence_regions
      ?.map((region) => region.metric_id ?? '')
      .map((metricId) => COMPARE_LAYER_BY_METRIC[metricId])
      .find((layerId): layerId is string => Boolean(layerId)) ?? null;
    return {
      ...emptyPlan(action.kind, null),
      layout: 'compare',
      enableModels: [...context.availableModels],
      compareLayer,
      noteKey: compareLayer ? null : 'advisories.noDirectCompareLayer',
    };
  }

  if (action.kind === 'method-context') {
    const reasons = new Set(
      (attributedResult?.evidence_regions ?? []).map((region) => region.reason_code),
    );
    const layerOverrides: Record<string, boolean> = {};
    if (reasons.has('dd_cloud_disagreement') || reasons.has('nwp_cloud_disagreement')) {
      layerOverrides['square-cloud-bands'] = true;
      layerOverrides['square-nwp-cloud-bands'] = true;
    }
    if (reasons.has('freezing_level_disagreement')) {
      layerOverrides['freezing-level'] = true;
    }
    return {
      ...emptyPlan(action.kind, attributedModel),
      layout: 'cross-section',
      layerOverrides,
    };
  }

  if (action.kind === 'airport-profile') {
    const supported = new Set(context.supportedAirportProfileModels);
    const available = new Set(context.availableModels);
    const airportProfileModel = supported.has(attributedModel)
      && available.has(attributedModel)
      ? attributedModel
      : context.supportedAirportProfileModels.find((model) => available.has(model)) ?? null;
    const fellBack = airportProfileModel !== null && airportProfileModel !== attributedModel;
    return {
      ...emptyPlan(action.kind, attributedModel),
      airportProfileModel,
      noteKey: fellBack ? 'advisories.airportProfileFallback' : null,
      noteParams: fellBack && airportProfileModel ? {
        advisoryModel: attributedModel,
        profileModel: airportProfileModel,
      } : {},
    };
  }

  if (action.kind === 'fronts-map') {
    return {
      ...emptyPlan(action.kind, attributedModel),
      layout: 'map',
      disabledReasonKey: context.hasFronts
        ? null
        : 'advisories.frontsUnavailable',
    };
  }

  return emptyPlan(action.kind, attributedModel);
}
~~~

The handler must:

1. set layout compare;
2. set every available model true in compareModels, even if a prior session disabled one;
3. inspect representative/model evidence metric IDs and select only a supported comparable layer;
4. otherwise keep the user's current compare layer and show the localized no-direct-layer note;
5. clear active advisory spatial focus because model_agreement evidence belongs to model="all", not one forecast model.

- [ ] **Step 3: Implement DD/NWP method-context action**

Use the chosen or representative forecast model. Do not set layout compare.

- Cloud disagreement: apply a clean view with both square DD and square NWP cloud layers enabled.
- Freezing disagreement: enable the freezing line and focus the route/altitude span; do not fabricate a second line.
- Mixed evidence: enable both cloud derivations plus freezing line.
- Set activeAdvisoryFocus so route ranges render, with primary method DD ↔ NWP.

- [ ] **Step 4: Build the briefing airport-profile drawer**

Create a component with:

~~~typescript
export interface BriefingAirportProfileRequest {
  departureIcao: string;
  arrivalIcao: string;
  departureTime: string;
  arrivalTime: string;
  advisoryModel: string;
  availableModels: string[];
}

export class BriefingAirportProfileDrawer {
  open(request: BriefingAirportProfileRequest): void
  close(): void
  destroy(): void
}
~~~

Requirements:

- append a dialog-like aside to document.body;
- role="dialog", aria-modal="true", labelled heading;
- move focus to the selected endpoint tab on open and trap Tab/Shift+Tab within the drawer while open;
- departure and arrival tabs, no automatic worst-endpoint guess;
- departure loads planned departure time;
- arrival loads `request.arrivalTime`; the caller computes it once as planned departure plus `flight_duration_hours` before opening the drawer;
- hold at most one `AirportProfilePanel` instance at a time; both endpoint tabs reuse it while the drawer is open, and close destroys it then sets the reference to null so a later open constructs one fresh instance;
- supported models are gfs/icon/ecmwf;
- unsupported advisory model falls back to the first available supported model in preferred order ecmwf, gfs, icon and shows a visible note;
- close and pack/briefing change call AirportProfilePanel.destroy, which aborts SSE;
- Escape closes the drawer and focus returns to the action button.
- opening an airport action clears any previous spatial advisory focus so the background banner cannot describe a different advisory.

- [ ] **Step 5: Implement fronts-map action**

When routeFronts exists:

1. clear any previous spatial advisory focus;
2. select representative_model when it is a real route model;
3. set layout map;
4. set mapFrontsVisible true;
5. scroll #viz-section into view;
6. after render, call mapRenderer.fitRouteAndFronts once.

When absent, render the action disabled with localized availability explanation. Do not silently open an empty map.

- [ ] **Step 6: Move and extend reusable CSS**

Move every .ap-split-host, .ap-panel, .ap-*, and associated mobile media rule from maps.html into style.css without changing selectors. Verify /maps.html still renders the existing panel.

Add:

- advisory-focus-banner and close button;
- method badge;
- action button disabled state;
- partial-data hatch/dash key;
- briefing airport drawer, endpoint tabs, fallback note;
- focus-visible outlines;
- prefers-reduced-motion handling for smooth scroll/drawer transition;
- forced-colors outline rules so focus is not colour-only.

- [ ] **Step 7: Add localized copy in all four locale files**

Add matching keys for:

~~~text
advisories.showOnChart
advisories.openCompare
advisories.openAirportProfile
advisories.openFrontsMap
advisories.locationUnavailable
advisories.locationLegacy
advisories.partialData
advisories.noDirectCompareLayer
advisories.frontsUnavailable
advisories.focusClose
advisories.focusLabel
advisories.airportProfileTitle
advisories.airportProfileDeparture
advisories.airportProfileArrival
advisories.airportProfileFallback
advisories.methods.dewpoint_depression
advisories.methods.nwp
advisories.methods.nwp_synthesized
advisories.methods.nwp_precipitation_profile
advisories.methods.ogimet_dd
advisories.methods.ogimet_nwp
advisories.methods.sfip
advisories.methods.ieng
advisories.methods.thermo
advisories.methods.nwp_with_dd_floor
advisories.methods.richardson_cat
advisories.methods.vertical_motion
advisories.methods.cat_with_vertical_motion
advisories.methods.terrain_wind
advisories.methods.terrain_wind_wave
advisories.methods.model_divergence
advisories.methods.dd_vs_nwp
advisories.methods.airport_conditions
advisories.methods.runway_components
advisories.methods.density_altitude
advisories.methods.bulk_shear
advisories.methods.hewson
advisories.methods.vfr_composite
advisories.methods.ifr_composite
~~~

In `advisory-methods.test.ts`, compare the complete `advisories.methods` key set
across en/fr/de/es so a description cannot silently fall back in one locale.

- [ ] **Step 8: Extend the representative fixture**

The fixture must include:

- cloud_top with representative_model="ecmwf", disconnected ECMWF regions, and different GFS geometry;
- turbulence partial data;
- an all-unavailable advisory;
- convective nwp_with_dd_floor method;
- dd_nwp_agreement cloud and freezing regions;
- model_agreement;
- airport_wind and flight_category;
- fronts with and without artifact coverage through separate test mutations;
- one legacy result with absent data_state/evidence.

Every region point index must exist in route_analyses.json.

- [ ] **Step 9: Add Playwright acceptance tests**

Add ten separate Playwright cases with these exact names and assertions:

1. `aggregate focus uses only representative-model geometry`: click Cloud Tops; assert ECMWF in the focus banner, two disconnected route-map halo paths, and zero GFS-only halo paths.
2. `per-model keyboard action focuses the requested model`: open a per-model popup, focus Show on chart, press Enter, and assert that exact model in the banner.
3. `manual model selection clears advisory focus`: focus an advisory, change the model select, and assert the banner is hidden and all three surfaces have no focus primitives.
4. `manual layer edit retains evidence but clears emphasis`: toggle one layer; assert focus primitives remain while the non-emphasized opacity state is absent.
5. `forecast confidence opens Compare with every model`: click Forecast Confidence; assert compare layout and every available compare-model checkbox checked.
6. `DD versus NWP stays within one model`: click DD ↔ NWP; assert layout is not compare and both square DD/NWP cloud controls are checked.
7. `airport profile uses endpoint times and visible model fallback`: open the drawer, assert departure time, switch to arrival and assert ETA, assert fallback note, press Escape, and assert focus returns to the invoking button.
8. `fronts action fits available data and disables without artifact`: assert the available fixture opens map/front overlay; mutate the fixture to remove fronts and assert the action has `aria-disabled="true"` plus the localized explanation.
9. `legacy and unavailable packs never fabricate a halo`: activate each fixture, assert the Phase-1 preset applies, the location-unavailable/older-briefing text is visible, and no halo element is present.
10. `partial evidence has text and non-colour cues`: assert visible Partial data copy, an aria-label containing Partial data, and the dashed/hatched focus class.

For the airport SSE route, fulfill a finite event stream:

~~~typescript
await page.route('**/api/maps/airport-profile?**', route => route.fulfill({
  status: 200,
  contentType: 'text/event-stream',
  body: [
    'event: meta',
    'data: {"icao":"EGTF","lat":51.3,"lon":-0.8,"elevation_ft":300,"model":"ecmwf","start_hour":"2026-07-10T10:00:00Z","window_h":4,"hours":["2026-07-10T10:00:00Z"]}',
    '',
    'event: complete',
    'data: {}',
    '',
  ].join('\n'),
}));
~~~

- [ ] **Step 10: Verify frontend integration and commit**

Run:

~~~bash
cd web
npx vitest run
npx tsc --noEmit
npx playwright test tests/briefing.spec.ts
git diff --check
git add ts/components/briefing-airport-profile-drawer.ts ts/visualization/advisory-actions.ts ts/visualization/airport-profile-panel.ts ts/briefing-main.ts ts/managers/advisories-ui.ts maps.html css/style.css ts/i18n/locales/en.json ts/i18n/locales/fr.json ts/i18n/locales/de.json ts/i18n/locales/es.json tests/fixtures/egtf_eglf/advisories.json tests/briefing.spec.ts tests/unit/advisory-action-plans.test.ts tests/unit/advisory-methods.test.ts
git commit -m "feat(web): add advisory context actions and airport drawer"
~~~

Expected: unit, compile, and browser tests pass without npm run build.

### Task 14: Synchronize design docs, add the permanent review gate, verify, and prepare the PR

**Files:**

- Modify: designs/advisories.md
- Modify: designs/data-models.md
- Modify: designs/analysis-metrics.md
- Modify: designs/visualization.md
- Modify: designs/route-graph.md
- Modify: docs/superpowers/audits/2026-07-10-issue-223-meteorology-audit.md
- Create: docs/meteorology-review-checklist.md
- Create: .github/PULL_REQUEST_TEMPLATE.md
- Create during review: docs/superpowers/reviews/2026-07-10-issue-223-fresh-model-review.md
- Create during delivery: docs/superpowers/prs/2026-07-10-issue-223.md

- [ ] **Step 1: Synchronize authoritative module docs**

Document:

- AdvisoryEvidenceRegion, data_state, primary_method_id, representative_model, and legacy semantics.
- One-assessment evaluator architecture and midpoint-owned route cells.
- Partial-data safety and exception results.
- Ogimet-NWP native-cloud availability semantics and explicit synthesized-cloud provenance.
- Corrected SFIP 15/30/55 display mapping and normalized no-vv weights.
- Correct interval Jaccard.
- Ephemeral activeAdvisoryFocus, preset highlights/emphasis, model-specific rendering, and lifecycle.
- Compare/DD-NWP/airport/fronts actions.
- Route graph focus background and map halo behaviour.

In analysis-metrics.md, clearly distinguish objective corrections from unchanged meteorological thresholds. Do not add a meteorology-decisions.md entry unless execution uncovered qualifying external evidence for an actual meteorological decision change.

- [ ] **Step 2: Close every audit row with evidence**

For each A223 row, add:

- commit hash;
- exact tests;
- final disposition;
- any new follow-up issue URL.

High/medium safety or contract rows must be Resolved before PR creation. Calibration-dependent rows may be Deferred only with the evidence gap stated.

- [ ] **Step 3: Add the permanent meteorology review checklist**

Create:

~~~markdown
# Meteorology Change Review Checklist

Use this checklist for any PR that changes weather equations, units, thresholds,
missing-data handling, severity mapping, aggregation, provenance, or weather
visualization thresholds.

- [ ] A fresh model/session reviewed the authoritative design docs and full diff.
- [ ] The reviewer independently checked equations and units.
- [ ] Missing data cannot appear as clear/GREEN.
- [ ] Backend and visual thresholds agree.
- [ ] Method and representative-model attribution are correct.
- [ ] Any calibration change cites literature, an independent oracle, or observations.
- [ ] Deferred calibration findings have explicit follow-up issues.
- [ ] The review contains concrete findings/evidence, not a bare approval.
~~~

- [ ] **Step 4: Add a conditional PR template gate**

Create:

~~~markdown
## Summary

## Verification

## Meteorology / metrics review

- [ ] Not applicable: this PR does not alter meteorology, weather metrics,
      missing-data semantics, aggregation, provenance, or weather display thresholds.
- [ ] Applicable: docs/meteorology-review-checklist.md is complete and the fresh-model
      review is linked below.

Fresh-model review:

Deferred calibration follow-ups:

## Screenshots
~~~

Contributors select one applicability box; they do not check both.

- [ ] **Step 5: Run the fresh-model review with an evidence-seeking prompt**

Use superpowers:requesting-code-review in a fresh context and provide:

~~~text
Review issue #223 as an independent aviation-meteorology and software-contract
reviewer. Read .claude/CLAUDE.md, designs/INDEX.md,
designs/meteorology-decisions.md, designs/advisories.md,
designs/analysis-metrics.md, designs/visualization.md, the approved #223 spec,
the audit ledger, and the complete branch diff.

Independently check:
1. equations, units, and distance geometry;
2. missing-versus-clear semantics;
3. severity and display threshold parity;
4. model and method attribution;
5. whether evidence regions can bridge unsupported air;
6. whether any change is actually a calibration change needing literature,
   an oracle, or observations;
7. frontend model isolation and lack of browser-side meteorology.

Report concrete findings with file/symbol references and severity. A bare
"looks good" is not sufficient. Separate blockers from observation-dependent
follow-ups.
~~~

Save the complete response and resolution notes in docs/superpowers/reviews/2026-07-10-issue-223-fresh-model-review.md. Fix every high/medium safety or contract finding and rerun affected tests.

- [ ] **Step 6: Run full automated verification**

Run:

~~~bash
source venv/bin/activate
pytest -q
cd web
npx vitest run
npx tsc --noEmit
npx playwright test
cd ..
git diff --check
git status --short
~~~

Expected: all commands pass. Record exact command output summaries in the PR.

- [ ] **Step 7: Run manual browser acceptance**

Run /devserver from this worktree and verify:

- cloud, icing, turbulence, and convection overlays align with underlying model layers;
- switching models clears or replaces evidence immediately;
- Compare focus is explicitly labelled with one model;
- route-map metric colours remain visible above halos;
- partial/legacy/unavailable states are understandable;
- airport drawer aborts on close/pack change;
- fronts action fits useful route/front context;
- keyboard focus, Escape, and forced-colour/reduced-motion states work.

Capture screenshots of:

1. cross-section + graph focus;
2. split view with map halo;
3. Compare with explicit model evidence label;
4. partial-data state;
5. airport drawer;
6. fronts map.

Attach screenshots to the PR; do not commit generated browser artifacts unless explicitly requested.

- [ ] **Step 8: Commit documentation and review evidence**

After Steps 6–7 provide the real results and screenshot URLs, create
`docs/superpowers/prs/2026-07-10-issue-223.md` using the exact structure in
Step 10 and replace its instructional sentences with the observed evidence.

Run:

~~~bash
git add designs/advisories.md designs/data-models.md designs/analysis-metrics.md designs/visualization.md designs/route-graph.md docs/superpowers/audits/2026-07-10-issue-223-meteorology-audit.md docs/meteorology-review-checklist.md .github/PULL_REQUEST_TEMPLATE.md docs/superpowers/reviews/2026-07-10-issue-223-fresh-model-review.md docs/superpowers/prs/2026-07-10-issue-223.md
git commit -m "docs: synchronize advisory evidence design and review gate"
~~~

- [ ] **Step 9: Perform final branch review**

Use superpowers:verification-before-completion, then superpowers:finishing-a-development-branch. Review:

~~~bash
git log --oneline origin/main..HEAD
BASE=$(git merge-base origin/main HEAD)
git diff --stat "$BASE" HEAD
git diff "$BASE" HEAD -- src/weatherbrief/analysis src/weatherbrief/models web/ts web/css designs docs .github app/flyfun-weather/flyfun-weatherTests
~~~

Confirm:

- no unsupported threshold recalibration;
- no browser-side hazard predicate;
- no cross-model evidence union;
- no activeAdvisoryFocus persistence;
- no unrelated user changes;
- no npm run build output.

- [ ] **Step 10: Push and submit the issue #223 PR**

Unless the audit exposed a genuine prerequisite blocker that materially improves reviewability as a separate PR, keep this as one tested PR:

Use the committed `docs/superpowers/prs/2026-07-10-issue-223.md`, which must
contain the final observed test counts/results, links to uploaded screenshots,
audit/review links, and any new follow-up issue URLs. It uses this exact section
structure and contains no bracketed tokens or unchecked claims:

~~~markdown
Closes #223

## Summary

- Adds the model-specific advisory evidence/data-state/provenance contract and representative-model attribution.
- Fixes the evidence-gated missing-data, aggregation, geometry, SFIP display/weight, and cloud-overlap defects documented in the audit.
- Adds web focus overlays, emphasis, Compare/DD-NWP/airport/front actions, and backend-sourced method badges.
- Preserves old-pack behaviour without guessed geometry; reuses the Skew-T linkage from #309.

## Verification

Record the exact pytest, Vitest, TypeScript, Playwright, manual-browser, and iOS-Codable test results observed in Steps 6–7.

## Meteorology / metrics review

Link the issue #223 audit, fresh-model review, resolved findings, and every deferred calibration follow-up.

## Screenshots

Link the six uploaded acceptance screenshots captured in Step 7.
~~~

~~~bash
git push -u origin codex/issue-223-evidence-contract
gh pr create \
  --repo roznet/flyfun-weather \
  --base main \
  --head codex/issue-223-evidence-contract \
  --title "Add model-specific advisory evidence and focus" \
  --body-file docs/superpowers/prs/2026-07-10-issue-223.md
~~~

The PR body must:

- link Closes #223;
- summarize backend contract, audit fixes, web interactions, and compatibility;
- list exact verification commands/results;
- link the audit and fresh-model review;
- identify deferred calibration issues;
- include screenshots;
- state explicitly that Skew-T linkage was reused from #309.

Expected: a reviewable tested PR with all merge gates satisfied.
