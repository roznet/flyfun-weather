/** Drawing primitives for cross-section layers: smooth splines and column steps. */

import type { CoordTransform } from '../../types';

export interface PointData {
  distance: number;
  value: number | null;
}

export interface BandPointData {
  distance: number;
  base: number | null;
  top: number | null;
}

// --- Smooth line: Fritsch-Carlson monotone cubic spline ---

export function drawSmoothLine(
  ctx: CanvasRenderingContext2D,
  points: PointData[],
  transform: CoordTransform,
  style: { color: string; width: number; dash?: number[] },
): void {
  ctx.strokeStyle = style.color;
  ctx.lineWidth = style.width;
  ctx.setLineDash(style.dash ?? []);

  // Split into segments of non-null consecutive points
  const segments = splitNonNull(points);

  for (const seg of segments) {
    if (seg.length < 2) {
      // Single point — draw a dot
      const x = transform.distanceToX(seg[0].distance);
      const y = transform.altitudeToY(seg[0].value!);
      ctx.beginPath();
      ctx.arc(x, y, style.width, 0, Math.PI * 2);
      ctx.fillStyle = style.color;
      ctx.fill();
      continue;
    }

    const xs = seg.map((p) => transform.distanceToX(p.distance));
    const ys = seg.map((p) => transform.altitudeToY(p.value!));
    const tangents = monotoneCubicTangents(xs, ys);

    ctx.beginPath();
    ctx.moveTo(xs[0], ys[0]);

    for (let i = 0; i < seg.length - 1; i++) {
      const dx = xs[i + 1] - xs[i];
      ctx.bezierCurveTo(
        xs[i] + dx / 3, ys[i] + tangents[i] * dx / 3,
        xs[i + 1] - dx / 3, ys[i + 1] - tangents[i + 1] * dx / 3,
        xs[i + 1], ys[i + 1],
      );
    }

    ctx.stroke();
  }

  ctx.setLineDash([]);
}

// --- Column (step) line ---

export function drawColumnLine(
  ctx: CanvasRenderingContext2D,
  points: PointData[],
  transform: CoordTransform,
  style: { color: string; width: number; dash?: number[] },
): void {
  ctx.strokeStyle = style.color;
  ctx.lineWidth = style.width;
  ctx.setLineDash(style.dash ?? []);

  const validPoints = points.filter((p) => p.value !== null);
  if (validPoints.length === 0) return;

  ctx.beginPath();
  let started = false;

  for (let i = 0; i < validPoints.length; i++) {
    const p = validPoints[i];
    const y = transform.altitudeToY(p.value!);
    const xLeft = columnLeft(validPoints, i, transform);
    const xRight = columnRight(validPoints, i, transform);

    if (!started) {
      ctx.moveTo(xLeft, y);
      started = true;
    } else {
      ctx.lineTo(xLeft, y);
    }
    ctx.lineTo(xRight, y);
  }

  ctx.stroke();
  ctx.setLineDash([]);
}

// --- Smooth band: filled area between two spline curves ---

export function drawSmoothBand(
  ctx: CanvasRenderingContext2D,
  bandPoints: BandPointData[],
  transform: CoordTransform,
  fillStyle: string,
): void {
  // Filter to points where both base and top are defined
  const valid = bandPoints.filter((p) => p.base !== null && p.top !== null);
  if (valid.length < 2) {
    // Draw single column for single point
    if (valid.length === 1) {
      drawSingleBandColumn(ctx, valid[0], transform, fillStyle, bandPoints);
    }
    return;
  }

  const xs = valid.map((p) => transform.distanceToX(p.distance));
  const baseYs = valid.map((p) => transform.altitudeToY(p.base!));
  const topYs = valid.map((p) => transform.altitudeToY(p.top!));

  const baseTangents = monotoneCubicTangents(xs, baseYs);
  const topTangents = monotoneCubicTangents(xs, topYs);

  ctx.fillStyle = fillStyle;
  ctx.beginPath();

  // Top curve (left to right)
  ctx.moveTo(xs[0], topYs[0]);
  for (let i = 0; i < valid.length - 1; i++) {
    const dx = xs[i + 1] - xs[i];
    ctx.bezierCurveTo(
      xs[i] + dx / 3, topYs[i] + topTangents[i] * dx / 3,
      xs[i + 1] - dx / 3, topYs[i + 1] - topTangents[i + 1] * dx / 3,
      xs[i + 1], topYs[i + 1],
    );
  }

  // Base curve (right to left)
  ctx.lineTo(xs[valid.length - 1], baseYs[valid.length - 1]);
  for (let i = valid.length - 2; i >= 0; i--) {
    const dx = xs[i] - xs[i + 1];
    ctx.bezierCurveTo(
      xs[i + 1] + dx / 3, baseYs[i + 1] + baseTangents[i + 1] * dx / 3,
      xs[i] - dx / 3, baseYs[i] - baseTangents[i] * dx / 3,
      xs[i], baseYs[i],
    );
  }

  ctx.closePath();
  ctx.fill();
}

// --- Column band: filled rectangles per point ---

export function drawColumnBand(
  ctx: CanvasRenderingContext2D,
  bandPoints: BandPointData[],
  transform: CoordTransform,
  fillStyle: string,
): void {
  ctx.fillStyle = fillStyle;

  for (let i = 0; i < bandPoints.length; i++) {
    const p = bandPoints[i];
    if (p.base === null || p.top === null) continue;

    const xLeft = columnLeftBand(bandPoints, i, transform);
    const xRight = columnRightBand(bandPoints, i, transform);
    const yTop = transform.altitudeToY(p.top);
    const yBase = transform.altitudeToY(p.base);

    ctx.fillRect(xLeft, yTop, xRight - xLeft, yBase - yTop);
  }
}

// --- Monotone cubic tangent computation (Fritsch-Carlson) ---

function monotoneCubicTangents(xs: number[], ys: number[]): number[] {
  const n = xs.length;
  if (n < 2) return new Array(n).fill(0);

  // Step 1: compute slopes between adjacent points
  const deltas: number[] = [];
  for (let i = 0; i < n - 1; i++) {
    const dx = xs[i + 1] - xs[i];
    deltas.push(dx === 0 ? 0 : (ys[i + 1] - ys[i]) / dx);
  }

  // Step 2: initial tangent estimates
  const tangents: number[] = new Array(n);
  tangents[0] = deltas[0];
  tangents[n - 1] = deltas[n - 2];

  for (let i = 1; i < n - 1; i++) {
    if (deltas[i - 1] * deltas[i] <= 0) {
      // Sign change or zero — set tangent to 0 (monotone requirement)
      tangents[i] = 0;
    } else {
      tangents[i] = (deltas[i - 1] + deltas[i]) / 2;
    }
  }

  // Step 3: Fritsch-Carlson modification to ensure monotonicity
  for (let i = 0; i < n - 1; i++) {
    if (deltas[i] === 0) {
      tangents[i] = 0;
      tangents[i + 1] = 0;
      continue;
    }

    const alpha = tangents[i] / deltas[i];
    const beta = tangents[i + 1] / deltas[i];

    // Check the constraint: alpha^2 + beta^2 <= 9
    const tau = alpha * alpha + beta * beta;
    if (tau > 9) {
      const s = 3 / Math.sqrt(tau);
      tangents[i] = s * alpha * deltas[i];
      tangents[i + 1] = s * beta * deltas[i];
    }
  }

  return tangents;
}

// --- Helpers ---

/** Split points into segments of consecutive non-null values. */
function splitNonNull(points: PointData[]): PointData[][] {
  const segments: PointData[][] = [];
  let current: PointData[] = [];

  for (const p of points) {
    if (p.value !== null) {
      current.push(p);
    } else if (current.length > 0) {
      segments.push(current);
      current = [];
    }
  }
  if (current.length > 0) segments.push(current);

  return segments;
}

function columnLeft(points: PointData[], i: number, transform: CoordTransform): number {
  const x = transform.distanceToX(points[i].distance);
  if (i === 0) return x;
  const prevX = transform.distanceToX(points[i - 1].distance);
  return (prevX + x) / 2;
}

function columnRight(points: PointData[], i: number, transform: CoordTransform): number {
  const x = transform.distanceToX(points[i].distance);
  if (i === points.length - 1) return x;
  const nextX = transform.distanceToX(points[i + 1].distance);
  return (x + nextX) / 2;
}

function columnLeftBand(points: BandPointData[], i: number, transform: CoordTransform): number {
  const x = transform.distanceToX(points[i].distance);
  if (i === 0) return x;
  const prevX = transform.distanceToX(points[i - 1].distance);
  return (prevX + x) / 2;
}

function columnRightBand(points: BandPointData[], i: number, transform: CoordTransform): number {
  const x = transform.distanceToX(points[i].distance);
  if (i === points.length - 1) return x;
  const nextX = transform.distanceToX(points[i + 1].distance);
  return (x + nextX) / 2;
}

function drawSingleBandColumn(
  ctx: CanvasRenderingContext2D,
  point: BandPointData,
  transform: CoordTransform,
  fillStyle: string,
  allPoints: BandPointData[],
): void {
  ctx.fillStyle = fillStyle;
  const x = transform.distanceToX(point.distance);
  // Determine width from neighbors
  const idx = allPoints.indexOf(point);
  let halfWidth = 10;
  if (idx > 0) {
    halfWidth = (x - transform.distanceToX(allPoints[idx - 1].distance)) / 2;
  } else if (idx < allPoints.length - 1) {
    halfWidth = (transform.distanceToX(allPoints[idx + 1].distance) - x) / 2;
  }
  const yTop = transform.altitudeToY(point.top!);
  const yBase = transform.altitudeToY(point.base!);
  ctx.fillRect(x - halfWidth, yTop, halfWidth * 2, yBase - yTop);
}
