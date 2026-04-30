/** Draw axes, grid lines, and labels for the cross-section plot. */

import type { CoordTransform, VizRouteData } from '../types';
import { altitudeToPressureHpa } from '../scales';
import { chooseDistanceTickInterval, findNearestPointIndex, isDarkTheme } from '../interaction-utils';
import { getActiveTheme } from './theme';
function labelColor(): string { return isDarkTheme() ? '#9ca3af' : '#6c757d'; }
function waypointLabelColor(): string { return isDarkTheme() ? '#d1d5db' : '#495057'; }
function borderColor(): string { return isDarkTheme() ? '#4a4a5a' : '#adb5bd'; }
const FONT = '11px -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif';
const ICAO_FONT = 'bold 11px -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif';
const LABEL_PAD_PX = 4;

export function drawAxes(
  ctx: CanvasRenderingContext2D,
  transform: CoordTransform,
  data: VizRouteData,
): void {
  ctx.font = FONT;

  const visibleWaypoints = pickVisibleWaypointLabels(ctx, transform, data);

  drawAltitudeAxis(ctx, transform, data);
  drawDistanceAxis(ctx, transform, data, visibleWaypoints);
  drawWaypointLines(ctx, transform, data, visibleWaypoints);
  drawPlotBorder(ctx, transform);
}

/** Greedy left-to-right culling: keep first + last waypoints, plus any whose
 *  label box clears the previous kept label. Returns boolean[] aligned with
 *  data.waypointMarkers. Used identically for both ICAO row (top) and time row
 *  (bottom) so the two stay vertically aligned. */
function pickVisibleWaypointLabels(
  ctx: CanvasRenderingContext2D,
  transform: CoordTransform,
  data: VizRouteData,
): boolean[] {
  const wps = data.waypointMarkers;
  const n = wps.length;
  const visible = new Array(n).fill(false);
  if (n === 0) return visible;

  ctx.save();
  const xs: number[] = [];
  const halfWidths: number[] = [];
  for (const wp of wps) {
    ctx.font = ICAO_FONT;
    const wIcao = ctx.measureText(wp.icao).width;
    let wTime = 0;
    const nearestIdx = findNearestPointIndex(data.points, wp.distanceNm);
    const nearest = data.points[nearestIdx];
    if (nearest) {
      ctx.font = FONT;
      wTime = ctx.measureText(formatTimeUTC(nearest.time)).width;
    }
    halfWidths.push(Math.max(wIcao, wTime) / 2 + LABEL_PAD_PX);
    xs.push(transform.distanceToX(wp.distanceNm));
  }
  ctx.restore();

  visible[0] = true;
  if (n > 1) visible[n - 1] = true;

  const lastIdx = n - 1;
  let lastKept = 0;
  for (let i = 1; i < n - 1; i++) {
    const fitsAfterPrev = xs[i] - halfWidths[i] >= xs[lastKept] + halfWidths[lastKept];
    const fitsBeforeLast = xs[i] + halfWidths[i] <= xs[lastIdx] - halfWidths[lastIdx];
    if (fitsAfterPrev && fitsBeforeLast) {
      visible[i] = true;
      lastKept = i;
    }
  }
  return visible;
}

function drawAltitudeAxis(
  ctx: CanvasRenderingContext2D,
  transform: CoordTransform,
  data: VizRouteData,
): void {
  const { plotArea } = transform;
  const maxAlt = data.flightCeilingFt;

  // Determine tick interval based on altitude range
  const tickInterval = maxAlt > 15000 ? 5000 : maxAlt > 8000 ? 2000 : 1000;
  const ticks: number[] = [];
  for (let alt = 0; alt <= maxAlt; alt += tickInterval) {
    ticks.push(alt);
  }

  for (const alt of ticks) {
    const y = transform.altitudeToY(alt);

    // Horizontal grid line
    ctx.strokeStyle = getActiveTheme().axes.gridColor;
    ctx.lineWidth = 0.5;
    ctx.setLineDash([]);
    ctx.beginPath();
    ctx.moveTo(plotArea.left, y);
    ctx.lineTo(plotArea.left + plotArea.width, y);
    ctx.stroke();

    // Left label: altitude in feet
    ctx.fillStyle = labelColor();
    ctx.textAlign = 'right';
    ctx.textBaseline = 'middle';
    const label = alt >= 10000 ? `FL${Math.round(alt / 100)}` : `${alt.toLocaleString()}`;
    ctx.fillText(label, plotArea.left - 6, y);

    // Right label: pressure in hPa
    const pressure = altitudeToPressureHpa(alt);
    ctx.textAlign = 'left';
    ctx.fillText(`${Math.round(pressure)}`, plotArea.left + plotArea.width + 6, y);
  }

  // Axis titles
  ctx.save();
  ctx.fillStyle = labelColor();
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';

  // Left: "ft"
  ctx.translate(12, plotArea.top + plotArea.height / 2);
  ctx.rotate(-Math.PI / 2);
  ctx.fillText('ft', 0, 0);
  ctx.restore();

  ctx.save();
  ctx.fillStyle = labelColor();
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';

  // Right: "hPa"
  ctx.translate(plotArea.left + plotArea.width + PRESSURE_MARGIN, plotArea.top + plotArea.height / 2);
  ctx.rotate(-Math.PI / 2);
  ctx.fillText('hPa', 0, 0);
  ctx.restore();
}

const PRESSURE_MARGIN = 44;

function drawDistanceAxis(
  ctx: CanvasRenderingContext2D,
  transform: CoordTransform,
  data: VizRouteData,
  visibleWaypoints: boolean[],
): void {
  const { plotArea } = transform;
  const maxDist = data.totalDistanceNm;

  // Auto-space distance ticks
  const tickInterval = chooseDistanceTickInterval(maxDist);
  const ticks: number[] = [];
  for (let d = 0; d <= maxDist; d += tickInterval) {
    ticks.push(d);
  }
  // Always include the endpoint
  if (ticks[ticks.length - 1] < maxDist - tickInterval * 0.3) {
    ticks.push(maxDist);
  }

  ctx.fillStyle = labelColor();
  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';

  for (const d of ticks) {
    const x = transform.distanceToX(d);

    // Vertical grid line (light)
    ctx.strokeStyle = getActiveTheme().axes.gridColor;
    ctx.lineWidth = 0.5;
    ctx.setLineDash([]);
    ctx.beginPath();
    ctx.moveTo(x, plotArea.top);
    ctx.lineTo(x, plotArea.top + plotArea.height);
    ctx.stroke();

    // Bottom label
    ctx.fillText(`${Math.round(d)} nm`, x, plotArea.top + plotArea.height + 6);
  }

  // Time labels at waypoint positions (skip overlapping ones)
  ctx.textBaseline = 'top';
  for (let i = 0; i < data.waypointMarkers.length; i++) {
    if (!visibleWaypoints[i]) continue;
    const wp = data.waypointMarkers[i];
    const x = transform.distanceToX(wp.distanceNm);
    const nearestIdx = findNearestPointIndex(data.points, wp.distanceNm);
    const nearest = data.points[nearestIdx];
    if (nearest) {
      const timeStr = formatTimeUTC(nearest.time);
      ctx.fillStyle = labelColor();
      ctx.fillText(timeStr, x, plotArea.top + plotArea.height + 20);
    }
  }
}

function drawWaypointLines(
  ctx: CanvasRenderingContext2D,
  transform: CoordTransform,
  data: VizRouteData,
  visibleWaypoints: boolean[],
): void {
  const { plotArea } = transform;

  // Dashed lines at every waypoint (geometry stays visible for hover)
  ctx.strokeStyle = getActiveTheme().axes.waypointLineColor;
  ctx.lineWidth = 1;
  ctx.setLineDash([4, 4]);
  for (const wp of data.waypointMarkers) {
    const x = transform.distanceToX(wp.distanceNm);
    ctx.beginPath();
    ctx.moveTo(x, plotArea.top);
    ctx.lineTo(x, plotArea.top + plotArea.height);
    ctx.stroke();
  }
  ctx.setLineDash([]);

  // ICAO labels — only at non-overlapping positions
  ctx.fillStyle = waypointLabelColor();
  ctx.font = ICAO_FONT;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'bottom';
  for (let i = 0; i < data.waypointMarkers.length; i++) {
    if (!visibleWaypoints[i]) continue;
    const wp = data.waypointMarkers[i];
    const x = transform.distanceToX(wp.distanceNm);
    ctx.fillText(wp.icao, x, plotArea.top - 3);
  }
  ctx.font = FONT;
}

function drawPlotBorder(
  ctx: CanvasRenderingContext2D,
  transform: CoordTransform,
): void {
  const { plotArea } = transform;
  ctx.strokeStyle = borderColor();
  ctx.lineWidth = 1;
  ctx.setLineDash([]);
  ctx.strokeRect(plotArea.left, plotArea.top, plotArea.width, plotArea.height);
}

// --- Helpers ---

function formatTimeUTC(isoTime: string): string {
  try {
    const d = new Date(isoTime);
    return d.toISOString().slice(11, 16) + 'Z';
  } catch {
    return '';
  }
}
