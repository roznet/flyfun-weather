/** E-Shear turbulence bands: wind shear magnitude (horizontal + vertical). */

import type { CrossSectionLayer, CoordTransform, VizRouteData } from '../../types';
import { catRiskColor } from '../../scales';
import { renderMatchedZones, maxRisk } from './zone-matching';

export const eShearBandsLayer: CrossSectionLayer = {
  id: 'e-shear-bands',
  name: 'CAT (E-Shear)',
  group: 'turbulence',
  defaultEnabled: false,
  metricId: 'e_shear_risk',

  render(ctx: CanvasRenderingContext2D, transform: CoordTransform, data: VizRouteData) {
    renderMatchedZones(ctx, transform, data, {
      getZones: (p) => p.eShearLayers.filter((z) => z.risk !== 'none'),
      getColor: (z, matched) => catRiskColor(matched ? maxRisk(z.risk, matched.risk) : z.risk),
    });
  },
};
