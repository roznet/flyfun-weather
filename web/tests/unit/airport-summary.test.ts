/** Tests for the departure/arrival card consensus (computeSummaryCondition). */

import { describe, it, expect } from 'vitest';
import { computeSummaryCondition } from '../../ts/helpers/airport-summary';
import type { AirportModelCondition, FlightCategory } from '../../ts/types/advisories';

function cond(overrides: Partial<AirportModelCondition> = {}): AirportModelCondition {
  return {
    model: 'gfs',
    flight_category: 'VFR' as FlightCategory,
    ceiling_ft: null,
    ceiling_evaluated: false,
    visibility_m: null,
    visibility_sm: null,
    wind_speed_kt: null,
    wind_direction_deg: null,
    wind_gust_kt: null,
    best_runway: null,
    all_runways: [],
    ...overrides,
  };
}

describe('computeSummaryCondition', () => {
  it('returns null for an empty model list', () => {
    expect(computeSummaryCondition([], 'majority')).toBeNull();
  });

  it('excludes missing-derived VFR votes from the category summary', () => {
    const summary = computeSummaryCondition([
      cond({ model: 'gfs' }),
      cond({ model: 'icon' }),
      cond({
        model: 'ecmwf',
        flight_category: 'IFR',
        ceiling_ft: 800,
        visibility_sm: 2,
      }),
    ], 'majority');

    expect(summary?.flight_category).toBe('IFR');
  });

  it('returns null when every condition lacks ceiling and visibility evidence', () => {
    expect(computeSummaryCondition([
      cond({ model: 'gfs', wind_speed_kt: 10 }),
      cond({ model: 'ecmwf', wind_speed_kt: 20 }),
    ], 'worst')).toBeNull();
  });

  it('carries assessed-clear ceiling availability into the summary', () => {
    const summary = computeSummaryCondition([
      cond({ ceiling_evaluated: true, visibility_sm: 10 }),
    ], 'majority');

    expect(summary?.ceiling_ft).toBeNull();
    expect(summary?.ceiling_evaluated).toBe(true);
  });

  it('does not call a mixed assessed and unassessed null ceiling clear', () => {
    const summary = computeSummaryCondition([
      cond({ model: 'gfs', ceiling_evaluated: true, visibility_sm: 10 }),
      cond({ model: 'ecmwf', ceiling_evaluated: false, visibility_sm: 10 }),
    ], 'majority');

    expect(summary?.ceiling_ft).toBeNull();
    expect(summary?.ceiling_evaluated).toBe(false);
  });

  it('worst mode: worst category + lowest ceiling/vis + highest wind across all', () => {
    const summary = computeSummaryCondition([
      cond({ flight_category: 'VFR', ceiling_ft: 5000, visibility_sm: 10, wind_speed_kt: 10 }),
      cond({ flight_category: 'MVFR', ceiling_ft: 2000, visibility_sm: 4, wind_speed_kt: 22 }),
      cond({ flight_category: 'IFR', ceiling_ft: 800, visibility_sm: 2, wind_speed_kt: 15 }),
    ], 'worst');
    expect(summary!.flight_category).toBe('IFR');
    expect(summary!.ceiling_ft).toBe(800);
    expect(summary!.visibility_sm).toBe(2);
    expect(summary!.wind_speed_kt).toBe(22); // strongest wind
  });

  it('worst mode keeps wind-only rows out of category voting but in wind reduction', () => {
    const summary = computeSummaryCondition([
      cond({
        model: 'gfs',
        flight_category: 'IFR',
        ceiling_ft: 800,
        visibility_sm: 2,
        wind_speed_kt: 10,
        wind_direction_deg: 180,
      }),
      cond({
        model: 'ecmwf',
        flight_category: 'VFR',
        wind_speed_kt: 35,
        wind_direction_deg: 270,
      }),
    ], 'worst');

    expect(summary?.flight_category).toBe('IFR');
    expect(summary?.wind_speed_kt).toBe(35);
    expect(summary?.wind_direction_deg).toBe(270);
  });

  it('majority mode: median within the winning-category pool only', () => {
    // 2 VFR + 1 IFR → category VFR; ceiling/wind come only from the VFR models.
    const summary = computeSummaryCondition([
      cond({ flight_category: 'VFR', ceiling_ft: 5000, wind_speed_kt: 10 }),
      cond({ flight_category: 'VFR', ceiling_ft: 4000, wind_speed_kt: 14 }),
      cond({ flight_category: 'IFR', ceiling_ft: 800, wind_speed_kt: 30 }),
    ], 'majority');
    expect(summary!.flight_category).toBe('VFR');
    expect(summary!.ceiling_ft).toBe(4500);   // median(5000, 4000)
    expect(summary!.wind_speed_kt).toBe(12);   // median(10, 14) — IFR model excluded
  });

  it('majority mode: winning-category tie breaks to the worse category', () => {
    const summary = computeSummaryCondition([
      cond({ flight_category: 'VFR', ceiling_ft: 5000 }),
      cond({ flight_category: 'IFR', ceiling_ft: 900 }),
    ], 'majority');
    expect(summary!.flight_category).toBe('IFR');
    expect(summary!.ceiling_ft).toBe(900); // only the IFR model is in the pool
  });

  it('majority wind vector stays coherent (dir/gust/runway from the representative model)', () => {
    const summary = computeSummaryCondition([
      cond({ flight_category: 'VFR', visibility_sm: 10, wind_speed_kt: 8, wind_direction_deg: 100, wind_gust_kt: 12 }),
      cond({ flight_category: 'VFR', visibility_sm: 10, wind_speed_kt: 20, wind_direction_deg: 250, wind_gust_kt: 30 }),
      cond({ flight_category: 'VFR', visibility_sm: 10, wind_speed_kt: 14, wind_direction_deg: 180, wind_gust_kt: 20 }),
    ], 'majority');
    // median wind speed is 14 → that model supplies dir/gust together.
    expect(summary!.wind_speed_kt).toBe(14);
    expect(summary!.wind_direction_deg).toBe(180);
    expect(summary!.wind_gust_kt).toBe(20);
  });
});
