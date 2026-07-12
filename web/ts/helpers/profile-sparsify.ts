/** Sparse-profile helpers (#403 Part B).
 *
 *  We persist only what differs from the default: on save the settings page drops
 *  any advisory param or engine method equal to its default. Absence resolves to
 *  the same default server-side (advisory params via
 *  `evaluate_all`'s `{**catalog_defaults, **user_params}` merge; engine methods
 *  via `ENGINE_METHOD_DEFAULTS`), so pruning is lossless for grading and lets an
 *  already-dense profile shrink. These are pure so they can be unit-tested away
 *  from the DOM. */

import type { AdvisoryCatalogEntry } from '../types/advisories';

/** Lookup of catalog default keyed `advisoryId:paramKey`. */
export function buildParamDefaults(catalog: AdvisoryCatalogEntry[]): Map<string, number> {
  const m = new Map<string, number>();
  for (const entry of catalog) {
    for (const p of entry.parameters) m.set(`${entry.id}:${p.key}`, p.default);
  }
  return m;
}

/** Drop any advisory param whose value equals the catalog default. Params with no
 *  catalog default (unknown key) are kept. Enable flags are NOT touched here —
 *  they are deliberately never sparsified (#402: `fronts` false equals the catalog
 *  default yet is a meaningful override). */
export function pruneAdvisoryParams(
  rawParams: Record<string, Record<string, number>>,
  paramDefaults: Map<string, number>,
): Record<string, Record<string, number>> {
  const out: Record<string, Record<string, number>> = {};
  for (const advId of Object.keys(rawParams)) {
    for (const key of Object.keys(rawParams[advId])) {
      const val = rawParams[advId][key];
      const dflt = paramDefaults.get(`${advId}:${key}`);
      if (dflt !== undefined && val === dflt) continue;  // equals default → prune
      if (!out[advId]) out[advId] = {};
      out[advId][key] = val;
    }
  }
  return out;
}

/** Map an engine method to `null` ("follow the default") when it equals its
 *  declared default, else return it unchanged. Sent as an explicit null so the
 *  preview endpoint (which keys on JSON presence) grades on the resolved default
 *  and the save deletes the stored key. */
export function pruneEngineMethod(value: string, dflt: string | undefined): string | null {
  return dflt !== undefined && value === dflt ? null : value;
}
