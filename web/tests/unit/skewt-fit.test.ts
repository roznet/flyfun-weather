import { describe, it, expect } from 'vitest';
import { fitSkewT, skewedExtent, SkewTTransform, SKEW_SHIFT_DEG } from '../../ts/visualization/skewt/skewt-transform';
import { DEFAULT_CONFIG } from '../../ts/visualization/skewt/types';

/** Visual isotherm angle (degrees from horizontal) for a fitted box. */
function isothermAngle(fit: { width: number; height: number; tMin: number; tMax: number }): number {
  const cfg = { ...DEFAULT_CONFIG, tMin: fit.tMin, tMax: fit.tMax };
  const t = new SkewTTransform({ left: 0, top: 0, width: fit.width, height: fit.height, right: fit.width, bottom: fit.height }, cfg);
  const a = t.toPixel(0, cfg.pBottom);
  const b = t.toPixel(0, cfg.pTop);
  return Math.atan2(a.y - b.y, b.x - a.x) * 180 / Math.PI;
}

describe('fitSkewT', () => {
  it('leaves the classic wide web layout unchanged (−60…+40, aspect 1.8)', () => {
    const fit = fitSkewT(1300, 382, [-20, 20]);
    expect(fit.tMin).toBe(-60);
    expect(fit.tMax).toBe(40);
    expect(fit.height).toBe(382);
    expect(fit.width).toBeCloseTo(382 * 1.8, 6);
  });

  it('narrow box: window = shift·w/h, centred on the sounding, 45° isotherms', () => {
    const fit = fitSkewT(300, 360, [-12, 18]);
    expect(fit.width).toBe(300);
    expect(fit.height).toBe(360);
    expect(fit.tMax - fit.tMin).toBeCloseTo(SKEW_SHIFT_DEG * 300 / 360, 6);
    expect((fit.tMin + fit.tMax) / 2).toBeCloseTo(3, 6);
    expect(isothermAngle(fit)).toBeCloseTo(45, 6);
  });

  it('tall narrow box shrinks the height so the sounding fits', () => {
    const fit = fitSkewT(220, 765, [-18, 18]); // need 46°
    expect(fit.height).toBeCloseTo(SKEW_SHIFT_DEG * 220 / 46, 6);
    expect(fit.tMax - fit.tMin).toBeCloseTo(46, 6);
    expect(isothermAngle(fit)).toBeCloseTo(45, 6);
  });

  it('never goes below the minimum height (sounding may clip)', () => {
    const fit = fitSkewT(140, 380, [-50, 20]);
    expect(fit.height).toBe(220);
    expect(fit.tMax - fit.tMin).toBeLessThan(80);
  });

  it('keeps the window inside the config range', () => {
    const fit = fitSkewT(300, 360, [30, 45]);
    expect(fit.tMax).toBeCloseTo(40, 6);
    const cold = fitSkewT(300, 360, [-80, -70]);
    expect(cold.tMin).toBeCloseTo(-60, 6);
  });

  it('a very wide sounding falls back to the full range at the same geometry', () => {
    const fit = fitSkewT(500, 600, [-70, 50]);
    expect(fit.tMin).toBe(-60);
    expect(fit.tMax).toBe(40);
    expect(isothermAngle(fit)).toBeCloseTo(45, 6);
  });
});

describe('skewedExtent', () => {
  it('reads each point at the bottom axis and ignores out-of-range pressures', () => {
    const ext = skewedExtent([
      { t: 12, p: 1050 },
      { t: -45, p: 250 },
      { t: null, p: 500 },
      { t: -60, p: 200 },
    ]);
    expect(ext![0]).toBeCloseTo(-45 + SKEW_SHIFT_DEG, 6);
    expect(ext![1]).toBeCloseTo(12, 6);
  });
  it('returns null with no points in range', () => {
    expect(skewedExtent([])).toBeNull();
  });
});
