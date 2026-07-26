/** Reference lines: cruise altitude and flight ceiling. */

import type { CrossSectionLayer, CoordTransform, VizRouteData } from '../../types';
import { getActiveTheme, type CrossSectionTheme, type LineStyle } from '../theme';
import { t } from '../../../i18n/i18n';

/** Stroke specs for the two reference lines. The theme carries only the
 *  colours, so width + dash live here — exported so the legend and the theme
 *  preview draw the same stroke this layer does. */
export function referenceLineStyles(
  theme: CrossSectionTheme,
): { cruise: LineStyle; ceiling: LineStyle } {
  return {
    cruise: { color: theme.reference.cruiseColor, width: 2.5, dash: [8, 4] },
    ceiling: { color: theme.reference.ceilingColor, width: 1.5, dash: [4, 4] },
  };
}

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
  const theme = getActiveTheme();

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

  const bgColor = theme.sky.background;
  ctx.fillStyle = bgColor + 'dd';
  ctx.fillRect(plotArea.left + 4, y + labelYOffset - 12, textWidth + 8, 14);

  ctx.fillStyle = color;
  ctx.textAlign = 'left';
  ctx.textBaseline = 'bottom';
  ctx.fillText(label, plotArea.left + 8, y + labelYOffset + 1);
}

export const cruiseAltitudeLayer: CrossSectionLayer = {
  id: 'cruise-altitude',
  name: 'Cruise / Flight ceiling',
  group: 'reference',
  defaultEnabled: true,

  render(ctx: CanvasRenderingContext2D, transform: CoordTransform, data: VizRouteData) {
    const styles = referenceLineStyles(getActiveTheme());

    // Cruise altitude — always drawn
    const cruiseLabel = t('viz.refCruise', { alt: data.cruiseAltitudeFt.toLocaleString() });
    drawRefLine(ctx, transform, data.cruiseAltitudeFt, cruiseLabel,
      styles.cruise.color, styles.cruise.width, styles.cruise.dash ?? [], -4);

    // Flight ceiling — only when meaningfully different from cruise
    const separation = Math.abs(data.ceilingAltitudeFt - data.cruiseAltitudeFt);
    if (separation >= 1000) {
      const ceilingLabel = t('viz.refCeiling', { alt: data.ceilingAltitudeFt.toLocaleString() });
      drawRefLine(ctx, transform, data.ceilingAltitudeFt, ceilingLabel,
        styles.ceiling.color, styles.ceiling.width, styles.ceiling.dash ?? [], -4);
    }
  },
};
