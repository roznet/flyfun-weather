/**
 * Renders T/Td profile curves, parcel path, and CAPE/CIN shading
 * on the Skew-T diagram.
 */

import { SkewTTransform } from './skewt-transform';
import { SoundingProfileLevel, ParcelPathPoint } from './types';

// Profile curve colors
const TEMP_COLOR = '#e03030';         // Red — temperature
const DEWPOINT_COLOR = '#30a030';     // Green — dewpoint
const PARCEL_COLOR = 'rgba(40, 40, 40, 0.7)'; // Dark gray — parcel path
const CAPE_COLOR = 'rgba(220, 60, 60, 0.12)'; // Red tint — CAPE
const CIN_COLOR = 'rgba(60, 60, 220, 0.12)';  // Blue tint — CIN

const PROFILE_LINE_WIDTH = 2.0;
const PARCEL_LINE_WIDTH = 1.5;
const PARCEL_DASH = [6, 4];

/**
 * Draw a smooth profile curve through data points.
 * Uses lineTo for now — monotone cubic can be added later.
 */
function drawProfileLine(
  ctx: CanvasRenderingContext2D,
  transform: SkewTTransform,
  points: Array<{ tempC: number; pressureHPa: number }>,
  color: string,
  lineWidth: number = PROFILE_LINE_WIDTH,
  dash: number[] = [],
): void {
  if (points.length < 2) return;

  ctx.strokeStyle = color;
  ctx.lineWidth = lineWidth;
  ctx.setLineDash(dash);
  ctx.lineJoin = 'round';
  ctx.lineCap = 'round';
  ctx.beginPath();

  const first = transform.toPixel(points[0].tempC, points[0].pressureHPa);
  ctx.moveTo(first.x, first.y);
  for (let i = 1; i < points.length; i++) {
    const pt = transform.toPixel(points[i].tempC, points[i].pressureHPa);
    ctx.lineTo(pt.x, pt.y);
  }
  ctx.stroke();
  ctx.setLineDash([]);
}

/**
 * Render CAPE/CIN shading between parcel path and environment temperature.
 *
 * Where parcel T > environment T → CAPE fill (red).
 * Where parcel T < environment T → CIN fill (blue).
 *
 * We pair up parcel and environment by nearest pressure level.
 */
function renderCapeCinShading(
  ctx: CanvasRenderingContext2D,
  transform: SkewTTransform,
  levels: SoundingProfileLevel[],
  parcelPath: ParcelPathPoint[],
  lclPressureHPa: number | null,
  elPressureHPa: number | null,
): void {
  if (parcelPath.length < 2 || levels.length < 2) return;

  // No EL means no convective instability — skip CAPE/CIN shading entirely.
  // Without an EL the parcel never becomes buoyant, so shading would just
  // paint misleading CIN from LCL to the top of the diagram.
  if (elPressureHPa === null) return;

  for (let i = 0; i < parcelPath.length - 1; i++) {
    const pp0 = parcelPath[i];
    const pp1 = parcelPath[i + 1];

    // Below LCL — no shading (parcel hasn't reached condensation)
    if (lclPressureHPa !== null && pp0.pressure_hpa > lclPressureHPa) continue;

    // Above EL — no shading (parcel is back in stable air)
    if (pp1.pressure_hpa < elPressureHPa) continue;

    const envT0 = findClosestEnvTemp(levels, pp0.pressure_hpa);
    const envT1 = findClosestEnvTemp(levels, pp1.pressure_hpa);
    if (envT0 === null || envT1 === null) continue;

    const buoyancy0 = pp0.temperature_c - envT0;
    const buoyancy1 = pp1.temperature_c - envT1;
    const avgBuoyancy = (buoyancy0 + buoyancy1) / 2;
    if (Math.abs(avgBuoyancy) < 0.1) continue;

    // CAPE (red) = positive buoyancy between LFC and EL
    // CIN (blue) = negative buoyancy between LCL and LFC
    const fillColor = avgBuoyancy > 0 ? CAPE_COLOR : CIN_COLOR;

    const pxParcel0 = transform.toPixel(pp0.temperature_c, pp0.pressure_hpa);
    const pxParcel1 = transform.toPixel(pp1.temperature_c, pp1.pressure_hpa);
    const pxEnv0 = transform.toPixel(envT0, pp0.pressure_hpa);
    const pxEnv1 = transform.toPixel(envT1, pp1.pressure_hpa);

    ctx.fillStyle = fillColor;
    ctx.beginPath();
    ctx.moveTo(pxParcel0.x, pxParcel0.y);
    ctx.lineTo(pxParcel1.x, pxParcel1.y);
    ctx.lineTo(pxEnv1.x, pxEnv1.y);
    ctx.lineTo(pxEnv0.x, pxEnv0.y);
    ctx.closePath();
    ctx.fill();
  }
}

/** Find the environment temperature closest to a given pressure. */
function findClosestEnvTemp(
  levels: SoundingProfileLevel[],
  pressureHPa: number,
): number | null {
  let bestDist = Infinity;
  let bestT: number | null = null;
  for (const lv of levels) {
    const dist = Math.abs(lv.pressure_hpa - pressureHPa);
    if (dist < bestDist) {
      bestDist = dist;
      bestT = lv.temperature_c;
    }
  }
  return bestT;
}

/** Dataset for multi-model compare rendering. */
export interface CompareProfileDataset {
  model: string;
  levels: SoundingProfileLevel[];
  color: string;
  isPrimary: boolean;
  parcelPath?: ParcelPathPoint[];
  lclP?: number | null;
  elP?: number | null;
}

/**
 * Render T/Td curves from multiple models on the same Skew-T.
 * Each model gets one color: solid for T, dashed for Td.
 * Secondary models drawn first (reduced opacity), primary on top.
 */
export function renderCompareProfileCurves(
  ctx: CanvasRenderingContext2D,
  transform: SkewTTransform,
  datasets: CompareProfileDataset[],
  showCapeCin: boolean,
): void {
  if (datasets.length === 0) return;

  ctx.save();
  const plot = transform.plotArea;
  ctx.beginPath();
  ctx.rect(plot.left, plot.top, plot.width, plot.height);
  ctx.clip();

  // 1. Optional CAPE/CIN shading for all models (overlapping shows agreement)
  if (showCapeCin) {
    for (const ds of datasets) {
      if (!ds.parcelPath?.length) continue;
      ctx.globalAlpha = ds.isPrimary ? 1.0 : 0.55;
      renderCapeCinShading(ctx, transform, ds.levels, ds.parcelPath,
        ds.lclP ?? null, ds.elP ?? null);
    }
    ctx.globalAlpha = 1.0;
  }

  // 2. Secondary models first (behind primary)
  for (const ds of datasets) {
    if (ds.isPrimary) continue;
    ctx.globalAlpha = 0.55;
    // Parcel path
    if (showCapeCin && ds.parcelPath?.length) {
      const parcelPoints = ds.parcelPath.map(pp => ({
        tempC: pp.temperature_c, pressureHPa: pp.pressure_hpa,
      }));
      drawProfileLine(ctx, transform, parcelPoints, ds.color, 1.0, PARCEL_DASH);
    }
    const tPoints = ds.levels.map(lv => ({ tempC: lv.temperature_c, pressureHPa: lv.pressure_hpa }));
    drawProfileLine(ctx, transform, tPoints, ds.color, 1.5);
    const tdPoints = ds.levels
      .filter(lv => lv.dewpoint_c !== null)
      .map(lv => ({ tempC: lv.dewpoint_c!, pressureHPa: lv.pressure_hpa }));
    drawProfileLine(ctx, transform, tdPoints, ds.color, 1.5, [4, 3]);
  }

  // 3. Primary model on top (full opacity, thick)
  const primary = datasets.find(d => d.isPrimary);
  if (primary) {
    ctx.globalAlpha = 1.0;
    // Parcel path
    if (showCapeCin && primary.parcelPath?.length) {
      const parcelPoints = primary.parcelPath.map(pp => ({
        tempC: pp.temperature_c, pressureHPa: pp.pressure_hpa,
      }));
      drawProfileLine(ctx, transform, parcelPoints, primary.color, PARCEL_LINE_WIDTH, PARCEL_DASH);
    }
    const tPoints = primary.levels.map(lv => ({ tempC: lv.temperature_c, pressureHPa: lv.pressure_hpa }));
    drawProfileLine(ctx, transform, tPoints, primary.color, 2.5);
    const tdPoints = primary.levels
      .filter(lv => lv.dewpoint_c !== null)
      .map(lv => ({ tempC: lv.dewpoint_c!, pressureHPa: lv.pressure_hpa }));
    drawProfileLine(ctx, transform, tdPoints, primary.color, 2.5, [4, 3]);
  }

  ctx.restore();
}

/**
 * Render all profile curves: T, Td, parcel path, and CAPE/CIN shading.
 */
export function renderProfileCurves(
  ctx: CanvasRenderingContext2D,
  transform: SkewTTransform,
  levels: SoundingProfileLevel[],
  parcelPath: ParcelPathPoint[],
  lclPressureHPa: number | null,
  elPressureHPa: number | null,
): void {
  ctx.save();
  const plot = transform.plotArea;
  ctx.beginPath();
  ctx.rect(plot.left, plot.top, plot.width, plot.height);
  ctx.clip();

  // 1. CAPE/CIN shading (behind curves)
  if (parcelPath.length > 0) {
    renderCapeCinShading(ctx, transform, levels, parcelPath, lclPressureHPa, elPressureHPa);
  }

  // 2. Parcel path (dashed, behind profile curves)
  if (parcelPath.length > 0) {
    const parcelPoints = parcelPath.map(pp => ({
      tempC: pp.temperature_c,
      pressureHPa: pp.pressure_hpa,
    }));
    drawProfileLine(ctx, transform, parcelPoints, PARCEL_COLOR, PARCEL_LINE_WIDTH, PARCEL_DASH);
  }

  // 3. Dewpoint curve (green)
  const tdPoints = levels
    .filter(lv => lv.dewpoint_c !== null)
    .map(lv => ({ tempC: lv.dewpoint_c!, pressureHPa: lv.pressure_hpa }));
  drawProfileLine(ctx, transform, tdPoints, DEWPOINT_COLOR);

  // 4. Temperature curve (red) — on top
  const tPoints = levels.map(lv => ({
    tempC: lv.temperature_c,
    pressureHPa: lv.pressure_hpa,
  }));
  drawProfileLine(ctx, transform, tPoints, TEMP_COLOR);

  ctx.restore();
}
