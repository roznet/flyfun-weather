/** Shared consensus reduction helpers for the forecast overview map.
 *
 * The forecast map's worst/majority consensus is now baked server-side and
 * read straight off the payload (`airport.consensus` / `consensus_majority`),
 * so the client no longer recomputes it — see `weather-map-format.getConsensus`
 * and issue #419. What remains here are the small pure reducers that other
 * surfaces still share: the mode type-guard, the ordinal reducer used by the
 * client-side FAA/EASA alternate-required aggregation
 * (`weather-map-format.aggAltRequired`), and the `median`/`CAT_ORDER` helpers
 * the briefing arrival card reuses (`helpers/airport-summary`).
 */

export type ConsensusMode = 'worst' | 'majority';

export const CAT_ORDER: Record<string, number> = { VFR: 0, MVFR: 1, IFR: 2, LIFR: 3 };
export const RISK_ORDER: readonly string[] = ['none', 'marginal', 'low', 'moderate', 'high', 'extreme'];

export function isConsensusMode(model: string): model is ConsensusMode {
  return model === 'worst' || model === 'majority';
}

// --- Numeric helpers ---

export function median(vals: number[]): number {
  if (vals.length === 0) throw new Error('median: empty input');
  const sorted = [...vals].sort((a, b) => a - b);
  const mid = sorted.length >> 1;
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

/** Vector mean of compass bearings (degrees). Handles wrap-around at 0/360. */
export function circularMean(degs: number[]): number {
  const sinSum = degs.reduce((s, d) => s + Math.sin(d * Math.PI / 180), 0);
  const cosSum = degs.reduce((s, d) => s + Math.cos(d * Math.PI / 180), 0);
  return ((Math.atan2(sinSum, cosSum) * 180 / Math.PI) + 360) % 360;
}

/** Ordinal aggregation for categorical fields (flight_category, convective_risk).
 *
 * - worst: highest-ranked value.
 * - majority: modal value; ties broken by picking the worst among tied candidates.
 */
export function ordinalConsensus(
  values: string[],
  order: Record<string, number> | readonly string[],
  mode: ConsensusMode,
): string {
  if (values.length === 0) throw new Error('ordinalConsensus: empty values');
  const rank = Array.isArray(order)
    ? (v: string) => order.indexOf(v)
    : (v: string) => (order as Record<string, number>)[v] ?? 0;
  if (mode === 'worst') {
    return values.reduce((a, b) => rank(a) >= rank(b) ? a : b);
  }
  // majority: modal with worse tiebreak
  const counts = new Map<string, number>();
  for (const v of values) counts.set(v, (counts.get(v) ?? 0) + 1);
  const maxCount = Math.max(...counts.values());
  const tied = [...counts.entries()].filter(([, n]) => n === maxCount).map(([v]) => v);
  return tied.reduce((a, b) => rank(a) >= rank(b) ? a : b);
}
