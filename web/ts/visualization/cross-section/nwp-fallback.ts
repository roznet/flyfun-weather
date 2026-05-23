/** Render-time NWP→DD substitution for models without native NWP data.
 *
 *  Works purely on a throwaway `effectiveEnabled` map — the stored
 *  `enabledLayers` preference is never mutated, so switching back to an
 *  NWP-capable model auto-restores NWP with no persisted "downgraded" flag.
 */

import {
  ALL_CLOUD_LAYER_IDS,
  CLOUD_LAYER_BY_AXES,
  parseCloudLayerId,
} from './layers/cloud-bands-factory';

// Canonical "NWP clouds unavailable" signal — getUnavailableLayers emits only
// this natural-style id, but it covers every NWP cloud variant (all read the
// same native feed).
const NWP_CLOUDS_SIGNAL = 'nwp-cloud-bands';
const OGIMET_NWP = 'icing-ogimet-nwp-bands';
const OGIMET_DD = 'icing-bands';

/** Same-style DD layer that substitutes for an unavailable NWP layer, or null
 *  if `id` has no NWP→DD pair (DD layers, IENG, non-cloud/icing layers). */
export function getDdSubstituteId(id: string): string | null {
  if (id === OGIMET_NWP) return OGIMET_DD;
  const axes = parseCloudLayerId(id);
  if (axes && axes.source === 'nwp') return CLOUD_LAYER_BY_AXES.dd[axes.style];
  return null;
}

/** Build the throwaway effective-enable map for one render: start from the
 *  stored pref, disable what the model can't provide, then substitute same-
 *  style DD layers for any wanted-but-unavailable NWP layer. Never mutates. */
export function applyNwpFallback(
  enabledLayers: Record<string, boolean>,
  unavailable: Set<string>,
): Record<string, boolean> {
  const effective: Record<string, boolean> = { ...enabledLayers };
  for (const id of unavailable) effective[id] = false;

  if (unavailable.has(NWP_CLOUDS_SIGNAL)) {
    for (const id of ALL_CLOUD_LAYER_IDS) {
      if (enabledLayers[id] !== true) continue;
      const ddId = getDdSubstituteId(id);
      if (!ddId) continue;
      effective[id] = false;  // the replaced NWP variant must not stay enabled
      if (enabledLayers[ddId] !== true) effective[ddId] = true;
    }
  }

  // IENG has no DD pair, so it stays disabled by the loop above.
  if (unavailable.has(OGIMET_NWP) && enabledLayers[OGIMET_NWP] === true
      && enabledLayers[OGIMET_DD] !== true) {
    effective[OGIMET_DD] = true;
  }

  return effective;
}

/** Layer ids the fallback turned on but the user did not — the auto substitutes. */
export function getSubstitutedLayers(
  enabledLayers: Record<string, boolean>,
  effectiveEnabled: Record<string, boolean>,
): Set<string> {
  const substituted = new Set<string>();
  for (const [id, on] of Object.entries(effectiveEnabled)) {
    if (on && enabledLayers[id] !== true) substituted.add(id);
  }
  return substituted;
}
