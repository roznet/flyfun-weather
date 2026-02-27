/** SFIP icing zone bands: diagonal-hatch fills by risk level, visually distinct from Ogimet solid fills. */

import type { CrossSectionLayer, CoordTransform, VizRouteData } from '../../types';
import { drawSmoothBand, type BandPointData } from './base';

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

export const sfipBandsLayer: CrossSectionLayer = {
  id: 'sfip-bands',
  name: 'SFIP Icing Index',
  group: 'icing',
  defaultEnabled: false,
  metricId: 'sfip_risk',

  render(ctx: CanvasRenderingContext2D, transform: CoordTransform, data: VizRouteData) {
    // Draw hatched bands between adjacent points
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
