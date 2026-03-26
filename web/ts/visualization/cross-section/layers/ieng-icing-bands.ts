/** IENG icing bands: cloud-fraction-weighted Ogimet index (no glaciation correction). */

import type { CrossSectionLayer, CoordTransform, VizRouteData } from '../../types';
import { icingRiskColor } from '../../scales';
import { renderMatchedZones, maxRisk } from './zone-matching';

export const iengIcingBandsLayer: CrossSectionLayer = {
  id: 'ieng-icing-bands',
  name: 'IENG',
  group: 'icing',
  defaultEnabled: false,
  metricId: 'ieng_icing_risk',

  render(ctx: CanvasRenderingContext2D, transform: CoordTransform, data: VizRouteData) {
    renderMatchedZones(ctx, transform, data, {
      getZones: (p) => p.iengIcingZones.filter((z) => z.risk !== 'none'),
      getColor: (z, matched) => icingRiskColor(matched ? maxRisk(z.risk, matched.risk) : z.risk),
    });
  },
};
