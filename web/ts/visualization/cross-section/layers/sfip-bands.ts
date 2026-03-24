/** SFIP icing zone bands: fills by risk level, visually distinct from Ogimet solid fills. */

import type { CrossSectionLayer, CoordTransform, VizRouteData } from '../../types';
import { getActiveTheme } from '../theme';
import { renderMatchedZones, maxRisk } from './zone-matching';

function sfipRiskColor(risk: string): string {
  return getActiveTheme().sfipIcing[risk] ?? 'transparent';
}

export const sfipBandsLayer: CrossSectionLayer = {
  id: 'sfip-bands',
  name: 'SFIP-NWP',
  group: 'icing',
  defaultEnabled: false,
  metricId: 'sfip_risk',

  render(ctx: CanvasRenderingContext2D, transform: CoordTransform, data: VizRouteData) {
    renderMatchedZones(ctx, transform, data, {
      getZones: (p) => p.sfipZones.filter((z) => z.risk !== 'none'),
      getColor: (z, matched) => sfipRiskColor(matched ? maxRisk(z.risk, matched.risk) : z.risk),
    });
  },
};
