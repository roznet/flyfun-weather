/** Pure computation: given points + metrics + altitude, return per-segment {color, weight}. */

import type { VizPoint } from '../types';
import type { MapMetric } from './metrics';

export interface SegmentStyle {
  color: string;
  weight: number;
}

/**
 * Compute the visual style for each segment between consecutive route points.
 * Returns an array of length `points.length - 1`.
 */
export function computeSegmentStyles(
  points: readonly VizPoint[],
  colorMetric: MapMetric | null,
  widthMetric: MapMetric | null,
  altitudeFt: number,
): SegmentStyle[] {
  const styles: SegmentStyle[] = [];

  for (let i = 0; i < points.length - 1; i++) {
    // Use the midpoint (average of two endpoints) for metric evaluation
    const p1 = points[i];
    const p2 = points[i + 1];

    let color = '#2563eb'; // default blue
    let weight = 15;

    if (colorMetric) {
      const v1 = colorMetric.getValue(p1, altitudeFt);
      const v2 = colorMetric.getValue(p2, altitudeFt);
      // Use average of endpoints if both available, otherwise whichever is available
      const v = v1 !== null && v2 !== null ? (v1 + v2) / 2
        : v1 ?? v2;
      if (v !== null) {
        color = colorMetric.getColor(v);
      }
    }

    if (widthMetric) {
      const v1 = widthMetric.getValue(p1, altitudeFt);
      const v2 = widthMetric.getValue(p2, altitudeFt);
      const v = v1 !== null && v2 !== null ? (v1 + v2) / 2
        : v1 ?? v2;
      if (v !== null) {
        weight = widthMetric.getWidth(v);
      }
    }

    styles.push({ color, weight });
  }

  return styles;
}
