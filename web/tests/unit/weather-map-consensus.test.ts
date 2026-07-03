/** Tests for forecast-map consensus aggregation (worst / majority modes). */

import { describe, it, expect } from 'vitest';
import {
  isConsensusMode, median, circularMean, ordinalConsensus, computeConsensus,
  CAT_ORDER, RISK_ORDER,
} from '../../ts/visualization/weather-map-consensus';
import type {
  ForecastAirport, ModelForecast,
} from '../../ts/adapters/maps-adapter';

function makeModelForecast(overrides: Partial<ModelForecast> = {}): ModelForecast {
  return {
    ceiling_ft: null,
    visibility_m: null,
    wind_speed_kt: null,
    wind_dir_deg: null,
    wind_gust_kt: null,
    crosswind_kt: null,
    headwind_kt: null,
    best_runway_id: null,
    gust_crosswind_kt: null,
    gust_headwind_kt: null,
    cloud_cover_pct: null,
    cape_jkg: null,
    convective_risk: 'none',
    temperature_c: null,
    flight_category: 'VFR',
    ...overrides,
  };
}

function makeAirport(models: Record<string, Partial<ModelForecast>>): ForecastAirport {
  const built: Record<string, ModelForecast> = {};
  for (const [k, v] of Object.entries(models)) built[k] = makeModelForecast(v);
  return {
    icao: 'EGLF',
    lat: 51.276,
    lon: -0.776,
    models: built,
    consensus: { flight_category: 'VFR', agreement: {} },
  };
}

describe('isConsensusMode', () => {
  it('detects "worst" and "majority"', () => {
    expect(isConsensusMode('worst')).toBe(true);
    expect(isConsensusMode('majority')).toBe(true);
  });

  it('rejects model names', () => {
    expect(isConsensusMode('gfs')).toBe(false);
    expect(isConsensusMode('ecmwf')).toBe(false);
    expect(isConsensusMode('')).toBe(false);
  });
});

describe('median', () => {
  it('handles odd and even counts', () => {
    expect(median([1, 2, 3])).toBe(2);
    expect(median([1, 2, 3, 4])).toBe(2.5);
  });

  it('does not mutate input', () => {
    const xs = [3, 1, 2];
    median(xs);
    expect(xs).toEqual([3, 1, 2]);
  });

  it('throws on empty input rather than returning NaN', () => {
    expect(() => median([])).toThrow(/empty/);
  });
});

describe('circularMean', () => {
  it('returns the angle for a single value', () => {
    expect(circularMean([90])).toBeCloseTo(90, 5);
  });

  it('handles wrap-around at 0/360', () => {
    // Mean of 350 and 10 should be ~0 (or 360), not 180
    const m = circularMean([350, 10]);
    expect(Math.min(m, 360 - m)).toBeLessThan(1);
  });

  it('returns the linear mean for nearby angles', () => {
    expect(circularMean([80, 100])).toBeCloseTo(90, 1);
  });

  it('returns a value in [0, 360)', () => {
    const m = circularMean([350, 10, 20]);
    expect(m).toBeGreaterThanOrEqual(0);
    expect(m).toBeLessThan(360);
  });
});

describe('ordinalConsensus', () => {
  describe('with CAT_ORDER (object)', () => {
    it('worst returns highest-ranked', () => {
      expect(ordinalConsensus(['VFR', 'MVFR', 'IFR'], CAT_ORDER, 'worst')).toBe('IFR');
      expect(ordinalConsensus(['VFR', 'LIFR', 'MVFR'], CAT_ORDER, 'worst')).toBe('LIFR');
    });

    it('majority returns the modal value', () => {
      expect(ordinalConsensus(['VFR', 'VFR', 'IFR'], CAT_ORDER, 'majority')).toBe('VFR');
    });

    it('majority breaks ties by picking the worse value', () => {
      // 1 each — picks worst among tied (LIFR)
      expect(ordinalConsensus(['VFR', 'IFR', 'LIFR'], CAT_ORDER, 'majority')).toBe('LIFR');
      // 2 vs 1, but since two distinct candidates have count 1, modal is the 2 — let's check actual
      expect(ordinalConsensus(['VFR', 'VFR', 'IFR'], CAT_ORDER, 'majority')).toBe('VFR');
    });
  });

  describe('with RISK_ORDER (array)', () => {
    it('worst picks highest-ranked', () => {
      expect(ordinalConsensus(['none', 'low', 'high'], RISK_ORDER, 'worst')).toBe('high');
      expect(ordinalConsensus(['marginal', 'extreme', 'low'], RISK_ORDER, 'worst')).toBe('extreme');
    });

    it('majority picks the modal risk', () => {
      expect(ordinalConsensus(['low', 'low', 'high'], RISK_ORDER, 'majority')).toBe('low');
    });
  });

  it('worst returns the only value when all equal', () => {
    expect(ordinalConsensus(['VFR', 'VFR'], CAT_ORDER, 'worst')).toBe('VFR');
  });

  it('throws on empty input rather than reduce-of-empty TypeError', () => {
    expect(() => ordinalConsensus([], CAT_ORDER, 'worst')).toThrow(/empty/);
    expect(() => ordinalConsensus([], CAT_ORDER, 'majority')).toThrow(/empty/);
  });
});

describe('computeConsensus', () => {
  it('returns VFR / empty agreement when no models', () => {
    const airport = makeAirport({});
    const c = computeConsensus(airport, 'worst');
    expect(c.flight_category).toBe('VFR');
    expect(c.agreement).toEqual({});
  });

  it('worst flight_category picks highest-ranked', () => {
    const a = makeAirport({
      gfs: { flight_category: 'VFR' },
      icon: { flight_category: 'IFR' },
      ecmwf: { flight_category: 'MVFR' },
    });
    const c = computeConsensus(a, 'worst');
    expect(c.flight_category).toBe('IFR');
  });

  it('worst takes max wind speed and min ceiling (most adverse)', () => {
    const a = makeAirport({
      gfs:   { wind_speed_kt: 10, ceiling_ft: 4000 },
      icon:  { wind_speed_kt: 25, ceiling_ft: 1500 },
      ecmwf: { wind_speed_kt: 18, ceiling_ft: 800 },
    });
    const c = computeConsensus(a, 'worst');
    expect(c.wind_speed_kt).toBe(25);
    expect(c.ceiling_ft).toBe(800);
  });

  it('majority uses median of numeric values', () => {
    const a = makeAirport({
      gfs:   { wind_speed_kt: 10 },
      icon:  { wind_speed_kt: 20 },
      ecmwf: { wind_speed_kt: 30 },
    });
    const c = computeConsensus(a, 'majority');
    expect(c.wind_speed_kt).toBe(20);
  });

  it('majority takes the median of ONLY the winning-category pool', () => {
    // 2 VFR + 1 IFR → category VFR; numbers come only from the two VFR models,
    // so the IFR model's low ceiling / high wind never leak into the badge.
    const a = makeAirport({
      gfs:   { flight_category: 'VFR', wind_speed_kt: 10, ceiling_ft: 5000 },
      icon:  { flight_category: 'VFR', wind_speed_kt: 14, ceiling_ft: 4000 },
      ecmwf: { flight_category: 'IFR', wind_speed_kt: 30, ceiling_ft: 800 },
    });
    const c = computeConsensus(a, 'majority');
    expect(c.flight_category).toBe('VFR');
    expect(c.ceiling_ft).toBe(4500);   // median(5000, 4000)
    expect(c.wind_speed_kt).toBe(12);  // median(10, 14)
  });

  it('skips numeric fields when no model has data', () => {
    const a = makeAirport({
      gfs:  { wind_speed_kt: 10 },
      icon: { wind_speed_kt: 20 },
    });
    const c = computeConsensus(a, 'worst');
    expect(c.wind_speed_kt).toBe(20);
    expect(c.ceiling_ft).toBeUndefined();
    expect(c.cape_jkg).toBeUndefined();
  });

  it('handles partial nulls (drops them, aggregates remainder)', () => {
    const a = makeAirport({
      gfs:   { wind_speed_kt: 10 },
      icon:  { wind_speed_kt: null },
      ecmwf: { wind_speed_kt: 20 },
    });
    const c = computeConsensus(a, 'worst');
    expect(c.wind_speed_kt).toBe(20);
  });

  it('rounds aggregated numerics to 1 decimal', () => {
    const a = makeAirport({
      gfs:   { wind_speed_kt: 10.123 },
      icon:  { wind_speed_kt: 20.456 },
      ecmwf: { wind_speed_kt: 30.789 },
    });
    const c = computeConsensus(a, 'majority');
    expect(c.wind_speed_kt).toBe(20.5);
  });

  it('treats blank convective_risk as "none"', () => {
    const a = makeAirport({
      gfs: { convective_risk: '' as never },
      icon: { convective_risk: 'low' },
    });
    const c = computeConsensus(a, 'worst');
    expect(c.convective_risk).toBe('low');
  });

  it('aggregates wind direction with circular mean, rounded to whole degrees', () => {
    const a = makeAirport({
      gfs:  { wind_dir_deg: 350 },
      icon: { wind_dir_deg: 10 },
    });
    const c = computeConsensus(a, 'worst');
    expect(c.wind_dir_deg).toBeDefined();
    // Whole-degree result, no float artifacts like 359.9999999996
    expect(Number.isInteger(c.wind_dir_deg!)).toBe(true);
    // Mean of 350+10 should be 0 (or 360 — circular wrap)
    expect(c.wind_dir_deg === 0 || c.wind_dir_deg === 360).toBe(true);
  });

  it('preserves the airport.consensus.agreement map', () => {
    const a = makeAirport({ gfs: { flight_category: 'VFR' } });
    a.consensus.agreement = { wind_speed_kt: 'consistent' };
    const c = computeConsensus(a, 'worst');
    expect(c.agreement).toEqual({ wind_speed_kt: 'consistent' });
  });
});
