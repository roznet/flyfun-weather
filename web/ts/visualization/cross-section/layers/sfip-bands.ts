/** SFIP icing zone bands: diagonal-hatch fills by risk level, visually distinct from Ogimet solid fills. */

import type { CrossSectionLayer, CoordTransform, VizRouteData, RenderMode } from '../../types';
import { drawSmoothBand, drawColumnBand, type BandPointData } from './base';

/** SFIP risk colors — higher opacity than Ogimet to stand out when toggled on. */
const SFIP_RISK_COLORS: Record<string, string> = {
  none: 'transparent',
  light: 'rgba(100, 149, 237, 0.50)',
  moderate: 'rgba(255, 165, 0, 0.55)',
  severe: 'rgba(220, 53, 69, 0.65)',
};

function sfipRiskColor(risk: string): string {
  return SFIP_RISK_COLORS[risk] ?? 'transparent';
}

/** Create a diagonal hatch CanvasPattern for a given color. */
function createHatchPattern(ctx: CanvasRenderingContext2D, color: string): CanvasPattern | null {
  const size = 8;
  const offscreen = document.createElement('canvas');
  offscreen.width = size;
  offscreen.height = size;
  const oc = offscreen.getContext('2d');
  if (!oc) return null;

  oc.clearRect(0, 0, size, size);
  oc.strokeStyle = color;
  oc.lineWidth = 2;
  // Diagonal stripes with wrap-around for seamless tiling
  oc.beginPath();
  oc.moveTo(0, size);
  oc.lineTo(size, 0);
  oc.moveTo(-size / 2, size / 2);
  oc.lineTo(size / 2, -size / 2);
  oc.moveTo(size / 2, size + size / 2);
  oc.lineTo(size + size / 2, size / 2);
  oc.stroke();

  return ctx.createPattern(offscreen, 'repeat');
}

const patternCache = new Map<string, CanvasPattern | null>();

function getHatchPattern(ctx: CanvasRenderingContext2D, risk: string): CanvasPattern | null {
  const color = sfipRiskColor(risk);
  if (color === 'transparent') return null;
  if (patternCache.has(color)) return patternCache.get(color)!;
  const pattern = createHatchPattern(ctx, color);
  patternCache.set(color, pattern);
  return pattern;
}

/** Draw a hatched column at a single point. */
function drawHatchedColumn(
  ctx: CanvasRenderingContext2D,
  transform: CoordTransform,
  distanceNm: number,
  baseFt: number,
  topFt: number,
  risk: string,
  allPoints: BandPointData[],
): void {
  // Light solid base
  const bandPoints: BandPointData[] = [{ distance: distanceNm, base: baseFt, top: topFt }];
  drawColumnBand(ctx, bandPoints, transform, sfipRiskColor(risk).replace(/[\d.]+\)$/, '0.18)'));
  // Hatch overlay
  const pattern = getHatchPattern(ctx, risk);
  if (pattern) {
    ctx.save();
    ctx.fillStyle = pattern;
    const idx = allPoints.length > 0 ? 0 : 0;
    // Use drawColumnBand to get correct column width
    drawColumnBand(ctx, bandPoints, transform, 'transparent');
    // Manual rect with pattern
    const x = transform.distanceToX(distanceNm);
    const yTop = transform.altitudeToY(topFt);
    const yBase = transform.altitudeToY(baseFt);
    ctx.fillRect(x - 25, yTop, 50, yBase - yTop);
    ctx.restore();
  }
}

export const sfipBandsLayer: CrossSectionLayer = {
  id: 'sfip-bands',
  name: 'SFIP Icing Index',
  group: 'icing',
  defaultEnabled: false,
  metricId: 'sfip_risk',

  render(ctx: CanvasRenderingContext2D, transform: CoordTransform, data: VizRouteData, mode: RenderMode) {
    if (mode === 'columns') {
      for (const point of data.points) {
        for (const sz of point.sfipZones) {
          if (sz.risk === 'none') continue;
          drawHatchedColumn(ctx, transform, point.distanceNm, sz.baseFt, sz.topFt, sz.risk, []);
        }
      }
      return;
    }

    // Smooth mode: draw hatched bands between adjacent points
    for (let i = 0; i < data.points.length - 1; i++) {
      const curr = data.points[i];
      const next = data.points[i + 1];
      const usedNext = new Set<number>();

      for (const sz of curr.sfipZones) {
        if (sz.risk === 'none') continue;

        // Find best overlap match in next point
        let bestIdx = -1;
        let bestOverlap = 0;
        for (let j = 0; j < next.sfipZones.length; j++) {
          if (usedNext.has(j) || next.sfipZones[j].risk === 'none') continue;
          const nz = next.sfipZones[j];
          const overlap = Math.min(sz.topFt, nz.topFt) - Math.max(sz.baseFt, nz.baseFt);
          if (overlap > bestOverlap) { bestOverlap = overlap; bestIdx = j; }
        }

        if (bestIdx >= 0) {
          usedNext.add(bestIdx);
          const nz = next.sfipZones[bestIdx];
          const riskOrder = ['none', 'light', 'moderate', 'severe'];
          const maxRisk = riskOrder.indexOf(sz.risk) >= riskOrder.indexOf(nz.risk) ? sz.risk : nz.risk;
          drawSmoothBand(ctx, [
            { distance: curr.distanceNm, base: sz.baseFt, top: sz.topFt },
            { distance: next.distanceNm, base: nz.baseFt, top: nz.topFt },
          ], transform, sfipRiskColor(maxRisk));
        } else {
          const midDist = (curr.distanceNm + next.distanceNm) / 2;
          const midAlt = (sz.baseFt + sz.topFt) / 2;
          drawSmoothBand(ctx, [
            { distance: curr.distanceNm, base: sz.baseFt, top: sz.topFt },
            { distance: midDist, base: midAlt, top: midAlt },
          ], transform, sfipRiskColor(sz.risk));
        }
      }

      // Unmatched next zones: fade in from midpoint
      for (let j = 0; j < next.sfipZones.length; j++) {
        if (usedNext.has(j) || next.sfipZones[j].risk === 'none') continue;
        const nz = next.sfipZones[j];
        const midDist = (curr.distanceNm + next.distanceNm) / 2;
        const midAlt = (nz.baseFt + nz.topFt) / 2;
        drawSmoothBand(ctx, [
          { distance: midDist, base: midAlt, top: midAlt },
          { distance: next.distanceNm, base: nz.baseFt, top: nz.topFt },
        ], transform, sfipRiskColor(nz.risk));
      }
    }
  },
};
