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

/** The flight path (#1005): a polyline through climb, cruise and descent. */
function drawPathLine(
  ctx: CanvasRenderingContext2D,
  transform: CoordTransform,
  path: { distanceNm: number; altitudeFt: number }[],
  label: string,
  cruiseFt: number,
  color: string,
  lineWidth: number,
  dashPattern: number[],
): void {
  const theme = getActiveTheme();
  ctx.strokeStyle = color;
  ctx.lineWidth = lineWidth;
  ctx.setLineDash(dashPattern);
  ctx.beginPath();
  path.forEach((p, i) => {
    const x = transform.distanceToX(p.distanceNm);
    const y = transform.altitudeToY(p.altitudeFt);
    if (i === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  });
  ctx.stroke();
  ctx.setLineDash([]);

  // Label at the start of the level part (top of climb), above the line.
  const top = path.find((p) => p.altitudeFt >= cruiseFt - 1) ?? path[0];
  const x = transform.distanceToX(top.distanceNm) + 4;
  const y = transform.altitudeToY(top.altitudeFt) - 4;
  ctx.font = 'bold 11px -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif';
  const textWidth = ctx.measureText(label).width;
  ctx.fillStyle = theme.sky.background + 'dd';
  ctx.fillRect(x, y - 12, textWidth + 8, 14);
  ctx.fillStyle = color;
  ctx.textAlign = 'left';
  ctx.textBaseline = 'bottom';
  ctx.fillText(label, x + 4, y + 1);
}

export const cruiseAltitudeLayer: CrossSectionLayer = {
  id: 'cruise-altitude',
  name: 'Cruise / Flight ceiling',
  group: 'reference',
  defaultEnabled: true,

  render(ctx: CanvasRenderingContext2D, transform: CoordTransform, data: VizRouteData) {
    const styles = referenceLineStyles(getActiveTheme());

    // Cruise altitude — always drawn. With a flight path (#1005) the line
    // follows the climb and descent, labelled at the cruise level.
    const cruiseLabel = t('viz.refCruise', { alt: data.cruiseAltitudeFt.toLocaleString() });
    if (data.flightPath && data.flightPath.length >= 2) {
      drawPathLine(ctx, transform, data.flightPath, cruiseLabel, data.cruiseAltitudeFt,
        styles.cruise.color, styles.cruise.width, styles.cruise.dash ?? []);
    } else {
      drawRefLine(ctx, transform, data.cruiseAltitudeFt, cruiseLabel,
        styles.cruise.color, styles.cruise.width, styles.cruise.dash ?? [], -4);
    }

    // Flight ceiling — only when meaningfully different from cruise
    const separation = Math.abs(data.ceilingAltitudeFt - data.cruiseAltitudeFt);
    if (separation >= 1000) {
      const ceilingLabel = t('viz.refCeiling', { alt: data.ceilingAltitudeFt.toLocaleString() });
      drawRefLine(ctx, transform, data.ceilingAltitudeFt, ceilingLabel,
        styles.ceiling.color, styles.ceiling.width, styles.ceiling.dash ?? [], -4);
    }
  },
};
