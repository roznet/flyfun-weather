/** Tests for route-map metric registry — altitude-dependent value extraction.
 *
 * Tested through the public MapMetric API (no internal helper imports), so
 * that refactors of the internal `worstRiskAtAlt` / `cloudAtAlt` /
 * `tempAtAltitude` helpers don't break these tests as long as the
 * user-visible behavior is preserved.
 */

import { describe, it, expect } from 'vitest';
import { getMapMetricById, MAP_METRICS } from '../../ts/visualization/route-map/metrics';
import { riskMapColor } from '../../ts/visualization/scales';
import {
  makeVizPoint, makeAltitudeLines, makeIcingZone, makeCloudLayer,
  makeCatLayer, makeSfipZone,
} from './fixtures/viz-point';

function getMetric(id: string) {
  const m = getMapMetricById(id);
  if (!m) throw new Error(`map metric ${id} not found`);
  return m;
}

describe('MAP_METRICS registry', () => {
  it('all metrics have unique IDs', () => {
    const ids = MAP_METRICS.map((m) => m.id);
    expect(new Set(ids).size).toBe(ids.length);
  });

  it('altitudeDependent flag is set correctly', () => {
    expect(getMetric('icing-risk-at-level').altitudeDependent).toBe(true);
    expect(getMetric('cloud-at-level').altitudeDependent).toBe(true);
    expect(getMetric('temp-at-level').altitudeDependent).toBe(true);
    expect(getMetric('cape').altitudeDependent).toBe(false);
    expect(getMetric('cloud-cover-total').altitudeDependent).toBe(false);
  });
});

describe('icing-risk-at-level', () => {
  const m = () => getMetric('icing-risk-at-level');

  it('returns 0 when no zones intersect the altitude', () => {
    const point = makeVizPoint({
      icingZones: [makeIcingZone({ baseFt: 4000, topFt: 8000, risk: 'moderate' })],
    });
    expect(m().getValue(point, 10000)).toBe(0);
  });

  it('picks the worst risk among overlapping zones', () => {
    const point = makeVizPoint({
      icingZones: [
        makeIcingZone({ baseFt: 4000, topFt: 8000, risk: 'light' }),
        makeIcingZone({ baseFt: 5000, topFt: 7000, risk: 'severe' }),
      ],
    });
    // RISK_ORDER: severe = 3
    expect(m().getValue(point, 6000)).toBe(3);
  });

  it('inclusive at zone boundaries', () => {
    const point = makeVizPoint({
      icingZones: [makeIcingZone({ baseFt: 4000, topFt: 8000, risk: 'moderate' })],
    });
    expect(m().getValue(point, 4000)).toBe(2); // moderate=2
    expect(m().getValue(point, 8000)).toBe(2);
  });
});

describe('cat-risk-at-level', () => {
  const m = () => getMetric('cat-risk-at-level');

  it('reads from catLayers (not icingZones)', () => {
    const point = makeVizPoint({
      catLayers: [makeCatLayer({ baseFt: 18000, topFt: 22000, risk: 'severe' })],
      icingZones: [makeIcingZone({ baseFt: 18000, topFt: 22000, risk: 'none' })],
    });
    expect(m().getValue(point, 20000)).toBe(3); // severe
  });
});

describe('sfip-at-level', () => {
  const m = () => getMetric('sfip-at-level');

  it('returns null when no zones intersect', () => {
    const point = makeVizPoint({
      sfipZones: [makeSfipZone({ baseFt: 4000, topFt: 8000, meanSfip100: 60 })],
    });
    expect(m().getValue(point, 10000)).toBeNull();
  });

  it('picks the highest SFIP score among overlapping zones', () => {
    const point = makeVizPoint({
      sfipZones: [
        makeSfipZone({ baseFt: 4000, topFt: 8000, meanSfip100: 30 }),
        makeSfipZone({ baseFt: 5000, topFt: 7000, meanSfip100: 70 }),
      ],
    });
    expect(m().getValue(point, 6000)).toBe(70);
  });

  it('skips zones with null SFIP score', () => {
    const point = makeVizPoint({
      sfipZones: [
        makeSfipZone({ baseFt: 4000, topFt: 8000, meanSfip100: null }),
        makeSfipZone({ baseFt: 4000, topFt: 8000, meanSfip100: 25 }),
      ],
    });
    expect(m().getValue(point, 6000)).toBe(25);
  });

  it('color thresholds map SFIP to the shared icing-risk colors', () => {
    // Boundaries track the backend's sfip_to_risk() (15/30/55), and the colors
    // come from the shared risk scale rather than a private palette — so the
    // map speaks the same vocabulary as the icing-risk metric beside it.
    expect(m().getColor(10)).toBe(riskMapColor('none'));
    expect(m().getColor(20)).toBe(riskMapColor('light'));
    expect(m().getColor(35)).toBe(riskMapColor('moderate'));
    expect(m().getColor(70)).toBe(riskMapColor('severe'));
    expect(m().getColor(90)).toBe(riskMapColor('severe'));
  });
});

describe('cloud-at-level', () => {
  const m = () => getMetric('cloud-at-level');

  it('returns 0 when altitude is between layers', () => {
    const point = makeVizPoint({
      cloudLayers: [makeCloudLayer({ baseFt: 1000, topFt: 3000, coverage: 'bkn' })],
    });
    expect(m().getValue(point, 5000)).toBe(0);
  });

  it('coverage weights: sct=30, bkn=70, ovc=100', () => {
    const sct = makeVizPoint({
      cloudLayers: [makeCloudLayer({ baseFt: 0, topFt: 5000, coverage: 'sct' })],
    });
    expect(m().getValue(sct, 2500)).toBeCloseTo(30, 5);

    const bkn = makeVizPoint({
      cloudLayers: [makeCloudLayer({ baseFt: 0, topFt: 5000, coverage: 'bkn' })],
    });
    expect(m().getValue(bkn, 2500)).toBeCloseTo(70, 5);

    const ovc = makeVizPoint({
      cloudLayers: [makeCloudLayer({ baseFt: 0, topFt: 5000, coverage: 'ovc' })],
    });
    expect(m().getValue(ovc, 2500)).toBe(100);
  });

  it('picks the heaviest coverage among overlapping layers', () => {
    const point = makeVizPoint({
      cloudLayers: [
        makeCloudLayer({ baseFt: 0, topFt: 5000, coverage: 'sct' }),
        makeCloudLayer({ baseFt: 1000, topFt: 4000, coverage: 'ovc' }),
      ],
    });
    expect(m().getValue(point, 2000)).toBe(100); // ovc wins
  });
});

describe('temp-at-level', () => {
  const m = () => getMetric('temp-at-level');

  it('returns null when freezing level unknown', () => {
    expect(m().getValue(makeVizPoint(), 5000)).toBeNull();
  });

  it('returns 0 at the freezing level', () => {
    const p = makeVizPoint({ altitudeLines: makeAltitudeLines({ freezingLevelFt: 8000 }) });
    expect(m().getValue(p, 8000)).toBe(0);
  });

  it('interpolates linearly between 0°C and -10°C', () => {
    const p = makeVizPoint({
      altitudeLines: makeAltitudeLines({
        freezingLevelFt: 8000,
        minus10cLevelFt: 13000,
      }),
    });
    // Halfway between 8000 (0°C) and 13000 (-10°C) → -5°C
    expect(m().getValue(p, 10500)).toBeCloseTo(-5, 5);
  });

  it('extrapolates below freezing level using ~2°C/1000ft lapse', () => {
    const p = makeVizPoint({ altitudeLines: makeAltitudeLines({ freezingLevelFt: 8000 }) });
    // 1000 ft below freezing should be ~+2°C
    expect(m().getValue(p, 7000)).toBeCloseTo(2, 5);
  });

  it('extrapolates above the highest known reference', () => {
    const p = makeVizPoint({
      altitudeLines: makeAltitudeLines({
        freezingLevelFt: 8000,
        minus10cLevelFt: 13000,
      }),
    });
    // 1000 ft above -10°C reference should be ~-12°C
    expect(m().getValue(p, 14000)).toBeCloseTo(-12, 5);
  });
});

describe('nwp-ceiling', () => {
  const m = () => getMetric('nwp-ceiling');

  it('returns null when nwpCloudDiag missing', () => {
    expect(m().getValue(makeVizPoint())).toBeNull();
  });

  it('width is inverted: low ceiling → thick line', () => {
    const wLow = m().getWidth(0);
    const wHigh = m().getWidth(5000);
    expect(wLow).toBeGreaterThan(wHigh);
    expect(wLow).toBeCloseTo(25, 1);
    expect(wHigh).toBeCloseTo(3, 1);
  });
});

describe('headwind-only / tailwind-only', () => {
  it('headwind clamps negative to 0', () => {
    const m = getMetric('headwind');
    expect(m.getValue(makeVizPoint({ headwindKt: -10 }))).toBe(0);
    expect(m.getValue(makeVizPoint({ headwindKt: 15 }))).toBe(15);
  });

  it('tailwind reads positive when headwind is negative', () => {
    const m = getMetric('tailwind');
    expect(m.getValue(makeVizPoint({ headwindKt: -20 }))).toBe(20);
    expect(m.getValue(makeVizPoint({ headwindKt: 15 }))).toBe(0);
  });
});

describe('sfip-at-level honours the backend icing tiers', () => {
  // The map used to classify SFIP on a private 20/50/80 scale while the
  // backend's sfip_to_risk() used 15/30/55, so a score the backend graded as
  // LIGHT icing rendered as green "Low" on the map. These boundaries MUST
  // track _SFIP_NONE / _SFIP_LIGHT / _SFIP_MODERATE in analysis/sounding/sfip.py.
  const tierAt = (score: number) => {
    const m = getMetric('sfip-at-level');
    return m.formatValue!(score);
  };

  it.each([
    [0, 'none'],
    [14.9, 'none'],
    [15, 'light'],
    [29.9, 'light'],
    [30, 'moderate'],
    [54.9, 'moderate'],
    [55, 'severe'],
    [100, 'severe'],
  ])('SFIP %s is %s', (score, tier) => {
    expect(tierAt(score as number)).toContain(tier as string);
  });

  it('a score the backend calls LIGHT is not coloured as none', () => {
    const m = getMetric('sfip-at-level');
    expect(m.getColor(18)).not.toBe(m.getColor(0));
  });

  it('legend boundaries match the backend thresholds', () => {
    const m = getMetric('sfip-at-level');
    expect(m.legendStops!.map((s) => s.value)).toEqual([0, 15, 30, 55]);
  });
});
