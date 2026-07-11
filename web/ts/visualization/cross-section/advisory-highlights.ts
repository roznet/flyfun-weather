/**
 * Advisory highlight derivation (issue #373).
 *
 * The client stores ONLY the tracked advisory id (`activeHighlightAdvisoryId`),
 * never a copy of the geometry. The scrim regions + verdict ribbon are derived
 * reactively at render time from `getEffectiveAdvisories(state)` × `selectedModel`
 * — so model switches, recalcs, and altitude changes update the highlight with no
 * stale-copy bugs, and a vanished advisory (old pack / recalc dropped it) simply
 * derives to `null`.
 *
 * Also home to the representative-model policy, mirroring the Python
 * `RouteAdvisoryResult.from_per_model` / `_aggregate_mitigations` semantics: the
 * first per-model entry whose status equals the aggregate status. The chip switches
 * the cross-section to this model so the highlight reflects the aggregate verdict.
 */

import type {
  AdvisoryHighlights,
  RouteAdvisoriesManifest,
  RouteAdvisoryResult,
} from '../../types/advisories';

/** Layer id of the advisory highlight layer (scrim + ribbon). Shared so the
 *  store's toggle special-casing and the layer definition never drift. */
export const HIGHLIGHT_LAYER_ID = 'advisory-highlight';

/**
 * The representative model for an advisory: the first `per_model` entry whose
 * status equals `aggregate_status` (the same policy Python uses to choose
 * `aggregate_detail` / `aggregate_mitigations`), falling back to the first
 * per-model entry. Returns `null` only when there are no per-model results.
 */
export function representativeModel(adv: RouteAdvisoryResult): string | null {
  const match = adv.per_model.find((m) => m.status === adv.aggregate_status);
  if (match) return match.model;
  return adv.per_model[0]?.model ?? null;
}

/** Find one advisory by id in a manifest (or null). */
export function findAdvisory(
  manifest: RouteAdvisoriesManifest | null,
  advisoryId: string | null | undefined,
): RouteAdvisoryResult | null {
  if (!manifest || !advisoryId) return null;
  return manifest.advisories.find((a) => a.advisory_id === advisoryId) ?? null;
}

/**
 * Derive the highlight geometry to render: look up `advisoryId` in the effective
 * advisories, find the `per_model` entry for `model`, and return its `.highlights`.
 * Returns `null` when the advisory is not highlighted, no longer exists, the
 * model has no entry, or the pack carries no highlight data (old pack) — in every
 * case the highlight layer and its panel toggle stay hidden.
 */
export function deriveHighlights(
  manifest: RouteAdvisoriesManifest | null,
  advisoryId: string | null | undefined,
  model: string,
): AdvisoryHighlights | null {
  const adv = findAdvisory(manifest, advisoryId);
  if (!adv) return null;
  const perModel = adv.per_model.find((m) => m.model === model);
  return perModel?.highlights ?? null;
}
