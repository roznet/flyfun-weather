/** Map metric registry — defines how route data maps to color/width for map segments. */

import type { VizPoint } from '../types';
import {
  riskMapColor, cloudCoverMapColor, headwindMapColor, crosswindMapColor,
  capeMapColor, freezingLevelMapColor, ceilingMapColor, temperatureMapColor,
  agreementMapColor, linearWidth,
} from '../scales';

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

// --- Default width (uniform 4px) for color-only metrics ---
const DEFAULT_WIDTH = 4;
const defaultWidth = () => DEFAULT_WIDTH;

// --- Metric definitions ---

const cloudCoverTotal: MapMetric = {
  id: 'cloud-cover-total',
  label: 'Cloud Cover (Total)',
  unit: '%',
  altitudeDependent: false,
  getValue: (p) => p.cloudCoverTotalPct,
  getColor: (v) => cloudCoverMapColor(v),
  getWidth: defaultWidth,
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
  getWidth: defaultWidth,
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
  getWidth: defaultWidth,
  formatValue: (v) => CONVECTIVE_LABELS[Math.min(Math.round(v), 4)] ?? 'none',
  legendStops: [
    { value: 0, label: 'None', color: riskMapColor('none') },
    { value: 1, label: 'Low', color: riskMapColor('low') },
    { value: 2, label: 'Moderate', color: riskMapColor('moderate') },
    { value: 3, label: 'High', color: riskMapColor('high') },
    { value: 4, label: 'Extreme', color: riskMapColor('extreme') },
  ],
};

const headwind: MapMetric = {
  id: 'headwind',
  label: 'Head/Tailwind',
  unit: 'kt',
  altitudeDependent: false,
  getValue: (p) => p.headwindKt,
  getColor: (v) => headwindMapColor(v),
  getWidth: (v) => linearWidth(v, 30, 3, 7),
  formatValue: (v) => {
    const abs = Math.abs(v).toFixed(0);
    return v >= 0 ? `${abs} kt HW` : `${abs} kt TW`;
  },
  legendStops: [
    { value: -30, label: '30 kt TW', color: headwindMapColor(-30) },
    { value: -15, label: '15 kt TW', color: headwindMapColor(-15) },
    { value: 0, label: 'Calm', color: headwindMapColor(0) },
    { value: 15, label: '15 kt HW', color: headwindMapColor(15) },
    { value: 30, label: '30 kt HW', color: headwindMapColor(30) },
  ],
};

const crosswind: MapMetric = {
  id: 'crosswind',
  label: 'Crosswind',
  unit: 'kt',
  altitudeDependent: false,
  getValue: (p) => Math.abs(p.crosswindKt),
  getColor: (v) => crosswindMapColor(v),
  getWidth: (v) => linearWidth(v, 25, 3, 7),
  formatValue: (v) => `${Math.round(v)} kt`,
  legendStops: [
    { value: 0, label: 'Calm', color: crosswindMapColor(0) },
    { value: 10, label: '10 kt', color: crosswindMapColor(10) },
    { value: 20, label: '20 kt', color: crosswindMapColor(20) },
    { value: 25, label: '25+ kt', color: crosswindMapColor(25) },
  ],
};

const cape: MapMetric = {
  id: 'cape',
  label: 'CAPE',
  unit: 'J/kg',
  altitudeDependent: false,
  getValue: (p) => p.capeSurfaceJkg,
  getColor: (v) => capeMapColor(v),
  getWidth: defaultWidth,
  formatValue: (v) => `${Math.round(v)} J/kg`,
  legendStops: [
    { value: 0, label: '0', color: capeMapColor(0) },
    { value: 500, label: '500', color: capeMapColor(500) },
    { value: 1000, label: '1000', color: capeMapColor(1000) },
    { value: 2000, label: '2000+', color: capeMapColor(2000) },
  ],
};

const freezingLevel: MapMetric = {
  id: 'freezing-level',
  label: 'Freezing Level',
  unit: 'ft',
  altitudeDependent: false,
  getValue: (p) => p.altitudeLines.freezingLevelFt,
  getColor: (v) => freezingLevelMapColor(v),
  getWidth: defaultWidth,
  formatValue: (v) => `${Math.round(v).toLocaleString()} ft`,
  legendStops: [
    { value: 0, label: 'SFC', color: freezingLevelMapColor(0) },
    { value: 5000, label: '5000 ft', color: freezingLevelMapColor(5000) },
    { value: 10000, label: '10000 ft', color: freezingLevelMapColor(10000) },
    { value: 15000, label: '15000 ft', color: freezingLevelMapColor(15000) },
  ],
};

const nwpCeiling: MapMetric = {
  id: 'nwp-ceiling',
  label: 'NWP Ceiling',
  unit: 'ft',
  altitudeDependent: false,
  getValue: (p) => p.nwpCloudDiag?.ceilingFt ?? null,
  getColor: (v) => ceilingMapColor(v),
  getWidth: defaultWidth,
  formatValue: (v) => `${Math.round(v).toLocaleString()} ft`,
  legendStops: [
    { value: 200, label: 'LIFR <500', color: ceilingMapColor(200) },
    { value: 800, label: 'IFR <1000', color: ceilingMapColor(800) },
    { value: 2000, label: 'MVFR <3000', color: ceilingMapColor(2000) },
    { value: 5000, label: 'VFR 3000+', color: ceilingMapColor(5000) },
  ],
};

const temperature: MapMetric = {
  id: 'temperature',
  label: 'Temperature (2m)',
  unit: '°C',
  altitudeDependent: false,
  getValue: (p) => p.temperatureC,
  getColor: (v) => temperatureMapColor(v),
  getWidth: defaultWidth,
  formatValue: (v) => `${v.toFixed(1)}°C`,
  legendStops: [
    { value: -10, label: '-10°C', color: temperatureMapColor(-10) },
    { value: 0, label: '0°C', color: temperatureMapColor(0) },
    { value: 15, label: '15°C', color: temperatureMapColor(15) },
    { value: 30, label: '30°C', color: temperatureMapColor(30) },
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
  getWidth: defaultWidth,
  formatValue: (v) => {
    if (v >= 2) return 'Poor';
    if (v >= 1) return 'Moderate';
    return 'Good';
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
  getWidth: defaultWidth,
  formatValue: (v) => RISK_LABELS[Math.min(Math.round(v), 3)] ?? 'none',
  legendStops: [
    { value: 0, label: 'None', color: riskMapColor('none') },
    { value: 1, label: 'Light', color: riskMapColor('light') },
    { value: 2, label: 'Moderate', color: riskMapColor('moderate') },
    { value: 3, label: 'Severe', color: riskMapColor('severe') },
  ],
};

const sfipAtLevel: MapMetric = {
  id: 'sfip-at-level',
  label: 'SFIP at FL',
  unit: '',
  altitudeDependent: true,
  getValue: (p, altFt) => sfipAtAlt(p.sfipZones, altFt ?? 0),
  getColor: (v) => {
    if (v <= 20) return '#22c55e';
    if (v <= 50) return '#facc15';
    if (v <= 80) return '#f97316';
    return '#ef4444';
  },
  getWidth: defaultWidth,
  formatValue: (v) => `SFIP ${Math.round(v)}`,
  legendStops: [
    { value: 0, label: 'Low (0-20)', color: '#22c55e' },
    { value: 35, label: 'Med (20-50)', color: '#facc15' },
    { value: 65, label: 'High (50-80)', color: '#f97316' },
    { value: 90, label: 'Very High (80+)', color: '#ef4444' },
  ],
};

const catRiskAtLevel: MapMetric = {
  id: 'cat-risk-at-level',
  label: 'CAT Risk at FL',
  unit: '',
  altitudeDependent: true,
  getValue: (p, altFt) => RISK_ORDER[worstRiskAtAlt(p.catLayers, altFt ?? 0)] ?? 0,
  getColor: (v) => riskMapColor(RISK_LABELS[Math.min(v, 3)] ?? 'none'),
  getWidth: defaultWidth,
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
  getWidth: defaultWidth,
  formatValue: (v) => `${Math.round(v)}%`,
  legendStops: cloudCoverTotal.legendStops,
};

// --- Registry ---

export const MAP_METRICS: readonly MapMetric[] = [
  cloudCoverTotal,
  cloudCoverLow,
  convectiveRisk,
  headwind,
  crosswind,
  cape,
  freezingLevel,
  nwpCeiling,
  temperature,
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
    options.push({ id: MAP_METRIC_NONE, label: 'None' });
  }
  for (const m of MAP_METRICS) {
    options.push({ id: m.id, label: m.label });
  }
  return options;
}
