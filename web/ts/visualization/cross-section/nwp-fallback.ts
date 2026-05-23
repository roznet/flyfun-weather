/** Render-time layer substitution for models without native NWP data.
 *
 *  When the selected model lacks native NWP cloud data (e.g. ECMWF without
 *  GRIB enrichment), the NWP cloud layers and the Ogimet-NWP icing layer have
 *  no zones to draw and silently render nothing. Rather than letting the
 *  clouds/icing vanish, we transparently substitute the same-style DD layer.
 *
 *  This works purely on a throwaway `effectiveEnabled` object built per render.
 *  The user's stored `enabledLayers` preference is never mutated, so switching
 *  back to an NWP-capable model auto-restores NWP and drops the DD substitute —
 *  no persisted "downgraded" flag to track or distinguish from a genuine DD
 *  choice (an explicit DD pick lives in `enabledLayers`; the substitute only
 *  *adds* DD on top when NWP is unavailable).
 */

import {
  ALL_CLOUD_LAYER_IDS,
  CLOUD_LAYER_BY_AXES,
  parseCloudLayerId,
} from './layers/cloud-bands-factory';

/** Source-level "NWP clouds unavailable" signal. `getUnavailableLayers` adds
 *  only this canonical (natural-style) id, but it covers every NWP cloud
 *  variant — soft/natural/square all read the same native NWP cloud feed. */
const NWP_CLOUDS_SIGNAL = 'nwp-cloud-bands';

/** Ogimet-NWP icing → Ogimet-DD icing fallback pair (per PREFERRED_METHOD_LAYER.icing). */
const OGIMET_NWP = 'icing-ogimet-nwp-bands';
const OGIMET_DD = 'icing-bands';

/**
 * Build the throwaway `effectiveEnabled` map for a single cross-section render:
 *  1. start from the user's stored preference,
 *  2. disable every layer the current model can't provide,
 *  3. substitute same-style DD layers for any wanted-but-unavailable NWP layer.
 *
 * Returns a new object; never mutates the inputs.
 */
export function applyNwpFallback(
  enabledLayers: Record<string, boolean>,
  unavailable: Set<string>,
): Record<string, boolean> {
  const effective: Record<string, boolean> = { ...enabledLayers };

  // Disable what the model can't provide (without touching the stored pref).
  for (const id of unavailable) effective[id] = false;

  // Clouds: when the NWP source has no native data, substitute the same-style
  // DD layer for each NWP cloud style the user has enabled.
  if (unavailable.has(NWP_CLOUDS_SIGNAL)) {
    for (const id of ALL_CLOUD_LAYER_IDS) {
      if (enabledLayers[id] !== true) continue;
      const axes = parseCloudLayerId(id);
      if (!axes || axes.source !== 'nwp') continue;
      const ddId = CLOUD_LAYER_BY_AXES.dd[axes.style];
      if (enabledLayers[ddId] !== true) effective[ddId] = true;
    }
  }

  // Icing: Ogimet-NWP → Ogimet-DD (same mechanism). IENG has no DD pair, so it
  // stays disabled by the unavailable loop above.
  if (unavailable.has(OGIMET_NWP) && enabledLayers[OGIMET_NWP] === true
      && enabledLayers[OGIMET_DD] !== true) {
    effective[OGIMET_DD] = true;
  }

  return effective;
}

/** Layer ids the fallback turned on but the user did not — i.e. the auto
 *  substitutes. The panel renders these as checked + dimmed with a tooltip so
 *  it's clear DD is standing in for unavailable NWP. */
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
