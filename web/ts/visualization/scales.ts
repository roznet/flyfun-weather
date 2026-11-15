/** Color and opacity scale functions for visualization layers.
 *
 * Cross-section colors are read from the active theme (see cross-section/theme.ts).
 * Map colors remain hardcoded here (separate scope, not themed).
 *
 * Every theme-aware function takes an optional explicit `theme`, defaulting to
 * the active one. Surfaces that render a theme the user has not applied yet —
 * the theme preview being the only one today — pass it explicitly so their
 * swatches come from these same formulas instead of a local copy that drifts.
 */

import { getActiveTheme, type CrossSectionTheme } from './cross-section/theme';

// --- Risk-based colors (theme-aware) ---

export function icingRiskColor(risk: string, theme: CrossSectionTheme = getActiveTheme()): string {
  return theme.icing[risk] ?? 'transparent';
}

export function catRiskColor(risk: string, theme: CrossSectionTheme = getActiveTheme()): string {
  return theme.cat[risk] ?? 'transparent';
}

export function convectiveRiskColor(risk: string, theme: CrossSectionTheme = getActiveTheme()): string {
  return theme.convective.riskColors[risk] ?? 'transparent';
}

export function coverageOpacity(coverage: string, theme: CrossSectionTheme = getActiveTheme()): number {
  return theme.coverageOpacity[coverage] ?? 0.3;
}

function coverageAlpha(coverage: string, t: number, theme: CrossSectionTheme): number {
  const [lo, hi] = theme.clouds.coverageAlpha[coverage] ?? [0.30, 0.65];
  return hi - (hi - lo) * t;
}

/**
 * Continuous cloud fill from dewpoint depression (0–3°C) modulated by coverage.
 * DD ≈ 0 → medium gray (dense cloud), high opacity.
 * DD ≈ 3 → white (thin cloud edge), lower opacity with visible floor.
 * Falls back to coverage-based fill when DD unavailable.
 */
export function cloudFillFromDD(
  dd: number | undefined,
  coverage: string,
  theme: CrossSectionTheme = getActiveTheme(),
): string {
  if (dd === undefined) {
    const [, hi] = theme.clouds.coverageAlpha[coverage] ?? [0.30, 0.65];
    const [fr, fg, fb] = theme.clouds.fallbackGray;
    return `rgba(${fr}, ${fg}, ${fb}, ${hi.toFixed(2)})`;
  }
  const t = Math.min(1, Math.max(0, dd / 3));
  const [dr, dg, db] = theme.clouds.denseRgb;
  const [tr, tg, tb] = theme.clouds.thinRgb;
  const r = Math.round(dr + (tr - dr) * t);
  const g = Math.round(dg + (tg - dg) * t);
  const b = Math.round(db + (tb - db) * t);
  const a = coverageAlpha(coverage, t, theme);
  return `rgba(${r}, ${g}, ${b}, ${a.toFixed(2)})`;
}

/**
 * Blue-tinted fill from NWP cloud cover percentage.
 * Mixes theme.nwpClouds.brightRgb → (brightRgb - deltaRgb) as pct rises 0→100,
 * with alpha rising from opacityRange[0] to opacityRange[0]+opacityRange[1].
 *
 * Used by the hatched-NWP and square-NWP cloud styles, the NWP cloud legend,
 * and any other consumer that needs the canonical NWP cloud fill.
 */
export function nwpCloudFill(pct: number, theme: CrossSectionTheme = getActiveTheme()): string {
  const nwp = theme.nwpClouds;
  const t = Math.min(1, Math.max(0, pct / 100));
  const [br, bg, bb] = nwp.brightRgb;
  const [dr, dg, db] = nwp.deltaRgb;
  const r = Math.round(br - dr * t);
  const g = Math.round(bg - dg * t);
  const b = Math.round(bb - db * t);
  const [opFloor, opScale] = nwp.opacityRange;
  const opacity = opFloor + opScale * t;
  return `rgba(${r}, ${g}, ${b}, ${opacity.toFixed(2)})`;
}

export function inversionOpacity(
  strengthC: number,
  theme: CrossSectionTheme = getActiveTheme(),
): number {
  const { floor, scale, maxStrengthC, cap } = theme.inversion.opacityParams;
  return Math.min(cap, floor + scale * Math.min(strengthC / maxStrengthC, 1));
}

// Route-map line colours live in the served catalog (#998,
// web/ts/data/route-map-metrics-catalog.json), not here.

/**
 * Combine low/mid/high cloud-cover percentages into a total using the
 * standard random-overlap approximation (assumes layer independence):
 *   total = 1 − (1 − L)(1 − M)(1 − H)
 * Avoids the double-counting bias of additive summing in the 30–60% band.
 */
export function randomOverlapPct(low: number, mid: number, high: number): number {
  const clamp = (x: number) => Math.min(100, Math.max(0, x)) / 100;
  const l = clamp(low);
  const m = clamp(mid);
  const h = clamp(high);
  return (1 - (1 - l) * (1 - m) * (1 - h)) * 100;
}

/**
 * METAR flight-category → opaque color, mirroring the `.flight-cat-*` CSS
 * literals (style.css). Case-insensitive.
 */
export function flightCategoryColor(cat: string): string {
  switch (cat.toUpperCase()) {
    case 'VFR': return '#2d8a4e';
    case 'MVFR': return '#2563eb';
    case 'IFR': return '#c0392b';
    case 'LIFR': return '#8e24aa';
    default: return '#6b7280';  // gray — unknown category
  }
}

/** Standard atmosphere altitude→pressure (approximate for display ticks). */
export function altitudeToPressureHpa(altitudeFt: number): number {
  // Simplified barometric formula for standard atmosphere
  const altitudeM = altitudeFt * 0.3048;
  return 1013.25 * Math.pow(1 - 0.0000225577 * altitudeM, 5.25588);
}
