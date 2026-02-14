/** CAT turbulence bands: amber/red fills by risk level. */

import type { CrossSectionLayer, CoordTransform, VizRouteData, RenderMode } from '../../types';
import { catRiskColor } from '../../scales';
import { drawSmoothBand, drawColumnBand, type BandPointData } from './base';

export const catBandsLayer: CrossSectionLayer = {
  id: 'cat-bands',
  name: 'CAT Turbulence',
  group: 'turbulence',
  defaultEnabled: false,

  render(ctx: CanvasRenderingContext2D, transform: CoordTransform, data: VizRouteData, mode: RenderMode) {
    if (mode === 'columns') {
      for (const point of data.points) {
        for (const cl of point.catLayers) {
          if (cl.risk === 'none') continue;
          drawColumnBand(ctx,
            [{ distance: point.distanceNm, base: cl.baseFt, top: cl.topFt }],
            transform, catRiskColor(cl.risk));
        }
      }
      return;
    }

    // Smooth mode
    for (let i = 0; i < data.points.length - 1; i++) {
      const curr = data.points[i];
      const next = data.points[i + 1];
      const usedNext = new Set<number>();

      for (const cl of curr.catLayers) {
        if (cl.risk === 'none') continue;

        let bestIdx = -1;
        let bestOverlap = 0;
        for (let j = 0; j < next.catLayers.length; j++) {
          if (usedNext.has(j) || next.catLayers[j].risk === 'none') continue;
          const nl = next.catLayers[j];
          const overlap = Math.min(cl.topFt, nl.topFt) - Math.max(cl.baseFt, nl.baseFt);
          if (overlap > bestOverlap) { bestOverlap = overlap; bestIdx = j; }
        }

        if (bestIdx >= 0) {
          usedNext.add(bestIdx);
          const nl = next.catLayers[bestIdx];
          const riskOrder = ['none', 'light', 'moderate', 'severe'];
          const maxRisk = riskOrder.indexOf(cl.risk) >= riskOrder.indexOf(nl.risk) ? cl.risk : nl.risk;
          drawSmoothBand(ctx, [
            { distance: curr.distanceNm, base: cl.baseFt, top: cl.topFt },
            { distance: next.distanceNm, base: nl.baseFt, top: nl.topFt },
          ], transform, catRiskColor(maxRisk));
        } else {
          const midDist = (curr.distanceNm + next.distanceNm) / 2;
          const midAlt = (cl.baseFt + cl.topFt) / 2;
          drawSmoothBand(ctx, [
            { distance: curr.distanceNm, base: cl.baseFt, top: cl.topFt },
            { distance: midDist, base: midAlt, top: midAlt },
          ], transform, catRiskColor(cl.risk));
        }
      }

      for (let j = 0; j < next.catLayers.length; j++) {
        if (usedNext.has(j) || next.catLayers[j].risk === 'none') continue;
        const nl = next.catLayers[j];
        const midDist = (curr.distanceNm + next.distanceNm) / 2;
        const midAlt = (nl.baseFt + nl.topFt) / 2;
        drawSmoothBand(ctx, [
          { distance: midDist, base: midAlt, top: midAlt },
          { distance: next.distanceNm, base: nl.baseFt, top: nl.topFt },
        ], transform, catRiskColor(nl.risk));
      }
    }
  },
};
