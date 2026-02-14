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

    ctx.strokeStyle = '#9e9e9e';
    ctx.lineWidth = 1;
    ctx.setLineDash([2, 4]);
    ctx.beginPath();
    ctx.moveTo(plotArea.left, y);
    ctx.lineTo(plotArea.left + plotArea.width, y);
    ctx.stroke();
    ctx.setLineDash([]);

    // Label
    ctx.fillStyle = '#9e9e9e';
    ctx.font = '10px -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif';
    ctx.textAlign = 'left';
    ctx.textBaseline = 'bottom';
    ctx.fillText(`Cruise ${data.cruiseAltitudeFt.toLocaleString()} ft`, plotArea.left + 4, y - 3);
  },
};
