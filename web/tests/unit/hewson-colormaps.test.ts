/** Tests for Hewson colormap value→color mapping and range resolution. */

import { describe, it, expect } from 'vitest';
import {
  colorFor, vRangeFor, gradientCss, COLORMAPS,
  type HewsonMetric,
} from '../../ts/visualization/hewson-colormaps';

describe('vRangeFor', () => {
  it('returns the metric-specific default range', () => {
    const r = vRangeFor('theta_e', 'default');
    expect(r.vmin).toBe(COLORMAPS.theta_e.defaultVmin);
    expect(r.vmax).toBe(COLORMAPS.theta_e.defaultVmax);
  });

  it('returns the storm range for storm scale', () => {
    const r = vRangeFor('gradient', 'storm');
    expect(r.vmin).toBe(COLORMAPS.gradient.stormVmin);
    expect(r.vmax).toBe(COLORMAPS.gradient.stormVmax);
  });

  it('storm range is at least as wide as default (gradient)', () => {
    const def = vRangeFor('gradient', 'default');
    const storm = vRangeFor('gradient', 'storm');
    expect(storm.vmax - storm.vmin).toBeGreaterThanOrEqual(def.vmax - def.vmin);
  });
});

describe('colorFor', () => {
  it('returns transparent rgba for null', () => {
    expect(colorFor('gradient', null, 0, 10)).toBe('rgba(0,0,0,0)');
  });

  it('returns transparent rgba for NaN/Infinity', () => {
    expect(colorFor('gradient', NaN, 0, 10)).toBe('rgba(0,0,0,0)');
    expect(colorFor('gradient', Infinity, 0, 10)).toBe('rgba(0,0,0,0)');
  });

  it('produces valid hex colors at boundary and midpoint', () => {
    for (const v of [0, 5, 10]) {
      const c = colorFor('gradient', v, 0, 10);
      expect(c).toMatch(/^#[0-9a-f]{6}$/);
    }
  });

  it('first anchor at vmin, last anchor at vmax', () => {
    const c0 = colorFor('gradient', 0, 0, 10);
    expect(c0).toBe(COLORMAPS.gradient.ramp[0]);
    const c1 = colorFor('gradient', 10, 0, 10);
    expect(c1).toBe(COLORMAPS.gradient.ramp[COLORMAPS.gradient.ramp.length - 1]);
  });

  it('clamps below vmin to first anchor', () => {
    const cBelow = colorFor('gradient', -100, 0, 10);
    expect(cBelow).toBe(COLORMAPS.gradient.ramp[0]);
  });

  it('clamps above vmax to last anchor', () => {
    const cAbove = colorFor('gradient', 1000, 0, 10);
    expect(cAbove).toBe(COLORMAPS.gradient.ramp[COLORMAPS.gradient.ramp.length - 1]);
  });

  it('handles vmax==vmin by returning the ramp midpoint', () => {
    // 9 anchors → midpoint is index 4
    const c = colorFor('gradient', 5, 5, 5);
    expect(c).toBe(COLORMAPS.gradient.ramp[4]);
  });

  it('produces monotone ramp for sequential metric (gradient)', () => {
    // Sample a few points and ensure the colors differ — quick sanity check
    // that interpolation is wired correctly.
    const c0 = colorFor('gradient', 0, 0, 10);
    const c5 = colorFor('gradient', 5, 0, 10);
    const c10 = colorFor('gradient', 10, 0, 10);
    expect(new Set([c0, c5, c10]).size).toBe(3);
  });
});

describe('gradientCss', () => {
  it('returns a linear-gradient with stops at evenly spaced percentages', () => {
    for (const metric of Object.keys(COLORMAPS) as HewsonMetric[]) {
      const css = gradientCss(metric);
      expect(css).toMatch(/^linear-gradient\(to right,/);
      const ramp = COLORMAPS[metric].ramp;
      expect(css).toContain(`${ramp[0]} 0%`);
      expect(css).toContain(`${ramp[ramp.length - 1]} 100%`);
    }
  });
});

describe('COLORMAPS catalog integrity', () => {
  it('every metric has a 9-anchor ramp', () => {
    for (const [name, spec] of Object.entries(COLORMAPS)) {
      expect(spec.ramp.length, `${name} ramp length`).toBe(9);
    }
  });

  it('every anchor is a 7-char hex color', () => {
    for (const spec of Object.values(COLORMAPS)) {
      for (const hex of spec.ramp) {
        expect(hex).toMatch(/^#[0-9a-f]{6}$/i);
      }
    }
  });

  it('storm range includes default range for sequential metrics', () => {
    for (const [name, spec] of Object.entries(COLORMAPS)) {
      if (spec.type !== 'sequential') continue;
      expect(spec.stormVmin, `${name} stormVmin`).toBeLessThanOrEqual(spec.defaultVmin);
      expect(spec.stormVmax, `${name} stormVmax`).toBeGreaterThanOrEqual(spec.defaultVmax);
    }
  });
});
