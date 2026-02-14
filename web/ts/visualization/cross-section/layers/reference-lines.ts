/** Reference lines: cruise altitude and flight ceiling. */

import type { CrossSectionLayer, CoordTransform, VizRouteData, RenderMode } from '../../types';

/** Shared helper: draw a horizontal reference line with a label. */
function drawRefLine(
  ctx: CanvasRenderingContext2D,
  transform: CoordTransform,
  altitudeFt: number,
  label: string,
  color: string,
  lineWidth: number,
  dashPattern: number[],
  labelYOffset: number,
): void {
  const y = transform.altitudeToY(altitudeFt);
  const { plotArea } = transform;

  ctx.strokeStyle = color;
  ctx.lineWidth = lineWidth;
  ctx.setLineDash(dashPattern);
  ctx.beginPath();
  ctx.moveTo(plotArea.left, y);
  ctx.lineTo(plotArea.left + plotArea.width, y);
  ctx.stroke();
  ctx.setLineDash([]);

  ctx.font = 'bold 11px -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif';
  const textWidth = ctx.measureText(label).width;

  ctx.fillStyle = 'rgba(255, 255, 255, 0.85)';
  ctx.fillRect(plotArea.left + 4, y + labelYOffset - 12, textWidth + 8, 14);

  ctx.fillStyle = color;
  ctx.textAlign = 'left';
  ctx.textBaseline = 'bottom';
  ctx.fillText(label, plotArea.left + 8, y + labelYOffset + 1);
}

export const cruiseAltitudeLayer: CrossSectionLayer = {
  id: 'cruise-altitude',
  name: 'Cruise / Ceiling',
  group: 'reference',
  defaultEnabled: true,

  render(ctx: CanvasRenderingContext2D, transform: CoordTransform, data: VizRouteData, _mode: RenderMode) {
    // Cruise altitude — always drawn
    const cruiseLabel = `Cruise ${data.cruiseAltitudeFt.toLocaleString()} ft`;
    drawRefLine(ctx, transform, data.cruiseAltitudeFt, cruiseLabel,
      '#374151', 2.5, [8, 4], -4);

    // Flight ceiling — only when meaningfully different from cruise
    const separation = Math.abs(data.ceilingAltitudeFt - data.cruiseAltitudeFt);
    if (separation >= 1000) {
      const ceilingLabel = `Ceiling ${data.ceilingAltitudeFt.toLocaleString()} ft`;
      drawRefLine(ctx, transform, data.ceilingAltitudeFt, ceilingLabel,
        '#9467bd', 1.5, [4, 4], 12);
    }
  },
};
