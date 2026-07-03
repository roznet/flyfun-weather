/** Shared-vector parity test for the TypeScript forecast-map consensus.
 *
 * Pins `weather-map-consensus.computeConsensus` to the same expected output as
 * the Python `airport_consensus.consensus` (see `tests/test_consensus_parity.py`),
 * both driven off `tests/fixtures/consensus_vectors.json`. If the two
 * implementations drift, one of the two parity tests fails.
 */

import { describe, it, expect } from 'vitest';
import { computeConsensus, type ConsensusMode } from '../../ts/visualization/weather-map-consensus';
import { computeSummaryCondition } from '../../ts/helpers/airport-summary';
import type { ForecastAirport, ModelForecast } from '../../ts/adapters/maps-adapter';
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

function makeModel(f: VectorFields): ModelForecast {
  return {
    ceiling_ft: f.ceiling_ft,
    visibility_m: f.visibility_m,
    wind_speed_kt: f.wind_speed_kt,
    wind_dir_deg: null,
    wind_gust_kt: null,
    crosswind_kt: f.crosswind_kt,
    headwind_kt: f.headwind_kt,
    best_runway_id: null,
    gust_crosswind_kt: null,
    gust_headwind_kt: null,
    cloud_cover_pct: null,
    cape_jkg: null,
    convective_risk: 'none',
    temperature_c: null,
    flight_category: f.flight_category,
  };
}

describe('computeConsensus — shared-vector parity with Python', () => {
  for (const mode of ['worst', 'majority'] as ConsensusMode[]) {
    for (const c of vectors.cases) {
      it(`${mode}: ${c.name}`, () => {
        const models: Record<string, ModelForecast> = {};
        for (const [name, fields] of Object.entries(c.models)) {
          models[name] = makeModel(fields as VectorFields);
        }
        const airport: ForecastAirport = {
          icao: 'TEST', lat: 0, lon: 0, models,
          consensus: { flight_category: 'VFR', agreement: {} },
        };
        const result = computeConsensus(airport, mode);
        const expected = (c.expected as Record<string, VectorFields>)[mode];
        expect(result.flight_category).toBe(expected.flight_category);
        expect(result.ceiling_ft).toBeCloseTo(expected.ceiling_ft, 4);
        expect(result.visibility_m).toBeCloseTo(expected.visibility_m, 4);
        expect(result.wind_speed_kt).toBeCloseTo(expected.wind_speed_kt, 4);
        expect(result.crosswind_kt).toBeCloseTo(expected.crosswind_kt, 4);
        expect(result.headwind_kt).toBeCloseTo(expected.headwind_kt, 4);
      });
    }
  }
});

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
