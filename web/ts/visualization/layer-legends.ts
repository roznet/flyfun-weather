/** Layer legend data: maps layer IDs to visual legend entries for the info popup.
 *
 * All colors are derived dynamically from the active cross-section theme.
 */

import { icingRiskColor, catRiskColor, cloudFillFromDD, nwpCloudFill, inversionOpacity, flightCategoryColor } from './scales';
import { getActiveTheme } from './cross-section/theme';
import { parseCloudLayerId, DEFAULT_NATURAL_CONFIG, type CloudStyle } from './cross-section/layers/cloud-bands-factory';
import { t } from '../i18n/i18n';

export interface LegendEntry {
  label: string;
  color: string;
  meaning: string;
  /** Optional CSS for a hatch overlay on the swatch (e.g. repeating-linear-gradient). */
  hatchStyle?: string;
}

/** CSS background that simulates the natural-style puff/gap pattern at a
 *  given fill fraction. Returns a horizontal repeating gradient of
 *  `cloudColor` filled puff segments alternating with transparent gaps,
 *  so the sky behind the swatch shows through where coverage is missing.
 *  At fillFraction == 1.0 returns the solid color (no gaps). */
function naturalPuffOverlay(cloudColor: string, fillFraction: number): string {
  if (fillFraction >= 0.99) return cloudColor;
  const cycle = 14; // px in the swatch
  const puffPx = Math.max(2, Math.round(cycle * fillFraction));
  return `repeating-linear-gradient(90deg, `
    + `${cloudColor} 0px, ${cloudColor} ${puffPx}px, `
    + `transparent ${puffPx}px, transparent ${cycle}px)`;
}

function cloudStyleForLayer(layerId: string): CloudStyle {
  return parseCloudLayerId(layerId)?.style ?? 'natural';
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

/** DD cloud legend, branched by the layer's rendering style:
 *  - natural: puff/gap overlay (coverage encoded as horizontal fill fraction)
 *  - soft / square: solid swatch (alpha already encodes coverage via `cloudFillFromDD`)
 */
function cloudBandsLegend(layerId: string): LegendEntry[] {
  const style = cloudStyleForLayer(layerId);
  const ovcColor = cloudFillFromDD(0.5, 'ovc');
  const bknColor = cloudFillFromDD(1.5, 'bkn');
  const sctColor = cloudFillFromDD(2.5, 'sct');

  if (style === 'natural') {
    const f = DEFAULT_NATURAL_CONFIG.fillFraction;
    return [
      { label: t('legend.cloud.ovc'), color: 'transparent', meaning: t('legend.cloud.ovcDesc'),
        hatchStyle: naturalPuffOverlay(ovcColor, f.OVC) },
      { label: t('legend.cloud.bkn'), color: 'transparent', meaning: t('legend.cloud.bknDesc'),
        hatchStyle: naturalPuffOverlay(bknColor, f.BKN) },
      { label: t('legend.cloud.sct'), color: 'transparent', meaning: t('legend.cloud.sctDesc'),
        hatchStyle: naturalPuffOverlay(sctColor, f.SCT) },
    ];
  }

  // soft and square render as solid fills — the rgba alpha already encodes
  // coverage. No overlay needed; the swatch reads correctly with just the color.
  return [
    { label: t('legend.cloud.ovc'), color: ovcColor, meaning: t('legend.cloud.ovcDesc') },
    { label: t('legend.cloud.bkn'), color: bknColor, meaning: t('legend.cloud.bknDesc') },
    { label: t('legend.cloud.sct'), color: sctColor, meaning: t('legend.cloud.sctDesc') },
  ];
}

function nwpCloudLegend(layerId: string): LegendEntry[] {
  const style = cloudStyleForLayer(layerId);
  const colorAt = (pct: number) => nwpCloudFill(pct);

  if (style === 'natural') {
    // Natural NWP uses `meanCloudCoverPct / 100` directly as fill fraction,
    // so legend swatches show the same encoding: 25%/50%/75% gap pattern.
    return [
      { label: t('legend.nwpCloud.25'), color: 'transparent', meaning: t('legend.nwpCloud.25Desc'),
        hatchStyle: naturalPuffOverlay(colorAt(25), 0.25) },
      { label: t('legend.nwpCloud.50'), color: 'transparent', meaning: t('legend.nwpCloud.50Desc'),
        hatchStyle: naturalPuffOverlay(colorAt(50), 0.50) },
      { label: t('legend.nwpCloud.75'), color: 'transparent', meaning: t('legend.nwpCloud.75Desc'),
        hatchStyle: naturalPuffOverlay(colorAt(75), 0.75) },
    ];
  }

  // soft and square render as solid fills — alpha encodes coverage.
  return [
    { label: t('legend.nwpCloud.25'), color: colorAt(25), meaning: t('legend.nwpCloud.25Desc') },
    { label: t('legend.nwpCloud.50'), color: colorAt(50), meaning: t('legend.nwpCloud.50Desc') },
    { label: t('legend.nwpCloud.75'), color: colorAt(75), meaning: t('legend.nwpCloud.75Desc') },
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

function currentConditionsLegend(): LegendEntry[] {
  // Diagonal hatch overlay matching the SIGMET zone's 45° canvas hatching.
  const sigmetHatch = 'repeating-linear-gradient(45deg, rgba(200, 45, 45, 0.85) 0px, rgba(200, 45, 45, 0.85) 2px, transparent 2px, transparent 8px)';
  return [
    { label: t('legend.conditions.vfr'), color: flightCategoryColor('VFR'), meaning: t('legend.conditions.vfrDesc') },
    { label: t('legend.conditions.mvfr'), color: flightCategoryColor('MVFR'), meaning: t('legend.conditions.mvfrDesc') },
    { label: t('legend.conditions.ifr'), color: flightCategoryColor('IFR'), meaning: t('legend.conditions.ifrDesc') },
    { label: t('legend.conditions.lifr'), color: flightCategoryColor('LIFR'), meaning: t('legend.conditions.lifrDesc') },
    { label: t('legend.conditions.sigmet'), color: 'rgba(200, 45, 45, 0.16)', meaning: t('legend.conditions.sigmetDesc'), hatchStyle: sigmetHatch },
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
  // Cloud legends are style-aware: they branch on the layer ID to render
  // natural-puff / soft / square swatches matching the actual rendering.
  const cloudIds = new Set([
    'cloud-bands', 'soft-cloud-bands', 'square-cloud-bands',
  ]);
  const nwpCloudIds = new Set([
    'nwp-cloud-bands', 'soft-nwp-cloud-bands', 'square-nwp-cloud-bands',
  ]);
  if (cloudIds.has(layerId)) return cloudBandsLegend(layerId);
  if (nwpCloudIds.has(layerId)) return nwpCloudLegend(layerId);

  const bandLegends: Record<string, () => LegendEntry[]> = {
    'icing-bands': icingLegend,
    'icing-ogimet-nwp-bands': icingLegend,
    'ieng-icing-bands': icingLegend,
    'sfip-bands': icingLegend,
    'sld-bands': sldLegend,
    'cat-bands': catLegend,
    'e-shear-bands': catLegend,
    'convective-bg': convectiveLegend,
    'inversion-bands': inversionLegend,
    'surface-obscuration-bands': obscurationLegend,
    'current-conditions': currentConditionsLegend,
  };

  const bandBuilder = bandLegends[layerId];
  if (bandBuilder) return bandBuilder();

  const lines = lineLegends();
  return lines[layerId] ?? null;
}
