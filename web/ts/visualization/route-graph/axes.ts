/** Draw Y-axes and X grid lines for the route graph. */

import type { PlotArea, VizRouteData } from '../types';
import type { RouteGraphMetric } from './metrics';

const GRID_COLOR = 'rgba(0, 0, 0, 0.08)';
const LABEL_COLOR = '#6c757d';
const FONT = '10px -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif';
const ZERO_LINE_COLOR = 'rgba(0, 0, 0, 0.25)';

export interface YAxisScale {
  min: number;
  max: number;
  valueToY: (v: number) => number;
}

/** Compute a nice Y-axis scale for a metric's data values. */
export function computeYScale(
  values: (number | null)[],
  metric: RouteGraphMetric,
  plotArea: PlotArea,
): YAxisScale {
  const nums = values.filter((v): v is number => v !== null);

  let min: number;
  let max: number;

  if (metric.suggestedRange) {
    [min, max] = metric.suggestedRange;
    // Expand to fit data if it exceeds the suggested range
    if (nums.length > 0) {
      const dataMin = Math.min(...nums);
      const dataMax = Math.max(...nums);
      if (dataMin < min) min = dataMin;
      if (dataMax > max) max = dataMax;
    }
  } else if (nums.length === 0) {
    min = 0;
    max = 1;
  } else {
    min = Math.min(...nums);
    max = Math.max(...nums);
  }

  // Add padding (10% each side)
  const range = max - min || 1;
  const padding = range * 0.1;
  min = min - padding;
  max = max + padding;

  // If showZeroLine, ensure zero is within range
  if (metric.showZeroLine) {
    if (min > 0) min = -padding;
    if (max < 0) max = padding;
  }

  // Round to nice numbers
  const niceStep = niceTickInterval(max - min, 4);
  min = Math.floor(min / niceStep) * niceStep;
  max = Math.ceil(max / niceStep) * niceStep;
  if (min === max) max = min + niceStep;

  return {
    min,
    max,
    valueToY: (v: number) => plotArea.top + (1 - (v - min) / (max - min)) * plotArea.height,
  };
}

/** Draw the left Y-axis with ticks and labels. */
export function drawLeftYAxis(
  ctx: CanvasRenderingContext2D,
  scale: YAxisScale,
  metric: RouteGraphMetric,
  plotArea: PlotArea,
): void {
  ctx.font = FONT;
  ctx.fillStyle = metric.color;
  ctx.strokeStyle = metric.color;

  const step = niceTickInterval(scale.max - scale.min, 4);
  const start = Math.ceil(scale.min / step) * step;

  ctx.textAlign = 'right';
  ctx.textBaseline = 'middle';

  for (let v = start; v <= scale.max + step * 0.01; v += step) {
    const y = scale.valueToY(v);
    if (y < plotArea.top - 1 || y > plotArea.top + plotArea.height + 1) continue;
    ctx.fillText(formatTick(v), plotArea.left - 5, y);
  }

  // Unit label
  ctx.save();
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.translate(10, plotArea.top + plotArea.height / 2);
  ctx.rotate(-Math.PI / 2);
  ctx.fillText(metric.unit, 0, 0);
  ctx.restore();
}

/** Draw the right Y-axis with ticks and labels. */
export function drawRightYAxis(
  ctx: CanvasRenderingContext2D,
  scale: YAxisScale,
  metric: RouteGraphMetric,
  plotArea: PlotArea,
): void {
  ctx.font = FONT;
  ctx.fillStyle = metric.color;

  const step = niceTickInterval(scale.max - scale.min, 4);
  const start = Math.ceil(scale.min / step) * step;

  ctx.textAlign = 'left';
  ctx.textBaseline = 'middle';

  for (let v = start; v <= scale.max + step * 0.01; v += step) {
    const y = scale.valueToY(v);
    if (y < plotArea.top - 1 || y > plotArea.top + plotArea.height + 1) continue;
    ctx.fillText(formatTick(v), plotArea.left + plotArea.width + 5, y);
  }

  // Unit label
  ctx.save();
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  const rightEdge = plotArea.left + plotArea.width + 44;
  ctx.translate(rightEdge, plotArea.top + plotArea.height / 2);
  ctx.rotate(-Math.PI / 2);
  ctx.fillText(metric.unit, 0, 0);
  ctx.restore();
}

/** Draw X-axis grid lines (aligned with cross-section distance ticks). */
export function drawXGrid(
  ctx: CanvasRenderingContext2D,
  data: VizRouteData,
  plotArea: PlotArea,
  distanceToX: (d: number) => number,
): void {
  const maxDist = data.totalDistanceNm;
  const tickInterval = chooseTickInterval(maxDist);

  ctx.strokeStyle = GRID_COLOR;
  ctx.lineWidth = 0.5;
  ctx.setLineDash([]);

  for (let d = 0; d <= maxDist; d += tickInterval) {
    const x = distanceToX(d);
    ctx.beginPath();
    ctx.moveTo(x, plotArea.top);
    ctx.lineTo(x, plotArea.top + plotArea.height);
    ctx.stroke();
  }

  // Waypoint markers
  ctx.strokeStyle = 'rgba(0, 0, 0, 0.12)';
  ctx.lineWidth = 1;
  ctx.setLineDash([3, 3]);
  for (const wp of data.waypointMarkers) {
    const x = distanceToX(wp.distanceNm);
    ctx.beginPath();
    ctx.moveTo(x, plotArea.top);
    ctx.lineTo(x, plotArea.top + plotArea.height);
    ctx.stroke();
  }
  ctx.setLineDash([]);
}

/** Draw a horizontal zero-reference line. */
export function drawZeroLine(
  ctx: CanvasRenderingContext2D,
  scale: YAxisScale,
  plotArea: PlotArea,
): void {
  if (scale.min > 0 || scale.max < 0) return;
  const y = scale.valueToY(0);
  ctx.strokeStyle = ZERO_LINE_COLOR;
  ctx.lineWidth = 1;
  ctx.setLineDash([4, 3]);
  ctx.beginPath();
  ctx.moveTo(plotArea.left, y);
  ctx.lineTo(plotArea.left + plotArea.width, y);
  ctx.stroke();
  ctx.setLineDash([]);
}

/** Draw the plot border. */
export function drawBorder(
  ctx: CanvasRenderingContext2D,
  plotArea: PlotArea,
): void {
  ctx.strokeStyle = '#adb5bd';
  ctx.lineWidth = 1;
  ctx.setLineDash([]);
  ctx.strokeRect(plotArea.left, plotArea.top, plotArea.width, plotArea.height);
}

// --- Helpers ---

function chooseTickInterval(maxDistance: number): number {
  if (maxDistance <= 50) return 10;
  if (maxDistance <= 150) return 25;
  if (maxDistance <= 300) return 50;
  if (maxDistance <= 600) return 100;
  return 200;
}

function niceTickInterval(range: number, targetTicks: number): number {
  const rough = range / targetTicks;
  const pow = Math.pow(10, Math.floor(Math.log10(rough)));
  const normalized = rough / pow;
  let nice: number;
  if (normalized <= 1.5) nice = 1;
  else if (normalized <= 3.5) nice = 2;
  else if (normalized <= 7.5) nice = 5;
  else nice = 10;
  return nice * pow;
}

function formatTick(v: number): string {
  if (Math.abs(v) >= 1000) return v.toLocaleString();
  if (Number.isInteger(v)) return v.toString();
  return v.toFixed(1);
}
