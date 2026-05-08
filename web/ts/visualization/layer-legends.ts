/** Layer legend data: maps layer IDs to visual legend entries for the info popup.
 *
 * All colors are derived dynamically from the active cross-section theme.
 */

import { icingRiskColor, catRiskColor, cloudFillFromDD, nwpCloudFill, inversionOpacity } from './scales';
import { getActiveTheme } from './cross-section/theme';
import { t } from '../i18n/i18n';

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
    { label: t('legend.icing.light'), color: icingRiskColor('light'), meaning: t('legend.icing.lightDesc') },
    { label: t('legend.icing.moderate'), color: icingRiskColor('moderate'), meaning: t('legend.icing.moderateDesc') },
    { label: t('legend.icing.severe'), color: icingRiskColor('severe'), meaning: t('legend.icing.severeDesc') },
  ];
}

function sldLegend(): LegendEntry[] {
  return [
    { label: t('legend.sld.light'), color: 'rgba(220, 53, 69, 0.25)', meaning: t('legend.sld.lightDesc') },
    { label: t('legend.sld.moderate'), color: 'rgba(220, 53, 69, 0.40)', meaning: t('legend.sld.moderateDesc') },
    { label: t('legend.sld.severe'), color: 'rgba(220, 53, 69, 0.55)', meaning: t('legend.sld.severeDesc') },
  ];
}

function catLegend(): LegendEntry[] {
  return [
    { label: t('legend.cat.light'), color: catRiskColor('light'), meaning: t('legend.cat.lightDesc') },
    { label: t('legend.cat.moderate'), color: catRiskColor('moderate'), meaning: t('legend.cat.moderateDesc') },
    { label: t('legend.cat.severe'), color: catRiskColor('severe'), meaning: t('legend.cat.severeDesc') },
  ];
}

function convectiveLegend(): LegendEntry[] {
  const theme = getActiveTheme().convective.towerFill;
  return [
    { label: t('legend.convective.marginal'), color: theme['marginal'] ?? 'transparent', meaning: t('legend.convective.marginalDesc') },
    { label: t('legend.convective.low'), color: theme['low'] ?? 'transparent', meaning: t('legend.convective.lowDesc') },
    { label: t('legend.convective.moderate'), color: theme['moderate'] ?? 'transparent', meaning: t('legend.convective.moderateDesc') },
    { label: t('legend.convective.high'), color: theme['high'] ?? 'transparent', meaning: t('legend.convective.highDesc') },
    { label: t('legend.convective.extreme'), color: theme['extreme'] ?? 'transparent', meaning: t('legend.convective.extremeDesc') },
  ];
}

function cloudBandsLegend(): LegendEntry[] {
  const theme = getActiveTheme().clouds;
  const grid = theme.hatchGridPx;
  const hColor = theme.hatchColor;
  return [
    { label: t('legend.cloud.ovc'), color: cloudFillFromDD(0.5, 'ovc'), meaning: t('legend.cloud.ovcDesc'),
      hatchStyle: hatchGradient(grid, theme.hatchLineWidth['ovc'] ?? grid, hColor) },
    { label: t('legend.cloud.bkn'), color: cloudFillFromDD(1.5, 'bkn'), meaning: t('legend.cloud.bknDesc'),
      hatchStyle: hatchGradient(grid, theme.hatchLineWidth['bkn'] ?? grid, hColor) },
    { label: t('legend.cloud.sct'), color: cloudFillFromDD(2.5, 'sct'), meaning: t('legend.cloud.sctDesc'),
      hatchStyle: hatchGradient(grid, theme.hatchLineWidth['sct'] ?? grid, hColor) },
  ];
}

function nwpCloudLegend(): LegendEntry[] {
  const theme = getActiveTheme();
  const grid = theme.clouds.hatchGridPx;
  const hColor = theme.clouds.hatchColor;
  function nwpHatch(pct: number): string {
    const lw = grid * (pct / 100);
    return hatchGradient(grid, lw, hColor);
  }
  return [
    { label: t('legend.nwpCloud.25'), color: nwpCloudFill(25), meaning: t('legend.nwpCloud.25Desc'),
      hatchStyle: nwpHatch(25) },
    { label: t('legend.nwpCloud.50'), color: nwpCloudFill(50), meaning: t('legend.nwpCloud.50Desc'),
      hatchStyle: nwpHatch(50) },
    { label: t('legend.nwpCloud.75'), color: nwpCloudFill(75), meaning: t('legend.nwpCloud.75Desc'),
      hatchStyle: nwpHatch(75) },
  ];
}

function inversionLegend(): LegendEntry[] {
  const [r, g, b] = getActiveTheme().inversion.baseRgb;
  return [
    { label: t('legend.inversion.weak'), color: `rgba(${r}, ${g}, ${b}, ${inversionOpacity(0.5)})`, meaning: t('legend.inversion.weakDesc') },
    { label: t('legend.inversion.moderate'), color: `rgba(${r}, ${g}, ${b}, ${inversionOpacity(2)})`, meaning: t('legend.inversion.moderateDesc') },
    { label: t('legend.inversion.strong'), color: `rgba(${r}, ${g}, ${b}, ${inversionOpacity(4)})`, meaning: t('legend.inversion.strongDesc') },
  ];
}

function obscurationLegend(): LegendEntry[] {
  const theme = getActiveTheme().obscuration;
  // Diagonal hatch overlay matches the canvas rendering's 45° hatching.
  const hatch = `repeating-linear-gradient(45deg, ${theme.hatchColor} 0px, ${theme.hatchColor} ${theme.hatchLineWidth}px, transparent ${theme.hatchLineWidth}px, transparent ${theme.hatchSpacingPx}px)`;
  return [
    { label: t('legend.obscuration.lifr'), color: theme.lifr, meaning: t('legend.obscuration.lifrDesc'), hatchStyle: hatch },
    { label: t('legend.obscuration.ifr'), color: theme.ifr, meaning: t('legend.obscuration.ifrDesc'), hatchStyle: hatch },
    { label: t('legend.obscuration.mvfr'), color: theme.mvfr, meaning: t('legend.obscuration.mvfrDesc'), hatchStyle: hatch },
  ];
}

function lineLegends(): Record<string, LegendEntry[]> {
  const theme = getActiveTheme();
  return {
    'freezing-level': [{ label: t('legend.line.freezingLevel'), color: theme.temperature.freezingLevel.color, meaning: t('legend.line.solidLine') }],
    'minus-10c': [{ label: t('legend.line.minus10'), color: theme.temperature.minus10c.color, meaning: t('legend.line.solidLine') }],
    'minus-20c': [{ label: t('legend.line.minus20'), color: theme.temperature.minus20c.color, meaning: t('legend.line.dashedLine') }],
    'lcl': [{ label: t('legend.line.lcl'), color: theme.stability.lcl.color, meaning: t('legend.line.dashedCloudBase') }],
    'lfc': [{ label: t('legend.line.lfc'), color: theme.stability.lfc.color, meaning: t('legend.line.dashedFreeConvection') }],
    'el': [{ label: t('legend.line.el'), color: theme.stability.el.color, meaning: t('legend.line.dashedStormTop') }],
  };
}

/** Get the visual legend entries for a cross-section layer. */
export function getLayerLegend(layerId: string): LegendEntry[] | null {
  const bandLegends: Record<string, () => LegendEntry[]> = {
    'icing-bands': icingLegend,
    'icing-ogimet-nwp-bands': icingLegend,
    'ieng-icing-bands': icingLegend,
    'sfip-bands': icingLegend,
    'sld-bands': sldLegend,
    'cat-bands': catLegend,
    'e-shear-bands': catLegend,
    'convective-bg': convectiveLegend,
    'cloud-bands': cloudBandsLegend,
    'nwp-cloud-bands': nwpCloudLegend,
    'soft-cloud-bands': cloudBandsLegend,
    'soft-nwp-cloud-bands': nwpCloudLegend,
    'square-cloud-bands': cloudBandsLegend,
    'square-nwp-cloud-bands': nwpCloudLegend,
    'inversion-bands': inversionLegend,
    'surface-obscuration-bands': obscurationLegend,
  };

  const bandBuilder = bandLegends[layerId];
  if (bandBuilder) return bandBuilder();

  const lines = lineLegends();
  return lines[layerId] ?? null;
}
