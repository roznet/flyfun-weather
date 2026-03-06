/** Cloud layer bands: gray-gradient fills from dewpoint depression (sounding-derived). */

import type { CrossSectionLayer, CoordTransform, VizRouteData, VizPoint, VizCloudLayer } from '../../types';
import { cloudFillFromDD } from '../../scales';
import { drawSmoothBand, drawBandHatch, drawColumnBand, type BandPointData } from './base';
import { getActiveTheme } from '../theme';

export const cloudBandsLayer: CrossSectionLayer = {
  id: 'cloud-bands',
  name: 'DD Layers',
  group: 'clouds',
  defaultEnabled: true,
  metricId: 'cloud_coverage',

  render(ctx: CanvasRenderingContext2D, transform: CoordTransform, data: VizRouteData) {
    // Collect the maximum number of cloud layers across all points
    const maxLayers = data.points.reduce((max, p) => Math.max(max, p.cloudLayers.length), 0);
    if (maxLayers === 0) return;

    const theme = getActiveTheme();
    const hatch = theme.clouds;

    // Match cloud layers between adjacent points by altitude overlap
    for (let i = 0; i < data.points.length - 1; i++) {
      const curr = data.points[i];
      const next = data.points[i + 1];
      drawMatchedCloudSegment(ctx, transform, curr, next, hatch);
    }

    if (data.points.length === 1) {
      const p = data.points[0];
      for (const cl of p.cloudLayers) {
        const fill = cloudFillFromDD(cl.meanDewpointDepressionC, cl.coverage);
        const bandPoints: BandPointData[] = [{ distance: p.distanceNm, base: cl.baseFt, top: cl.topFt }];
        drawColumnBand(ctx, bandPoints, transform, fill);
      }
    }
  },
};

/** Average DD of two cloud layers; falls back to undefined if both missing. */
function avgDD(a: VizCloudLayer, b: VizCloudLayer): number | undefined {
  if (a.meanDewpointDepressionC !== undefined && b.meanDewpointDepressionC !== undefined) {
    return (a.meanDewpointDepressionC + b.meanDewpointDepressionC) / 2;
  }
  return a.meanDewpointDepressionC ?? b.meanDewpointDepressionC;
}

interface CloudHatchConfig {
  hatchGridPx: number;
  hatchLineWidth: Record<string, number>;
  hatchColor: string;
}

function hatchBand(
  ctx: CanvasRenderingContext2D,
  bandPoints: BandPointData[],
  transform: CoordTransform,
  coverage: string,
  hatch: CloudHatchConfig,
): void {
  const lw = hatch.hatchLineWidth[coverage] ?? hatch.hatchGridPx;
  drawBandHatch(ctx, bandPoints, transform, hatch.hatchGridPx, lw, hatch.hatchColor);
}

function drawMatchedCloudSegment(
  ctx: CanvasRenderingContext2D,
  transform: CoordTransform,
  curr: VizPoint,
  next: VizPoint,
  hatch: CloudHatchConfig,
): void {
  const usedNext = new Set<number>();

  for (const cl of curr.cloudLayers) {
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
      const fill = cloudFillFromDD(avgDD(cl, nl), cl.coverage);
      const bandPoints: BandPointData[] = [
        { distance: curr.distanceNm, base: cl.baseFt, top: cl.topFt },
        { distance: next.distanceNm, base: nl.baseFt, top: nl.topFt },
      ];
      drawSmoothBand(ctx, bandPoints, transform, fill);
      hatchBand(ctx, bandPoints, transform, cl.coverage, hatch);
    } else {
      const midDist = (curr.distanceNm + next.distanceNm) / 2;
      const midAlt = (cl.baseFt + cl.topFt) / 2;
      const fill = cloudFillFromDD(cl.meanDewpointDepressionC, cl.coverage);
      const bandPoints: BandPointData[] = [
        { distance: curr.distanceNm, base: cl.baseFt, top: cl.topFt },
        { distance: midDist, base: midAlt, top: midAlt },
      ];
      drawSmoothBand(ctx, bandPoints, transform, fill);
      hatchBand(ctx, bandPoints, transform, cl.coverage, hatch);
    }
  }

  for (let j = 0; j < next.cloudLayers.length; j++) {
    if (usedNext.has(j)) continue;
    const nl = next.cloudLayers[j];
    const midDist = (curr.distanceNm + next.distanceNm) / 2;
    const midAlt = (nl.baseFt + nl.topFt) / 2;
    const fill = cloudFillFromDD(nl.meanDewpointDepressionC, nl.coverage);
    const bandPoints: BandPointData[] = [
      { distance: midDist, base: midAlt, top: midAlt },
      { distance: next.distanceNm, base: nl.baseFt, top: nl.topFt },
    ];
    drawSmoothBand(ctx, bandPoints, transform, fill);
    hatchBand(ctx, bandPoints, transform, nl.coverage, hatch);
  }
}
