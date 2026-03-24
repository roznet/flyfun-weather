/** Ogimet-NWP icing zone bands: colored fills by risk level, scaled by NWP cloud %. */

import type { CrossSectionLayer, CoordTransform, VizRouteData } from '../../types';
import { icingRiskColor } from '../../scales';
import { renderMatchedZones, maxRisk } from './zone-matching';

export const icingOgimetNwpBandsLayer: CrossSectionLayer = {
  id: 'icing-ogimet-nwp-bands',
  name: 'Ogimet-NWP',
  group: 'icing',
  defaultEnabled: false,
  metricId: 'icing_ogimet_nwp_risk',

  render(ctx: CanvasRenderingContext2D, transform: CoordTransform, data: VizRouteData) {
    renderMatchedZones(ctx, transform, data, {
      getZones: (p) => p.icingOgimetNwpZones.filter((z) => z.risk !== 'none'),
      getColor: (z, matched) => icingRiskColor(matched ? maxRisk(z.risk, matched.risk) : z.risk),
    });
  },
};
