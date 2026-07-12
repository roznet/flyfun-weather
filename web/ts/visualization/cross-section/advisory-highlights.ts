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
 * The representative-model choice (which model's geometry the chip switches the
 * cross-section to) is decided by the backend and shipped as
 * `RouteAdvisoryResult.representative_model` (#393). The client just reads it —
 * the former line-for-line TypeScript reimplementation of the Python rule is gone.
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
 * The representative model for an advisory — the model whose geometry the chip
 * highlights. Read straight from the backend's `representative_model` field
 * (#393), which the server derives with the same rule it uses for
 * `aggregate_detail` / `aggregate_mitigations`. Falls back to the first
 * per-model entry only for old packs that predate the field. Returns `null`
 * only when there are no per-model results.
 */
export function representativeModel(adv: RouteAdvisoryResult): string | null {
  return adv.representative_model ?? adv.per_model[0]?.model ?? null;
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
