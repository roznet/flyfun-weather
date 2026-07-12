/** Map metric registry — defines how route data maps to color/width for map segments. */

import type { VizPoint } from '../types';
import {
  riskMapColor, cloudCoverMapColor, headwindMapColor,
  capeMapColor, freezingLevelMapColor, ceilingMapColor, temperatureMapColor,
  agreementMapColor, linearWidth,
} from '../scales';
import { t } from '../../i18n/i18n';

export interface MapMetric {
  readonly id: string;
  readonly label: string;
  readonly unit: string;
  readonly altitudeDependent: boolean;
  getValue(point: VizPoint, altitudeFt?: number): number | null;
  getColor(value: number): string;
  getWidth(value: number): number;
  formatValue(value: number): string;
  legendStops: Array<{ value: number; label: string; color: string }>;
}

// --- Helpers for altitude-dependent zone search ---

/** Risk numeric encoding for comparison (shared by icing/CAT/convective). */
const RISK_ORDER: Record<string, number> = {
  none: 0, marginal: 1, light: 1, low: 1, moderate: 2, high: 3, severe: 3, extreme: 4,
};
const RISK_LABELS = ['none', 'light', 'moderate', 'severe', 'extreme'];

function worstRiskAtAlt(
  zones: Array<{ baseFt: number; topFt: number; risk: string }>,
  altFt: number,
): string {
  let worst = 0;
  for (const z of zones) {
    if (altFt >= z.baseFt && altFt <= z.topFt) {
      const r = RISK_ORDER[z.risk] ?? 0;
      if (r > worst) worst = r;
    }
  }
  return RISK_LABELS[worst];
}

function sfipAtAlt(
  zones: Array<{ baseFt: number; topFt: number; meanSfip100: number | null }>,
  altFt: number,
): number | null {
  let best: number | null = null;
  for (const z of zones) {
    if (altFt >= z.baseFt && altFt <= z.topFt && z.meanSfip100 !== null) {
      if (best === null || z.meanSfip100 > best) best = z.meanSfip100;
    }
  }
  return best;
}

/** Coverage weight for cloud presence (sct=0.3, bkn=0.7, ovc=1.0). */
const COVERAGE_WEIGHT: Record<string, number> = { sct: 0.3, bkn: 0.7, ovc: 1.0 };

function cloudAtAlt(
  layers: Array<{ baseFt: number; topFt: number; coverage: string }>,
  altFt: number,
): number {
  let maxCov = 0;
  for (const l of layers) {
    if (altFt >= l.baseFt && altFt <= l.topFt) {
      const w = COVERAGE_WEIGHT[l.coverage] ?? 0.5;
      if (w > maxCov) maxCov = w;
    }
  }
  return maxCov * 100;
}

/**
 * Estimate temperature at a given altitude using known isotherm levels.
 * Interpolates between 0°C, -10°C, -20°C levels; extrapolates with standard lapse rate.
 */
function tempAtAltitude(p: VizPoint, altFt: number): number | null {
  const alt = p.altitudeLines;
  const fz = alt.freezingLevelFt;
  if (fz == null) return null;

  // Build known (altitude, temperature) reference points
  const refs: Array<[number, number]> = [[fz, 0]];
  if (alt.minus10cLevelFt != null) refs.push([alt.minus10cLevelFt, -10]);
  if (alt.minus20cLevelFt != null) refs.push([alt.minus20cLevelFt, -20]);
  refs.sort((a, b) => a[0] - b[0]); // ascending by altitude

  // Below lowest ref or above highest: extrapolate with ~2°C/1000ft lapse rate
  if (altFt <= refs[0][0]) {
    return refs[0][1] + (refs[0][0] - altFt) * 2 / 1000;
  }
  if (altFt >= refs[refs.length - 1][0]) {
    return refs[refs.length - 1][1] - (altFt - refs[refs.length - 1][0]) * 2 / 1000;
  }

  // Interpolate between bracketing refs
  for (let i = 0; i < refs.length - 1; i++) {
    if (altFt >= refs[i][0] && altFt <= refs[i + 1][0]) {
      const frac = (altFt - refs[i][0]) / (refs[i + 1][0] - refs[i][0]);
      return refs[i][1] + frac * (refs[i + 1][1] - refs[i][1]);
    }
  }
  return null;
}

// --- Default width (uniform) for metrics without meaningful width variation ---
const DEFAULT_WIDTH = 15;
const defaultWidth = () => DEFAULT_WIDTH;

// --- Metric definitions ---

const cloudCoverTotal: MapMetric = {
  id: 'cloud-cover-total',
  label: 'Cloud Cover (Total)',
  unit: '%',
  altitudeDependent: false,
  getValue: (p) => p.cloudCoverTotalPct,
  getColor: (v) => cloudCoverMapColor(v),
  getWidth: (v) => linearWidth(v, 100, 3, 25),
  formatValue: (v) => `${Math.round(v)}%`,
  legendStops: [
    { value: 0, label: 'Clear', color: cloudCoverMapColor(0) },
    { value: 25, label: '25%', color: cloudCoverMapColor(25) },
    { value: 50, label: '50%', color: cloudCoverMapColor(50) },
    { value: 75, label: '75%', color: cloudCoverMapColor(75) },
    { value: 100, label: 'Overcast', color: cloudCoverMapColor(100) },
  ],
};

const cloudCoverLow: MapMetric = {
  id: 'cloud-cover-low',
  label: 'Cloud Cover (Low)',
  unit: '%',
  altitudeDependent: false,
  getValue: (p) => p.cloudCoverLowPct,
  getColor: (v) => cloudCoverMapColor(v),
  getWidth: (v) => linearWidth(v, 100, 3, 25),
  formatValue: (v) => `${Math.round(v)}%`,
  legendStops: cloudCoverTotal.legendStops,
};

const CONVECTIVE_LABELS = ['none', 'low', 'moderate', 'high', 'extreme'];

const convectiveRisk: MapMetric = {
  id: 'convective-risk',
  label: 'Convective Risk',
  unit: '',
  altitudeDependent: false,
  getValue: (p) => RISK_ORDER[p.convectiveRisk] ?? 0,
  getColor: (v) => riskMapColor(CONVECTIVE_LABELS[Math.min(Math.round(v), 4)] ?? 'none'),
  getWidth: (v) => linearWidth(v, 4, 3, 25),
  formatValue: (v) => CONVECTIVE_LABELS[Math.min(Math.round(v), 4)] ?? 'none',
  legendStops: [
    { value: 0, label: 'None', color: riskMapColor('none') },
    { value: 1, label: 'Low', color: riskMapColor('low') },
    { value: 2, label: 'Moderate', color: riskMapColor('moderate') },
    { value: 3, label: 'High', color: riskMapColor('high') },
    { value: 4, label: 'Extreme', color: riskMapColor('extreme') },
  ],
};

const headwindOnly: MapMetric = {
  id: 'headwind',
  label: 'Headwind',
  unit: 'kt',
  altitudeDependent: false,
  getValue: (p) => Math.max(0, p.headwindKt),
  getColor: (v) => headwindMapColor(v),
  getWidth: (v) => linearWidth(v, 30, 3, 25),
  formatValue: (v) => `${Math.round(v)} kt HW`,
  legendStops: [
    { value: 0, label: 'Calm', color: headwindMapColor(0) },
    { value: 10, label: '10 kt', color: headwindMapColor(10) },
    { value: 20, label: '20 kt', color: headwindMapColor(20) },
    { value: 30, label: '30 kt', color: headwindMapColor(30) },
  ],
};

const tailwindOnly: MapMetric = {
  id: 'tailwind',
  label: 'Tailwind',
  unit: 'kt',
  altitudeDependent: false,
  getValue: (p) => Math.max(0, -p.headwindKt),
  getColor: (v) => headwindMapColor(-v),
  getWidth: (v) => linearWidth(v, 30, 3, 25),
  formatValue: (v) => `${Math.round(v)} kt TW`,
  legendStops: [
    { value: 0, label: 'Calm', color: headwindMapColor(0) },
    { value: 10, label: '10 kt', color: headwindMapColor(-10) },
    { value: 20, label: '20 kt', color: headwindMapColor(-20) },
    { value: 30, label: '30 kt', color: headwindMapColor(-30) },
  ],
};

const cape: MapMetric = {
  id: 'cape',
  label: 'CAPE',
  unit: 'J/kg',
  altitudeDependent: false,
  getValue: (p) => p.capeSurfaceJkg,
  getColor: (v) => capeMapColor(v),
  getWidth: (v) => linearWidth(v, 2000, 3, 25),
  formatValue: (v) => `${Math.round(v)} J/kg`,
  legendStops: [
    { value: 0, label: '0', color: capeMapColor(0) },
    { value: 500, label: '500', color: capeMapColor(500) },
    { value: 1000, label: '1000', color: capeMapColor(1000) },
    { value: 2000, label: '2000+', color: capeMapColor(2000) },
  ],
};

const nwpCeiling: MapMetric = {
  id: 'nwp-ceiling',
  label: 'NWP Ceiling',
  unit: 'ft',
  altitudeDependent: false,
  getValue: (p) => p.nwpCloudDiag?.ceilingFt ?? null,
  getColor: (v) => ceilingMapColor(v),
  // Inverted: low ceiling = thick (danger), high ceiling = thin
  getWidth: (v) => {
    const clamped = Math.max(0, Math.min(5000, v));
    return 25 - (clamped / 5000) * 22; // 25px at 0ft → 3px at 5000ft
  },
  formatValue: (v) => `${Math.round(v).toLocaleString()} ft`,
  legendStops: [
    { value: 200, label: 'LIFR <500', color: ceilingMapColor(200) },
    { value: 800, label: 'IFR <1000', color: ceilingMapColor(800) },
    { value: 2000, label: 'MVFR <3000', color: ceilingMapColor(2000) },
    { value: 5000, label: 'VFR 3000+', color: ceilingMapColor(5000) },
  ],
};

const tempAtLevel: MapMetric = {
  id: 'temp-at-level',
  label: 'Temperature at FL',
  unit: '°C',
  altitudeDependent: true,
  getValue: (p, altFt) => tempAtAltitude(p, altFt ?? 0),
  getColor: (v) => temperatureMapColor(v),
  // Thick when cold (icing territory), thin when warm
  getWidth: (v) => {
    // 5°C → 3px, -20°C → 25px
    const clamped = Math.max(-20, Math.min(5, v));
    return 3 + (5 - clamped) / 25 * 22;
  },
  formatValue: (v) => `${v.toFixed(1)}°C`,
  legendStops: [
    { value: -20, label: '-20°C', color: temperatureMapColor(-20) },
    { value: -10, label: '-10°C', color: temperatureMapColor(-10) },
    { value: 0, label: '0°C', color: temperatureMapColor(0) },
    { value: 10, label: '10°C', color: temperatureMapColor(10) },
  ],
};

const modelAgreement: MapMetric = {
  id: 'model-agreement',
  label: 'Model Agreement',
  unit: '',
  altitudeDependent: false,
  getValue: (p) => RISK_ORDER[p.worstModelAgreement === 'good' ? 'none' : p.worstModelAgreement] ?? 0,
  getColor: (v) => {
    if (v >= 2) return agreementMapColor('poor');
    if (v >= 1) return agreementMapColor('moderate');
    return agreementMapColor('good');
  },
  getWidth: (v) => linearWidth(v, 2, 3, 25),
  formatValue: (v) => {
    if (v >= 2) return t('map.formatPoor');
    if (v >= 1) return t('map.formatModerate');
    return t('map.formatGood');
  },
  legendStops: [
    { value: 0, label: 'Good', color: agreementMapColor('good') },
    { value: 1, label: 'Moderate', color: agreementMapColor('moderate') },
    { value: 2, label: 'Poor', color: agreementMapColor('poor') },
  ],
};

// --- Altitude-dependent metrics ---

const icingRiskAtLevel: MapMetric = {
  id: 'icing-risk-at-level',
  label: 'Icing Risk at FL',
  unit: '',
  altitudeDependent: true,
  getValue: (p, altFt) => RISK_ORDER[worstRiskAtAlt(p.icingZones, altFt ?? 0)] ?? 0,
  getColor: (v) => riskMapColor(RISK_LABELS[Math.min(v, 3)] ?? 'none'),
  getWidth: (v) => linearWidth(v, 3, 3, 25),
  formatValue: (v) => RISK_LABELS[Math.min(Math.round(v), 3)] ?? 'none',
  legendStops: [
    { value: 0, label: 'None', color: riskMapColor('none') },
    { value: 1, label: 'Light', color: riskMapColor('light') },
    { value: 2, label: 'Moderate', color: riskMapColor('moderate') },
    { value: 3, label: 'Severe', color: riskMapColor('severe') },
  ],
};

// SFIP 0-100 → icing tier. These MUST track `sfip_to_risk` in
// analysis/sounding/sfip.py (_SFIP_NONE / _SFIP_LIGHT / _SFIP_MODERATE).
// The map previously used a private 20/50/80 scale, so a score the backend
// graded as LIGHT icing rendered green "Low" here.
const SFIP_LIGHT = 15;
const SFIP_MODERATE = 30;
const SFIP_SEVERE = 55;

function sfipRisk(v: number): string {
  if (v >= SFIP_SEVERE) return 'severe';
  if (v >= SFIP_MODERATE) return 'moderate';
  if (v >= SFIP_LIGHT) return 'light';
  return 'none';
}

const sfipAtLevel: MapMetric = {
  id: 'sfip-at-level',
  label: 'SFIP at FL',
  unit: '',
  altitudeDependent: true,
  getValue: (p, altFt) => sfipAtAlt(p.sfipZones, altFt ?? 0),
  getColor: (v) => riskMapColor(sfipRisk(v)),
  getWidth: (v) => linearWidth(v, 100, 3, 25),
  formatValue: (v) => `SFIP ${Math.round(v)} (${sfipRisk(v)})`,
  legendStops: [
    { value: 0, label: `None (<${SFIP_LIGHT})`, color: riskMapColor('none') },
    { value: SFIP_LIGHT, label: `Light (${SFIP_LIGHT}-${SFIP_MODERATE})`, color: riskMapColor('light') },
    { value: SFIP_MODERATE, label: `Moderate (${SFIP_MODERATE}-${SFIP_SEVERE})`, color: riskMapColor('moderate') },
    { value: SFIP_SEVERE, label: `Severe (${SFIP_SEVERE}+)`, color: riskMapColor('severe') },
  ],
};

const catRiskAtLevel: MapMetric = {
  id: 'cat-risk-at-level',
  label: 'CAT Risk at FL',
  unit: '',
  altitudeDependent: true,
  getValue: (p, altFt) => RISK_ORDER[worstRiskAtAlt(p.catLayers, altFt ?? 0)] ?? 0,
  getColor: (v) => riskMapColor(RISK_LABELS[Math.min(v, 3)] ?? 'none'),
  getWidth: (v) => linearWidth(v, 3, 3, 25),
  formatValue: (v) => RISK_LABELS[Math.min(Math.round(v), 3)] ?? 'none',
  legendStops: icingRiskAtLevel.legendStops,
};

const cloudAtLevel: MapMetric = {
  id: 'cloud-at-level',
  label: 'Cloud at FL',
  unit: '%',
  altitudeDependent: true,
  getValue: (p, altFt) => cloudAtAlt(p.cloudLayers, altFt ?? 0),
  getColor: (v) => cloudCoverMapColor(v),
  getWidth: (v) => linearWidth(v, 100, 3, 25),
  formatValue: (v) => `${Math.round(v)}%`,
  legendStops: cloudCoverTotal.legendStops,
};

// --- Registry ---

export const MAP_METRICS: readonly MapMetric[] = [
  cloudCoverTotal,
  cloudCoverLow,
  convectiveRisk,
  headwindOnly,
  tailwindOnly,
  cape,
  nwpCeiling,
  tempAtLevel,
  modelAgreement,
  icingRiskAtLevel,
  sfipAtLevel,
  catRiskAtLevel,
  cloudAtLevel,
];

export function getMapMetricById(id: string): MapMetric | undefined {
  return MAP_METRICS.find((m) => m.id === id);
}

export const MAP_METRIC_NONE = 'none';

export function getMapMetricOptions(includeNone: boolean): Array<{ id: string; label: string }> {
  const options: Array<{ id: string; label: string }> = [];
  if (includeNone) {
    options.push({ id: MAP_METRIC_NONE, label: t('map.none') });
  }
  for (const m of MAP_METRICS) {
    options.push({ id: m.id, label: t('map.' + m.id) });
  }
  return options;
}
