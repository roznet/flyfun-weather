/** Ogimet-NWP icing zone bands: colored fills by risk level, scaled by NWP cloud %. */

import type { CrossSectionLayer, CoordTransform, VizRouteData } from '../../types';
import { icingRiskColor } from '../../scales';
import { drawSmoothBand, type BandPointData } from './base';

export const icingOgimetNwpBandsLayer: CrossSectionLayer = {
  id: 'icing-ogimet-nwp-bands',
  name: 'Ogimet-NWP',
  group: 'icing',
  defaultEnabled: false,
  metricId: 'icing_ogimet_nwp_risk',

  render(ctx: CanvasRenderingContext2D, transform: CoordTransform, data: VizRouteData) {
    // Draw matched bands between adjacent points
    for (let i = 0; i < data.points.length - 1; i++) {
      const curr = data.points[i];
      const next = data.points[i + 1];
      const usedNext = new Set<number>();

      for (const iz of curr.icingOgimetNwpZones) {
        if (iz.risk === 'none') continue;

        let bestIdx = -1;
        let bestOverlap = 0;
        for (let j = 0; j < next.icingOgimetNwpZones.length; j++) {
          if (usedNext.has(j) || next.icingOgimetNwpZones[j].risk === 'none') continue;
          const nz = next.icingOgimetNwpZones[j];
          const overlap = Math.min(iz.topFt, nz.topFt) - Math.max(iz.baseFt, nz.baseFt);
          if (overlap > bestOverlap) { bestOverlap = overlap; bestIdx = j; }
        }

        if (bestIdx >= 0) {
          usedNext.add(bestIdx);
          const nz = next.icingOgimetNwpZones[bestIdx];
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
      for (let j = 0; j < next.icingOgimetNwpZones.length; j++) {
        if (usedNext.has(j) || next.icingOgimetNwpZones[j].risk === 'none') continue;
        const nz = next.icingOgimetNwpZones[j];
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
