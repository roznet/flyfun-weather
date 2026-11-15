/** Tests for route-map segment style computation (#998: one metric drives
 *  colour and width; a segment's value aggregates its endpoints per the
 *  catalog; no value draws grey at the thinnest width). */

import { describe, it, expect } from 'vitest';
import { computeSegmentStyles } from '../../ts/visualization/route-map/segment-style';
import {
  getMapMetricById, ROUTE_NO_DATA_COLOR, ROUTE_WEIGHT_MIN, ROUTE_WEIGHT_MAX,
} from '../../ts/visualization/route-map/metrics';
import { makeVizPoint, makeIcingZone } from './fixtures/viz-point';

function metric(id: string) {
  const m = getMapMetricById(id);
  if (!m) throw new Error(`no metric ${id}`);
  return m;
}

describe('computeSegmentStyles', () => {
  it('returns N-1 segments for N points', () => {
    const points = [makeVizPoint({ distanceNm: 0 }), makeVizPoint({ distanceNm: 10 }), makeVizPoint({ distanceNm: 20 })];
    expect(computeSegmentStyles(points, metric('cloud-cover-total'), 5000)).toHaveLength(2);
  });

  it('returns empty array for single point', () => {
    expect(computeSegmentStyles([makeVizPoint()], metric('cloud-cover-total'), 5000)).toHaveLength(0);
  });

  it('draws no-data grey at the thinnest width without a metric', () => {
    const styles = computeSegmentStyles([makeVizPoint(), makeVizPoint()], null, 5000);
    expect(styles[0]).toEqual({ color: ROUTE_NO_DATA_COLOR, weight: ROUTE_WEIGHT_MIN });
  });

  it('draws no-data grey when both endpoints have no value', () => {
    // nwp-ceiling reads nwpCloudDiag, absent on the default fixture.
    const styles = computeSegmentStyles([makeVizPoint(), makeVizPoint()], metric('nwp-ceiling'), 5000);
    expect(styles[0]).toEqual({ color: ROUTE_NO_DATA_COLOR, weight: ROUTE_WEIGHT_MIN });
  });

  it('averages endpoints for a non-hazard metric', () => {
    const m = metric('cloud-cover-total');
    const styles = computeSegmentStyles(
      [makeVizPoint({ cloudCoverTotalPct: 20 }), makeVizPoint({ cloudCoverTotalPct: 80 })], m, 0);
    expect(styles[0].color).toBe(m.getColor(50));
    expect(styles[0].weight).toBeCloseTo(m.getWidth(50), 5);
  });

  it('takes the worse endpoint for a hazard, so one bad point is never averaged away', () => {
    const m = metric('icing-risk-at-level');
    const clear = makeVizPoint();
    const severe = makeVizPoint({ icingZones: [makeIcingZone({ baseFt: 0, topFt: 10000, risk: 'severe' })] });
    const styles = computeSegmentStyles([clear, severe], m, 5000);
    expect(styles[0].color).toBe(m.getColor(3));
  });

  it('uses the available endpoint when the other has no value', () => {
    const m = metric('visibility');
    const styles = computeSegmentStyles(
      [makeVizPoint({ visibilityM: null }), makeVizPoint({ visibilityM: 2000 })], m, 0);
    expect(styles[0].color).toBe(m.getColor(2000));
  });

  it('keeps every weight within the route range', () => {
    const m = metric('wind-speed');
    for (const kt of [0, 10, 60, 120]) {
      const [s] = computeSegmentStyles([makeVizPoint({ windSpeedKt: kt }), makeVizPoint({ windSpeedKt: kt })], m, 0);
      expect(s.weight).toBeGreaterThanOrEqual(ROUTE_WEIGHT_MIN);
      expect(s.weight).toBeLessThanOrEqual(ROUTE_WEIGHT_MAX);
    }
  });
});
