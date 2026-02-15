/** NWP cloud cover bands: white fills at ICAO altitude bands from model cloud parameterization. */

import type { CrossSectionLayer, CoordTransform, VizRouteData, RenderMode, VizPoint } from '../../types';
import { drawColumnBand, type BandPointData } from './base';

/** Low cloud band: terrain surface → 6500ft. Mid band: 6500ft → 20000ft. */
const LOW_TOP_FT = 6500;
const MID_TOP_FT = 20000;

/** Convert cloud cover percentage to capped opacity. */
function cloudOpacity(pct: number): number {
  return Math.min(0.7, (pct / 100) * 0.8);
}

/** Get terrain elevation at a point, falling back to 0. */
function terrainElevationAt(data: VizRouteData, distanceNm: number): number {
  if (!data.terrainProfile || data.terrainProfile.length === 0) return 0;

  // Find the two surrounding terrain points and interpolate
  const profile = data.terrainProfile;
  if (distanceNm <= profile[0].distanceNm) return profile[0].elevationFt;
  if (distanceNm >= profile[profile.length - 1].distanceNm) return profile[profile.length - 1].elevationFt;

  for (let i = 0; i < profile.length - 1; i++) {
    if (distanceNm >= profile[i].distanceNm && distanceNm <= profile[i + 1].distanceNm) {
      const t = (distanceNm - profile[i].distanceNm) / (profile[i + 1].distanceNm - profile[i].distanceNm);
      return profile[i].elevationFt + t * (profile[i + 1].elevationFt - profile[i].elevationFt);
    }
  }
  return 0;
}

export const nwpCloudBandsLayer: CrossSectionLayer = {
  id: 'nwp-cloud-bands',
  name: 'NWP Cloud Cover',
  group: 'clouds',
  defaultEnabled: true,
  metricId: 'nwp_cloud_cover',

  render(ctx: CanvasRenderingContext2D, transform: CoordTransform, data: VizRouteData, mode: RenderMode) {
    // Check if any point has NWP cloud data
    const hasData = data.points.some((p) => p.cloudCoverLowPct > 0 || p.cloudCoverMidPct > 0);
    if (!hasData) return;

    if (mode === 'columns') {
      renderColumns(ctx, transform, data);
    } else {
      renderSmooth(ctx, transform, data);
    }
  },
};

function renderColumns(
  ctx: CanvasRenderingContext2D,
  transform: CoordTransform,
  data: VizRouteData,
): void {
  for (const point of data.points) {
    const terrainFt = terrainElevationAt(data, point.distanceNm);

    // Low band: terrain → 6500ft
    if (point.cloudCoverLowPct > 0) {
      const opacity = cloudOpacity(point.cloudCoverLowPct);
      const fill = `rgba(255, 255, 255, ${opacity})`;
      const bandPoints: BandPointData[] = [{ distance: point.distanceNm, base: terrainFt, top: LOW_TOP_FT }];
      drawColumnBand(ctx, bandPoints, transform, fill);
    }

    // Mid band: 6500ft → 20000ft
    if (point.cloudCoverMidPct > 0) {
      const opacity = cloudOpacity(point.cloudCoverMidPct);
      const fill = `rgba(255, 255, 255, ${opacity})`;
      const bandPoints: BandPointData[] = [{ distance: point.distanceNm, base: LOW_TOP_FT, top: MID_TOP_FT }];
      drawColumnBand(ctx, bandPoints, transform, fill);
    }
  }
}

function renderSmooth(
  ctx: CanvasRenderingContext2D,
  transform: CoordTransform,
  data: VizRouteData,
): void {
  for (let i = 0; i < data.points.length - 1; i++) {
    const curr = data.points[i];
    const next = data.points[i + 1];
    drawSegment(ctx, transform, data, curr, next);
  }

  // Single point fallback
  if (data.points.length === 1) {
    const p = data.points[0];
    const terrainFt = terrainElevationAt(data, p.distanceNm);
    drawSinglePointBands(ctx, transform, p, terrainFt);
  }
}

function drawSegment(
  ctx: CanvasRenderingContext2D,
  transform: CoordTransform,
  data: VizRouteData,
  curr: VizPoint,
  next: VizPoint,
): void {
  const x1 = transform.distanceToX(curr.distanceNm);
  const x2 = transform.distanceToX(next.distanceNm);
  const terrainCurr = terrainElevationAt(data, curr.distanceNm);
  const terrainNext = terrainElevationAt(data, next.distanceNm);

  // Low band: terrain → 6500ft (trapezoid since terrain varies)
  const avgLowPct = (curr.cloudCoverLowPct + next.cloudCoverLowPct) / 2;
  if (avgLowPct > 0) {
    const opacity = cloudOpacity(avgLowPct);
    ctx.fillStyle = `rgba(255, 255, 255, ${opacity})`;
    ctx.beginPath();
    ctx.moveTo(x1, transform.altitudeToY(LOW_TOP_FT));
    ctx.lineTo(x2, transform.altitudeToY(LOW_TOP_FT));
    ctx.lineTo(x2, transform.altitudeToY(terrainNext));
    ctx.lineTo(x1, transform.altitudeToY(terrainCurr));
    ctx.closePath();
    ctx.fill();
  }

  // Mid band: 6500ft → 20000ft (rectangle)
  const avgMidPct = (curr.cloudCoverMidPct + next.cloudCoverMidPct) / 2;
  if (avgMidPct > 0) {
    const opacity = cloudOpacity(avgMidPct);
    ctx.fillStyle = `rgba(255, 255, 255, ${opacity})`;
    const yTop = transform.altitudeToY(MID_TOP_FT);
    const yBase = transform.altitudeToY(LOW_TOP_FT);
    ctx.fillRect(x1, yTop, x2 - x1, yBase - yTop);
  }
}

function drawSinglePointBands(
  ctx: CanvasRenderingContext2D,
  transform: CoordTransform,
  p: VizPoint,
  terrainFt: number,
): void {
  if (p.cloudCoverLowPct > 0) {
    const opacity = cloudOpacity(p.cloudCoverLowPct);
    const fill = `rgba(255, 255, 255, ${opacity})`;
    const bandPoints: BandPointData[] = [{ distance: p.distanceNm, base: terrainFt, top: LOW_TOP_FT }];
    drawColumnBand(ctx, bandPoints, transform, fill);
  }
  if (p.cloudCoverMidPct > 0) {
    const opacity = cloudOpacity(p.cloudCoverMidPct);
    const fill = `rgba(255, 255, 255, ${opacity})`;
    const bandPoints: BandPointData[] = [{ distance: p.distanceNm, base: LOW_TOP_FT, top: MID_TOP_FT }];
    drawColumnBand(ctx, bandPoints, transform, fill);
  }
}
