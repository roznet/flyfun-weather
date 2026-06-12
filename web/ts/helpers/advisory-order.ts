/** Advisory ordering + display banding for the briefing dashboard.
 *
 * As the advisory list grows, a long run of all-green cards drowns out what
 * actually varies. We group cards into bands by actionability so the consistent
 * greens collapse into compact pills and the eye lands on the rest:
 *
 *   red → amber → green-mixed → green-clear → unavailable
 *
 * where green-mixed = overall GREEN but at least one model dissents (worth a
 * glance), and green-clear = every model GREEN (boring; rendered as a pill).
 *
 * These helpers are pure (no DOM) so the ordering logic is unit-tested; the
 * rendering lives in advisories-ui.
 */
import type { RouteAdvisoryResult } from '../types/advisories';

export type AdvisoryBand =
  | 'red' | 'amber' | 'green-mixed' | 'green-clear' | 'unavailable';

const BAND_ORDER: AdvisoryBand[] =
  ['red', 'amber', 'green-mixed', 'green-clear', 'unavailable'];

/** Within-band display priority (operational importance), applied after the
 *  band sort so every band reads in the same predictable order: feasibility →
 *  airport → cloud → convective/fronts → icing → turbulence/shear → performance
 *  → meta/confidence. Advisories not listed here sort last, keeping their
 *  backend (registry) order. When a new advisory ships, add it here — otherwise
 *  it lands at the bottom of its band. */
export const ADVISORY_PRIORITY: string[] = [
  'ifr_feasibility',
  'vfr_feasibility',
  'flight_category',     // Airport Weather
  'airport_wind',
  'cloud_top',
  'vmc_cruise',
  'enroute_precip',      // En-route Precipitation (visibility)
  'convective',
  'fronts',
  'fiki_icing',
  'icing_escape',
  'freezing_precip',
  'turbulence',
  'mountain_wind',
  'llws',                // Low-Level Wind Shear
  'density_altitude',
  'headwind',            // Winds Aloft / trip impact
  'sun',
  'model_agreement',     // Forecast Confidence
  'dd_nwp_agreement',
];

const PRIORITY_INDEX: Map<string, number> =
  new Map(ADVISORY_PRIORITY.map((id, i) => [id, i]));

/** Position in ADVISORY_PRIORITY; unlisted advisories sort last (same value, so
 *  Array.sort stability preserves their registry order among themselves). */
function priorityIndex(adv: RouteAdvisoryResult): number {
  return PRIORITY_INDEX.get(adv.advisory_id) ?? ADVISORY_PRIORITY.length;
}

/** Classify an advisory into its display band. A green aggregate splits by
 *  whether every model agrees: 'green-clear' (unanimous) vs 'green-mixed'
 *  (overall green but ≥1 model is not green). */
export function advisoryBand(adv: RouteAdvisoryResult): AdvisoryBand {
  switch (adv.aggregate_status) {
    case 'red': return 'red';
    case 'amber': return 'amber';
    case 'unavailable': return 'unavailable';
    default:
      return adv.per_model.some(m => m.status !== 'green')
        ? 'green-mixed' : 'green-clear';
  }
}

/** True for bands rendered as compact pills (no per-model detail) rather than
 *  full cards: unanimous green and unavailable. */
export function isCompactBand(band: AdvisoryBand): boolean {
  return band === 'green-clear' || band === 'unavailable';
}

/** Comparator for Array.sort: band order first, then within-band display
 *  priority (ADVISORY_PRIORITY). Equal priority returns 0 so sort stability
 *  preserves the backend (registry) order. */
export function compareAdvisories(
  a: RouteAdvisoryResult, b: RouteAdvisoryResult,
): number {
  const ba = advisoryBand(a);
  const bb = advisoryBand(b);
  if (ba !== bb) return BAND_ORDER.indexOf(ba) - BAND_ORDER.indexOf(bb);
  return priorityIndex(a) - priorityIndex(b);
}
