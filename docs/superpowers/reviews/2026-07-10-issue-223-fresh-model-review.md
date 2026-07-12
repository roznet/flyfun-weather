# Issue #223 Fresh-Model Review

## Scope and independence

- Review base: `65f596ed2d45bbea34b849a5231150fe7dd9e9d9`
- Reviewed HEAD: `6b67dc2dcb454387209d484e7c4f79436769e649`
- Scope: meteorology, units, missing-data behavior, advisory denominators and
  geometry, method/representative attribution, and frontend threshold/evidence
  presentation for issue #223.
- Method: clean-room inspection of the committed range, direct reproductions,
  focused regressions, and the broader backend/frontend gates below.
- Independence: the range is linear with no merge commits. This is a standalone
  review with no dependency on another pull request or branch review.

## Verdict

**APPROVED** at the hashes above. No blocking meteorological, missing-data,
unit, attribution, threshold-display, or evidence-contract finding remains.

This approval is limited to the reviewed evidence contract. It does not claim a
browser acceptance run, a production build, or completion of unrelated
Playwright repairs.

## Six final reproductions and resolutions

### 1. Airport/IFR availability and missing-is-not-clear UI

The reviewer reproduced null airport ceiling/visibility inputs being usable as
clear/VFR evidence in IFR and browser summaries. Commits `aa470055` and
`bb983fd` now distinguish assessed-clear ceiling (`ceiling_evaluated=true`) from
missing ceiling, exclude evidence-free rows from category voting, render them as
muted `N/A`, and preserve independent wind display/reduction.

Regression evidence:

- `tests/analysis/advisories/test_feasibility_evidence.py::test_ifr_missing_airport_source_fields_is_partial_unavailable`
- `tests/analysis/advisories/test_feasibility_evidence.py::test_ifr_assessed_clear_ceiling_is_real_partial_airport_evidence`
- `tests/analysis/advisories/test_feasibility_evidence.py::test_ifr_known_ceiling_hazard_survives_missing_other_airport_sources`
- `web/tests/unit/airport-condition-rendering.test.ts` — `distinguishes assessed clear from an unassessed null ceiling`
- `web/tests/unit/airport-condition-rendering.test.ts` — `renders an evidence-free condition with a muted category and valid wind`
- `web/tests/unit/airport-summary.test.ts` — `excludes missing-derived VFR votes from the category summary`
- `web/tests/unit/airport-summary.test.ts` — `worst mode keeps wind-only rows out of category voting but in wind reduction`

### 2. Precipitation percentages over assessed evidence

The reviewer reproduced two hazardous precipitation assessments being diluted
by eight soundings with no `PrecipitationAssessment`. Commit `6b67dc2d` makes
snow/rain percentages use only evaluated precipitation assessments; the eight
missing points still make the route partial and do not vote clear.

Regression evidence:

- `tests/analysis/advisories/test_enroute_precip.py::test_partial_hazard_uses_only_precip_assessed_points_for_percentages`

### 3. FIKI local severity and terminal/cruise extent union

Earlier fixes made route-wide SLD/SEVERE triggers non-dilutable and made selected
icing-method availability explicit. The clean-room reproduction then showed
that an unrelated transit zone could inherit the route trigger grade and that
headline extent omitted terminal concern or double-counted cruise overlap.
Commit `02114469` keeps severity local to each zone and uses the unique union of
cruise concern and AMBER/RED terminal concern points.

Regression evidence:

- `tests/analysis/advisories/test_icing_evidence.py::test_fiki_severe_transit_zone_does_not_raise_unrelated_zone_severity`
- `tests/analysis/advisories/test_icing_evidence.py::test_fiki_cruise_and_terminal_concern_extent_is_unioned_without_double_counting`

### 4. `ModelDivergence.mean=None` absent semantics

The reviewer reproduced an all-null metric carrying `agreement=GOOD` and being
counted as assessed agreement. Commit `6b67dc2d` treats `mean=None` as absent:
all-absent input is unavailable, valid plus absent input is partial, and a
numeric mean remains assessed when only one individual model value is null.

Regression evidence:

- `tests/analysis/advisories/test_context_action_metadata.py::test_model_agreement_all_absent_metrics_are_unavailable_not_good`
- `tests/analysis/advisories/test_context_action_metadata.py::test_model_agreement_mixed_valid_and_absent_metrics_is_partial`
- `tests/analysis/advisories/test_context_action_metadata.py::test_model_agreement_numeric_mean_with_null_model_value_remains_complete`

### 5. Mixed-model freezing-rain descent safety

The reviewer reproduced a finite descent escape from one model absorbing an
icing-bearing freezing-rain model's no-escape result. Commit `02114469` makes
any such FZRA model block the aggregate descent recommendation. A normal model
with no icing still contributes `None` without blocking another model's finite
escape. A freezing-rain profile with no icing zones is explicitly outside the
approved correction.

Regression evidence:

- `tests/test_altitude_advisories.py::test_descend_freezing_rain_one_model_blocks_cross_model_escape`
- `tests/test_altitude_advisories.py::test_descend_model_without_icing_does_not_block_finite_escape`

### 6. Terminal-convection controlling provenance

The reviewer reproduced terminal convection being discarded when the top-level
airport artifact was absent, plus method attribution that lost same-grade ties.
Commits `aa470055` and `bb983fd` preserve terminal-only hazards and emit the
single controlling method or `flight_category_composite` when airport conditions
and convection tie, or when different convective methods produce the same
controlling grade. This includes HIGH and EXTREME both mapping to RED.

Regression evidence:

- `tests/analysis/advisories/test_airport_advisories.py::TestTerminalConvective::test_terminal_hazard_survives_missing_airport_domain`
- `tests/analysis/advisories/test_airport_advisories.py::TestTerminalConvective::test_condition_controlled_hazard_keeps_airport_provenance`
- `tests/analysis/advisories/test_airport_advisories.py::TestTerminalConvective::test_tied_condition_and_convection_use_composite_provenance`
- `tests/analysis/advisories/test_airport_advisories.py::TestTerminalConvective::test_tied_convective_methods_use_composite_provenance`
- `tests/analysis/advisories/test_airport_advisories.py::TestTerminalConvective::test_same_terminal_high_and_extreme_methods_tie_at_red`
- `tests/analysis/advisories/test_airport_advisories.py::TestFlightCategoryEvaluator::test_no_airport_conditions_or_terminal_convection_is_unavailable`

## Broader verified contract

The reviewer also rechecked the already-corrected evidence foundations:

- empty/all-unavailable aggregation and evaluator failures remain explicit;
- representative model, aggregate detail, mitigations, and method attribution
  remain aligned;
- midpoint-cell geometry and per-model evidence regions remain backend-owned;
- partial-clear results cannot appear GREEN while supported hazards survive;
- native-cloud unavailable versus assessed-clear semantics remain distinct;
- SFIP display uses 15/30/55 and DD/NWP Jaccard uses merged interval unions;
- CAT evidence survives absent omega, convective fallback provenance remains
  thermodynamic, and airport wind/LLWS ties retain compound provenance;
- browser focus consumes backend severity/geometry and does not recalibrate
  meteorological thresholds.

No unsupported calibration change was accepted. In particular, SFIP
missing-omega normalization is an objective missing-weight correction: the
absent member and its weight are removed, present weights are normalized, and
the membership functions and 15/30/55 thresholds are unchanged.

## Verification evidence

| Gate | Command/evidence | Result |
| --- | --- | --- |
| Backend advisory sweep | `venv/bin/pytest -q tests/analysis/advisories` | 665 passed |
| Focused backend metric gate | Reviewer-reported focused rerun; returned record did not retain its file-list command | 102 passed |
| Full frontend Vitest | `cd web && npx vitest run` | 36 files, 607 tests passed |
| Focused frontend contract | Reviewer-reported six-file rerun; returned record did not retain its file-list command | 6 files, 117 tests passed |
| Patch hygiene | `git diff --check` | clean |
| TypeScript | `cd web && npx tsc --noEmit` | only unchanged `web/ts/eval/label-panel.ts:233` and `:242` errors |

No build or browser acceptance run was part of this meteorology gate.

## Non-blocking follow-ups

- #382 — Ri/SLD/resolution calibration requires an independent oracle or observations.
- #383 — DD/okta labeling needs observational validation.
- #384 — route-graph above-scale versus unavailable UX remains to be designed.
- #385 — airport gust-vector calibration needs POH, standard, or observational evidence.

The detailed discovery and closure ledger is in
[the meteorology audit](../audits/2026-07-10-issue-223-meteorology-audit.md), and
future changes should use the permanent
[meteorology review checklist](../../meteorology-review-checklist.md).
