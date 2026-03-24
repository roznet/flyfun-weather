/** Icing zone bands: colored fills by risk level. */

import type { CrossSectionLayer, CoordTransform, VizRouteData } from '../../types';
import { icingRiskColor } from '../../scales';
import { renderMatchedZones, maxRisk } from './zone-matching';

export const icingBandsLayer: CrossSectionLayer = {
  id: 'icing-bands',
  name: 'Ogimet-DD',
  group: 'icing',
  defaultEnabled: true,
  metricId: 'icing_risk',

  render(ctx: CanvasRenderingContext2D, transform: CoordTransform, data: VizRouteData) {
    renderMatchedZones(ctx, transform, data, {
      getZones: (p) => p.icingZones.filter((z) => z.risk !== 'none'),
      getColor: (z, matched) => icingRiskColor(matched ? maxRisk(z.risk, matched.risk) : z.risk),
    });
  },
};
