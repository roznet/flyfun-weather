/** Tests for the route-graph metric registry. */

import { describe, it, expect } from 'vitest';
import {
  ROUTE_GRAPH_METRICS, getMetricById, getMetricOptions, METRIC_NONE,
} from '../../ts/visualization/route-graph/metrics';
import { isaTemperatureC } from '../../ts/visualization/data-extract';
import { makeVizPoint, makeAltitudeLines, makeNwpCloudDiag } from './fixtures/viz-point';

function metric(id: string) {
  const m = getMetricById(id);
  if (!m) throw new Error(`metric ${id} not found`);
  return m;
}

describe('getMetricById', () => {
  it('returns the matching metric', () => {
    expect(getMetricById('headwind')?.id).toBe('headwind');
    expect(getMetricById('cape')?.id).toBe('cape');
  });

  it('returns undefined for METRIC_NONE', () => {
    expect(getMetricById(METRIC_NONE)).toBeUndefined();
  });

  it('returns undefined for unknown id', () => {
    expect(getMetricById('does-not-exist')).toBeUndefined();
  });
});

describe('getMetricOptions', () => {
  it('omits "none" by default', () => {
    const opts = getMetricOptions(false);
    expect(opts.find((o) => o.id === METRIC_NONE)).toBeUndefined();
    expect(opts.length).toBe(ROUTE_GRAPH_METRICS.length);
  });

  it('prepends "none" when requested', () => {
    const opts = getMetricOptions(true);
    expect(opts[0].id).toBe(METRIC_NONE);
    expect(opts.length).toBe(ROUTE_GRAPH_METRICS.length + 1);
  });
});

describe('headwind metric', () => {
  it('reads headwindKt directly', () => {
    expect(metric('headwind').getValue(makeVizPoint({ headwindKt: 12 }))).toBe(12);
  });

  it('formats positive as HW and negative as TW', () => {
    const m = metric('headwind');
    expect(m.formatValue!(15)).toBe('15 kt HW');
    expect(m.formatValue!(-15)).toBe('15 kt TW');
    expect(m.formatValue!(0)).toBe('0 kt HW');
  });
});

describe('ceiling-dd metric (sounding ceiling AGL)', () => {
  const m = () => metric('ceiling-dd');

  it('returns null when soundingCeilingFt is null', () => {
    const p = makeVizPoint({ soundingCeilingFt: null, terrainElevationFt: 1000 });
    expect(m().getValue(p)).toBeNull();
  });

  it('subtracts terrain elevation to compute AGL', () => {
    const p = makeVizPoint({ soundingCeilingFt: 4000, terrainElevationFt: 1000 });
    expect(m().getValue(p)).toBe(3000);
  });

  it('clamps negative AGL to 0', () => {
    // Terrain higher than reported ceiling — shouldn't return negative.
    const p = makeVizPoint({ soundingCeilingFt: 800, terrainElevationFt: 1000 });
    expect(m().getValue(p)).toBe(0);
  });

  it('returns null when AGL exceeds 5000 ft cap', () => {
    // Ceiling well above the route → not interesting for plotting.
    const p = makeVizPoint({ soundingCeilingFt: 8000, terrainElevationFt: 1000 });
    expect(m().getValue(p)).toBeNull();
  });

  it('keeps values exactly at 5000 ft AGL', () => {
    const p = makeVizPoint({ soundingCeilingFt: 6000, terrainElevationFt: 1000 });
    expect(m().getValue(p)).toBe(5000);
  });
});

describe('ceiling-nwp metric', () => {
  const m = () => metric('ceiling-nwp');

  it('returns null when nwpCloudDiag is null', () => {
    const p = makeVizPoint({ nwpCloudDiag: null });
    expect(m().getValue(p)).toBeNull();
  });

  it('returns null when ceilingFt is null inside diag', () => {
    const p = makeVizPoint({ nwpCloudDiag: makeNwpCloudDiag({ ceilingFt: null }) });
    expect(m().getValue(p)).toBeNull();
  });

  it('subtracts terrain elevation', () => {
    const p = makeVizPoint({
      terrainElevationFt: 500,
      nwpCloudDiag: makeNwpCloudDiag({ ceilingFt: 3500 }),
    });
    expect(m().getValue(p)).toBe(3000);
  });

  it('returns null above 5000 ft AGL cap', () => {
    const p = makeVizPoint({
      terrainElevationFt: 500,
      nwpCloudDiag: makeNwpCloudDiag({ ceilingFt: 9500 }),
    });
    expect(m().getValue(p)).toBeNull();
  });
});

describe('freezing-level metric', () => {
  const m = () => metric('freezing-level');

  it('reads from altitudeLines.freezingLevelFt', () => {
    const p = makeVizPoint({ altitudeLines: makeAltitudeLines({ freezingLevelFt: 6500 }) });
    expect(m().getValue(p)).toBe(6500);
  });

  it('returns null when missing', () => {
    expect(m().getValue(makeVizPoint())).toBeNull();
  });

  it('formats with thousands separator and ft suffix', () => {
    expect(m().formatValue!(6500)).toMatch(/6,500\s*ft/);
  });
});

describe('precipitation/cape/cloud-cover/temperature metrics', () => {
  it('precipitation reads precipitationMm', () => {
    expect(metric('precipitation').getValue(makeVizPoint({ precipitationMm: 1.2 }))).toBe(1.2);
  });

  it('cape reads capeSurfaceJkg', () => {
    expect(metric('cape').getValue(makeVizPoint({ capeSurfaceJkg: 800 }))).toBe(800);
  });

  it('cloud-cover reads cloudCoverTotalPct', () => {
    expect(metric('cloud-cover').getValue(makeVizPoint({ cloudCoverTotalPct: 75 }))).toBe(75);
  });

  it('temperature reads temperatureC, allows null', () => {
    expect(metric('temperature').getValue(makeVizPoint({ temperatureC: -3 }))).toBe(-3);
    expect(metric('temperature').getValue(makeVizPoint({ temperatureC: null }))).toBeNull();
  });
});

describe('isaTemperatureC (standard atmosphere)', () => {
  it('is 15°C at MSL', () => {
    expect(isaTemperatureC(0)).toBe(15);
  });

  it('follows the troposphere lapse rate', () => {
    expect(isaTemperatureC(8000)).toBeCloseTo(15 - 1.9812 * 8, 6);
  });

  it('is isothermal above the 36,089 ft tropopause', () => {
    expect(isaTemperatureC(36089)).toBeCloseTo(-56.5, 2);
    expect(isaTemperatureC(40000)).toBe(-56.5);
  });

  it('matches the Python twin at cruise (cross-impl guard)', () => {
    // round-1.9812*alt — same constants as models/analysis.isa_temperature_c
    expect(isaTemperatureC(10000)).toBeCloseTo(-4.812, 3);
  });
});

describe('isa-dev metric', () => {
  const m = () => metric('isa-dev');

  it('reads isaDevC directly', () => {
    expect(m().getValue(makeVizPoint({ isaDevC: 8 }))).toBe(8);
    expect(m().getValue(makeVizPoint({ isaDevC: null }))).toBeNull();
  });

  it('formats positive deviation as ISA+N', () => {
    expect(m().formatValue!(8.2)).toBe('ISA+8 (+8.2°C)');
  });

  it('formats negative deviation as ISA−N', () => {
    expect(m().formatValue!(-4.6)).toBe('ISA−5 (−4.6°C)');
  });

  it('never renders ISA−0 for small negative deviations', () => {
    // Rounds to zero in magnitude → shows ±0, not −0.
    expect(m().formatValue!(-0.3)).toBe('ISA±0 (−0.3°C)');
    expect(m().formatValue!(0.3)).toBe('ISA±0 (+0.3°C)');
    expect(m().formatValue!(-0.04)).toBe('ISA±0 (±0.0°C)');
  });
});
