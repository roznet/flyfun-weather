/** Convective risk visualization: tower columns from LCL to EL + hatching + CB labels.
 *
 * When LCL (cloud base) and EL (cloud top) are available, draws a bounded
 * convective tower. Falls back to full-height column when altitude data is missing.
 */

import type { CrossSectionLayer, CoordTransform, VizRouteData, VizPoint, RenderMode } from '../../types';

// ---- Color palettes ----

/** Light background wash (full column) */
const BG_WASH: Record<string, string> = {
  marginal: 'rgba(200, 200, 200, 0.04)',
  low: 'rgba(255, 235, 59, 0.06)',
  moderate: 'rgba(255, 152, 0, 0.08)',
  high: 'rgba(220, 53, 69, 0.10)',
  extreme: 'rgba(183, 28, 28, 0.14)',
};

/** Tower fill color (LCL→EL column) */
const TOWER_FILL: Record<string, string> = {
  marginal: 'rgba(180, 180, 180, 0.15)',
  low: 'rgba(255, 235, 59, 0.18)',
  moderate: 'rgba(255, 152, 0, 0.25)',
  high: 'rgba(220, 53, 69, 0.30)',
  extreme: 'rgba(183, 28, 28, 0.35)',
};

/** Hatching line color */
const HATCH_COLOR: Record<string, string> = {
  marginal: 'rgba(140, 140, 140, 0.15)',
  low: 'rgba(180, 160, 0, 0.20)',
  moderate: 'rgba(200, 100, 0, 0.35)',
  high: 'rgba(200, 40, 40, 0.40)',
  extreme: 'rgba(150, 20, 20, 0.50)',
};

/** Top-of-tower strip color */
const STRIP_COLOR: Record<string, string> = {
  marginal: 'rgba(160, 160, 160, 0.4)',
  low: 'rgba(255, 235, 59, 0.5)',
  moderate: 'rgba(255, 152, 0, 0.75)',
  high: 'rgba(220, 53, 69, 0.85)',
  extreme: 'rgba(183, 28, 28, 0.9)',
};

/** Tower outline/edge stroke */
const EDGE_COLOR: Record<string, string> = {
  marginal: 'rgba(140, 140, 140, 0.25)',
  low: 'rgba(180, 160, 0, 0.3)',
  moderate: 'rgba(200, 100, 0, 0.5)',
  high: 'rgba(200, 40, 40, 0.6)',
  extreme: 'rgba(150, 20, 20, 0.7)',
};

const STRIP_HEIGHT = 5;

/** Minimum tower height in feet to consider the thermodynamic EL reliable. */
const MIN_RELIABLE_TOWER_FT = 3000;

/**
 * Estimate a reasonable visual tower top when the thermodynamic EL is
 * unreliably close to LFC (common with very low CAPE on coarse pressure levels).
 *
 * For shallow convection, the freezing level is a good proxy for cloud top
 * (Cu typically top out near or just above 0°C in low-CAPE environments).
 * For deeper convection (higher risk), use -10°C or -20°C level.
 */
function estimateTowerTop(p: VizPoint, baseFt: number, thermodynamicElFt: number): number {
  const towerDepth = thermodynamicElFt - baseFt;

  // If the thermodynamic tower is reasonably deep, trust it
  if (towerDepth >= MIN_RELIABLE_TOWER_FT) return thermodynamicElFt;

  const alt = p.altitudeLines;
  const risk = p.convectiveRisk;

  // For marginal/low risk: shallow Cu tops out near or above freezing level
  if (risk === 'marginal' || risk === 'low') {
    if (alt.freezingLevelFt != null) {
      // Shallow convection: use freezing level + 2000ft buffer
      return Math.max(thermodynamicElFt, alt.freezingLevelFt + 2000);
    }
  }

  // For moderate+: use -10°C or -20°C level
  if (risk === 'moderate' || risk === 'high' || risk === 'extreme') {
    if (alt.minus20cLevelFt != null) return Math.max(thermodynamicElFt, alt.minus20cLevelFt);
    if (alt.minus10cLevelFt != null) return Math.max(thermodynamicElFt, alt.minus10cLevelFt);
  }

  // Fallback: at least 4000ft above base
  return Math.max(thermodynamicElFt, baseFt + 4000);
}

export const convectiveBgLayer: CrossSectionLayer = {
  id: 'convective-bg',
  name: 'Convective Risk',
  group: 'convection',
  defaultEnabled: true,
  metricId: 'convective_risk',

  render(ctx: CanvasRenderingContext2D, transform: CoordTransform, data: VizRouteData, _mode: RenderMode) {
    const { plotArea } = transform;

    for (let i = 0; i < data.points.length; i++) {
      const p = data.points[i];
      if (p.convectiveRisk === 'none') continue;

      // Column x-bounds (midpoint between neighbors)
      const x = transform.distanceToX(p.distanceNm);
      const xLeft = i === 0
        ? plotArea.left
        : (transform.distanceToX(data.points[i - 1].distanceNm) + x) / 2;
      const xRight = i === data.points.length - 1
        ? plotArea.left + plotArea.width
        : (x + transform.distanceToX(data.points[i + 1].distanceNm)) / 2;
      const colWidth = xRight - xLeft;

      const hasTowerBounds = p.altitudeLines.lclAltitudeFt != null && p.altitudeLines.elAltitudeFt != null;

      if (hasTowerBounds) {
        drawTower(ctx, transform, p, xLeft, xRight, colWidth, plotArea);
      } else {
        drawFullHeightColumn(ctx, p, xLeft, colWidth, plotArea);
      }
    }
  },
};

/** Draw a bounded convective tower from LCL (base) to EL (top). */
function drawTower(
  ctx: CanvasRenderingContext2D,
  transform: CoordTransform,
  p: VizPoint,
  xLeft: number,
  xRight: number,
  colWidth: number,
  plotArea: { left: number; top: number; width: number; height: number },
): void {
  const risk = p.convectiveRisk;
  const lclFt = p.altitudeLines.lclAltitudeFt!;
  const elFt = p.altitudeLines.elAltitudeFt!;

  // Use LFC if available (free convection base), otherwise LCL
  const baseFt = p.altitudeLines.lfcAltitudeFt ?? lclFt;
  // Estimate visual top — MetPy EL can be unreliably shallow on coarse levels
  const topFt = estimateTowerTop(p, baseFt, elFt);

  const yBase = transform.altitudeToY(baseFt);
  const yTop = transform.altitudeToY(topFt);
  const towerHeight = yBase - yTop; // Y is inverted (top < base)

  if (towerHeight <= 0) return;

  // 1. Very subtle full-height background wash
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

  // 3. Diagonal hatching within tower bounds
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

  // 5. Anvil top indicator strip at EL
  const stripColor = STRIP_COLOR[risk];
  if (stripColor) {
    // Draw wider anvil at top (extends 20% beyond column on each side)
    const anvilExtend = Math.min(colWidth * 0.2, 8);
    ctx.fillStyle = stripColor;
    ctx.fillRect(xLeft - anvilExtend, yTop, colWidth + anvilExtend * 2, STRIP_HEIGHT);
  }

  // 6. CB label inside tower (moderate+)
  if (risk !== 'low' && colWidth > 18) {
    const cx = (xLeft + xRight) / 2;
    const cy = yTop + towerHeight * 0.3; // Upper third of tower
    drawCBLabel(ctx, cx, cy, risk);
  }
}

/** Fallback: full-height column when LCL/EL not available. */
function drawFullHeightColumn(
  ctx: CanvasRenderingContext2D,
  p: VizPoint,
  xLeft: number,
  colWidth: number,
  plotArea: { left: number; top: number; width: number; height: number },
): void {
  const risk = p.convectiveRisk;

  // Lighter fill for the unbounded case
  const fill = TOWER_FILL[risk];
  if (fill) {
    ctx.fillStyle = fill;
    ctx.fillRect(xLeft, plotArea.top, colWidth, plotArea.height);
  }

  const hatchColor = HATCH_COLOR[risk];
  if (hatchColor) {
    drawHatching(ctx, xLeft, plotArea.top, colWidth, plotArea.height, hatchColor);
  }

  // Top strip at plot top
  const stripColor = STRIP_COLOR[risk];
  if (stripColor) {
    ctx.fillStyle = stripColor;
    ctx.fillRect(xLeft, plotArea.top, colWidth, STRIP_HEIGHT);
  }
}

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

  const totalSpan = w + h;
  for (let offset = -h; offset < totalSpan; offset += spacing) {
    ctx.beginPath();
    ctx.moveTo(x + offset, y + h);
    ctx.lineTo(x + offset + h, y);
    ctx.stroke();
  }

  ctx.restore();
}

/** Draw a "CB" marker label with risk-colored pill. */
function drawCBLabel(
  ctx: CanvasRenderingContext2D,
  cx: number,
  cy: number,
  risk: string,
): void {
  const colors: Record<string, string> = {
    moderate: 'rgba(200, 100, 0, 0.8)',
    high: 'rgba(200, 40, 40, 0.9)',
    extreme: 'rgba(150, 20, 20, 0.95)',
  };

  ctx.save();
  ctx.font = 'bold 10px -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';

  const text = 'CB';
  const metrics = ctx.measureText(text);
  const pw = metrics.width + 6;
  const ph = 14;

  // White background pill
  ctx.fillStyle = 'rgba(255, 255, 255, 0.9)';
  ctx.beginPath();
  ctx.roundRect(cx - pw / 2, cy - ph / 2, pw, ph, 3);
  ctx.fill();

  // Text
  ctx.fillStyle = colors[risk] ?? 'rgba(200, 100, 0, 0.8)';
  ctx.fillText(text, cx, cy);
  ctx.restore();
}
