/** NWP convective risk visualization: tower columns from GFS convective base/top.
 *
 * Uses NWP model-computed convective cloud bounds directly — no estimateTowerTop
 * needed since these are model parameterization outputs, not thermodynamic estimates.
 * Same color palette as thermo layer for visual consistency.
 */

import type { CrossSectionLayer, CoordTransform, VizRouteData } from '../../types';
import {
  BG_WASH, TOWER_FILL, HATCH_COLOR, STRIP_COLOR, EDGE_COLOR, STRIP_HEIGHT,
  drawHatching, drawCBLabel,
} from './thermo-convective-bg';

export const nwpConvectiveBgLayer: CrossSectionLayer = {
  id: 'nwp-convective-bg',
  name: 'NWP Convective',
  group: 'convection',
  defaultEnabled: false,

  render(ctx: CanvasRenderingContext2D, transform: CoordTransform, data: VizRouteData) {
    const { plotArea } = transform;

    for (let i = 0; i < data.points.length; i++) {
      const p = data.points[i];
      const risk = p.nwpConvectiveRisk;
      if (risk === 'none') continue;

      // Column x-bounds (midpoint between neighbors)
      const x = transform.distanceToX(p.distanceNm);
      const xLeft = i === 0
        ? plotArea.left
        : (transform.distanceToX(data.points[i - 1].distanceNm) + x) / 2;
      const xRight = i === data.points.length - 1
        ? plotArea.left + plotArea.width
        : (x + transform.distanceToX(data.points[i + 1].distanceNm)) / 2;
      const colWidth = xRight - xLeft;

      // NWP bounds are model-computed — skip if absent (no full-height fallback)
      if (p.nwpConvectiveBaseFt == null || p.nwpConvectiveTopFt == null) continue;

      {
        const baseFt = p.nwpConvectiveBaseFt;
        const topFt = p.nwpConvectiveTopFt;
        const yBase = transform.altitudeToY(baseFt);
        const yTop = transform.altitudeToY(topFt);
        const towerHeight = yBase - yTop;

        if (towerHeight <= 0) continue;

        // 1. Full-height background wash
        const bgWash = BG_WASH[risk];
        if (bgWash) {
          ctx.fillStyle = bgWash;
          ctx.fillRect(xLeft, plotArea.top, colWidth, plotArea.height);
        }

        // 2. Tower body fill
        const towerFill = TOWER_FILL[risk];
        if (towerFill) {
          ctx.fillStyle = towerFill;
          ctx.fillRect(xLeft, yTop, colWidth, towerHeight);
        }

        // 3. Diagonal hatching
        const hatchColor = HATCH_COLOR[risk];
        if (hatchColor) {
          drawHatching(ctx, xLeft, yTop, colWidth, towerHeight, hatchColor);
        }

        // 4. Tower edge outline
        const edgeColor = EDGE_COLOR[risk];
        if (edgeColor) {
          ctx.strokeStyle = edgeColor;
          ctx.lineWidth = 1.5;
          ctx.setLineDash([]);
          ctx.strokeRect(xLeft + 0.5, yTop + 0.5, colWidth - 1, towerHeight - 1);
        }

        // 5. Anvil top strip
        const stripColor = STRIP_COLOR[risk];
        if (stripColor) {
          const anvilExtend = Math.min(colWidth * 0.2, 8);
          ctx.fillStyle = stripColor;
          ctx.fillRect(xLeft - anvilExtend, yTop, colWidth + anvilExtend * 2, STRIP_HEIGHT);
        }

        // 6. CB label (moderate+)
        if (risk !== 'low' && colWidth > 18) {
          const cx = (xLeft + xRight) / 2;
          const cy = yTop + towerHeight * 0.3;
          drawCBLabel(ctx, cx, cy, risk);
        }
      }
    }
  },
};
