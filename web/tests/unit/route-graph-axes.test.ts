/** Tests for the route-graph Y-axis scale. */

import { describe, it, expect } from 'vitest';
import { computeYScale } from '../../ts/visualization/route-graph/axes';
import {
  getMetricById, type MetricSample, type RouteGraphMetric,
} from '../../ts/visualization/route-graph/metrics';
import type { PlotArea } from '../../ts/visualization/types';

const PLOT: PlotArea = { left: 60, top: 8, width: 800, height: 200 };

function metric(id: string): RouteGraphMetric {
  const m = getMetricById(id);
  if (!m) throw new Error(`metric ${id} not found`);
  return m;
}

const value = (v: number): MetricSample => ({ kind: 'value', value: v });
const aboveScale = (v: number): MetricSample => ({ kind: 'above-scale', value: v });
const unavailable: MetricSample = { kind: 'unavailable' };

describe('computeYScale — above-scale metrics', () => {
  const ceiling = () => metric('ceiling-dd');

  it('pins the axis to the declared range so the top tick is the cap', () => {
    const scale = computeYScale([value(1200), value(3000)], ceiling(), PLOT);
    expect(scale.min).toBe(0);
    expect(scale.max).toBe(5000);
  });

  it('holds the range when every point is above the cap', () => {
    // The bug: with no suggestedRange the filtered values were empty, the scale
    // fell to its 0–1 branch and drew a 0–1 ft AGL axis with no line on it —
    // on the best possible weather day.
    const scale = computeYScale([aboveScale(7000), aboveScale(9000)], ceiling(), PLOT);
    expect(scale.min).toBe(0);
    expect(scale.max).toBe(5000);
  });

  it('holds the range when there is no data at all', () => {
    const scale = computeYScale([unavailable, unavailable], ceiling(), PLOT);
    expect(scale.min).toBe(0);
    expect(scale.max).toBe(5000);
  });

  it('maps the cap to the top edge of the plot area', () => {
    const scale = computeYScale([value(2500)], ceiling(), PLOT);
    expect(scale.valueToY(5000)).toBeCloseTo(PLOT.top);
    expect(scale.valueToY(0)).toBeCloseTo(PLOT.top + PLOT.height);
    expect(scale.valueToY(2500)).toBeCloseTo(PLOT.top + PLOT.height / 2);
  });
});

describe('computeYScale — ordinary metrics', () => {
  it('still expands past a suggested range to fit the data', () => {
    // cloud-cover suggests [0, 100] but does not declare `aboveScale`, so an
    // over-range value must widen the axis rather than be treated as off-chart.
    const scale = computeYScale([value(50), value(140)], metric('cloud-cover'), PLOT);
    expect(scale.max).toBeGreaterThanOrEqual(140);
  });

  it('auto-fits when no range is suggested', () => {
    const scale = computeYScale([value(-10), value(20)], metric('temperature'), PLOT);
    expect(scale.min).toBeLessThanOrEqual(-10);
    expect(scale.max).toBeGreaterThanOrEqual(20);
  });

  it('ignores unavailable samples when auto-fitting', () => {
    const scale = computeYScale([unavailable, value(5), unavailable], metric('temperature'), PLOT);
    expect(scale.min).toBeLessThanOrEqual(5);
    expect(scale.max).toBeGreaterThanOrEqual(5);
  });
});
