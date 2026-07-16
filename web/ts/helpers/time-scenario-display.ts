/** Pure display helpers for the timing-scenario section (#434/#435).
 *
 * Extracted from `renderTimeScenarios` so the partition rules and the
 * `advisory_status` → per-model-dot inversion are unit-testable in isolation —
 * the branching (confirmed-never-hidden, old-artifact-treated-as-not-worse,
 * coverage-scoped per-model map) is exactly the kind of logic that regresses
 * silently otherwise.
 */
import type { TimeCandidateDTO } from '../adapters/api-adapter';

/** True when a candidate belongs behind the "show all" toggle.
 *
 *  Only an **unconfirmed** graded-worse sweep row hides. A confirmed row is
 *  never hidden — the user paid a tap + a full multi-model check for it, so a
 *  confirm-downgrade stays visible with its honest verdict rather than
 *  vanishing (#435). Old artifacts have no `disposition` (defaults absent) and
 *  are treated as not-worse. */
export function isWorseCandidate(c: TimeCandidateDTO): boolean {
  return !c.is_baseline && !c.is_alternate && c.disposition === 'worse' && !c.confirmed;
}

/** Count of candidates that look smoother than the plan — drives the headline.
 *  Keyed off `disposition`, so it's confirm-aware once the scan re-derives it. */
export function improvingCount(candidates: TimeCandidateDTO[]): number {
  return candidates.filter(
    (c) => !c.is_baseline && !c.is_alternate && c.disposition === 'improving',
  ).length;
}

/** Invert a candidate's `advisory_status` ({advisory_id: {model: STATUS}}) into
 *  the per-model dot-column shape ({model, map: advisory_id → STATUS}).
 *
 *  `models` (the candidate's `models_used`) filters the result to what was
 *  actually graded, so a model-agnostic "all" entry — or any model not in the
 *  candidate's coverage — never implies a per-model check we didn't run. The
 *  map keeps GREEN statuses too, which is the whole point: it reconstructs the
 *  dot row at any coverage without re-grading. */
export function invertAdvisoryStatus(
  advisoryStatus: Record<string, Record<string, string>> | undefined,
  models: string[],
): { model: string; map: Map<string, string> }[] {
  const allow = new Set(models);
  const byModel = new Map<string, Map<string, string>>();
  for (const [advId, modelMap] of Object.entries(advisoryStatus ?? {})) {
    for (const [model, status] of Object.entries(modelMap)) {
      if (!allow.has(model)) continue;
      let m = byModel.get(model);
      if (!m) { m = new Map(); byModel.set(model, m); }
      m.set(advId, status);
    }
  }
  return [...byModel.entries()].map(([model, map]) => ({ model, map }));
}
