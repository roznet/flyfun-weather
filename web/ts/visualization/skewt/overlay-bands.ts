/**
 * Renders semi-transparent altitude bands on the Skew-T for:
 * - Cloud layers (DD and NWP)
 * - Icing zones (Ogimet-DD, Ogimet-NWP, SFIP)
 * - Inversion layers
 * - Convective zone (LFC → EL)
 *
 * Colors match the cross-section layer semantics.
 */

import { SkewTTransform } from './skewt-transform';
import type {
  SoundingProfileData,
  CloudLayer,
  IcingZone,
  InversionLayer,
} from './types';
import { altitudeToPressure } from './atmo-utils';

// --- Cloud colors (matching cross-section theme) ---
const CLOUD_DD_COLOR = 'rgba(140, 140, 150, ALPHA)';
const CLOUD_NWP_COLOR = 'rgba(120, 140, 170, ALPHA)';

const COVERAGE_ALPHA: Record<string, number> = {
  SCT: 0.20, sct: 0.20,
  BKN: 0.35, bkn: 0.35,
  OVC: 0.50, ovc: 0.50,
};

// --- Icing colors (both cases — API returns lowercase) ---
const ICING_COLORS: Record<string, string> = {
  NONE: 'transparent', none: 'transparent',
  LIGHT: 'rgba(100, 149, 237, 0.30)', light: 'rgba(100, 149, 237, 0.30)',
  MODERATE: 'rgba(255, 165, 0, 0.40)', moderate: 'rgba(255, 165, 0, 0.40)',
  SEVERE: 'rgba(220, 53, 69, 0.50)', severe: 'rgba(220, 53, 69, 0.50)',
};

// --- Inversion ---
const INVERSION_BASE_RGB = [233, 30, 99];
const INVERSION_OPACITY = { floor: 0.12, scale: 0.4, maxC: 3, cap: 0.50 };

// --- Convective ---
const CONVECTIVE_COLOR = 'rgba(255, 100, 50, 0.12)';

/** Which overlay layers are available. */
export type SkewTOverlayId =
  | 'clouds-dd'
  | 'clouds-nwp'
  | 'icing-dd'
  | 'icing-nwp'
  | 'icing-sfip'
  | 'inversions'
  | 'convective';

export interface SkewTOverlayDef {
  id: SkewTOverlayId;
  label: string;
  group: string;
  defaultEnabled: boolean;
}

export const SKEWT_OVERLAYS: SkewTOverlayDef[] = [
  { id: 'clouds-nwp',  label: 'Clouds (NWP)',    group: 'clouds',    defaultEnabled: true },
  { id: 'clouds-dd',   label: 'Clouds (DD)',      group: 'clouds',    defaultEnabled: false },
  { id: 'icing-nwp',   label: 'Icing (Ogimet-NWP)', group: 'icing',  defaultEnabled: true },
  { id: 'icing-dd',    label: 'Icing (Ogimet-DD)',   group: 'icing',  defaultEnabled: false },
  { id: 'icing-sfip',  label: 'Icing (SFIP)',     group: 'icing',     defaultEnabled: false },
  { id: 'inversions',  label: 'Inversions',       group: 'stability', defaultEnabled: true },
  { id: 'convective',  label: 'Convective zone',  group: 'stability', defaultEnabled: false },
];

export function getDefaultOverlayState(): Record<string, boolean> {
  const state: Record<string, boolean> = {};
  for (const o of SKEWT_OVERLAYS) state[o.id] = o.defaultEnabled;
  return state;
}

/** Get pressure bounds for a layer, falling back to altitude conversion. */
function getBandPressures(
  layer: { base_pressure_hpa?: number | null; top_pressure_hpa?: number | null; base_ft?: number; top_ft?: number },
): { topP: number; bottomP: number } | null {
  const topP = layer.top_pressure_hpa ?? (layer.top_ft != null ? altitudeToPressure(layer.top_ft) : null);
  const bottomP = layer.base_pressure_hpa ?? (layer.base_ft != null ? altitudeToPressure(layer.base_ft) : null);
  if (topP == null || bottomP == null) return null;
  return { topP, bottomP };
}

/** Draw a horizontal band between two pressures. */
function drawBand(
  ctx: CanvasRenderingContext2D,
  transform: SkewTTransform,
  topPressure: number,
  bottomPressure: number,
  color: string,
): void {
  const plot = transform.plotArea;
  const yTop = transform.pressureToY(topPressure);
  const yBottom = transform.pressureToY(bottomPressure);
  ctx.fillStyle = color;
  ctx.fillRect(plot.left, yTop, plot.width, yBottom - yTop);
}

/** Render all enabled overlay bands. */
export function renderOverlayBands(
  ctx: CanvasRenderingContext2D,
  transform: SkewTTransform,
  data: SoundingProfileData,
  enabled: Record<string, boolean>,
): void {
  const plot = transform.plotArea;
  ctx.save();
  ctx.beginPath();
  ctx.rect(plot.left, plot.top, plot.width, plot.height);
  ctx.clip();

  // Cloud bands
  if (enabled['clouds-dd']) {
    renderCloudBands(ctx, transform, data.cloud_layers, CLOUD_DD_COLOR);
  }
  if (enabled['clouds-nwp']) {
    renderCloudBands(ctx, transform, data.nwp_cloud_layers, CLOUD_NWP_COLOR);
  }

  // Icing zones
  if (enabled['icing-dd']) {
    renderIcingBands(ctx, transform, data.icing_zones);
  }
  if (enabled['icing-nwp']) {
    renderIcingBands(ctx, transform, data.icing_ogimet_nwp_zones);
  }
  if (enabled['icing-sfip']) {
    renderSfipBands(ctx, transform, data.sfip_zones);
  }

  // Inversions
  if (enabled['inversions']) {
    renderInversionBands(ctx, transform, data.inversion_layers);
  }

  // Convective zone (LFC → EL)
  if (enabled['convective']) {
    renderConvectiveZone(ctx, transform, data);
  }

  ctx.restore();
}

function renderCloudBands(
  ctx: CanvasRenderingContext2D,
  transform: SkewTTransform,
  layers: CloudLayer[],
  colorTemplate: string,
): void {
  for (const layer of layers) {
    const bounds = getBandPressures(layer);
    if (!bounds) continue;
    const alpha = COVERAGE_ALPHA[layer.coverage] ?? 0.30;
    const color = colorTemplate.replace('ALPHA', alpha.toFixed(2));
    drawBand(ctx, transform, bounds.topP, bounds.bottomP, color);
  }
}

function renderIcingBands(
  ctx: CanvasRenderingContext2D,
  transform: SkewTTransform,
  zones: IcingZone[],
): void {
  for (const zone of zones) {
    const bounds = getBandPressures(zone);
    if (!bounds) continue;
    const color = ICING_COLORS[zone.risk] ?? ICING_COLORS.LIGHT;
    if (color === 'transparent') continue;
    drawBand(ctx, transform, bounds.topP, bounds.bottomP, color);
  }
}

function renderSfipBands(
  ctx: CanvasRenderingContext2D,
  transform: SkewTTransform,
  zones: Record<string, unknown>[],
): void {
  for (const zone of zones) {
    const bounds = getBandPressures(zone as { base_pressure_hpa?: number; top_pressure_hpa?: number; base_ft?: number; top_ft?: number });
    if (!bounds) continue;
    const risk = (zone.risk as string) ?? 'LIGHT';
    const color = ICING_COLORS[risk] ?? ICING_COLORS.LIGHT;
    if (color === 'transparent') continue;
    drawBand(ctx, transform, bounds.topP, bounds.bottomP, color);
  }
}

function renderInversionBands(
  ctx: CanvasRenderingContext2D,
  transform: SkewTTransform,
  layers: InversionLayer[],
): void {
  const [r, g, b] = INVERSION_BASE_RGB;
  for (const layer of layers) {
    const bounds = getBandPressures(layer);
    if (!bounds) continue;
    const strength = layer.strength_c ?? 1;
    const t = Math.min(strength / INVERSION_OPACITY.maxC, 1);
    const alpha = Math.min(INVERSION_OPACITY.floor + t * INVERSION_OPACITY.scale, INVERSION_OPACITY.cap);
    drawBand(ctx, transform, bounds.topP, bounds.bottomP, `rgba(${r}, ${g}, ${b}, ${alpha.toFixed(2)})`);
  }
}

function renderConvectiveZone(
  ctx: CanvasRenderingContext2D,
  transform: SkewTTransform,
  data: SoundingProfileData,
): void {
  const indices = data.indices;
  if (!indices) return;
  const lfcP = indices.lfc_pressure_hpa as number | null;
  const elP = indices.el_pressure_hpa as number | null;
  if (!lfcP || !elP) return;
  drawBand(ctx, transform, elP, lfcP, CONVECTIVE_COLOR);
}
