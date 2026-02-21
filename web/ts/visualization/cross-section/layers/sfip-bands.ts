/** SFIP icing zone bands: hatched fills by risk level, distinct from Ogimet solid fills. */

import type { CrossSectionLayer, CoordTransform, VizRouteData, RenderMode, VizSfipZone } from '../../types';
import { drawSmoothBand, drawColumnBand, type BandPointData } from './base';

/** SFIP risk colors — same hues as Ogimet but rendered with diagonal hatching. */
const SFIP_RISK_COLORS: Record<string, string> = {
  none: 'transparent',
  light: 'rgba(100, 149, 237, 0.55)',
  moderate: 'rgba(255, 165, 0, 0.65)',
  severe: 'rgba(220, 53, 69, 0.75)',
};

function sfipRiskColor(risk: string): string {
  return SFIP_RISK_COLORS[risk] ?? 'transparent';
}

/** Create a diagonal hatch pattern for a given color. */
function createHatchPattern(ctx: CanvasRenderingContext2D, color: string): CanvasPattern | string {
  const size = 8;
  const offscreen = document.createElement('canvas');
  offscreen.width = size;
  offscreen.height = size;
  const oc = offscreen.getContext('2d');
  if (!oc) return color;

  // Transparent background
  oc.clearRect(0, 0, size, size);

  // Diagonal line
  oc.strokeStyle = color;
  oc.lineWidth = 2;
  oc.beginPath();
  oc.moveTo(0, size);
  oc.lineTo(size, 0);
  oc.stroke();
  // Wrap-around lines for seamless tiling
  oc.beginPath();
  oc.moveTo(-size / 2, size / 2);
  oc.lineTo(size / 2, -size / 2);
  oc.stroke();
  oc.beginPath();
  oc.moveTo(size / 2, size + size / 2);
  oc.lineTo(size + size / 2, size / 2);
  oc.stroke();

  const pattern = ctx.createPattern(offscreen, 'repeat');
  return pattern ?? color;
}

/** Cache patterns per color to avoid re-creating every frame. */
const patternCache = new Map<string, CanvasPattern | string>();

function getHatchFill(ctx: CanvasRenderingContext2D, risk: string): CanvasPattern | string {
  const color = sfipRiskColor(risk);
  if (color === 'transparent') return color;

  const key = color;
  if (patternCache.has(key)) return patternCache.get(key)!;

  const pattern = createHatchPattern(ctx, color);
  patternCache.set(key, pattern);
  return pattern;
}

/** Draw a hatched smooth band (fills with pattern instead of solid color). */
function drawHatchedSmoothBand(
  ctx: CanvasRenderingContext2D,
  bandPoints: BandPointData[],
  transform: CoordTransform,
  risk: string,
): void {
  const fill = getHatchFill(ctx, risk);
  if (fill === 'transparent') return;

  // Use the base drawSmoothBand with a solid fill, then overlay hatch
  // Since drawSmoothBand expects a string fillStyle, we need to set it manually
  const valid = bandPoints.filter((p) => p.base !== null && p.top !== null);
  if (valid.length < 2) {
    if (valid.length === 1) {
      drawColumnBand(ctx, bandPoints, transform, sfipRiskColor(risk));
    }
    return;
  }

  // Draw semi-transparent solid base
  const baseColor = sfipRiskColor(risk).replace(/[\d.]+\)$/, '0.15)');
  drawSmoothBand(ctx, bandPoints, transform, baseColor);

  // Draw hatch pattern on top by tracing the same path
  ctx.save();
  if (typeof fill !== 'string') {
    ctx.fillStyle = fill;
  } else {
    ctx.fillStyle = fill;
  }

  // Re-trace the band shape for the pattern fill
  drawSmoothBand(ctx, bandPoints, transform, typeof fill === 'string' ? fill : sfipRiskColor(risk));
  ctx.restore();
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
          const fill = sfipRiskColor(sz.risk);
          const bandPoints: BandPointData[] = [{ distance: point.distanceNm, base: sz.baseFt, top: sz.topFt }];
          // Draw light base fill + hatch overlay for distinction
          const baseColor = fill.replace(/[\d.]+\)$/, '0.15)');
          drawColumnBand(ctx, bandPoints, transform, baseColor);
          // Draw hatch pattern
          const hatch = getHatchFill(ctx, sz.risk);
          if (typeof hatch !== 'string') {
            ctx.save();
            ctx.fillStyle = hatch;
            const x = transform.distanceToX(point.distanceNm);
            const yTop = transform.altitudeToY(sz.topFt);
            const yBase = transform.altitudeToY(sz.baseFt);
            ctx.fillRect(x - 20, yTop, 40, yBase - yTop);
            ctx.restore();
          }
        }
      }
      return;
    }

    // Smooth mode: matched bands between adjacent points (same logic as icing-bands)
    for (let i = 0; i < data.points.length - 1; i++) {
      const curr = data.points[i];
      const next = data.points[i + 1];
      const usedNext = new Set<number>();

      for (const sz of curr.sfipZones) {
        if (sz.risk === 'none') continue;

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
          const fill = sfipRiskColor(maxRisk);
          // Light transparent base
          const baseColor = fill.replace(/[\d.]+\)$/, '0.15)');
          const pts: BandPointData[] = [
            { distance: curr.distanceNm, base: sz.baseFt, top: sz.topFt },
            { distance: next.distanceNm, base: nz.baseFt, top: nz.topFt },
          ];
          drawSmoothBand(ctx, pts, transform, baseColor);
          // Hatch overlay
          drawSmoothBand(ctx, pts, transform, fill);
        } else {
          const midDist = (curr.distanceNm + next.distanceNm) / 2;
          const midAlt = (sz.baseFt + sz.topFt) / 2;
          const fill = sfipRiskColor(sz.risk);
          const baseColor = fill.replace(/[\d.]+\)$/, '0.15)');
          const pts: BandPointData[] = [
            { distance: curr.distanceNm, base: sz.baseFt, top: sz.topFt },
            { distance: midDist, base: midAlt, top: midAlt },
          ];
          drawSmoothBand(ctx, pts, transform, baseColor);
          drawSmoothBand(ctx, pts, transform, fill);
        }
      }

      // Unmatched next zones
      for (let j = 0; j < next.sfipZones.length; j++) {
        if (usedNext.has(j) || next.sfipZones[j].risk === 'none') continue;
        const nz = next.sfipZones[j];
        const midDist = (curr.distanceNm + next.distanceNm) / 2;
        const midAlt = (nz.baseFt + nz.topFt) / 2;
        const fill = sfipRiskColor(nz.risk);
        const baseColor = fill.replace(/[\d.]+\)$/, '0.15)');
        const pts: BandPointData[] = [
          { distance: midDist, base: midAlt, top: midAlt },
          { distance: next.distanceNm, base: nz.baseFt, top: nz.topFt },
        ];
        drawSmoothBand(ctx, pts, transform, baseColor);
        drawSmoothBand(ctx, pts, transform, fill);
      }
    }
  },
};
