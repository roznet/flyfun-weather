# Observed-motion implementation decisions

These are controller decisions made while executing the user-approved design for
PR #600. Each preserves its rationale and the consequence if wrong. They do not
establish source registration, predictive usefulness, or permission to deploy.
Current verification and unresolved review gates are recorded in the
[verification report](2026-09-05-observed-motion-verification.md).

## Decision 1

Use independent delegation only on disjoint owned files once consumed interfaces are stable; serialize commits — developer parallelization instruction takes precedence over the skill's blanket ban on parallel implementers — if wrong, conflicting changes require reconciliation and rerunning tests; no scientific requirement is relaxed.

## Decision 2

Tracking receives explicit projected continuous route geometry and task 4 builds it — the prior signature could not implement the approved route-proximity candidate ranking — if wrong, these internal callers/tests need adjustment; numerical acceptance thresholds and user scope are unchanged.

## Decision 3

Extract pure route/timing implementation into task 10 before payload task 4 — continuous geometry can be independently tested using the agreed grid/track interface while source/tracking work proceeds — if wrong, helper integration and tests need reconciliation; no approved result or acceptance condition is removed.

## Decision 4

Add `tasks/retention.py` full-pack deletion and `api/app.py` individual-pack account deletion to task 5 ownership — a caller audit found direct pack deletions beyond `storage/flights.py`, and the generic `_rmtree` also deletes an account's parent namespace — if wrong, deletion integration requires rework; no real data deletion is performed by implementation/tests. Preserve ordinary pack high-water, never blindly apply the pack helper to an ancestor directory, and do not reuse a removed account namespace whose high-water is lost.

## Decision 5

Return `TrackingResult` (tracks, reasons, explicit count/completeness metadata) rather than only a list from task 3 — the approved payload must distinguish bounded/unevaluated work and known omissions from evaluated zero, which a bare list cannot preserve — if wrong, task 3/4 internal call sites and count tests need adjustment; the Track shape/scientific algorithm is unchanged. No tracking code existed when the interface was corrected. Task 2 also exposes additive `HistoryResult.input_counts` with source, considered/inspected/selected/emitted/omitted counts and selection completeness; null means not enumerated.

## Decision 6

Create the plan-named web/css/briefing.css and import it from owned briefing-main.ts — that source file does not exist yet, but briefing.html already loads esbuild's /dist/briefing.css and the existing no-server harness serves its in-memory CSS output — if wrong, the CSS import/asset wiring needs correction and browser retesting; no generated files or global styles need to change.

## Decision 7

Preserve the integer match when NCC is already one within absolute float64 roundoff tolerance1e-12 (rtol0), after attempting quadratic refinement — Task3's asymmetric identical-image RED case has NCC1 but window-truncated neighbouring scores yield a negative-definite quadratic with ~0.1cell bias, inventing ~0.6m/s stationary movement; perfect normalized correlation cannot be improved beyond its upper bound — if wrong, genuinely fractional movement indistinguishable from NCC1 at that numerical tolerance could be quantized and the guard/refinement needs revision. No physical/support/NCC/lineage acceptance threshold is weakened. Worker must retain asymmetric stationary/exact integer tests and test non-perfect fractional refinement; independent Task3 review judges the change. Clarification is written into the still-in-progress design, not concealed in a fixture change.

## Decision 8

Use Euclidean radius2 for the competing-peak exclusion neighbourhood — metric was previously unspecified, and Euclidean distance agrees with the grid displacement/search/patch separation; diagonal offset(2,2) is a competitor — if wrong, some tracks may be refused more often than with a square exclusion and the choice must be revisited with replay. It does not weaken a diagnostic threshold or remove unavailable evidence. Add literal diagonal tests and carry clarification into the design/Task3 independent review.

## Decision 9

Resume work using available fallback worker models after concrete usage-limit failures — original native/server/payload workers could not continue, but gpt-5.6-terra independently completed the web review; preserve written briefs/original BASE ranges and every test/re-review gate — if a replacement misses prior context, rework and further regression review are required; no output is assumed complete. Task6 fix round1 therefore uses a fresh fallback implementer instead of the usage-limited original.

## Decision 10

Expand Task7 ownership to existing DEBUG Services/FixtureBriefingData.swift and FixtureBriefingRepository.swift — the authored explorer UI test currently opens a snapshot without observed_motion and only proves containers exist, not feature/evidence cards; a real fixture is required by the approved scenario — if wrong, test-fixture/lifecycle behavior needs rework and static/review repetition; no production gate or Mac execution is authorized. Worker must assert actual feature/evidence content and include both paths in task report/review.

## Decision 11

Task4 freshness/tick fix follows the normative any-eligible-feature tick rule and maximum accepted expiry, not the review's suggested earliest-family bound — design §3 allows a tick supported by one eligible feature with explicit unavailable entries for others, and contract expiry is the maximum — if wrong, heterogeneous-source projections need rework; no stale track or unsupported per-feature projection becomes eligible.

## Decision 12

Task4 lightning completeness counts regional reported detections separately from precise per-feature association summaries — overlapping feature summaries cannot be summed into a regional total, and missing/unevaluated evidence must remain null rather than zero — if wrong, completeness/card counts require correction and regression review; no negative lightning-coverage claim is permitted.
