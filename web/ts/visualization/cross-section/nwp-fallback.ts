/** Render-time NWP→fallback substitution for models without native NWP data.
 *
 *  When the selected model can't provide a method the user (or a preset) asked
 *  for, we substitute the method shown instead so the layer never silently
 *  renders nothing: NWP clouds → same-style DD clouds, NWP/SFIP icing →
 *  Ogimet-DD (sounding-derived, always available), NWP convective →
 *  thermodynamic (CAPE/CIN) scheme. This mirrors the backend's
 *  `_resolve_analyses` (advise.py), so the cross-section agrees with the
 *  advisories.
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
const OGIMET_DD = 'icing-bands';

/** NWP→fallback substitutions for single (non-cloud) layers. Each NWP layer maps
 *  to the method displayed instead when the model can't provide it. Icing falls
 *  back to Ogimet-DD (always available from the sounding); NWP convective falls
 *  back to the thermodynamic scheme — both mirror `_resolve_analyses`. Clouds are
 *  handled separately because they fan out across styles via the cloud factory.
 *  (IENG has no fallback pair, so it is intentionally absent — it stays off.) */
const SINGLE_LAYER_FALLBACK: Record<string, string> = {
  'icing-ogimet-nwp-bands': OGIMET_DD,      // Ogimet-NWP → Ogimet-DD
  'sfip-bands': OGIMET_DD,                   // SFIP-NWP   → Ogimet-DD
  'nwp-convective-bg': 'thermo-convective-bg', // NWP convective → thermodynamic
};

/** Substitute layer shown when the NWP layer `id` is unavailable, or null if it
 *  has no fallback (DD layers, IENG, non-cloud/icing/convective layers). Covers
 *  the single-layer map above plus same-style DD clouds. */
export function getDdSubstituteId(id: string): string | null {
  const single = SINGLE_LAYER_FALLBACK[id];
  if (single) return single;
  const axes = parseCloudLayerId(id);
  if (axes && axes.source === 'nwp') return CLOUD_LAYER_BY_AXES.dd[axes.style];
  return null;
}

/** Build the throwaway effective-enable map for one render: start from the
 *  stored pref, disable what the model can't provide, then substitute the
 *  fallback method for any wanted-but-unavailable NWP layer. Never mutates. */
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

  // Single (non-cloud) NWP layers: substitute the fallback method when the model
  // can't provide the layer and the user hasn't already enabled that fallback.
  // IENG is absent from the map, so it stays disabled (by the unavailable loop).
  for (const [nwpId, fallbackId] of Object.entries(SINGLE_LAYER_FALLBACK)) {
    if (unavailable.has(nwpId) && enabledLayers[nwpId] === true
        && enabledLayers[fallbackId] !== true) {
      effective[fallbackId] = true;
    }
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
