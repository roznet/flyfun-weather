/** Cloud layer bands: gray fills with coverage-based opacity. */

import type { CrossSectionLayer, CoordTransform, VizRouteData, RenderMode, VizPoint } from '../../types';
import { coverageOpacity } from '../../scales';
import { drawSmoothBand, drawColumnBand, type BandPointData } from './base';

export const cloudBandsLayer: CrossSectionLayer = {
  id: 'cloud-bands',
  name: 'Cloud Layers',
  group: 'clouds',
  defaultEnabled: true,

  render(ctx: CanvasRenderingContext2D, transform: CoordTransform, data: VizRouteData, mode: RenderMode) {
    // Collect the maximum number of cloud layers across all points
    const maxLayers = data.points.reduce((max, p) => Math.max(max, p.cloudLayers.length), 0);
    if (maxLayers === 0) return;

    if (mode === 'columns') {
      // Column mode: draw each point's cloud layers as rectangles
      for (const point of data.points) {
        for (const cl of point.cloudLayers) {
          const opacity = coverageOpacity(cl.coverage);
          const fill = `rgba(136, 136, 136, ${opacity})`;
          const bandPoints: BandPointData[] = [{ distance: point.distanceNm, base: cl.baseFt, top: cl.topFt }];
          drawColumnBand(ctx, bandPoints, transform, fill);
        }
      }
      return;
    }

    // Smooth mode: match cloud layers between adjacent points by altitude overlap
    // Strategy: iterate pairs of adjacent points, match layers, draw bands
    for (let i = 0; i < data.points.length - 1; i++) {
      const curr = data.points[i];
      const next = data.points[i + 1];
      drawMatchedCloudSegment(ctx, transform, curr, next);
    }

    // Draw first/last point leftover layers as tapered bands
    if (data.points.length === 1) {
      const p = data.points[0];
      for (const cl of p.cloudLayers) {
        const opacity = coverageOpacity(cl.coverage);
        const fill = `rgba(136, 136, 136, ${opacity})`;
        const bandPoints: BandPointData[] = [{ distance: p.distanceNm, base: cl.baseFt, top: cl.topFt }];
        drawColumnBand(ctx, bandPoints, transform, fill);
      }
    }
  },
};

function drawMatchedCloudSegment(
  ctx: CanvasRenderingContext2D,
  transform: CoordTransform,
  curr: VizPoint,
  next: VizPoint,
): void {
  const usedNext = new Set<number>();

  for (const cl of curr.cloudLayers) {
    const opacity = coverageOpacity(cl.coverage);
    // Find best matching cloud layer in next point by altitude overlap
    let bestIdx = -1;
    let bestOverlap = 0;

    for (let j = 0; j < next.cloudLayers.length; j++) {
      if (usedNext.has(j)) continue;
      const nl = next.cloudLayers[j];
      const overlapBase = Math.max(cl.baseFt, nl.baseFt);
      const overlapTop = Math.min(cl.topFt, nl.topFt);
      const overlap = overlapTop - overlapBase;
      if (overlap > bestOverlap) {
        bestOverlap = overlap;
        bestIdx = j;
      }
    }

    if (bestIdx >= 0) {
      usedNext.add(bestIdx);
      const nl = next.cloudLayers[bestIdx];
      const avgOpacity = (opacity + coverageOpacity(nl.coverage)) / 2;
      const fill = `rgba(136, 136, 136, ${avgOpacity})`;
      const bandPoints: BandPointData[] = [
        { distance: curr.distanceNm, base: cl.baseFt, top: cl.topFt },
        { distance: next.distanceNm, base: nl.baseFt, top: nl.topFt },
      ];
      drawSmoothBand(ctx, bandPoints, transform, fill);
    } else {
      // No match — draw taper to midpoint
      const midDist = (curr.distanceNm + next.distanceNm) / 2;
      const midAlt = (cl.baseFt + cl.topFt) / 2;
      const fill = `rgba(136, 136, 136, ${opacity})`;
      const bandPoints: BandPointData[] = [
        { distance: curr.distanceNm, base: cl.baseFt, top: cl.topFt },
        { distance: midDist, base: midAlt, top: midAlt },
      ];
      drawSmoothBand(ctx, bandPoints, transform, fill);
    }
  }

  // Unmatched layers in next point — draw taper from midpoint
  for (let j = 0; j < next.cloudLayers.length; j++) {
    if (usedNext.has(j)) continue;
    const nl = next.cloudLayers[j];
    const midDist = (curr.distanceNm + next.distanceNm) / 2;
    const midAlt = (nl.baseFt + nl.topFt) / 2;
    const opacity = coverageOpacity(nl.coverage);
    const fill = `rgba(136, 136, 136, ${opacity})`;
    const bandPoints: BandPointData[] = [
      { distance: midDist, base: midAlt, top: midAlt },
      { distance: next.distanceNm, base: nl.baseFt, top: nl.topFt },
    ];
    drawSmoothBand(ctx, bandPoints, transform, fill);
  }
}
