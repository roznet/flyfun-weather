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

/**
 * Which layer group an advisory's `primary_method_id` speaks for. Only the
 * evaluators that grade off a user-selectable method appear here — the rest
 * (turbulence, mountain_wind, …) have no method axis to reconstitute.
 * `ifr_feasibility` is a composite whose `primary_method_id` is its icing axis.
 */
const ADVISORY_METHOD_GROUP: Record<string, 'clouds' | 'icing' | 'convection'> = {
  vmc_cruise: 'clouds',
  cloud_top: 'clouds',
  vfr_feasibility: 'clouds',   // composite; clouds are its only method-bearing axis
  icing_escape: 'icing',
  fiki_icing: 'icing',
  ifr_feasibility: 'icing',    // composite; icing is the axis it badges
  convective: 'convection',
};

/**
 * The preferred-method map to resolve an advisory's preset with, so the
 * cross-section shows the configuration the *advisory* was graded under rather
 * than whatever the user currently has selected in Settings.
 *
 * These normally coincide — the advisory is evaluated with the user's own
 * methods. They diverge exactly where it matters: when the requested method
 * could not run and the backend fell back (`*_method_effective`, #408). Showing
 * the requested layer there would paint evidence the grade never used.
 *
 * Only the one group the advisory speaks for is overridden; every other group
 * keeps the user's preference. An advisory with no `primary_method_id` (GREEN —
 * `driving_method_id` only fires on a flagged grade) changes nothing: there is
 * no highlight to explain, so the user's own view is the right one.
 *
 * Clouds needs care. The backend knows only the *source* (`dd` / `nwp` /
 * `nwp_synthesized`), while the layer registry keys on source **and** render
 * style (`soft_nwp`, `square_dd`, …). Swapping in a bare source would silently
 * change the user's cloud style too, so we keep their style and replace only the
 * source.
 */
export function advisoryMethodOverrides(
  adv: RouteAdvisoryResult,
  model: string | null,
  preferredMethods: Record<string, string>,
): Record<string, string> {
  const group = ADVISORY_METHOD_GROUP[adv.advisory_id];
  if (!group) return preferredMethods;

  const pm = adv.per_model.find((m) => m.model === model);
  const effective = pm?.primary_method_id;
  if (!effective) return preferredMethods;

  let value = effective;
  if (group === 'clouds') {
    // "nwp_synthesized" renders on the same NWP band as "nwp".
    const source = effective === 'dd' ? 'dd' : 'nwp';
    const current = preferredMethods.clouds ?? '';
    const style = current.replace(/_(dd|nwp)$/, '');
    value = style && style !== current ? `${style}_${source}` : source;
  }
  return { ...preferredMethods, [group]: value };
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
