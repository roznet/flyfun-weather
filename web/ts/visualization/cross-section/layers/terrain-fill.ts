/** Terrain fill layer: earth-tone filled area from ground to chart bottom. */

import type { CrossSectionLayer, CoordTransform, VizRouteData, RenderMode, TerrainPoint } from '../../types';
import { drawSmoothLine, type PointData } from './base';

const FILL_COLOR = '#8B7355';
const OUTLINE_COLOR = '#6B5B45';

export const terrainFillLayer: CrossSectionLayer = {
  id: 'terrain',
  name: 'Terrain',
  group: 'terrain',
  defaultEnabled: true,

  render(ctx: CanvasRenderingContext2D, transform: CoordTransform, data: VizRouteData, mode: RenderMode) {
    if (!data.terrainProfile || data.terrainProfile.length === 0) return;

    const { plotArea } = transform;
    const bottomY = plotArea.top + plotArea.height;

    if (mode === 'columns') {
      drawColumnTerrain(ctx, data.terrainProfile, transform, bottomY);
    } else {
      drawSmoothTerrain(ctx, data.terrainProfile, transform, bottomY);
    }

    // Outline along the terrain surface
    const linePoints: PointData[] = data.terrainProfile.map((p) => ({
      distance: p.distanceNm,
      value: p.elevationFt,
    }));
    drawSmoothLine(ctx, linePoints, transform, {
      color: OUTLINE_COLOR,
      width: 1.5,
    });
  },
};

function drawSmoothTerrain(
  ctx: CanvasRenderingContext2D,
  profile: TerrainPoint[],
  transform: CoordTransform,
  bottomY: number,
): void {
  // Build pixel arrays for monotone cubic spline
  const xs = profile.map((p) => transform.distanceToX(p.distanceNm));
  const ys = profile.map((p) => transform.altitudeToY(p.elevationFt));
  const tangents = monotoneCubicTangents(xs, ys);

  ctx.fillStyle = FILL_COLOR;
  ctx.beginPath();

  // Start at bottom-left
  ctx.moveTo(xs[0], bottomY);

  // Up to first terrain point
  ctx.lineTo(xs[0], ys[0]);

  // Spline along terrain surface
  for (let i = 0; i < profile.length - 1; i++) {
    const dx = xs[i + 1] - xs[i];
    ctx.bezierCurveTo(
      xs[i] + dx / 3, ys[i] + tangents[i] * dx / 3,
      xs[i + 1] - dx / 3, ys[i + 1] - tangents[i + 1] * dx / 3,
      xs[i + 1], ys[i + 1],
    );
  }

  // Down to bottom-right and close
  ctx.lineTo(xs[xs.length - 1], bottomY);
  ctx.closePath();
  ctx.fill();
}

function drawColumnTerrain(
  ctx: CanvasRenderingContext2D,
  profile: TerrainPoint[],
  transform: CoordTransform,
  bottomY: number,
): void {
  ctx.fillStyle = FILL_COLOR;

  for (let i = 0; i < profile.length; i++) {
    const p = profile[i];
    const x = transform.distanceToX(p.distanceNm);
    const y = transform.altitudeToY(p.elevationFt);

    // Column left/right edges
    let xLeft: number;
    let xRight: number;
    if (i === 0) {
      xLeft = x;
      xRight = (x + transform.distanceToX(profile[1]?.distanceNm ?? p.distanceNm)) / 2;
    } else if (i === profile.length - 1) {
      xLeft = (transform.distanceToX(profile[i - 1].distanceNm) + x) / 2;
      xRight = x;
    } else {
      xLeft = (transform.distanceToX(profile[i - 1].distanceNm) + x) / 2;
      xRight = (x + transform.distanceToX(profile[i + 1].distanceNm)) / 2;
    }

    const h = bottomY - y;
    if (h > 0) {
      ctx.fillRect(xLeft, y, xRight - xLeft, h);
    }
  }
}

/** Fritsch-Carlson monotone cubic tangents (same algorithm as base.ts). */
function monotoneCubicTangents(xs: number[], ys: number[]): number[] {
  const n = xs.length;
  if (n < 2) return new Array(n).fill(0);

  const deltas: number[] = [];
  for (let i = 0; i < n - 1; i++) {
    const dx = xs[i + 1] - xs[i];
    deltas.push(dx === 0 ? 0 : (ys[i + 1] - ys[i]) / dx);
  }

  const tangents: number[] = new Array(n);
  tangents[0] = deltas[0];
  tangents[n - 1] = deltas[n - 2];

  for (let i = 1; i < n - 1; i++) {
    if (deltas[i - 1] * deltas[i] <= 0) {
      tangents[i] = 0;
    } else {
      tangents[i] = (deltas[i - 1] + deltas[i]) / 2;
    }
  }

  for (let i = 0; i < n - 1; i++) {
    if (deltas[i] === 0) {
      tangents[i] = 0;
      tangents[i + 1] = 0;
      continue;
    }
    const alpha = tangents[i] / deltas[i];
    const beta = tangents[i + 1] / deltas[i];
    const tau = alpha * alpha + beta * beta;
    if (tau > 9) {
      const s = 3 / Math.sqrt(tau);
      tangents[i] = s * alpha * deltas[i];
      tangents[i + 1] = s * beta * deltas[i];
    }
  }

  return tangents;
}
