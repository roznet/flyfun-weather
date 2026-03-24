/** CAT turbulence bands: amber/red fills by risk level. */

import type { CrossSectionLayer, CoordTransform, VizRouteData } from '../../types';
import { catRiskColor } from '../../scales';
import { renderMatchedZones, maxRisk } from './zone-matching';

export const catBandsLayer: CrossSectionLayer = {
  id: 'cat-bands',
  name: 'CAT Turbulence',
  group: 'turbulence',
  defaultEnabled: false,
  metricId: 'cat_risk',

  render(ctx: CanvasRenderingContext2D, transform: CoordTransform, data: VizRouteData) {
    renderMatchedZones(ctx, transform, data, {
      getZones: (p) => p.catLayers.filter((z) => z.risk !== 'none'),
      getColor: (z, matched) => catRiskColor(matched ? maxRisk(z.risk, matched.risk) : z.risk),
    });
  },
};
