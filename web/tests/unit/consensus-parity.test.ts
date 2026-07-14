/** Shared-vector parity test for the TypeScript arrival-card consensus.
 *
 * The forecast map's own consensus was retired from the client in #419 (it now
 * reads the server-baked `consensus` / `consensus_majority` blocks straight off
 * the payload — the server is pinned to these same vectors by
 * `tests/test_consensus_parity.py`). What still recomputes client-side is the
 * briefing arrival card's `computeSummaryCondition`, so its parity with the
 * Python `airport_consensus.consensus` is what this file now guards. Both are
 * driven off `tests/fixtures/consensus_vectors.json`.
 */

import { describe, it, expect } from 'vitest';
import { type ConsensusMode } from '../../ts/visualization/weather-map-consensus';
import { computeSummaryCondition } from '../../ts/helpers/airport-summary';
import type { AirportModelCondition, FlightCategory } from '../../ts/types/advisories';
import vectors from '../../../tests/fixtures/consensus_vectors.json';

interface VectorFields {
  flight_category: string;
  ceiling_ft: number;
  visibility_m: number;
  wind_speed_kt: number;
  crosswind_kt: number;
  headwind_kt: number;
}

function makeCondition(model: string, f: VectorFields): AirportModelCondition {
  return {
    model,
    flight_category: f.flight_category as FlightCategory,
    ceiling_ft: f.ceiling_ft,
    visibility_m: f.visibility_m,
    visibility_sm: null,
    wind_speed_kt: f.wind_speed_kt,
    wind_direction_deg: null,
    wind_gust_kt: null,
    best_runway: null,
    all_runways: [],
  };
}

describe('computeSummaryCondition (arrival card) — shared-vector parity with Python', () => {
  for (const mode of ['worst', 'majority'] as ConsensusMode[]) {
    for (const c of vectors.cases) {
      it(`${mode}: ${c.name}`, () => {
        const conds = Object.entries(c.models).map(([name, f]) =>
          makeCondition(name, f as VectorFields));
        const summary = computeSummaryCondition(conds, mode);
        const expected = (c.expected as Record<string, VectorFields>)[mode];
        expect(summary).not.toBeNull();
        expect(summary!.flight_category).toBe(expected.flight_category);
        expect(summary!.ceiling_ft).toBeCloseTo(expected.ceiling_ft, 4);
        expect(summary!.visibility_m).toBeCloseTo(expected.visibility_m, 4);
        expect(summary!.wind_speed_kt).toBeCloseTo(expected.wind_speed_kt, 4);
      });
    }
  }
});
