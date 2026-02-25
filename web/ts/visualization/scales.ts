/** Color and opacity scale functions for visualization layers. */

// --- Risk-based colors ---

const ICING_RISK_COLORS: Record<string, string> = {
  none: 'transparent',
  light: 'rgba(100, 149, 237, 0.35)',   // cornflower blue
  moderate: 'rgba(255, 165, 0, 0.45)',   // orange
  severe: 'rgba(220, 53, 69, 0.55)',     // red
};

const CAT_RISK_COLORS: Record<string, string> = {
  none: 'transparent',
  light: 'rgba(255, 193, 7, 0.20)',      // amber light
  moderate: 'rgba(255, 152, 0, 0.40)',   // amber
  severe: 'rgba(220, 53, 69, 0.55)',     // red
};

const CONVECTIVE_RISK_COLORS: Record<string, string> = {
  none: 'transparent',
  marginal: 'rgba(160, 160, 160, 0.08)', // faint gray (shallow convection)
  low: 'rgba(255, 235, 59, 0.10)',       // faint yellow
  moderate: 'rgba(255, 152, 0, 0.15)',   // faint orange
  high: 'rgba(220, 53, 69, 0.20)',       // faint red
  extreme: 'rgba(183, 28, 28, 0.25)',    // dark red
};

const COVERAGE_OPACITY: Record<string, number> = {
  sct: 0.25,
  bkn: 0.50,
  ovc: 0.75,
};

export function icingRiskColor(risk: string): string {
  return ICING_RISK_COLORS[risk] ?? 'transparent';
}

export function catRiskColor(risk: string): string {
  return CAT_RISK_COLORS[risk] ?? 'transparent';
}

export function convectiveRiskColor(risk: string): string {
  return CONVECTIVE_RISK_COLORS[risk] ?? 'transparent';
}

export function coverageOpacity(coverage: string): number {
  return COVERAGE_OPACITY[coverage] ?? 0.3;
}

/** Coverage multiplier: SCT layers render more transparent than BKN/OVC. */
/** Coverage alpha range: [min, max] opacity for each coverage class. */
const COVERAGE_ALPHA: Record<string, [number, number]> = {
  sct: [0.40, 0.55],
  bkn: [0.50, 0.80],
  ovc: [0.60, 0.92],
};

function coverageAlpha(coverage: string, t: number): number {
  const [lo, hi] = COVERAGE_ALPHA[coverage] ?? [0.30, 0.65];
  return hi - (hi - lo) * t;
}

/**
 * Continuous cloud fill from dewpoint depression (0–3°C) modulated by coverage.
 * DD ≈ 0 → medium gray (dense cloud), high opacity.
 * DD ≈ 3 → white (thin cloud edge), lower opacity with visible floor.
 * Falls back to coverage-based fill when DD unavailable.
 */
export function cloudFillFromDD(dd: number | undefined, coverage: string): string {
  if (dd === undefined) {
    const [, hi] = COVERAGE_ALPHA[coverage] ?? [0.30, 0.65];
    return `rgba(190, 190, 195, ${hi.toFixed(2)})`;
  }
  const t = Math.min(1, Math.max(0, dd / 3));
  // White (245) at DD=3 → medium gray (170) at DD=0
  const r = Math.round(170 + 75 * t);
  const g = Math.round(170 + 75 * t);
  const b = Math.round(175 + 73 * t);
  const a = coverageAlpha(coverage, t);
  return `rgba(${r}, ${g}, ${b}, ${a.toFixed(2)})`;
}

export function inversionOpacity(strengthC: number): number {
  // NWP inversions are typically 0.1–3°C at standard pressure levels.
  // Scale so even a 0.5°C inversion is visible (0.25) and 3°C saturates (0.65).
  return Math.min(0.65, 0.15 + 0.5 * Math.min(strengthC / 3, 1));
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
