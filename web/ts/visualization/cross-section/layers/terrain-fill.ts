/** Terrain fill layer: earth-tone filled area from ground to chart bottom. */

import type { CrossSectionLayer, CoordTransform, VizRouteData, TerrainPoint } from '../../types';
import { drawSmoothLine, monotoneCubicTangents, type PointData } from './base';
import { getActiveTheme } from '../theme';

export const terrainFillLayer: CrossSectionLayer = {
  id: 'terrain',
  name: 'Terrain',
  group: 'terrain',
  defaultEnabled: true,

  render(ctx: CanvasRenderingContext2D, transform: CoordTransform, data: VizRouteData) {
    if (!data.terrainProfile || data.terrainProfile.length === 0) return;

    const { plotArea } = transform;
    const bottomY = plotArea.top + plotArea.height;

    drawSmoothTerrain(ctx, data.terrainProfile, transform, bottomY);

    // Outline along the terrain surface
    const linePoints: PointData[] = data.terrainProfile.map((p) => ({
      distance: p.distanceNm,
      value: p.elevationFt,
    }));
    drawSmoothLine(ctx, linePoints, transform, {
      color: getActiveTheme().terrain.outlineColor,
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

  ctx.fillStyle = getActiveTheme().terrain.fillColor;
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
