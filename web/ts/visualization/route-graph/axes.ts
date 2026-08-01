/** Draw Y-axes and X grid lines for the route graph. */

import type { PlotArea, VizRouteData } from '../types';
import type { MetricSample, RouteGraphMetric } from './metrics';
import { MARGIN } from './constants';
import { chooseDistanceTickInterval, isDarkTheme } from '../interaction-utils';

function gridColor(): string { return isDarkTheme() ? 'rgba(255, 255, 255, 0.1)' : 'rgba(0, 0, 0, 0.08)'; }
function labelColor(): string { return isDarkTheme() ? '#9ca3af' : '#6c757d'; }
function zeroLineColor(): string { return isDarkTheme() ? 'rgba(255, 255, 255, 0.25)' : 'rgba(0, 0, 0, 0.25)'; }
function borderColor(): string { return isDarkTheme() ? '#4a4a5a' : '#adb5bd'; }
function waypointGridColor(): string { return isDarkTheme() ? 'rgba(255, 255, 255, 0.15)' : 'rgba(0, 0, 0, 0.12)'; }
const FONT = '10px -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif';

export interface YAxisScale {
  min: number;
  max: number;
  valueToY: (v: number) => number;
}

/** Compute a nice Y-axis scale for a metric's sampled values. */
export function computeYScale(
  samples: MetricSample[],
  metric: RouteGraphMetric,
  plotArea: PlotArea,
): YAxisScale {
  // Only plottable values drive the scale. Above-scale samples deliberately do
  // not — expanding the axis to fit them is exactly what the cap exists to avoid.
  const nums = samples.filter((s) => s.kind === 'value').map((s) => s.value);

  let min: number;
  let max: number;

  if (metric.aboveScale && metric.suggestedRange) {
    // Pinned axis: use the range verbatim, so the top tick *is* the cap and a
    // marker riding the top edge unambiguously reads "above this number".
    return {
      min: metric.suggestedRange[0],
      max: metric.suggestedRange[1],
      valueToY: (v: number) =>
        plotArea.top +
        (1 - (v - metric.suggestedRange![0]) / (metric.suggestedRange![1] - metric.suggestedRange![0])) *
          plotArea.height,
    };
  }

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

    // Horizontal grid line
    ctx.save();
    ctx.strokeStyle = gridColor();
    ctx.lineWidth = 0.5;
    ctx.setLineDash([]);
    ctx.beginPath();
    ctx.moveTo(plotArea.left, y);
    ctx.lineTo(plotArea.left + plotArea.width, y);
    ctx.stroke();
    ctx.restore();

    ctx.fillStyle = metric.color;
    ctx.fillText(formatTick(v), plotArea.left - 5, y);
  }

  // Unit label (rotated, positioned within the left margin)
  ctx.save();
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  ctx.fillStyle = metric.color;
  const labelInset = Math.round(MARGIN.left / 6);
  ctx.translate(labelInset, plotArea.top + plotArea.height / 2);
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

  // Unit label (rotated, positioned within the right margin)
  ctx.save();
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  const rightLabelX = plotArea.left + plotArea.width + MARGIN.right - 6;
  ctx.translate(rightLabelX, plotArea.top + plotArea.height / 2);
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
  const tickInterval = chooseDistanceTickInterval(maxDist);

  ctx.strokeStyle = gridColor();
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
  ctx.strokeStyle = waypointGridColor();
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

/** Draw a horizontal zero-reference line with optional above/below labels. */
export function drawZeroLine(
  ctx: CanvasRenderingContext2D,
  scale: YAxisScale,
  plotArea: PlotArea,
  labels?: [string, string],
): void {
  if (scale.min > 0 || scale.max < 0) return;
  const y = scale.valueToY(0);
  ctx.strokeStyle = zeroLineColor();
  ctx.lineWidth = 1;
  ctx.setLineDash([4, 3]);
  ctx.beginPath();
  ctx.moveTo(plotArea.left, y);
  ctx.lineTo(plotArea.left + plotArea.width, y);
  ctx.stroke();
  ctx.setLineDash([]);

  if (labels) {
    ctx.save();
    ctx.font = '9px -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif';
    ctx.fillStyle = labelColor();
    const x = plotArea.left + 4;
    // Above zero line
    ctx.textBaseline = 'bottom';
    ctx.fillText(labels[0], x, y - 2);
    // Below zero line
    ctx.textBaseline = 'top';
    ctx.fillText(labels[1], x, y + 2);
    ctx.restore();
  }
}

/** Draw the plot border. */
export function drawBorder(
  ctx: CanvasRenderingContext2D,
  plotArea: PlotArea,
): void {
  ctx.strokeStyle = borderColor();
  ctx.lineWidth = 1;
  ctx.setLineDash([]);
  ctx.strokeRect(plotArea.left, plotArea.top, plotArea.width, plotArea.height);
}

// --- Helpers ---

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
