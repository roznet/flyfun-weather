/** Color and opacity scale functions for visualization layers.
 *
 * Cross-section colors are read from the active theme (see cross-section/theme.ts).
 * Map colors remain hardcoded here (separate scope, not themed).
 */

import { getActiveTheme } from './cross-section/theme';

// --- Risk-based colors (theme-aware) ---

export function icingRiskColor(risk: string): string {
  return getActiveTheme().icing[risk] ?? 'transparent';
}

export function catRiskColor(risk: string): string {
  return getActiveTheme().cat[risk] ?? 'transparent';
}

export function convectiveRiskColor(risk: string): string {
  return getActiveTheme().convective.riskColors[risk] ?? 'transparent';
}

export function coverageOpacity(coverage: string): number {
  return getActiveTheme().coverageOpacity[coverage] ?? 0.3;
}

function coverageAlpha(coverage: string, t: number): number {
  const theme = getActiveTheme();
  const [lo, hi] = theme.clouds.coverageAlpha[coverage] ?? [0.30, 0.65];
  return hi - (hi - lo) * t;
}

/**
 * Continuous cloud fill from dewpoint depression (0–3°C) modulated by coverage.
 * DD ≈ 0 → medium gray (dense cloud), high opacity.
 * DD ≈ 3 → white (thin cloud edge), lower opacity with visible floor.
 * Falls back to coverage-based fill when DD unavailable.
 */
export function cloudFillFromDD(dd: number | undefined, coverage: string): string {
  const theme = getActiveTheme();
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
  const a = coverageAlpha(coverage, t);
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
export function nwpCloudFill(pct: number): string {
  const theme = getActiveTheme().nwpClouds;
  const t = Math.min(1, Math.max(0, pct / 100));
  const [br, bg, bb] = theme.brightRgb;
  const [dr, dg, db] = theme.deltaRgb;
  const r = Math.round(br - dr * t);
  const g = Math.round(bg - dg * t);
  const b = Math.round(bb - db * t);
  const [opFloor, opScale] = theme.opacityRange;
  const opacity = opFloor + opScale * t;
  return `rgba(${r}, ${g}, ${b}, ${opacity.toFixed(2)})`;
}

export function inversionOpacity(strengthC: number): number {
  const { floor, scale, maxStrengthC, cap } = getActiveTheme().inversion.opacityParams;
  return Math.min(cap, floor + scale * Math.min(strengthC / maxStrengthC, 1));
}

// --- Map color scales (opaque, for polyline coloring) ---

/** Risk string to opaque color for map segments. */
const RISK_MAP_COLORS: Record<string, string> = {
  none: '#22c55e',       // green
  light: '#facc15',      // yellow
  moderate: '#f97316',   // orange
  severe: '#ef4444',     // red
  marginal: '#a3a3a3',   // gray
  low: '#facc15',
  high: '#ef4444',
  extreme: '#991b1b',
};

export function riskMapColor(risk: string): string {
  return RISK_MAP_COLORS[risk] ?? '#22c55e';
}

/** Continuous cloud cover percentage → gray scale. */
export function cloudCoverMapColor(pct: number): string {
  const t = Math.min(1, Math.max(0, pct / 100));
  const v = Math.round(220 - 160 * t);
  return `rgb(${v}, ${v}, ${v + 10})`;
}

/** Headwind diverging scale: green (tailwind) → white → red (headwind). */
export function headwindMapColor(kt: number): string {
  const t = Math.min(1, Math.max(0, Math.abs(kt) / 30));
  if (kt >= 0) {
    // headwind: white → red
    const r = 255;
    const g = Math.round(255 - 200 * t);
    const b = Math.round(255 - 200 * t);
    return `rgb(${r}, ${g}, ${b})`;
  } else {
    // tailwind: white → green
    const r = Math.round(255 - 200 * t);
    const g = Math.round(255 - 60 * t);
    const b = Math.round(255 - 200 * t);
    return `rgb(${r}, ${g}, ${b})`;
  }
}

/** CAPE (J/kg) → green→yellow→red continuous. */
export function capeMapColor(jkg: number): string {
  if (jkg <= 0) return '#22c55e';
  if (jkg >= 2000) return '#dc2626';
  const t = Math.min(1, jkg / 2000);
  if (t < 0.5) {
    const s = t * 2;
    const r = Math.round(34 + 221 * s);
    const g = Math.round(197 - 10 * s);
    const b = Math.round(94 - 73 * s);
    return `rgb(${r}, ${g}, ${b})`;
  } else {
    const s = (t - 0.5) * 2;
    const r = Math.round(255 - 35 * s);
    const g = Math.round(187 - 149 * s);
    const b = Math.round(21 + 17 * s);
    return `rgb(${r}, ${g}, ${b})`;
  }
}

/** Freezing level (ft) → blue gradient (low=cold=darker blue). */
export function freezingLevelMapColor(ft: number): string {
  // 0ft (danger) = dark blue, 15000ft (safe) = light blue
  const t = Math.min(1, Math.max(0, ft / 15000));
  const r = Math.round(30 + 175 * t);
  const g = Math.round(60 + 175 * t);
  const b = Math.round(200 + 55 * t);
  return `rgb(${r}, ${g}, ${b})`;
}

/** NWP ceiling (ft) → METAR-category-like coloring. */
export function ceilingMapColor(ft: number | null): string {
  if (ft === null) return '#22c55e';
  if (ft < 200) return '#8e24aa';   // LIFR — purple
  if (ft < 500) return '#dc3545';   // LIFR — red
  if (ft < 1000) return '#dc3545';  // IFR — red
  if (ft < 3000) return '#f59e0b';  // MVFR — amber
  return '#22c55e';                  // VFR — green
}

/** Temperature (°C) → blue→white→red diverging. */
export function temperatureMapColor(c: number): string {
  // -20°C = blue, 0°C = white-ish, +40°C = red
  const t = Math.min(1, Math.max(0, (c + 20) / 60));
  if (t < 0.33) {
    const s = t / 0.33;
    return `rgb(${Math.round(30 + 125 * s)}, ${Math.round(80 + 125 * s)}, ${Math.round(220 + 35 * s)})`;
  } else if (t < 0.67) {
    const s = (t - 0.33) / 0.34;
    return `rgb(${Math.round(155 + 100 * s)}, ${Math.round(205 + 50 * s)}, ${Math.round(255 - 40 * s)})`;
  } else {
    const s = (t - 0.67) / 0.33;
    return `rgb(255, ${Math.round(255 - 185 * s)}, ${Math.round(215 - 175 * s)})`;
  }
}

/** Crosswind magnitude → white→purple. */
export function crosswindMapColor(kt: number): string {
  const t = Math.min(1, Math.abs(kt) / 25);
  const r = Math.round(255 - 135 * t);
  const g = Math.round(255 - 197 * t);
  const b = Math.round(255 - 18 * t);
  return `rgb(${r}, ${g}, ${b})`;
}

/** Model agreement → green/orange/red discrete. */
export function agreementMapColor(agreement: string): string {
  if (agreement === 'poor') return '#ef4444';
  if (agreement === 'moderate') return '#f97316';
  return '#22c55e';
}

/** Linear width mapping: value 0→minW, value max→maxW. */
export function linearWidth(value: number, max: number, minW: number, maxW: number): number {
  const t = Math.min(1, Math.max(0, Math.abs(value) / max));
  return minW + (maxW - minW) * t;
}

/** Standard atmosphere altitude→pressure (approximate for display ticks). */
export function altitudeToPressureHpa(altitudeFt: number): number {
  // Simplified barometric formula for standard atmosphere
  const altitudeM = altitudeFt * 0.3048;
  return 1013.25 * Math.pow(1 - 0.0000225577 * altitudeM, 5.25588);
}
