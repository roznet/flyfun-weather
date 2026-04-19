/**
 * Renders Skew-T background grid lines (isotherms, adiabats, mixing ratios, isobars)
 * to an offscreen canvas for caching.
 *
 * The background is static for a given viewport size and transform config.
 * It's rendered once and blitted on each frame.
 */

import { SkewTTransform } from './skewt-transform';
import { SkewTConfig } from './types';
import {
  generateIsotherms,
  generateIsobars,
  generateDryAdiabats,
  generateMoistAdiabats,
  generateMixingRatioLines,
  AtmosphericPoint,
} from './thermodynamics';

// Colors matching rzskewt defaults
const ISOTHERM_COLOR = 'rgba(120, 120, 120, 0.3)';
const ZERO_ISOTHERM_COLOR = 'rgba(0, 180, 220, 0.6)';
const DRY_ADIABAT_COLOR = 'rgba(200, 80, 80, 0.25)';
const MOIST_ADIABAT_COLOR = 'rgba(80, 160, 80, 0.25)';
const MIXING_RATIO_COLOR = 'rgba(80, 80, 200, 0.2)';
const ISOBAR_COLOR = 'rgba(120, 120, 120, 0.3)';

/** Cache key for invalidation. */
export interface BackgroundCacheKey {
  width: number;
  height: number;
  dpr: number;
  pBottom: number;
  pTop: number;
  tMin: number;
  tMax: number;
}

function cacheKeyMatches(a: BackgroundCacheKey, b: BackgroundCacheKey): boolean {
  return a.width === b.width && a.height === b.height && a.dpr === b.dpr
    && a.pBottom === b.pBottom && a.pTop === b.pTop
    && a.tMin === b.tMin && a.tMax === b.tMax;
}

/** Draw a polyline through atmospheric points. */
function drawCurve(
  ctx: CanvasRenderingContext2D,
  transform: SkewTTransform,
  points: AtmosphericPoint[],
  color: string,
  lineWidth: number = 0.5,
  dash: number[] = [],
): void {
  if (points.length < 2) return;
  ctx.strokeStyle = color;
  ctx.lineWidth = lineWidth;
  ctx.setLineDash(dash);
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

export class BackgroundLinesRenderer {
  private cache: HTMLCanvasElement | null = null;
  private cacheKey: BackgroundCacheKey | null = null;

  /**
   * Render background lines to the target canvas context.
   * Uses an offscreen cache — only re-renders when size/config changes.
   */
  render(
    ctx: CanvasRenderingContext2D,
    transform: SkewTTransform,
    config: SkewTConfig,
    canvasWidth: number,
    canvasHeight: number,
    dpr: number,
  ): void {
    const key: BackgroundCacheKey = {
      width: canvasWidth, height: canvasHeight, dpr,
      pBottom: config.pBottom, pTop: config.pTop,
      tMin: config.tMin, tMax: config.tMax,
    };

    if (!this.cache || !this.cacheKey || !cacheKeyMatches(this.cacheKey, key)) {
      this.cache = document.createElement('canvas');
      this.cache.width = canvasWidth;
      this.cache.height = canvasHeight;
      const offCtx = this.cache.getContext('2d')!;
      offCtx.scale(dpr, dpr);
      this.renderToContext(offCtx, transform, config);
      this.cacheKey = key;
    }

    ctx.drawImage(this.cache, 0, 0);
  }

  private renderToContext(
    ctx: CanvasRenderingContext2D,
    transform: SkewTTransform,
    config: SkewTConfig,
  ): void {
    const { pBottom, pTop, tMin, tMax } = config;
    const plot = transform.plotArea;

    // Clip to plot area
    ctx.save();
    ctx.beginPath();
    ctx.rect(plot.left, plot.top, plot.width, plot.height);
    ctx.clip();

    // 1. Isobars — horizontal lines at standard pressure levels
    const isobars = generateIsobars(pBottom, pTop);
    ctx.strokeStyle = ISOBAR_COLOR;
    ctx.lineWidth = 0.5;
    for (const p of isobars) {
      const y = transform.pressureToY(p);
      ctx.beginPath();
      ctx.moveTo(plot.left, y);
      ctx.lineTo(plot.right, y);
      ctx.stroke();
    }

    // 2. Isotherms — skewed vertical lines
    const isotherms = generateIsotherms(pBottom, pTop, tMin, tMax, 10);
    for (const iso of isotherms) {
      const t = iso[0].tempC;
      const color = t === 0 ? ZERO_ISOTHERM_COLOR : ISOTHERM_COLOR;
      const width = t === 0 ? 1.5 : 0.5;
      drawCurve(ctx, transform, iso, color, width);
    }

    // 3. Dry adiabats — constant θ curves
    const dryAdiabats = generateDryAdiabats(pBottom, pTop, 20);
    for (const adiabat of dryAdiabats) {
      drawCurve(ctx, transform, adiabat, DRY_ADIABAT_COLOR, 0.5);
    }

    // 4. Moist adiabats — dashed curves
    const moistAdiabats = generateMoistAdiabats(pBottom, pTop, 5);
    for (const adiabat of moistAdiabats) {
      drawCurve(ctx, transform, adiabat, MOIST_ADIABAT_COLOR, 0.5, [4, 4]);
    }

    // 5. Mixing ratio lines — nearly vertical, dashed
    const mixingLines = generateMixingRatioLines(pBottom, pTop);
    for (const { points } of mixingLines) {
      drawCurve(ctx, transform, points, MIXING_RATIO_COLOR, 0.5, [2, 4]);
    }

    ctx.restore();
  }

  /** Invalidate the cache (e.g., on resize). */
  invalidate(): void {
    this.cache = null;
    this.cacheKey = null;
  }
}
