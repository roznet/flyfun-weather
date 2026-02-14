/** Reference lines: cruise altitude. */

import type { CrossSectionLayer, CoordTransform, VizRouteData, RenderMode } from '../../types';

export const cruiseAltitudeLayer: CrossSectionLayer = {
  id: 'cruise-altitude',
  name: 'Cruise Altitude',
  group: 'reference',
  defaultEnabled: true,

  render(ctx: CanvasRenderingContext2D, transform: CoordTransform, data: VizRouteData, _mode: RenderMode) {
    const y = transform.altitudeToY(data.cruiseAltitudeFt);
    const { plotArea } = transform;

    // Bold, prominent line
    ctx.strokeStyle = '#374151';
    ctx.lineWidth = 2.5;
    ctx.setLineDash([8, 4]);
    ctx.beginPath();
    ctx.moveTo(plotArea.left, y);
    ctx.lineTo(plotArea.left + plotArea.width, y);
    ctx.stroke();
    ctx.setLineDash([]);

    // Label with background for readability
    const label = `Cruise ${data.cruiseAltitudeFt.toLocaleString()} ft`;
    ctx.font = 'bold 11px -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif';
    const textWidth = ctx.measureText(label).width;

    ctx.fillStyle = 'rgba(255, 255, 255, 0.85)';
    ctx.fillRect(plotArea.left + 4, y - 16, textWidth + 8, 14);

    ctx.fillStyle = '#374151';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'bottom';
    ctx.fillText(label, plotArea.left + 8, y - 3);
  },
};
