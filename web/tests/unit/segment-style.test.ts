/** Tests for route-map segment style computation. */

import { describe, it, expect } from 'vitest';
import { computeSegmentStyles } from '../../ts/visualization/route-map/segment-style';
import type { MapMetric } from '../../ts/visualization/route-map/metrics';
import { makeVizPoint } from './fixtures/viz-point';

/** Build a stub MapMetric where getValue/getColor/getWidth are caller-controlled. */
function stubMetric(
  getValue: (idx: number) => number | null,
  getColor: (v: number) => string = (v) => `c-${v}`,
  getWidth: (v: number) => number = (v) => v,
): MapMetric {
  let callIdx = 0;
  return {
    id: 'stub',
    label: 'stub',
    unit: '',
    altitudeDependent: false,
    getValue: () => getValue(callIdx++),
    getColor,
    getWidth,
    formatValue: (v) => String(v),
    legendStops: [],
  };
}

describe('computeSegmentStyles', () => {
  it('returns N-1 segments for N points', () => {
    const points = [makeVizPoint({ distanceNm: 0 }), makeVizPoint({ distanceNm: 10 }), makeVizPoint({ distanceNm: 20 })];
    const styles = computeSegmentStyles(points, null, null, 5000);
    expect(styles).toHaveLength(2);
  });

  it('returns empty array for single point', () => {
    const styles = computeSegmentStyles([makeVizPoint()], null, null, 5000);
    expect(styles).toHaveLength(0);
  });

  it('uses default color/weight when no metrics provided', () => {
    const points = [makeVizPoint(), makeVizPoint()];
    const styles = computeSegmentStyles(points, null, null, 5000);
    expect(styles[0]).toEqual({ color: '#2563eb', weight: 15 });
  });

  it('averages endpoint values when both available', () => {
    const points = [makeVizPoint(), makeVizPoint()];
    let captured: number | null = null;
    const colorMetric = stubMetric(
      (i) => [10, 20][i] ?? null,
      (v) => { captured = v; return '#abc'; },
    );
    const styles = computeSegmentStyles(points, colorMetric, null, 0);
    expect(captured).toBe(15);
    expect(styles[0].color).toBe('#abc');
  });

  it('uses available endpoint when other is null', () => {
    const points = [makeVizPoint(), makeVizPoint()];
    let captured: number | null = null;
    const colorMetric = stubMetric(
      (i) => [null, 42][i] ?? null,
      (v) => { captured = v; return '#fff'; },
    );
    computeSegmentStyles(points, colorMetric, null, 0);
    expect(captured).toBe(42);
  });

  it('falls back to default color when both endpoints null', () => {
    const points = [makeVizPoint(), makeVizPoint()];
    const colorMetric = stubMetric(() => null);
    const styles = computeSegmentStyles(points, colorMetric, null, 0);
    expect(styles[0].color).toBe('#2563eb');
  });

  it('color and width metrics evaluated independently', () => {
    const points = [makeVizPoint(), makeVizPoint()];
    const colorMetric = stubMetric(
      (i) => [5, 15][i] ?? null,
      (v) => `color-${v}`,
    );
    const widthMetric = stubMetric(
      (i) => [100, 200][i] ?? null,
      undefined,
      (v) => v / 10,
    );
    const styles = computeSegmentStyles(points, colorMetric, widthMetric, 0);
    expect(styles[0]).toEqual({ color: 'color-10', weight: 15 }); // (100+200)/2/10
  });
});
