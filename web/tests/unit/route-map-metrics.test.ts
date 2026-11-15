/** Tests for route-map metric registry — altitude-dependent value extraction.
 *
 * Tested through the public MapMetric API (no internal helper imports), so
 * that refactors of the internal `worstRiskAtAlt` / `cloudAtAlt` /
 * `tempAtAltitude` helpers don't break these tests as long as the
 * user-visible behavior is preserved.
 */

import { describe, it, expect } from 'vitest';
import {
  getMapMetricById, MAP_METRICS, ROUTE_MAP_CATALOG, DEFAULT_MAP_METRIC_ID,
  ROUTE_WEIGHT_MIN, ROUTE_WEIGHT_MAX, resolveMapMetric, mapMetricGroups,
  validateRouteMapCatalog, widthFraction,
} from '../../ts/visualization/route-map/metrics';
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

  it('color thresholds map SFIP to the icing-risk colors', () => {
    // Boundaries track the backend's sfip_to_risk() (15/30/55), and the colors
    // are the icing-risk metric's, so the two speak one vocabulary.
    const icing = getMetric('icing-risk-at-level');
    expect(m().getColor(10)).toBe(icing.getColor(0));
    expect(m().getColor(20)).toBe(icing.getColor(1));
    expect(m().getColor(35)).toBe(icing.getColor(2));
    expect(m().getColor(70)).toBe(icing.getColor(3));
    expect(m().getColor(90)).toBe(icing.getColor(3));
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
    expect(wLow).toBeCloseTo(ROUTE_WEIGHT_MAX, 1);
    expect(wHigh).toBeCloseTo(ROUTE_WEIGHT_MIN, 1);
  });

  it('legend shows one distinct colour per category (#735)', () => {
    const colors = m().legendStops!.map((s) => s.color);
    expect(colors).toHaveLength(4);
    expect(new Set(colors).size).toBe(colors.length);
  });

  it('every colour the map can draw appears in the legend (#735)', () => {
    const legend = new Set(m().legendStops!.map((s) => s.color));
    for (let ft = 0; ft <= 10000; ft += 50) {
      expect(legend).toContain(m().getColor(ft));
    }
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
  const icing = () => getMetric('icing-risk-at-level');

  it.each([
    [0, 0], [14.9, 0], [15, 1], [29.9, 1], [30, 2], [54.9, 2], [55, 3], [100, 3],
  ])('SFIP %s draws icing tier %s', (score, tier) => {
    expect(getMetric('sfip-at-level').getColor(score as number)).toBe(icing().getColor(tier as number));
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

describe('served catalog (#998)', () => {
  it('validates clean', () => {
    expect(validateRouteMapCatalog(ROUTE_MAP_CATALOG)).toEqual([]);
  });

  it('every catalog metric is drawable on the web (no unknown extractor)', () => {
    expect(MAP_METRICS.map((m) => m.id)).toEqual(ROUTE_MAP_CATALOG.metrics.map((m) => m.key));
  });

  it('defaults to cloud cover, not a phone-side flight category', () => {
    expect(DEFAULT_MAP_METRIC_ID).toBe('cloud-cover-total');
    expect(getMapMetricById('flight-category')).toBeUndefined();
  });

  it('has no Width metric', () => {
    expect(MAP_METRICS.some((m) => /width/i.test(m.id))).toBe(false);
  });

  it('an unknown stored id falls back to the default', () => {
    expect(resolveMapMetric('flight-category').id).toBe(DEFAULT_MAP_METRIC_ID);
    expect(resolveMapMetric(null).id).toBe(DEFAULT_MAP_METRIC_ID);
    expect(resolveMapMetric('cape').id).toBe('cape');
  });

  it('groups: At altitude holds exactly the altitude metrics', () => {
    const groups = mapMetricGroups();
    expect(groups.map((g) => g.key)).toEqual(['at_altitude', 'along_route']);
    for (const m of groups[0].metrics) expect(m.altitudeDependent).toBe(true);
    for (const m of groups[1].metrics) expect(m.altitudeDependent).toBe(false);
  });

  it('flags a malformed catalog', () => {
    const bad = { ...ROUTE_MAP_CATALOG, default_metric: 'nope',
      metrics: [{ ...ROUTE_MAP_CATALOG.metrics[0], scale: 'missing', value: 'nothing' }] };
    const problems = validateRouteMapCatalog(bad);
    expect(problems.join('|')).toMatch(/unknown scale/);
    expect(problems.join('|')).toMatch(/unknown value/);
    expect(problems.join('|')).toMatch(/default/);
  });

  it('widthFraction clamps and handles inverted specs', () => {
    expect(widthFraction({ thin_at: 0, thick_at: 10 }, 5)).toBe(0.5);
    expect(widthFraction({ thin_at: 0, thick_at: 10 }, 20)).toBe(1);
    expect(widthFraction({ thin_at: 10, thick_at: 0 }, 0)).toBe(1);
    expect(widthFraction({ thin_at: 5, thick_at: 5 }, 5)).toBe(0);
  });

  it('wind speed reads the point speed; headwind colours white at calm', () => {
    expect(getMetric('wind-speed').getValue(makeVizPoint({ windSpeedKt: 25 }))).toBe(25);
    expect(getMetric('headwind').getColor(0).toLowerCase()).toMatch(/^(#ffffff|rgb\(255, 255, 255\))$/);
  });
});
