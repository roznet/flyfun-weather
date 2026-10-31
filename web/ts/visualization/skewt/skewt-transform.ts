/**
 * Coordinate transform for Skew-T log-P diagrams.
 *
 * Maps (temperature °C, pressure hPa) ↔ pixel coordinates.
 * Ported from rzskewt SkewTTransform.swift.
 */

import { PlotArea, SkewTConfig, DEFAULT_CONFIG } from './types';

export class SkewTTransform {
  readonly config: SkewTConfig;
  readonly plotArea: PlotArea;

  private readonly logPBottom: number;
  private readonly logPTop: number;
  private readonly logRange: number;
  private readonly tRange: number;
  private readonly skewFactor: number;

  constructor(plotArea: PlotArea, config: SkewTConfig = DEFAULT_CONFIG) {
    this.config = config;
    this.plotArea = plotArea;
    this.logPBottom = Math.log(config.pBottom);
    this.logPTop = Math.log(config.pTop);
    this.logRange = this.logPBottom - this.logPTop;
    this.tRange = config.tMax - config.tMin;
    // skewFactor adjusted for aspect ratio so isotherms tilt at the configured angle
    this.skewFactor = Math.tan(config.skewAngle * Math.PI / 180) * plotArea.height / plotArea.width;
  }

  /** Pressure (hPa) → Y pixel. Higher pressure = bottom. */
  pressureToY(pressureHPa: number): number {
    const logFrac = (this.logPBottom - Math.log(pressureHPa)) / this.logRange;
    return this.plotArea.bottom - logFrac * this.plotArea.height;
  }

  /** Y pixel → Pressure (hPa). */
  yToPressure(y: number): number {
    const logFrac = (this.plotArea.bottom - y) / this.plotArea.height;
    return Math.exp(this.logPBottom - logFrac * this.logRange);
  }

  /** (Temperature °C, Pressure hPa) → X pixel. Includes skew offset. */
  temperatureToX(tempC: number, pressureHPa: number): number {
    const logFrac = (this.logPBottom - Math.log(pressureHPa)) / this.logRange;
    const skewOffset = logFrac * this.skewFactor;
    const normalizedT = (tempC - this.config.tMin) / this.tRange;
    return this.plotArea.left + (normalizedT + skewOffset) * this.plotArea.width;
  }

  /** (X pixel, Pressure hPa) → Temperature °C. Inverse of temperatureToX. */
  xToTemperature(x: number, pressureHPa: number): number {
    const logFrac = (this.logPBottom - Math.log(pressureHPa)) / this.logRange;
    const skewOffset = logFrac * this.skewFactor;
    const normalizedT = (x - this.plotArea.left) / this.plotArea.width - skewOffset;
    return normalizedT * this.tRange + this.config.tMin;
  }

  /** Convert (temperature °C, pressure hPa) to pixel coordinates. */
  toPixel(tempC: number, pressureHPa: number): { x: number; y: number } {
    return {
      x: this.temperatureToX(tempC, pressureHPa),
      y: this.pressureToY(pressureHPa),
    };
  }

  /** Check if a pressure value is within the visible range. */
  isPressureVisible(pressureHPa: number): boolean {
    return pressureHPa >= this.config.pTop && pressureHPa <= this.config.pBottom;
  }
}

// ── Fitted layout (#962) ────────────────────────────────────────────────────
//
// The diagram's look is two invariants: isotherms at 45° and a skew shift of
// SKEW_SHIFT_DEG across pBottom→pTop. With both fixed, the visible temperature
// window follows from the plot box: window = SKEW_SHIFT_DEG · w / h. A wide box
// shows the full config range (and narrows the box — centred whitespace); a
// narrow box shows a smaller window centred on the sounding, and shrinks its
// height when the sounding is wider than the window. Same rule on iOS
// (RZSkewT) and Android (rzskewt) — see designs/skewt-canvas.md.

/** Skew shift (°C) across the full pBottom→pTop range. 100° / 1.8 = the
 *  classic web layout (−60…+40 at plot aspect 1.8), which stays unchanged. */
export const SKEW_SHIFT_DEG = 100 / 1.8;

export interface SkewTFitOptions {
  /** Skew shift in °C over the full pressure range (default SKEW_SHIFT_DEG). */
  shiftDeg?: number;
  /** Padding (°C) either side of the sounding's skewed extent (default 5). */
  paddingDeg?: number;
  /** Minimum plot height (px) before the window stops widening (default 220). */
  minHeight?: number;
}

export interface SkewTFit {
  /** Plot box size (px) — width ≤ availW, height ≤ availH. */
  width: number;
  height: number;
  /** Visible temperature window (°C, read at pBottom). */
  tMin: number;
  tMax: number;
}

/** Fraction (0 at pBottom, 1 at pTop) of the log-pressure range. */
function logFraction(pressureHPa: number, config: SkewTConfig): number {
  const lb = Math.log(config.pBottom);
  return (lb - Math.log(pressureHPa)) / (lb - Math.log(config.pTop));
}

/**
 * Horizontal extent of a set of (T, p) points in skewed coordinates — i.e. the
 * temperature each point lands on when read at the bottom axis
 * (X = T + shift · logFrac). Points outside [pTop, pBottom] are ignored.
 * Returns null when nothing is in range.
 */
export function skewedExtent(
  points: Iterable<{ t: number | null | undefined; p: number }>,
  config: SkewTConfig = DEFAULT_CONFIG,
  shiftDeg: number = SKEW_SHIFT_DEG,
): [number, number] | null {
  let lo = Infinity;
  let hi = -Infinity;
  for (const { t, p } of points) {
    if (t == null || !Number.isFinite(t) || p < config.pTop || p > config.pBottom) continue;
    const x = t + shiftDeg * logFraction(p, config);
    if (x < lo) lo = x;
    if (x > hi) hi = x;
  }
  return lo <= hi ? [lo, hi] : null;
}

/**
 * Fit the plot box and temperature window to the available space and the
 * sounding's skewed extent, keeping 45° isotherms and a fixed skew shift.
 * `config.tMin..tMax` is the widest window ever shown.
 */
export function fitSkewT(
  availW: number,
  availH: number,
  extent: [number, number] | null,
  config: SkewTConfig = DEFAULT_CONFIG,
  opts: SkewTFitOptions = {},
): SkewTFit {
  const shift = opts.shiftDeg ?? SKEW_SHIFT_DEG;
  const pad = opts.paddingDeg ?? 5;
  const minH = Math.min(opts.minHeight ?? 220, availH);
  const full = config.tMax - config.tMin;
  if (availW <= 0 || availH <= 0) {
    return { width: Math.max(0, availW), height: Math.max(0, availH), tMin: config.tMin, tMax: config.tMax };
  }

  // Shrink the height when the sounding (plus padding) is wider than the
  // window the full height allows — never below minH.
  let h = availH;
  const need = extent ? Math.min(full, extent[1] - extent[0] + 2 * pad) : 0;
  if (shift * availW / h < need) {
    h = Math.max(minH, shift * availW / need);
  }

  const window = shift * availW / h;
  if (window >= full - 1e-9) {
    // Wide box: full range, box narrowed to keep the geometry.
    return { width: full * h / shift, height: h, tMin: config.tMin, tMax: config.tMax };
  }

  // Narrow box: window centred on the sounding, kept inside the full range.
  const centre = extent ? (extent[0] + extent[1]) / 2 : (config.tMin + config.tMax) / 2;
  const tMin = Math.min(Math.max(centre - window / 2, config.tMin), config.tMax - window);
  return { width: availW, height: h, tMin, tMax: tMin + window };
}
