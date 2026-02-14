/** Convective risk visualization: hatched columns + risk indicator strip + CB labels. */

import type { CrossSectionLayer, CoordTransform, VizRouteData, RenderMode } from '../../types';

// Solid fill colors (stronger than before)
const CONVECTIVE_FILL: Record<string, string> = {
  none: '',
  low: 'rgba(255, 235, 59, 0.12)',
  moderate: 'rgba(255, 152, 0, 0.18)',
  high: 'rgba(220, 53, 69, 0.22)',
  extreme: 'rgba(183, 28, 28, 0.28)',
};

// Hatching line color per risk
const HATCH_COLOR: Record<string, string> = {
  low: 'rgba(180, 160, 0, 0.25)',
  moderate: 'rgba(200, 100, 0, 0.35)',
  high: 'rgba(200, 40, 40, 0.40)',
  extreme: 'rgba(150, 20, 20, 0.50)',
};

// Top indicator strip colors (solid, attention-grabbing)
const STRIP_COLOR: Record<string, string> = {
  low: 'rgba(255, 235, 59, 0.6)',
  moderate: 'rgba(255, 152, 0, 0.8)',
  high: 'rgba(220, 53, 69, 0.85)',
  extreme: 'rgba(183, 28, 28, 0.9)',
};

const STRIP_HEIGHT = 6;

// Risk label shown for moderate+
const RISK_LABEL: Record<string, string> = {
  moderate: 'CB',
  high: 'CB',
  extreme: 'CB',
};

export const convectiveBgLayer: CrossSectionLayer = {
  id: 'convective-bg',
  name: 'Convective Risk',
  group: 'convection',
  defaultEnabled: true,

  render(ctx: CanvasRenderingContext2D, transform: CoordTransform, data: VizRouteData, _mode: RenderMode) {
    const { plotArea } = transform;

    for (let i = 0; i < data.points.length; i++) {
      const p = data.points[i];
      if (p.convectiveRisk === 'none') continue;

      const fill = CONVECTIVE_FILL[p.convectiveRisk];
      if (!fill) continue;

      // Column bounds
      const x = transform.distanceToX(p.distanceNm);
      let xLeft: number, xRight: number;

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

      const colWidth = xRight - xLeft;

      // 1. Background wash
      ctx.fillStyle = fill;
      ctx.fillRect(xLeft, plotArea.top, colWidth, plotArea.height);

      // 2. Diagonal hatching pattern
      const hatchColor = HATCH_COLOR[p.convectiveRisk];
      if (hatchColor) {
        drawHatching(ctx, xLeft, plotArea.top, colWidth, plotArea.height, hatchColor);
      }

      // 3. Top indicator strip
      const stripColor = STRIP_COLOR[p.convectiveRisk];
      if (stripColor) {
        ctx.fillStyle = stripColor;
        ctx.fillRect(xLeft, plotArea.top, colWidth, STRIP_HEIGHT);
      }

      // 4. CB label for moderate+
      const label = RISK_LABEL[p.convectiveRisk];
      if (label && colWidth > 20) {
        drawCBLabel(ctx, x, plotArea.top + plotArea.height * 0.15, p.convectiveRisk);
      }
    }
  },
};

/** Draw diagonal hatching lines within a rectangular region. */
function drawHatching(
  ctx: CanvasRenderingContext2D,
  x: number, y: number,
  w: number, h: number,
  color: string,
): void {
  const spacing = 8;

  ctx.save();
  ctx.beginPath();
  ctx.rect(x, y, w, h);
  ctx.clip();

  ctx.strokeStyle = color;
  ctx.lineWidth = 1;
  ctx.setLineDash([]);

  // Draw diagonal lines from bottom-left to top-right
  const totalSpan = w + h;
  for (let offset = -h; offset < totalSpan; offset += spacing) {
    ctx.beginPath();
    ctx.moveTo(x + offset, y + h);
    ctx.lineTo(x + offset + h, y);
    ctx.stroke();
  }

  ctx.restore();
}

/** Draw a "CB" marker label. */
function drawCBLabel(
  ctx: CanvasRenderingContext2D,
  cx: number,
  cy: number,
  risk: string,
): void {
  const colors: Record<string, string> = {
    moderate: 'rgba(200, 100, 0, 0.7)',
    high: 'rgba(200, 40, 40, 0.8)',
    extreme: 'rgba(150, 20, 20, 0.9)',
  };

  ctx.save();
  ctx.font = 'bold 10px -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';

  // White background pill
  const text = 'CB';
  const metrics = ctx.measureText(text);
  const pw = metrics.width + 6;
  const ph = 14;

  ctx.fillStyle = 'rgba(255, 255, 255, 0.85)';
  ctx.beginPath();
  ctx.roundRect(cx - pw / 2, cy - ph / 2, pw, ph, 3);
  ctx.fill();

  // Text
  ctx.fillStyle = colors[risk] ?? 'rgba(200, 100, 0, 0.8)';
  ctx.fillText(text, cx, cy);
  ctx.restore();
}
