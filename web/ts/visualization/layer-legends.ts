/** Layer legend data: maps layer IDs to visual legend entries for the info popup.
 *
 * All colors are derived dynamically from the active cross-section theme.
 */

import { icingRiskColor, catRiskColor, cloudFillFromDD, inversionOpacity } from './scales';
import { getActiveTheme } from './cross-section/theme';

export interface LegendEntry {
  label: string;
  color: string;
  meaning: string;
  /** Optional CSS for a hatch overlay on the swatch (e.g. repeating-linear-gradient). */
  hatchStyle?: string;
}

/** CSS repeating-linear-gradient for horizontal hatch lines on a fixed grid. */
function hatchGradient(gridPx: number, lineWidth: number, color: string): string {
  if (lineWidth >= gridPx) return ''; // solid — no hatch
  // Build a gradient: line color for lineWidth px, then transparent for the gap
  return `repeating-linear-gradient(0deg, ${color} 0px, ${color} ${lineWidth}px, transparent ${lineWidth}px, transparent ${gridPx}px)`;
}

// --- Dynamic legend builders ---

function icingLegend(): LegendEntry[] {
  return [
    { label: 'Light', color: icingRiskColor('light'), meaning: 'Manageable for equipped aircraft' },
    { label: 'Moderate', color: icingRiskColor('moderate'), meaning: 'Exit clouds if ice persists' },
    { label: 'Severe', color: icingRiskColor('severe'), meaning: 'Rapid buildup — exit now' },
  ];
}

function catLegend(): LegendEntry[] {
  return [
    { label: 'Light', color: catRiskColor('light'), meaning: 'Intermittent bumps' },
    { label: 'Moderate', color: catRiskColor('moderate'), meaning: 'Unsecured items will move' },
    { label: 'Severe', color: catRiskColor('severe'), meaning: 'Avoid this layer' },
  ];
}

function convectiveLegend(): LegendEntry[] {
  const t = getActiveTheme().convective.towerFill;
  return [
    { label: 'Marginal', color: t['marginal'] ?? 'transparent', meaning: 'Shallow convection possible' },
    { label: 'Low', color: t['low'] ?? 'transparent', meaning: 'Weak showers, manageable' },
    { label: 'Moderate', color: t['moderate'] ?? 'transparent', meaning: 'Thunderstorms with trigger' },
    { label: 'High', color: t['high'] ?? 'transparent', meaning: 'Vigorous storms likely' },
    { label: 'Extreme', color: t['extreme'] ?? 'transparent', meaning: 'Severe weather — delay or cancel' },
  ];
}

function cloudBandsLegend(): LegendEntry[] {
  const t = getActiveTheme().clouds;
  const grid = t.hatchGridPx;
  const hColor = t.hatchColor;
  return [
    { label: 'OVC (DD < 1\u00b0C)', color: cloudFillFromDD(0.5, 'ovc'), meaning: 'Overcast — solid fill',
      hatchStyle: hatchGradient(grid, t.hatchLineWidth['ovc'] ?? grid, hColor) },
    { label: 'BKN (DD 1\u20132\u00b0C)', color: cloudFillFromDD(1.5, 'bkn'), meaning: 'Broken — thick lines',
      hatchStyle: hatchGradient(grid, t.hatchLineWidth['bkn'] ?? grid, hColor) },
    { label: 'SCT (DD 2\u20133\u00b0C)', color: cloudFillFromDD(2.5, 'sct'), meaning: 'Scattered — thin lines',
      hatchStyle: hatchGradient(grid, t.hatchLineWidth['sct'] ?? grid, hColor) },
  ];
}

function nwpCloudLegend(): LegendEntry[] {
  const theme = getActiveTheme();
  const nwp = theme.nwpClouds;
  const grid = theme.clouds.hatchGridPx;
  const hColor = theme.clouds.hatchColor;
  const [br, bg, bb] = nwp.brightRgb;
  const [dr, dg, db] = nwp.deltaRgb;
  const [opFloor, opScale] = nwp.opacityRange;
  function fill(pct: number): string {
    const t = Math.min(1, pct / 100);
    const r = Math.round(br - dr * t);
    const g = Math.round(bg - dg * t);
    const b = Math.round(bb - db * t);
    const a = Math.min(opFloor + opScale + 0.001, opFloor + opScale * t);
    return `rgba(${r}, ${g}, ${b}, ${a.toFixed(2)})`;
  }
  function nwpHatch(pct: number): string {
    const lw = grid * (pct / 100);
    return hatchGradient(grid, lw, hColor);
  }
  return [
    { label: '25% cover', color: fill(25), meaning: 'Scattered',
      hatchStyle: nwpHatch(25) },
    { label: '50% cover', color: fill(50), meaning: 'Broken',
      hatchStyle: nwpHatch(50) },
    { label: '75%+ cover', color: fill(75), meaning: 'Overcast',
      hatchStyle: nwpHatch(75) },
  ];
}

function inversionLegend(): LegendEntry[] {
  const [r, g, b] = getActiveTheme().inversion.baseRgb;
  return [
    { label: 'Weak (<1\u00b0C)', color: `rgba(${r}, ${g}, ${b}, ${inversionOpacity(0.5)})`, meaning: 'Mild haze trapping' },
    { label: 'Moderate (1\u20133\u00b0C)', color: `rgba(${r}, ${g}, ${b}, ${inversionOpacity(2)})`, meaning: 'Reduced visibility below' },
    { label: 'Strong (>3\u00b0C)', color: `rgba(${r}, ${g}, ${b}, ${inversionOpacity(4)})`, meaning: 'Dense haze/fog trapped' },
  ];
}

function lineLegends(): Record<string, LegendEntry[]> {
  const t = getActiveTheme();
  return {
    'freezing-level': [{ label: 'Freezing Level (0\u00b0C)', color: t.temperature.freezingLevel.color, meaning: 'Solid line' }],
    'minus-10c': [{ label: '\u221210\u00b0C Level', color: t.temperature.minus10c.color, meaning: 'Solid line' }],
    'minus-20c': [{ label: '\u221220\u00b0C Level', color: t.temperature.minus20c.color, meaning: 'Dashed line' }],
    'lcl': [{ label: 'LCL', color: t.stability.lcl.color, meaning: 'Dashed line — cloud base' }],
    'lfc': [{ label: 'LFC', color: t.stability.lfc.color, meaning: 'Dashed line — free convection' }],
    'el': [{ label: 'EL', color: t.stability.el.color, meaning: 'Dashed line — storm top' }],
  };
}

/** Get the visual legend entries for a cross-section layer. */
export function getLayerLegend(layerId: string): LegendEntry[] | null {
  const bandLegends: Record<string, () => LegendEntry[]> = {
    'icing-bands': icingLegend,
    'cat-bands': catLegend,
    'convective-bg': convectiveLegend,
    'cloud-bands': cloudBandsLegend,
    'nwp-cloud-bands': nwpCloudLegend,
    'inversion-bands': inversionLegend,
  };

  const bandBuilder = bandLegends[layerId];
  if (bandBuilder) return bandBuilder();

  const lines = lineLegends();
  return lines[layerId] ?? null;
}
