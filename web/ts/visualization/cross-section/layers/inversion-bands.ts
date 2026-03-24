/** Inversion layer bands: warm pink fills with strength-based opacity. */

import type { CrossSectionLayer, CoordTransform, VizRouteData } from '../../types';
import { inversionOpacity } from '../../scales';
import { getActiveTheme } from '../theme';
import { renderMatchedZones } from './zone-matching';

export const inversionBandsLayer: CrossSectionLayer = {
  id: 'inversion-bands',
  name: 'Inversions',
  group: 'stability',
  defaultEnabled: false,
  metricId: 'inversion_layer',

  render(ctx: CanvasRenderingContext2D, transform: CoordTransform, data: VizRouteData) {
    renderMatchedZones(ctx, transform, data, {
      getZones: (p) => p.inversions,
      getColor: (inv, matched) => {
        const avgStrength = matched
          ? (inv.strengthC + matched.strengthC) / 2
          : inv.strengthC;
        const opacity = inversionOpacity(avgStrength);
        const [ir, ig, ib] = getActiveTheme().inversion.baseRgb;
        return `rgba(${ir}, ${ig}, ${ib}, ${opacity})`;
      },
    });
  },
};
