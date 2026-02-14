/** Draw axes, grid lines, and labels for the cross-section plot. */

import type { CoordTransform, VizRouteData } from '../types';
import { altitudeToPressureHpa } from '../scales';

const GRID_COLOR = '#e5e7eb';
const LABEL_COLOR = '#6c757d';
const FONT = '11px -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif';

export function drawAxes(
  ctx: CanvasRenderingContext2D,
  transform: CoordTransform,
  data: VizRouteData,
): void {
  ctx.font = FONT;

  drawAltitudeAxis(ctx, transform, data);
  drawDistanceAxis(ctx, transform, data);
  drawWaypointLines(ctx, transform, data);
  drawPlotBorder(ctx, transform);
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
    ctx.strokeStyle = GRID_COLOR;
    ctx.lineWidth = 0.5;
    ctx.setLineDash([]);
    ctx.beginPath();
    ctx.moveTo(plotArea.left, y);
    ctx.lineTo(plotArea.left + plotArea.width, y);
    ctx.stroke();

    // Left label: altitude in feet
    ctx.fillStyle = LABEL_COLOR;
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
  ctx.fillStyle = LABEL_COLOR;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';

  // Left: "ft"
  ctx.translate(12, plotArea.top + plotArea.height / 2);
  ctx.rotate(-Math.PI / 2);
  ctx.fillText('ft', 0, 0);
  ctx.restore();

  ctx.save();
  ctx.fillStyle = LABEL_COLOR;
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
): void {
  const { plotArea } = transform;
  const maxDist = data.totalDistanceNm;

  // Auto-space distance ticks
  const tickInterval = chooseTickInterval(maxDist);
  const ticks: number[] = [];
  for (let d = 0; d <= maxDist; d += tickInterval) {
    ticks.push(d);
  }
  // Always include the endpoint
  if (ticks[ticks.length - 1] < maxDist - tickInterval * 0.3) {
    ticks.push(maxDist);
  }

  ctx.fillStyle = LABEL_COLOR;
  ctx.textAlign = 'center';
  ctx.textBaseline = 'top';

  for (const d of ticks) {
    const x = transform.distanceToX(d);

    // Vertical grid line (light)
    ctx.strokeStyle = GRID_COLOR;
    ctx.lineWidth = 0.5;
    ctx.setLineDash([]);
    ctx.beginPath();
    ctx.moveTo(x, plotArea.top);
    ctx.lineTo(x, plotArea.top + plotArea.height);
    ctx.stroke();

    // Bottom label
    ctx.fillText(`${Math.round(d)} nm`, x, plotArea.top + plotArea.height + 6);
  }

  // Time labels at waypoint positions
  ctx.textBaseline = 'top';
  for (const wp of data.waypointMarkers) {
    const x = transform.distanceToX(wp.distanceNm);
    // Find the nearest point to get the time
    const nearest = findNearestPoint(data, wp.distanceNm);
    if (nearest) {
      const timeStr = formatTimeUTC(nearest.time);
      ctx.fillStyle = LABEL_COLOR;
      ctx.fillText(timeStr, x, plotArea.top + plotArea.height + 20);
    }
  }
}

function drawWaypointLines(
  ctx: CanvasRenderingContext2D,
  transform: CoordTransform,
  data: VizRouteData,
): void {
  const { plotArea } = transform;

  for (const wp of data.waypointMarkers) {
    const x = transform.distanceToX(wp.distanceNm);

    // Vertical dashed line
    ctx.strokeStyle = '#adb5bd';
    ctx.lineWidth = 1;
    ctx.setLineDash([4, 4]);
    ctx.beginPath();
    ctx.moveTo(x, plotArea.top);
    ctx.lineTo(x, plotArea.top + plotArea.height);
    ctx.stroke();
    ctx.setLineDash([]);

    // ICAO label at top
    ctx.fillStyle = '#495057';
    ctx.font = 'bold 11px -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif';
    ctx.textAlign = 'center';
    ctx.textBaseline = 'bottom';
    ctx.fillText(wp.icao, x, plotArea.top - 3);
    ctx.font = FONT;
  }
}

function drawPlotBorder(
  ctx: CanvasRenderingContext2D,
  transform: CoordTransform,
): void {
  const { plotArea } = transform;
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

function findNearestPoint(data: VizRouteData, distanceNm: number) {
  let nearest = data.points[0];
  let minDelta = Math.abs(nearest.distanceNm - distanceNm);
  for (const pt of data.points) {
    const delta = Math.abs(pt.distanceNm - distanceNm);
    if (delta < minDelta) {
      minDelta = delta;
      nearest = pt;
    }
  }
  return nearest;
}

function formatTimeUTC(isoTime: string): string {
  try {
    const d = new Date(isoTime);
    return d.toISOString().slice(11, 16) + 'Z';
  } catch {
    return '';
  }
}
