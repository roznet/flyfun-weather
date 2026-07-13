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
  EngineMethodDefaults,
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
 * Clouds carry only a bare *source* (`dd` / `nwp` / `nwp_synthesized`) here —
 * the render style lives entirely in `vizSettings.cloudStyle` and is fused with
 * the source at layer-composition time (#410), so nothing about the user's style
 * is touched. `nwp_synthesized` renders on the same NWP band as `nwp`.
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

  const value = group === 'clouds' ? (effective === 'dd' ? 'dd' : 'nwp') : effective;
  return { ...preferredMethods, [group]: value };
}

/**
 * The route-point position to jump to when an advisory is focused — the "worst
 * point" (#223's jump-to-worst-point, the last piece of the Skew-T linkage).
 *
 * The backend already ships `AdvisoryHighlights.peak_dist_nm` (#373): the point
 * that earned the grade, in route distance. It had no consumer until now — the
 * chip lit up the chart but left the cursor wherever it happened to be, so the
 * Skew-T kept showing an unrelated point.
 *
 * Returns an **array position** into `analyses` (what `setSelectedPoint` wants),
 * not a `point_index`. `null` when the advisory has no peak — a GREEN advisory
 * has nothing to point at, and we leave the user's cursor alone rather than
 * moving it somewhere arbitrary.
 */
export function peakPointPosition(
  adv: RouteAdvisoryResult,
  model: string | null,
  analyses: Array<{ distance_from_origin_nm: number }>,
): number | null {
  const peak = adv.per_model.find((m) => m.model === model)?.highlights?.peak_dist_nm;
  if (peak == null || !analyses.length) return null;

  let best = -1;
  let bestGap = Infinity;
  for (let i = 0; i < analyses.length; i++) {
    const gap = Math.abs(analyses[i].distance_from_origin_nm - peak);
    if (gap < bestGap) {
      bestGap = gap;
      best = i;
    }
  }
  return best >= 0 ? best : null;
}

/**
 * Derive the profile's graded methods for each cross-section group from the
 * loaded advisory manifest — the honest source now that account-level engine
 * methods are retired (#410). Each method-bearing advisory carries, on its
 * representative model, the `primary_method_id` it actually graded on (already
 * reflecting any backend fallback). We take the first such signal per group and
 * fall back to the catalog's declared defaults when the pack is silent.
 *
 * Clouds resolve to a bare source (`dd` / `nwp`); the render style is applied
 * later from `vizSettings.cloudStyle`. Icing/convection resolve to their full
 * method ids (`ogimet_dd`, `sfip_nwp`, `thermo`, …).
 */
export function deriveGradedMethods(
  manifest: RouteAdvisoriesManifest | null,
  defaults: EngineMethodDefaults,
): Record<string, string> {
  const result: Record<string, string> = {
    clouds: defaults.cloud_source === 'dd' ? 'dd' : 'nwp',
    icing: defaults.icing_method,
    convection: defaults.convective_method,
  };
  if (!manifest) return result;

  const found = new Set<string>();
  for (const adv of manifest.advisories) {
    const group = ADVISORY_METHOD_GROUP[adv.advisory_id];
    if (!group || found.has(group)) continue;
    const rep = representativeModel(adv);
    const pm = adv.per_model.find((m) => m.model === rep) ?? adv.per_model[0];
    const method = pm?.primary_method_id;
    if (!method) continue;
    result[group] = group === 'clouds' ? (method === 'dd' ? 'dd' : 'nwp') : method;
    found.add(group);
  }
  return result;
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
