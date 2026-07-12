/** Alternate-departure-time comparison (#392).
 *
 * Pure logic, kept out of `managers/briefing-ui.ts` so it can be unit-tested
 * without dragging in the Leaflet/DOM module graph.
 */

import type { RouteAdvisoriesManifest } from '../types/advisories';

interface AdvisoryCounts {
  red: number;
  amber: number;
  green: number;
}

/**
 * Compare an alternate departure time against the planned one.
 *
 * Only advisories that **both** sides actually graded are comparable — the same
 * rule the backend uses when it diffs two scan candidates (`tasks/time_scan.py`
 * `_compare`: "either side unavailable → not comparable").
 *
 * This used to tally red/amber over each manifest whole, counting UNAVAILABLE as
 * neither. An alt time where nothing graded therefore scored {red: 0, amber: 0},
 * beat a planned time with real hazards, and rendered a green "Better" chip —
 * a slot we have no data for presenting as the better time to fly (#392). Absent
 * data is not an absence of hazards.
 *
 * Returns `null` when nothing is comparable, so the caller can withhold the chip
 * rather than invent a verdict.
 */
export function compareAdvisories(
  alt: RouteAdvisoriesManifest | null | undefined,
  primary: RouteAdvisoriesManifest | null | undefined,
): 'better' | 'same' | 'worse' | null {
  if (!alt || !primary) return null;

  const graded = (m: RouteAdvisoriesManifest) => {
    const map = new Map<string, string>();
    for (const a of m.advisories) {
      if (a.aggregate_status !== 'unavailable') {
        map.set(a.advisory_id, a.aggregate_status);
      }
    }
    return map;
  };

  const altGraded = graded(alt);
  const primaryGraded = graded(primary);

  const comparable = [...altGraded.keys()].filter((id) => primaryGraded.has(id));
  if (comparable.length === 0) return null;

  const tally = (map: Map<string, string>): AdvisoryCounts => {
    const c: AdvisoryCounts = { red: 0, amber: 0, green: 0 };
    for (const id of comparable) {
      const s = map.get(id);
      if (s === 'red') c.red++;
      else if (s === 'amber') c.amber++;
      else if (s === 'green') c.green++;
    }
    return c;
  };

  const a = tally(altGraded);
  const p = tally(primaryGraded);

  // Fewer reds wins, ties broken by fewer ambers (same rule as the altitude table).
  if (a.red < p.red) return 'better';
  if (a.red > p.red) return 'worse';
  if (a.amber < p.amber) return 'better';
  if (a.amber > p.amber) return 'worse';
  return 'same';
}
