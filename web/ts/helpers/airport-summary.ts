/** Pure per-airport consensus for the departure/arrival cards.
 *
 * Collapses the per-model `AirportModelCondition` list into one summary
 * condition under the user's aggregation mode. Extracted from `advisories-ui`
 * so the math is independently testable and mirrors the forecast-map consensus
 * (`weather-map-consensus.computeConsensus`) and the Python
 * `analysis.airport_consensus.consensus`.
 *
 * Numeric fields follow the category so they can never contradict the badge:
 *  - **worst**:    worst value across ALL models (min ceiling/vis, max wind).
 *  - **majority**: modal category (ties → worst), then the MEDIAN ceiling/vis/
 *                  wind within that winning-category pool — a robust "typical"
 *                  value guaranteed to sit inside the shown category.
 */

import type { AirportModelCondition, FlightCategory } from '../types/advisories';
// Single source of truth for flight-category severity — shared with the forecast
// map consensus so a future category reorder can't drift between the two.
import { median, CAT_ORDER } from '../visualization/weather-map-consensus';

export function hasAirportConditionEvidence(
  condition: AirportModelCondition,
): boolean {
  return condition.ceiling_evaluated === true
    || condition.ceiling_ft != null
    || condition.visibility_sm != null
    || condition.visibility_m != null;
}

export function computeSummaryCondition(
  conditions: AirportModelCondition[],
  aggregation: 'worst' | 'majority',
): AirportModelCondition | null {
  const evidenceConditions = conditions.filter(hasAirportConditionEvidence);
  if (evidenceConditions.length === 0) return null;

  let winningCat: FlightCategory;
  let pool: AirportModelCondition[];

  if (aggregation === 'majority') {
    // Count votes per category
    const counts = new Map<FlightCategory, number>();
    for (const c of evidenceConditions) {
      counts.set(c.flight_category, (counts.get(c.flight_category) ?? 0) + 1);
    }
    // Pick category with most votes; ties broken by worst severity
    let bestCount = 0;
    let bestSeverity = -1;
    winningCat = evidenceConditions[0].flight_category;
    for (const [cat, count] of counts) {
      const sev = CAT_ORDER[cat];
      if (count > bestCount || (count === bestCount && sev > bestSeverity)) {
        bestCount = count;
        bestSeverity = sev;
        winningCat = cat;
      }
    }
    pool = evidenceConditions.filter(c => c.flight_category === winningCat);
  } else {
    // Worst: pick the worst category across all
    winningCat = evidenceConditions[0].flight_category;
    for (const c of evidenceConditions) {
      if (CAT_ORDER[c.flight_category] > CAT_ORDER[winningCat]) {
        winningCat = c.flight_category;
      }
    }
    pool = conditions;
  }

  const allRwysCombined = pool.flatMap(c => c.all_runways);

  const summary: AirportModelCondition = {
    model: 'Summary',
    flight_category: winningCat,
    ceiling_ft: null,
    ceiling_evaluated: pool.every(c =>
      c.ceiling_ft != null || c.ceiling_evaluated === true),
    visibility_m: null,
    visibility_sm: null,
    wind_speed_kt: null,
    wind_direction_deg: null,
    wind_gust_kt: null,
    best_runway: null,
    all_runways: allRwysCombined,
  };

  const pick = (fn: (c: AirportModelCondition) => number | null | undefined): number[] =>
    pool.map(fn).filter((v): v is number => v != null);

  if (aggregation === 'majority') {
    // Median within the winning pool for each scalar. Wind dir/gust/runway must
    // stay a coherent vector, so they come from the pool model whose wind speed
    // is the median (not an independent median of each component).
    // Round to 1 decimal like the forecast map and Python consensus, so all
    // three surfaces are bit-for-bit identical on the shared parity vectors
    // (an even-pool median of whole values can be X.5).
    const ceils = pick(c => c.ceiling_ft);
    const visSm = pick(c => c.visibility_sm);
    const visM = pick(c => c.visibility_m);
    if (ceils.length) summary.ceiling_ft = Math.round(median(ceils) * 10) / 10;
    if (visSm.length) summary.visibility_sm = Math.round(median(visSm) * 10) / 10;
    if (visM.length) summary.visibility_m = Math.round(median(visM) * 10) / 10;

    // Wind speed is the median of the pool (matching every other numeric). The
    // direction/gust/runway can't be independently reduced without desyncing
    // the vector, so they come from the "representative" pool model — the one
    // whose wind speed is closest to that median.
    const windModels = pool.filter(c => c.wind_speed_kt != null);
    if (windModels.length) {
      const medSpeed = median(windModels.map(c => c.wind_speed_kt as number));
      summary.wind_speed_kt = Math.round(medSpeed * 10) / 10;
      const rep = windModels.reduce((best, c) =>
        Math.abs((c.wind_speed_kt as number) - medSpeed)
          < Math.abs((best.wind_speed_kt as number) - medSpeed) ? c : best);
      summary.wind_direction_deg = rep.wind_direction_deg;
      summary.wind_gust_kt = rep.wind_gust_kt;
      summary.best_runway = rep.best_runway;
    }
  } else {
    // Worst across all: lowest ceiling/vis, highest wind (dir/gust/runway from
    // the strongest-wind model so the vector stays coherent).
    for (const c of pool) {
      if (c.ceiling_ft !== null) {
        summary.ceiling_ft = summary.ceiling_ft === null
          ? c.ceiling_ft : Math.min(summary.ceiling_ft, c.ceiling_ft);
      }
      if (c.visibility_sm !== null) {
        summary.visibility_sm = summary.visibility_sm === null
          ? c.visibility_sm : Math.min(summary.visibility_sm, c.visibility_sm);
      }
      if (c.visibility_m != null) {
        summary.visibility_m = summary.visibility_m == null
          ? c.visibility_m : Math.min(summary.visibility_m, c.visibility_m);
      }
      if (c.wind_speed_kt !== null
          && (summary.wind_speed_kt === null || c.wind_speed_kt > summary.wind_speed_kt)) {
        summary.wind_speed_kt = c.wind_speed_kt;
        summary.wind_direction_deg = c.wind_direction_deg;
        summary.wind_gust_kt = c.wind_gust_kt;
        summary.best_runway = c.best_runway;
      }
    }
  }

  return summary;
}
