/** Pure consensus aggregation for the forecast overview map.
 *
 * Combines per-model forecast values into a single "worst" or "majority"
 * value used by consensus mode on the forecast map. Extracted from
 * weather-map.ts so the math is independently testable.
 *
 * Modes:
 *  - "worst":    most-adverse value (max wind, min ceiling, max risk).
 *  - "majority": modal/median value, with worst-case tiebreak for
 *                ordinal categories.
 */

import type { ForecastAirport, ConsensusForecast } from '../adapters/maps-adapter';

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

const _max = (v: number[]) => Math.max(...v);
const _min = (v: number[]) => Math.min(...v);

/** Per-numeric-field worst/majority aggregators. */
export const NUMERIC_CONSENSUS: Record<
  'wind_speed_kt' | 'crosswind_kt' | 'headwind_kt' | 'ceiling_ft' | 'cape_jkg' | 'visibility_m' | 'cloud_cover_pct',
  { worst: (v: number[]) => number; majority: (v: number[]) => number }
> = {
  wind_speed_kt:   { worst: _max, majority: median },
  crosswind_kt:    { worst: _max, majority: median },
  // Headwind is favourable, so the "worst" case is the WEAKEST headwind /
  // strongest tailwind (min) — not the strongest headwind. Mirrors Python
  // airport_consensus._WORST_IS_MIN (a positive headwind helps).
  headwind_kt:     { worst: _min, majority: median },
  ceiling_ft:      { worst: _min, majority: median },
  cape_jkg:        { worst: _max, majority: median },
  visibility_m:    { worst: _min, majority: median },
  cloud_cover_pct: { worst: _max, majority: median },
};

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

/** Compute a single consensus forecast across all an airport's models.
 *
 * Numeric fields follow the category so they can never contradict the badge:
 *  - worst mode:    worst value across ALL models (min ceiling/vis, max wind…).
 *  - majority mode: MEDIAN within the winning-category pool (the models that
 *                   voted for the shown category). Mirrors the Python
 *                   `airport_consensus.consensus` exactly.
 * Wind direction is a circular mean of the same pool (never a min/max/median).
 */
export function computeConsensus(airport: ForecastAirport, mode: ConsensusMode): ConsensusForecast {
  const models = Object.values(airport.models);
  if (models.length === 0) return { flight_category: 'VFR', agreement: {} };

  const cats = models.map((m) => m.flight_category);
  const risks = models.map((m) => m.convective_risk || 'none');

  const flightCategory = ordinalConsensus(cats, CAT_ORDER, mode);
  const result: ConsensusForecast = {
    flight_category: flightCategory,
    convective_risk: ordinalConsensus(risks, RISK_ORDER, mode),
    agreement: airport.consensus.agreement,
  };

  // In majority mode restrict numeric reductions to the winning-category pool;
  // in worst mode the pool is all models (the reduction is worst-across-all).
  const pool = mode === 'majority'
    ? models.filter((m) => m.flight_category === flightCategory)
    : models;

  for (const [field, fns] of Object.entries(NUMERIC_CONSENSUS) as Array<
    [keyof typeof NUMERIC_CONSENSUS, (typeof NUMERIC_CONSENSUS)[keyof typeof NUMERIC_CONSENSUS]]
  >) {
    const vals = pool.map((m) => m[field]).filter((v): v is number => v != null);
    if (!vals.length) continue;
    const fn = mode === 'worst' ? fns.worst : fns.majority; // majority => median
    (result as unknown as Record<string, unknown>)[field] = Math.round(fn(vals) * 10) / 10;
  }

  const dirs = pool.map((m) => m.wind_dir_deg).filter((v): v is number => v != null);
  // Wind direction is reported in whole degrees by METAR/TAF — sub-degree
  // precision is false, and serializing the raw float yields strings like
  // "359.9999999999996". Round to the same convention as the source data.
  if (dirs.length) result.wind_dir_deg = Math.round(circularMean(dirs));

  return result;
}
