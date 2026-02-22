/** NWP cloud cover bands: gray fills at ICAO altitude bands from model cloud parameterization. */

import type {
  CrossSectionLayer,
  CoordTransform,
  VizRouteData,
  RenderMode,
  VizPoint,
  VizInversionLayer,
  VizCloudLayer,
  VizCloudDiag,
} from '../../types';
import { drawColumnBand, type BandPointData } from './base';

/** Low cloud band: terrain surface → 6500ft. Mid band: 6500ft → 20000ft. */
const LOW_TOP_FT = 6500;
const MID_TOP_FT = 20000;

/** Inversions weaker than this don't reliably cap clouds. */
const INVERSION_STRENGTH_THRESHOLD_C = 2.0;

/** Convert cloud cover percentage to capped opacity. */
function cloudOpacity(pct: number): number {
  return Math.min(0.7, (pct / 100) * 0.8);
}

/** Gray cloud fill at the given opacity. */
function nwpCloudFill(opacity: number): string {
  return `rgba(180, 185, 190, ${opacity})`;
}

/** Find the lowest inversion within [floor, ceiling] that exceeds the strength threshold. */
function findCappingInversion(
  inversions: VizInversionLayer[],
  floorFt: number,
  ceilingFt: number,
): number | null {
  let lowest: number | null = null;
  for (const inv of inversions) {
    if (inv.strengthC >= INVERSION_STRENGTH_THRESHOLD_C &&
        inv.baseFt > floorFt &&
        inv.baseFt < ceilingFt) {
      if (lowest === null || inv.baseFt < lowest) {
        lowest = inv.baseFt;
      }
    }
  }
  return lowest;
}

/** Compute the envelope (lowest base, highest top) of cloud layers overlapping [floor, ceiling]. */
function cloudEnvelopeInBand(
  layers: VizCloudLayer[],
  floor: number,
  ceiling: number,
): { base: number; top: number } | null {
  let minBase = ceiling;
  let maxTop = floor;
  for (const cl of layers) {
    if (cl.topFt > floor && cl.baseFt < ceiling) {
      minBase = Math.min(minBase, Math.max(cl.baseFt, floor));
      maxTop = Math.max(maxTop, Math.min(cl.topFt, ceiling));
    }
  }
  return maxTop > minBase ? { base: minBase, top: maxTop } : null;
}

interface BandLimits {
  lowBase: number;
  lowTop: number;
  midBase: number;
  midTop: number;
  highBase: number;
  highTop: number;
  highCoverPct: number;
}

/**
 * Compute band limits for NWP cloud rendering.
 *
 * Hybrid approach: uses actual GFS base/top boundaries when available for
 * each band, and falls back to heuristic narrowing (sounding cloud envelopes,
 * inversions, LCL) for bands where boundaries are missing (ICON-EU, or GFS
 * with NaN boundaries for a specific layer).
 */
function computeBandLimits(point: VizPoint, terrainFt: number): BandLimits {
  const diag = point.nwpCloudDiag;
  const lcl = point.altitudeLines.lclAltitudeFt;

  // --- Low band ---
  let lowBase: number;
  let lowTop: number;
  if (diag?.low.baseFt != null && diag?.low.topFt != null) {
    lowBase = diag.low.baseFt;
    lowTop = diag.low.topFt;
  } else {
    lowBase = terrainFt;
    if (lcl !== null && lcl > terrainFt && lcl < LOW_TOP_FT) {
      lowBase = lcl;
    }
    const lowCap = findCappingInversion(point.inversions, lowBase, LOW_TOP_FT);
    lowTop = lowCap ?? LOW_TOP_FT;
    const lowEnvelope = cloudEnvelopeInBand(point.cloudLayers, lowBase, lowTop);
    if (lowEnvelope) {
      lowBase = Math.max(lowBase, lowEnvelope.base);
      lowTop = Math.min(lowTop, lowEnvelope.top);
    }
  }

  // --- Mid band ---
  let midBase: number;
  let midTop: number;
  if (diag?.mid.baseFt != null && diag?.mid.topFt != null) {
    midBase = diag.mid.baseFt;
    midTop = diag.mid.topFt;
  } else {
    midBase = LOW_TOP_FT;
    if (lcl !== null && lcl > LOW_TOP_FT && lcl < MID_TOP_FT) {
      midBase = lcl;
    }
    const midCap = findCappingInversion(point.inversions, midBase, MID_TOP_FT);
    midTop = midCap ?? MID_TOP_FT;
    const midEnvelope = cloudEnvelopeInBand(point.cloudLayers, midBase, midTop);
    if (midEnvelope) {
      midBase = Math.max(midBase, midEnvelope.base);
      midTop = Math.min(midTop, midEnvelope.top);
    } else if (point.cloudCoverMidPct > 0) {
      const m20 = point.altitudeLines.minus20cLevelFt;
      if (m20 !== null && m20 > midBase && m20 < midTop) {
        midTop = m20;
      }
    }
  }

  // --- High band ---
  let highBase: number;
  let highTop: number;
  let highCoverPct: number;
  if (diag?.high.baseFt != null && diag?.high.topFt != null) {
    highBase = diag.high.baseFt;
    highTop = diag.high.topFt;
    highCoverPct = diag.high.coverPct ?? 0;
  } else {
    highBase = MID_TOP_FT;
    highTop = 40000;
    highCoverPct = diag?.high.coverPct ?? 0;
  }

  return { lowBase, lowTop, midBase, midTop, highBase, highTop, highCoverPct };
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
    // Check if any point has NWP cloud data (from Open-Meteo or GFS diagnostics)
    const hasData = data.points.some((p) =>
      p.cloudCoverLowPct > 0 || p.cloudCoverMidPct > 0 || p.nwpCloudDiag !== null);
    if (!hasData) return;

    if (mode === 'columns') {
      renderColumns(ctx, transform, data);
    } else {
      renderSmooth(ctx, transform, data);
    }
  },
};

/** Get cloud cover % for a band, using GFS diagnostics when available. */
function bandCoverPct(point: VizPoint, band: 'low' | 'mid' | 'high'): number {
  const diag = point.nwpCloudDiag;
  if (diag) {
    return diag[band].coverPct ?? 0;
  }
  if (band === 'low') return point.cloudCoverLowPct;
  if (band === 'mid') return point.cloudCoverMidPct;
  return 0; // No high-cloud data without diagnostics
}

function renderColumns(
  ctx: CanvasRenderingContext2D,
  transform: CoordTransform,
  data: VizRouteData,
): void {
  for (const point of data.points) {
    const terrainFt = terrainElevationAt(data, point.distanceNm);
    const limits = computeBandLimits(point, terrainFt);

    // Low band
    const lowPct = bandCoverPct(point, 'low');
    if (lowPct > 0 && limits.lowBase < limits.lowTop) {
      const fill = nwpCloudFill(cloudOpacity(lowPct));
      const bandPoints: BandPointData[] = [{ distance: point.distanceNm, base: limits.lowBase, top: limits.lowTop }];
      drawColumnBand(ctx, bandPoints, transform, fill);
    }

    // Mid band
    const midPct = bandCoverPct(point, 'mid');
    if (midPct > 0 && limits.midBase < limits.midTop) {
      const fill = nwpCloudFill(cloudOpacity(midPct));
      const bandPoints: BandPointData[] = [{ distance: point.distanceNm, base: limits.midBase, top: limits.midTop }];
      drawColumnBand(ctx, bandPoints, transform, fill);
    }

    // High band (only with GFS diagnostics)
    const highPct = bandCoverPct(point, 'high');
    if (highPct > 0 && limits.highBase < limits.highTop) {
      const fill = nwpCloudFill(cloudOpacity(highPct));
      const bandPoints: BandPointData[] = [{ distance: point.distanceNm, base: limits.highBase, top: limits.highTop }];
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

/** Draw a single band trapezoid between two points. */
function drawBandTrapezoid(
  ctx: CanvasRenderingContext2D,
  transform: CoordTransform,
  x1: number, x2: number,
  currBase: number, currTop: number,
  nextBase: number, nextTop: number,
  coverPct: number,
): void {
  if (coverPct <= 0) return;
  if (currBase >= currTop && nextBase >= nextTop) return;

  ctx.fillStyle = nwpCloudFill(cloudOpacity(coverPct));
  ctx.beginPath();
  ctx.moveTo(x1, transform.altitudeToY(currTop));
  ctx.lineTo(x2, transform.altitudeToY(nextTop));
  ctx.lineTo(x2, transform.altitudeToY(nextBase));
  ctx.lineTo(x1, transform.altitudeToY(currBase));
  ctx.closePath();
  ctx.fill();
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
  const limitsCurr = computeBandLimits(curr, terrainCurr);
  const limitsNext = computeBandLimits(next, terrainNext);

  // Low band
  const avgLowPct = (bandCoverPct(curr, 'low') + bandCoverPct(next, 'low')) / 2;
  drawBandTrapezoid(ctx, transform, x1, x2,
    limitsCurr.lowBase, limitsCurr.lowTop,
    limitsNext.lowBase, limitsNext.lowTop, avgLowPct);

  // Mid band
  const avgMidPct = (bandCoverPct(curr, 'mid') + bandCoverPct(next, 'mid')) / 2;
  drawBandTrapezoid(ctx, transform, x1, x2,
    limitsCurr.midBase, limitsCurr.midTop,
    limitsNext.midBase, limitsNext.midTop, avgMidPct);

  // High band (only with GFS diagnostics)
  const avgHighPct = (bandCoverPct(curr, 'high') + bandCoverPct(next, 'high')) / 2;
  drawBandTrapezoid(ctx, transform, x1, x2,
    limitsCurr.highBase, limitsCurr.highTop,
    limitsNext.highBase, limitsNext.highTop, avgHighPct);
}

function drawSinglePointBands(
  ctx: CanvasRenderingContext2D,
  transform: CoordTransform,
  p: VizPoint,
  terrainFt: number,
): void {
  const limits = computeBandLimits(p, terrainFt);

  const lowPct = bandCoverPct(p, 'low');
  if (lowPct > 0 && limits.lowBase < limits.lowTop) {
    const fill = nwpCloudFill(cloudOpacity(lowPct));
    const bandPoints: BandPointData[] = [{ distance: p.distanceNm, base: limits.lowBase, top: limits.lowTop }];
    drawColumnBand(ctx, bandPoints, transform, fill);
  }

  const midPct = bandCoverPct(p, 'mid');
  if (midPct > 0 && limits.midBase < limits.midTop) {
    const fill = nwpCloudFill(cloudOpacity(midPct));
    const bandPoints: BandPointData[] = [{ distance: p.distanceNm, base: limits.midBase, top: limits.midTop }];
    drawColumnBand(ctx, bandPoints, transform, fill);
  }

  const highPct = bandCoverPct(p, 'high');
  if (highPct > 0 && limits.highBase < limits.highTop) {
    const fill = nwpCloudFill(cloudOpacity(highPct));
    const bandPoints: BandPointData[] = [{ distance: p.distanceNm, base: limits.highBase, top: limits.highTop }];
    drawColumnBand(ctx, bandPoints, transform, fill);
  }
}
