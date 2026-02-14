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

export function inversionOpacity(strengthC: number): number {
  return Math.min(0.6, 0.2 * (strengthC / 10));
}

/** Standard atmosphere altitude→pressure (approximate for display ticks). */
export function altitudeToPressureHpa(altitudeFt: number): number {
  // Simplified barometric formula for standard atmosphere
  const altitudeM = altitudeFt * 0.3048;
  return 1013.25 * Math.pow(1 - 0.0000225577 * altitudeM, 5.25588);
}
