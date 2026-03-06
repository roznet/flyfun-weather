/** Thermo convective risk visualization: tower columns from LFC to EL + hatching + CB labels.
 *
 * Uses thermodynamic base_ft (LFC or LCL fallback) and top_ft (EL) from the
 * ConvectiveAssessment. Skips points where bounds are missing.
 */

import type { CrossSectionLayer, CoordTransform, VizRouteData, VizPoint } from '../../types';
import { getActiveTheme } from '../theme';

// ---- Color palette getters (shared with NWP layer for visual consistency) ----

export function getBgWash(): Record<string, string> { return getActiveTheme().convective.bgWash; }
export function getTowerFill(): Record<string, string> { return getActiveTheme().convective.towerFill; }
export function getHatchColor(): Record<string, string> { return getActiveTheme().convective.hatchColor; }
export function getStripColor(): Record<string, string> { return getActiveTheme().convective.stripColor; }
export function getEdgeColor(): Record<string, string> { return getActiveTheme().convective.edgeColor; }

export const STRIP_HEIGHT = 5;

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

  // For low risk: shallow Cu tops out near or above freezing level
  if (risk === 'low') {
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

export const thermoConvectiveBgLayer: CrossSectionLayer = {
  id: 'thermo-convective-bg',
  name: 'Thermo Convective',
  group: 'convection',
  defaultEnabled: true,
  metricId: 'convective_risk',

  render(ctx: CanvasRenderingContext2D, transform: CoordTransform, data: VizRouteData) {
    const { plotArea } = transform;

    for (let i = 0; i < data.points.length; i++) {
      const p = data.points[i];
      if (p.convectiveRisk === 'none' || p.convectiveRisk === 'marginal') continue;

      // Column x-bounds (midpoint between neighbors)
      const x = transform.distanceToX(p.distanceNm);
      const xLeft = i === 0
        ? plotArea.left
        : (transform.distanceToX(data.points[i - 1].distanceNm) + x) / 2;
      const xRight = i === data.points.length - 1
        ? plotArea.left + plotArea.width
        : (x + transform.distanceToX(data.points[i + 1].distanceNm)) / 2;
      const colWidth = xRight - xLeft;

      // Skip if no tower bounds — don't draw misleading full-height columns
      if (p.convectiveBaseFt == null || p.convectiveTopFt == null) continue;

      drawTower(ctx, transform, p, xLeft, xRight, colWidth, plotArea);
    }
  },
};

/** Draw a bounded convective tower from base to top. */
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
  const rawBaseFt = p.convectiveBaseFt!;
  const rawTopFt = p.convectiveTopFt!;

  // Estimate visual top — MetPy EL can be unreliably shallow on coarse levels
  const topFt = estimateTowerTop(p, rawBaseFt, rawTopFt);

  const yBase = transform.altitudeToY(rawBaseFt);
  const yTop = transform.altitudeToY(topFt);
  const towerHeight = yBase - yTop; // Y is inverted (top < base)

  if (towerHeight <= 0) return;

  const theme = getActiveTheme().convective;

  // 1. Very subtle full-height background wash
  const bgWash = theme.bgWash[risk];
  if (bgWash) {
    ctx.fillStyle = bgWash;
    ctx.fillRect(xLeft, plotArea.top, colWidth, plotArea.height);
  }

  // 2. Tower body fill
  const towerFill = theme.towerFill[risk];
  if (towerFill) {
    ctx.fillStyle = towerFill;
    ctx.fillRect(xLeft, yTop, colWidth, towerHeight);
  }

  // 3. Diagonal hatching within tower bounds
  const hatchColor = theme.hatchColor[risk];
  if (hatchColor) {
    drawHatching(ctx, xLeft, yTop, colWidth, towerHeight, hatchColor);
  }

  // 4. Tower edge outline
  const edgeColor = theme.edgeColor[risk];
  if (edgeColor) {
    ctx.strokeStyle = edgeColor;
    ctx.lineWidth = 1.5;
    ctx.setLineDash([]);
    ctx.strokeRect(xLeft + 0.5, yTop + 0.5, colWidth - 1, towerHeight - 1);
  }

  // 5. Anvil top indicator strip at EL
  const stripColor = theme.stripColor[risk];
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

/** Draw diagonal hatching lines within a rectangular region. */
export function drawHatching(
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
export function drawCBLabel(
  ctx: CanvasRenderingContext2D,
  cx: number,
  cy: number,
  risk: string,
): void {
  const colors = getActiveTheme().convective.cbLabelColor;

  ctx.save();
  ctx.font = 'bold 10px -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';

  const text = 'CB';
  const metrics = ctx.measureText(text);
  const pw = metrics.width + 6;
  const ph = 14;

  // Background pill matching sky
  const theme = getActiveTheme();
  ctx.fillStyle = theme.sky.background + 'e6';
  ctx.beginPath();
  ctx.roundRect(cx - pw / 2, cy - ph / 2, pw, ph, 3);
  ctx.fill();

  // Text
  ctx.fillStyle = colors[risk] ?? 'rgba(200, 100, 0, 0.8)';
  ctx.fillText(text, cx, cy);
  ctx.restore();
}
