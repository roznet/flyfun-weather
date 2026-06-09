/** Night/twilight shading: full-height column tint behind the weather (#227).
 *
 * Reads precomputed `nightIntervals` from VizRouteData (sourced from
 * `manifest.sun`). Registered first in ALL_LAYERS so it sits at the very back of
 * the stack — terrain and all weather bands draw over it, and terrain masks the
 * below-surface tint. Two tones: a light twilight wash and a darker night band.
 */

import type { CrossSectionLayer, CoordTransform, VizRouteData } from '../../types';
import { getActiveTheme } from '../theme';

export const nightShadingLayer: CrossSectionLayer = {
  id: 'night-shading',
  name: 'Night / Twilight',
  group: 'sun',
  defaultEnabled: true,

  render(ctx: CanvasRenderingContext2D, transform: CoordTransform, data: VizRouteData) {
    if (!data.nightIntervals || data.nightIntervals.length === 0) return;

    const { plotArea } = transform;
    const top = plotArea.top;
    const height = plotArea.height;
    const theme = getActiveTheme().nightShading;

    for (const interval of data.nightIntervals) {
      const x0 = transform.distanceToX(interval.startNm);
      const x1 = transform.distanceToX(interval.endNm);
      const w = x1 - x0;
      if (w <= 0) continue;
      ctx.fillStyle = interval.phase === 'night' ? theme.night : theme.twilight;
      ctx.fillRect(x0, top, w, height);
    }
  },
};
