/** Icing zone bands: colored fills by risk level. */

import type { CrossSectionLayer, CoordTransform, VizRouteData, RenderMode } from '../../types';
import { icingRiskColor } from '../../scales';
import { drawSmoothBand, drawColumnBand, type BandPointData } from './base';

export const icingBandsLayer: CrossSectionLayer = {
  id: 'icing-bands',
  name: 'Icing Zones',
  group: 'icing',
  defaultEnabled: true,

  render(ctx: CanvasRenderingContext2D, transform: CoordTransform, data: VizRouteData, mode: RenderMode) {
    if (mode === 'columns') {
      for (const point of data.points) {
        for (const iz of point.icingZones) {
          if (iz.risk === 'none') continue;
          const fill = icingRiskColor(iz.risk);
          const bandPoints: BandPointData[] = [{ distance: point.distanceNm, base: iz.baseFt, top: iz.topFt }];
          drawColumnBand(ctx, bandPoints, transform, fill);
        }
      }
      return;
    }

    // Smooth mode: draw matched bands between adjacent points
    for (let i = 0; i < data.points.length - 1; i++) {
      const curr = data.points[i];
      const next = data.points[i + 1];
      const usedNext = new Set<number>();

      for (const iz of curr.icingZones) {
        if (iz.risk === 'none') continue;

        let bestIdx = -1;
        let bestOverlap = 0;
        for (let j = 0; j < next.icingZones.length; j++) {
          if (usedNext.has(j) || next.icingZones[j].risk === 'none') continue;
          const nz = next.icingZones[j];
          const overlap = Math.min(iz.topFt, nz.topFt) - Math.max(iz.baseFt, nz.baseFt);
          if (overlap > bestOverlap) { bestOverlap = overlap; bestIdx = j; }
        }

        if (bestIdx >= 0) {
          usedNext.add(bestIdx);
          const nz = next.icingZones[bestIdx];
          // Use the higher risk color
          const riskOrder = ['none', 'light', 'moderate', 'severe'];
          const maxRisk = riskOrder.indexOf(iz.risk) >= riskOrder.indexOf(nz.risk) ? iz.risk : nz.risk;
          drawSmoothBand(ctx, [
            { distance: curr.distanceNm, base: iz.baseFt, top: iz.topFt },
            { distance: next.distanceNm, base: nz.baseFt, top: nz.topFt },
          ], transform, icingRiskColor(maxRisk));
        } else {
          const midDist = (curr.distanceNm + next.distanceNm) / 2;
          const midAlt = (iz.baseFt + iz.topFt) / 2;
          drawSmoothBand(ctx, [
            { distance: curr.distanceNm, base: iz.baseFt, top: iz.topFt },
            { distance: midDist, base: midAlt, top: midAlt },
          ], transform, icingRiskColor(iz.risk));
        }
      }

      // Unmatched next zones
      for (let j = 0; j < next.icingZones.length; j++) {
        if (usedNext.has(j) || next.icingZones[j].risk === 'none') continue;
        const nz = next.icingZones[j];
        const midDist = (curr.distanceNm + next.distanceNm) / 2;
        const midAlt = (nz.baseFt + nz.topFt) / 2;
        drawSmoothBand(ctx, [
          { distance: midDist, base: midAlt, top: midAlt },
          { distance: next.distanceNm, base: nz.baseFt, top: nz.topFt },
        ], transform, icingRiskColor(nz.risk));
      }
    }
  },
};
