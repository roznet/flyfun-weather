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

/**
 * Continuous cloud fill from dewpoint depression (0–3°C).
 * DD ≈ 0 → dense gray, high opacity.
 * DD ≈ 3 → light gray, lower opacity.
 * Falls back to coverage-based gray fill when DD unavailable.
 */
export function cloudFillFromDD(dd: number | undefined, coverage: string): string {
  if (dd === undefined) {
    const opacity = Math.min(0.85, coverageOpacity(coverage) + 0.15);
    return `rgba(180, 185, 190, ${opacity.toFixed(2)})`;
  }
  const t = Math.min(1, Math.max(0, dd / 3));
  const r = Math.round(140 + 60 * t);
  const g = Math.round(145 + 60 * t);
  const b = Math.round(155 + 55 * t);
  const a = 0.88 - 0.55 * t;
  return `rgba(${r}, ${g}, ${b}, ${a.toFixed(2)})`;
}

export function inversionOpacity(strengthC: number): number {
  // NWP inversions are typically 0.1–3°C at standard pressure levels.
  // Scale so even a 0.5°C inversion is visible (0.25) and 3°C saturates (0.65).
  return Math.min(0.65, 0.15 + 0.5 * Math.min(strengthC / 3, 1));
}

/** Standard atmosphere altitude→pressure (approximate for display ticks). */
export function altitudeToPressureHpa(altitudeFt: number): number {
  // Simplified barometric formula for standard atmosphere
  const altitudeM = altitudeFt * 0.3048;
  return 1013.25 * Math.pow(1 - 0.0000225577 * altitudeM, 5.25588);
}
