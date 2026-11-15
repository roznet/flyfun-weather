/** Pure computation: given points + the route metric + altitude, return per-segment {color, weight}.
 *
 *  One metric drives both colour and width (#998: the separate Width metric is
 *  gone). A segment takes its value from its two endpoints per the metric's
 *  catalog `aggregation` (`max` for hazards, so one bad endpoint is never
 *  averaged away). A segment with no value draws the catalog's no-data grey at
 *  the thinnest width, so a gap never reads as a colour. */

import type { VizPoint } from '../types';
import { ROUTE_NO_DATA_COLOR, ROUTE_WEIGHT_MIN, type MapMetric } from './metrics';

export interface SegmentStyle {
  color: string;
  weight: number;
}

/** Returns an array of length `points.length - 1`. */
export function computeSegmentStyles(
  points: readonly VizPoint[],
  metric: MapMetric | null,
  altitudeFt: number,
): SegmentStyle[] {
  const styles: SegmentStyle[] = [];
  for (let i = 0; i < points.length - 1; i++) {
    const v = metric ? metric.segmentValue(points[i], points[i + 1], altitudeFt) : null;
    styles.push(metric && v !== null
      ? { color: metric.getColor(v), weight: metric.getWidth(v) }
      : { color: ROUTE_NO_DATA_COLOR, weight: ROUTE_WEIGHT_MIN });
  }
  return styles;
}
