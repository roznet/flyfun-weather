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
