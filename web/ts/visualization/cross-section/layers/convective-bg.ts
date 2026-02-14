/** Convective risk background: full-height colored column per segment. */

import type { CrossSectionLayer, CoordTransform, VizRouteData, RenderMode } from '../../types';
import { convectiveRiskColor } from '../../scales';

export const convectiveBgLayer: CrossSectionLayer = {
  id: 'convective-bg',
  name: 'Convective Risk',
  group: 'convection',
  defaultEnabled: false,

  render(ctx: CanvasRenderingContext2D, transform: CoordTransform, data: VizRouteData, _mode: RenderMode) {
    const { plotArea } = transform;

    for (let i = 0; i < data.points.length; i++) {
      const p = data.points[i];
      const color = convectiveRiskColor(p.convectiveRisk);
      if (color === 'transparent') continue;

      // Compute column bounds (midpoint between neighbors)
      let xLeft: number;
      let xRight: number;
      const x = transform.distanceToX(p.distanceNm);

      if (i === 0) {
        xLeft = plotArea.left;
      } else {
        xLeft = (transform.distanceToX(data.points[i - 1].distanceNm) + x) / 2;
      }

      if (i === data.points.length - 1) {
        xRight = plotArea.left + plotArea.width;
      } else {
        xRight = (x + transform.distanceToX(data.points[i + 1].distanceNm)) / 2;
      }

      ctx.fillStyle = color;
      ctx.fillRect(xLeft, plotArea.top, xRight - xLeft, plotArea.height);
    }
  },
};
