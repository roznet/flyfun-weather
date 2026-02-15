/** Layer legend data: maps layer IDs to visual legend entries for the info popup.
 *
 * Colors come from scales.ts and layer files (single source of truth).
 * Threshold labels come from metrics-catalog.json.
 */

import { icingRiskColor, catRiskColor, coverageOpacity, inversionOpacity } from './scales';

export interface LegendEntry {
  label: string;
  color: string;
  meaning: string;
}

// --- Risk-based layer legends ---

const ICING_LEGEND: LegendEntry[] = [
  { label: 'Light', color: icingRiskColor('light'), meaning: 'Manageable for equipped aircraft' },
  { label: 'Moderate', color: icingRiskColor('moderate'), meaning: 'Exit clouds if ice persists' },
  { label: 'Severe', color: icingRiskColor('severe'), meaning: 'Rapid buildup — exit now' },
];

const CAT_LEGEND: LegendEntry[] = [
  { label: 'Light', color: catRiskColor('light'), meaning: 'Intermittent bumps' },
  { label: 'Moderate', color: catRiskColor('moderate'), meaning: 'Unsecured items will move' },
  { label: 'Severe', color: catRiskColor('severe'), meaning: 'Avoid this layer' },
];

// Convective uses tower fill colors (most visible component)
const CONVECTIVE_LEGEND: LegendEntry[] = [
  { label: 'Marginal', color: 'rgba(180, 180, 180, 0.15)', meaning: 'Shallow convection possible' },
  { label: 'Low', color: 'rgba(255, 235, 59, 0.18)', meaning: 'Weak showers, manageable' },
  { label: 'Moderate', color: 'rgba(255, 152, 0, 0.25)', meaning: 'Thunderstorms with trigger' },
  { label: 'High', color: 'rgba(220, 53, 69, 0.30)', meaning: 'Vigorous storms likely' },
  { label: 'Extreme', color: 'rgba(183, 28, 28, 0.35)', meaning: 'Severe weather — delay or cancel' },
];

// --- Cloud layer legends ---

const CLOUD_BANDS_LEGEND: LegendEntry[] = [
  { label: 'SCT (Scattered)', color: `rgba(255, 255, 255, ${Math.min(0.85, coverageOpacity('sct') + 0.15)})`, meaning: '3-4 oktas' },
  { label: 'BKN (Broken)', color: `rgba(255, 255, 255, ${Math.min(0.85, coverageOpacity('bkn') + 0.15)})`, meaning: '5-7 oktas' },
  { label: 'OVC (Overcast)', color: `rgba(255, 255, 255, ${Math.min(0.85, coverageOpacity('ovc') + 0.15)})`, meaning: '8/8 coverage' },
];

const NWP_CLOUD_LEGEND: LegendEntry[] = [
  { label: '25% cover', color: 'rgba(255, 255, 255, 0.20)', meaning: 'Scattered' },
  { label: '50% cover', color: 'rgba(255, 255, 255, 0.40)', meaning: 'Broken' },
  { label: '75%+ cover', color: 'rgba(255, 255, 255, 0.60)', meaning: 'Overcast' },
];

// --- Inversion legend ---

const INVERSION_LEGEND: LegendEntry[] = [
  { label: 'Weak (~2\u00b0C)', color: `rgba(233, 30, 99, ${inversionOpacity(2)})`, meaning: 'Mild haze trapping' },
  { label: 'Moderate (~5\u00b0C)', color: `rgba(233, 30, 99, ${inversionOpacity(5)})`, meaning: 'Reduced visibility below' },
  { label: 'Strong (~10\u00b0C)', color: `rgba(233, 30, 99, ${inversionOpacity(10)})`, meaning: 'Dense haze/fog trapped' },
];

// --- Line layer legends ---

const LINE_LEGENDS: Record<string, LegendEntry[]> = {
  'freezing-level': [{ label: 'Freezing Level (0\u00b0C)', color: '#00bcd4', meaning: 'Solid line' }],
  'minus-10c': [{ label: '\u221210\u00b0C Level', color: '#2196f3', meaning: 'Solid line' }],
  'minus-20c': [{ label: '\u221220\u00b0C Level', color: '#1a237e', meaning: 'Dashed line' }],
  'lcl': [{ label: 'LCL', color: '#4caf50', meaning: 'Dashed line — cloud base' }],
  'lfc': [{ label: 'LFC', color: '#ff9800', meaning: 'Dashed line — free convection' }],
  'el': [{ label: 'EL', color: '#f44336', meaning: 'Dashed line — storm top' }],
};

// --- Band legends (keyed by background color context) ---

const BAND_LEGENDS: Record<string, LegendEntry[]> = {
  'icing-bands': ICING_LEGEND,
  'cat-bands': CAT_LEGEND,
  'convective-bg': CONVECTIVE_LEGEND,
  'cloud-bands': CLOUD_BANDS_LEGEND,
  'nwp-cloud-bands': NWP_CLOUD_LEGEND,
  'inversion-bands': INVERSION_LEGEND,
};

/** Get the visual legend entries for a cross-section layer. */
export function getLayerLegend(layerId: string): LegendEntry[] | null {
  return BAND_LEGENDS[layerId] ?? LINE_LEGENDS[layerId] ?? null;
}
