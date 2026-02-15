/** Inversion layer bands: warm pink fills with strength-based opacity. */

import type { CrossSectionLayer, CoordTransform, VizRouteData, RenderMode } from '../../types';
import { inversionOpacity } from '../../scales';
import { drawSmoothBand, drawColumnBand, type BandPointData } from './base';

export const inversionBandsLayer: CrossSectionLayer = {
  id: 'inversion-bands',
  name: 'Inversions',
  group: 'stability',
  defaultEnabled: false,
  metricId: 'inversion_layer',

  render(ctx: CanvasRenderingContext2D, transform: CoordTransform, data: VizRouteData, mode: RenderMode) {
    if (mode === 'columns') {
      for (const point of data.points) {
        for (const inv of point.inversions) {
          const opacity = inversionOpacity(inv.strengthC);
          const fill = `rgba(233, 30, 99, ${opacity})`;
          drawColumnBand(ctx,
            [{ distance: point.distanceNm, base: inv.baseFt, top: inv.topFt }],
            transform, fill);
        }
      }
      return;
    }

    // Smooth mode
    for (let i = 0; i < data.points.length - 1; i++) {
      const curr = data.points[i];
      const next = data.points[i + 1];
      const usedNext = new Set<number>();

      for (const inv of curr.inversions) {
        let bestIdx = -1;
        let bestOverlap = 0;
        for (let j = 0; j < next.inversions.length; j++) {
          if (usedNext.has(j)) continue;
          const ni = next.inversions[j];
          const overlap = Math.min(inv.topFt, ni.topFt) - Math.max(inv.baseFt, ni.baseFt);
          if (overlap > bestOverlap) { bestOverlap = overlap; bestIdx = j; }
        }

        const avgStrength = bestIdx >= 0
          ? (inv.strengthC + next.inversions[bestIdx].strengthC) / 2
          : inv.strengthC;
        const opacity = inversionOpacity(avgStrength);
        const fill = `rgba(233, 30, 99, ${opacity})`;

        if (bestIdx >= 0) {
          usedNext.add(bestIdx);
          const ni = next.inversions[bestIdx];
          drawSmoothBand(ctx, [
            { distance: curr.distanceNm, base: inv.baseFt, top: inv.topFt },
            { distance: next.distanceNm, base: ni.baseFt, top: ni.topFt },
          ], transform, fill);
        } else {
          const midDist = (curr.distanceNm + next.distanceNm) / 2;
          const midAlt = (inv.baseFt + inv.topFt) / 2;
          drawSmoothBand(ctx, [
            { distance: curr.distanceNm, base: inv.baseFt, top: inv.topFt },
            { distance: midDist, base: midAlt, top: midAlt },
          ], transform, fill);
        }
      }

      for (let j = 0; j < next.inversions.length; j++) {
        if (usedNext.has(j)) continue;
        const ni = next.inversions[j];
        const midDist = (curr.distanceNm + next.distanceNm) / 2;
        const midAlt = (ni.baseFt + ni.topFt) / 2;
        const opacity = inversionOpacity(ni.strengthC);
        drawSmoothBand(ctx, [
          { distance: midDist, base: midAlt, top: midAlt },
          { distance: next.distanceNm, base: ni.baseFt, top: ni.topFt },
        ], transform, `rgba(233, 30, 99, ${opacity})`);
      }
    }
  },
};
