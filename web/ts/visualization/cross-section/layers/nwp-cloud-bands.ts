/** NWP cloud layers: blue-tinted fills from model cloud parameterization.
 *
 * Renders server-computed NWP cloud layers (from GRIB diagnostics or
 * synthesized from Open-Meteo cloud % narrowed by DD envelope + inversions).
 * No client-side heuristic narrowing — boundaries come from the backend.
 */

import type {
  CrossSectionLayer,
  CoordTransform,
  VizRouteData,
  VizPoint,
  VizCloudLayer,
} from '../../types';
import { drawSmoothBand, drawBandHatch, drawColumnBand, type BandPointData } from './base';
import { getActiveTheme } from '../theme';

/** Map coverage category to a representative percentage for fill opacity. */
function coverageToPct(coverage: string): number {
  switch (coverage) {
    case 'OVC': return 90;
    case 'BKN': return 65;
    case 'SCT': return 35;
    default: return 35;
  }
}

/** Blue-tinted fill from coverage percentage — distinct from DD cloud layers. */
function nwpCloudFill(pct: number): string {
  const theme = getActiveTheme().nwpClouds;
  const t = Math.min(1, Math.max(0, pct / 100));
  const [br, bg, bb] = theme.brightRgb;
  const [dr, dg, db] = theme.deltaRgb;
  const r = Math.round(br - dr * t);
  const g = Math.round(bg - dg * t);
  const b = Math.round(bb - db * t);
  const [opFloor, opScale] = theme.opacityRange;
  const opacity = Math.min(opFloor + opScale + 0.001, opFloor + opScale * t);
  return `rgba(${r}, ${g}, ${b}, ${opacity.toFixed(2)})`;
}

export const nwpCloudBandsLayer: CrossSectionLayer = {
  id: 'nwp-cloud-bands',
  name: 'NWP Layers',
  group: 'clouds',
  defaultEnabled: true,
  metricId: 'nwp_cloud_cover',

  render(ctx: CanvasRenderingContext2D, transform: CoordTransform, data: VizRouteData) {
    const hasData = data.points.some((p) => p.nwpCloudLayers.length > 0);
    if (!hasData) return;

    const theme = getActiveTheme();
    const hatch = theme.clouds;

    // Match NWP cloud layers between adjacent points (same approach as DD bands)
    for (let i = 0; i < data.points.length - 1; i++) {
      drawMatchedNwpSegment(ctx, transform, data.points[i], data.points[i + 1], hatch);
    }

    // Single point fallback
    if (data.points.length === 1) {
      const p = data.points[0];
      for (const cl of p.nwpCloudLayers) {
        const pct = coverageToPct(cl.coverage);
        const fill = nwpCloudFill(pct);
        const bandPoints: BandPointData[] = [{ distance: p.distanceNm, base: cl.baseFt, top: cl.topFt }];
        drawColumnBand(ctx, bandPoints, transform, fill);
      }
    }
  },
};

interface CloudHatchConfig {
  hatchGridPx: number;
  hatchLineWidth: Record<string, number>;
  hatchColor: string;
}

function drawMatchedNwpSegment(
  ctx: CanvasRenderingContext2D,
  transform: CoordTransform,
  curr: VizPoint,
  next: VizPoint,
  hatch: CloudHatchConfig,
): void {
  const usedNext = new Set<number>();

  for (const cl of curr.nwpCloudLayers) {
    let bestIdx = -1;
    let bestOverlap = 0;

    for (let j = 0; j < next.nwpCloudLayers.length; j++) {
      if (usedNext.has(j)) continue;
      const nl = next.nwpCloudLayers[j];
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
      const nl = next.nwpCloudLayers[bestIdx];
      const avgPct = (coverageToPct(cl.coverage) + coverageToPct(nl.coverage)) / 2;
      const fill = nwpCloudFill(avgPct);
      const bandPoints: BandPointData[] = [
        { distance: curr.distanceNm, base: cl.baseFt, top: cl.topFt },
        { distance: next.distanceNm, base: nl.baseFt, top: nl.topFt },
      ];
      drawSmoothBand(ctx, bandPoints, transform, fill);
      hatchBand(ctx, bandPoints, transform, cl.coverage, hatch);
    } else {
      // Cloud layer disappears between points — taper to midpoint
      const midDist = (curr.distanceNm + next.distanceNm) / 2;
      const midAlt = (cl.baseFt + cl.topFt) / 2;
      const pct = coverageToPct(cl.coverage);
      const fill = nwpCloudFill(pct);
      const bandPoints: BandPointData[] = [
        { distance: curr.distanceNm, base: cl.baseFt, top: cl.topFt },
        { distance: midDist, base: midAlt, top: midAlt },
      ];
      drawSmoothBand(ctx, bandPoints, transform, fill);
      hatchBand(ctx, bandPoints, transform, cl.coverage, hatch);
    }
  }

  // Unmatched next-point layers (appear between points)
  for (let j = 0; j < next.nwpCloudLayers.length; j++) {
    if (usedNext.has(j)) continue;
    const nl = next.nwpCloudLayers[j];
    const midDist = (curr.distanceNm + next.distanceNm) / 2;
    const midAlt = (nl.baseFt + nl.topFt) / 2;
    const pct = coverageToPct(nl.coverage);
    const fill = nwpCloudFill(pct);
    const bandPoints: BandPointData[] = [
      { distance: midDist, base: midAlt, top: midAlt },
      { distance: next.distanceNm, base: nl.baseFt, top: nl.topFt },
    ];
    drawSmoothBand(ctx, bandPoints, transform, fill);
    hatchBand(ctx, bandPoints, transform, nl.coverage, hatch);
  }
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
