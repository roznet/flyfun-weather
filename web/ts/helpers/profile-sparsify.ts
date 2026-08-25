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

/** advisoryId → { oldKey: newKey } for the #571 extent-parameter consolidation.
 *  The lockstep sibling of `weatherbrief/analysis/advisories/extent_param_migration.py`
 *  — the two MUST move together, or a client would re-save under an old key a
 *  profile the server just migrated. Kept as a literal because the catalog no
 *  longer carries the old names: this is the only record of what each key used
 *  to be called. */
export const EXTENT_KEY_RENAMES: Record<string, Record<string, string>> = {
  cloud_top: { pct_amber: 'extent_pct_amber' },
  convective: {
    affected_pct_amber: 'extent_pct_amber',
    affected_pct_red: 'extent_pct_red',
  },
  dd_nwp_agreement: { amber_pct: 'extent_pct_amber', red_pct: 'extent_pct_red' },
  enroute_precip: {
    snow_pct_amber: 'extent_pct_amber',
    snow_moderate_pct_red: 'extent_pct_red',
  },
  fiki_icing: {
    clear_cruise_amber_pct: 'extent_pct_amber',
    clear_cruise_red_pct: 'extent_pct_red',
  },
  freezing_precip: { primed_pct_amber: 'extent_pct_amber' },
  icing_escape: {
    icing_coverage_pct_amber: 'extent_pct_amber',
    no_escape_pct_red: 'extent_pct_red',
    route_pct_amber: 'extent_pct_amber',
    min_route_pct: 'extent_pct_red',
  },
  ifr_feasibility: {
    icing_pct_amber: 'extent_pct_amber',
    icing_pct_red: 'extent_pct_red',
  },
  model_agreement: {
    poor_pct_amber: 'extent_pct_amber',
    poor_pct_red: 'extent_pct_red',
  },
  turbulence: { route_pct_amber: 'extent_pct_amber' },
  vfr_feasibility: {
    imc_pct_amber: 'extent_pct_amber',
    imc_pct_red: 'extent_pct_red',
  },
  vmc_cruise: { bkn_pct_amber: 'extent_pct_amber', ovc_pct_red: 'extent_pct_red' },
};

/** Keys whose stored VALUE flips with the name. `fiki_icing` expressed its
 *  thresholds as a percentage of the *clear* cruise, compared with `<` — the one
 *  gate that read the other way. "Amber below 70% clear" means "amber at or above
 *  30% affected", so the number must invert or the pilot's tuning would too. */
const INVERTED_PCT_KEYS: Record<string, Set<string>> = {
  fiki_icing: new Set(['clear_cruise_amber_pct', 'clear_cruise_red_pct']),
};

/** Aliases applied only when the primary name is absent, mirroring the read-path
 *  fallback chain `icing_escape` used to have (`min_route_pct` was never even a
 *  catalog key). */
const SECONDARY: Record<string, Set<string>> = {
  icing_escape: new Set(['route_pct_amber', 'min_route_pct']),
};

/** Rewrite pre-consolidation extent keys to the consolidated ones (#571 Stage 3).
 *
 *  Run this on load, before rendering: a profile the server has not migrated yet
 *  (or one restored from a stale client cache) would otherwise show the pilot
 *  their tuning missing while the old key sat in the payload doing nothing.
 *  `pruneAdvisoryParams` cannot help — it deliberately KEEPS any key it cannot
 *  prove is a default, so an unknown key lingers forever.
 *
 *  Pure: returns a new object, never mutates. A value already under the new key
 *  wins and the old key is dropped. */
export function renameExtentParams(
  rawParams: Record<string, Record<string, number>>,
): Record<string, Record<string, number>> {
  const out: Record<string, Record<string, number>> = {};
  for (const advId of Object.keys(rawParams)) {
    out[advId] = { ...rawParams[advId] };
  }
  for (const advId of Object.keys(EXTENT_KEY_RENAMES)) {
    const params = out[advId];
    if (!params) continue;
    const renames = EXTENT_KEY_RENAMES[advId];
    const secondary = SECONDARY[advId] ?? new Set<string>();
    const inverted = INVERTED_PCT_KEYS[advId] ?? new Set<string>();
    // Primaries first, so a secondary alias can never shadow a real value.
    const ordered = Object.keys(renames).sort((a, b) => {
      const sa = secondary.has(a) ? 1 : 0;
      const sb = secondary.has(b) ? 1 : 0;
      return sa - sb || a.localeCompare(b);
    });
    for (const old of ordered) {
      if (!(old in params)) continue;
      const next = renames[old];
      const value = params[old];
      delete params[old];
      if (next in params) continue;  // a newer write under the new key wins
      params[next] = inverted.has(old) ? 100 - value : value;
    }
    if (Object.keys(params).length === 0) delete out[advId];
  }
  return out;
}
