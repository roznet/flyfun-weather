/** SLD (Supercooled Large Droplet) bands: red semi-transparent overlay. */

import type { CrossSectionLayer, CoordTransform, VizRouteData } from '../../types';
import { getActiveTheme } from '../theme';
import { renderMatchedZones, maxRisk } from './zone-matching';

function sldRiskColor(risk: string): string {
  const theme = getActiveTheme();
  const sldColors = (theme as any).sld;
  if (sldColors && sldColors[risk]) return sldColors[risk];
  // Fallback colors if theme doesn't define sld
  switch (risk) {
    case 'light': return 'rgba(220, 53, 69, 0.25)';
    case 'moderate': return 'rgba(220, 53, 69, 0.40)';
    case 'severe': return 'rgba(220, 53, 69, 0.55)';
    default: return 'transparent';
  }
}

export const sldBandsLayer: CrossSectionLayer = {
  id: 'sld-bands',
  name: 'SLD',
  group: 'icing',
  defaultEnabled: false,
  metricId: 'sld_risk',

  render(ctx: CanvasRenderingContext2D, transform: CoordTransform, data: VizRouteData) {
    renderMatchedZones(ctx, transform, data, {
      getZones: (p) => p.sldZones.filter((z) => z.risk !== 'none'),
      getColor: (z, matched) => sldRiskColor(matched ? maxRisk(z.risk, matched.risk) : z.risk),
    });
  },
};
