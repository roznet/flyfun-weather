/** The scrim's coverage guard (highlight-layer).
 *
 * A scrim says "look here, not there". Once the cut-outs cover most of the plot
 * that sentence inverts: the small *unflagged* remainder becomes the only dimmed
 * thing, so the eye lands on the one stretch that is fine. Observed on
 * `ifr_feasibility` over a convective route — 14 regions covering 71% of the
 * plot, two of them full-column ghosts — which read as blank white boxes.
 */
import { describe, it, expect } from 'vitest';
import { cutoutCoverage, SCRIM_MAX_COVERAGE } from '../../ts/visualization/cross-section/layers/highlight-layer';
import type { HighlightRegion } from '../../ts/types/advisories';

/** 0-100nm mapped onto a 0-100px plot, so nm and px read 1:1 in the assertions. */
const transform = {
  plotArea: { left: 0, top: 0, width: 100, height: 50, bottom: 50 },
  distanceToX: (nm: number) => nm,
  altitudeToY: (ft: number) => ft,
} as never;

const region = (from: number, to: number): HighlightRegion => ({
  dist_from_nm: from, dist_to_nm: to,
  base_ft: null, top_ft: null, kind: 'tower', severity: 'amber',
} as HighlightRegion);

describe('cutoutCoverage', () => {
  it('is zero with no regions', () => {
    expect(cutoutCoverage(transform, [])).toBe(0);
  });

  it('measures a single span as a fraction of plot width', () => {
    expect(cutoutCoverage(transform, [region(10, 30)])).toBeCloseTo(0.2);
  });

  it('unions overlapping regions rather than summing them', () => {
    // An icing band and a convective tower at the same distance are two
    // regions; summing would report 0.4 for a chart that is 80% clear.
    expect(cutoutCoverage(transform, [region(10, 30), region(20, 30)])).toBeCloseTo(0.2);
  });

  it('adds disjoint spans', () => {
    expect(cutoutCoverage(transform, [region(0, 20), region(60, 80)])).toBeCloseTo(0.4);
  });

  it('merges spans that touch without double counting the seam', () => {
    expect(cutoutCoverage(transform, [region(0, 50), region(50, 90)])).toBeCloseTo(0.9);
  });

  it('is order-independent', () => {
    const a = cutoutCoverage(transform, [region(60, 80), region(0, 20)]);
    const b = cutoutCoverage(transform, [region(0, 20), region(60, 80)]);
    expect(a).toBeCloseTo(b);
  });

  it('handles a reversed span without going negative', () => {
    expect(cutoutCoverage(transform, [region(30, 10)])).toBeCloseTo(0.2);
  });

  it('clears the threshold for a focused highlight and trips it for a route-wide one', () => {
    // vmc_cruise on the reported pack: 12% of route flagged — a real spotlight.
    expect(cutoutCoverage(transform, [region(0, 12)])).toBeLessThanOrEqual(SCRIM_MAX_COVERAGE);
    // ifr_feasibility on the same pack: ~71% — not a spotlight any more.
    expect(cutoutCoverage(transform, [region(0, 71)])).toBeGreaterThan(SCRIM_MAX_COVERAGE);
  });
});
