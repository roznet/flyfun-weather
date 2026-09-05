/** Layer legend data: maps layer IDs to visual legend entries for the info popup.
 *
 * All colors are derived dynamically from a cross-section theme — the active
 * one by default. Every builder accepts an explicit theme so a surface that
 * renders a *not-yet-applied* theme (the theme preview) shares these exact
 * entries instead of keeping a parallel copy that drifts from the chart.
 */

import { icingRiskColor, catRiskColor, cloudFillFromDD, nwpCloudFill, inversionOpacity, flightCategoryColor } from './scales';
import { getActiveTheme, type CrossSectionTheme, type LineStyle } from './cross-section/theme';
import { parseCloudLayerId, DEFAULT_NATURAL_CONFIG, type CloudStyle } from './cross-section/layers/cloud-bands-factory';
import { referenceLineStyles } from './cross-section/layers/reference-lines';
import { t } from '../i18n/i18n';

export interface LegendEntry {
  label: string;
  color: string;
  meaning: string;
  /** Optional CSS for a hatch overlay on the swatch (e.g. repeating-linear-gradient). */
  hatchStyle?: string;
  /** Present for line layers: the exact stroke the cross-section draws, so the
   *  swatch reproduces colour AND dash pattern rather than a generic rule. */
  line?: LineStyle;
}

/** CSS background that simulates the natural-style puff/gap pattern at a
 *  given fill fraction. Returns a horizontal repeating gradient of
 *  `cloudColor` filled puff segments alternating with transparent gaps,
 *  so the sky behind the swatch shows through where coverage is missing.
 *
 *  Always an `<image>`, never a bare colour: `hatchStyle` is composed into a
 *  `background: <image>, <color>` shorthand, and a bare colour in the first
 *  slot invalidates the whole declaration — which blanked every natural-cloud
 *  swatch once the default fill fractions went to 1.0. */
function naturalPuffOverlay(cloudColor: string, fillFraction: number): string {
  if (fillFraction >= 0.99) return `linear-gradient(${cloudColor}, ${cloudColor})`;
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

function icingLegend(theme: CrossSectionTheme): LegendEntry[] {
  return [
    { label: t('legend.icing.light'), color: icingRiskColor('light', theme), meaning: t('legend.icing.lightDesc') },
    { label: t('legend.icing.moderate'), color: icingRiskColor('moderate', theme), meaning: t('legend.icing.moderateDesc') },
    { label: t('legend.icing.severe'), color: icingRiskColor('severe', theme), meaning: t('legend.icing.severeDesc') },
  ];
}

function sldLegend(): LegendEntry[] {
  return [
    { label: t('legend.sld.light'), color: 'rgba(220, 53, 69, 0.25)', meaning: t('legend.sld.lightDesc') },
    { label: t('legend.sld.moderate'), color: 'rgba(220, 53, 69, 0.40)', meaning: t('legend.sld.moderateDesc') },
    { label: t('legend.sld.severe'), color: 'rgba(220, 53, 69, 0.55)', meaning: t('legend.sld.severeDesc') },
  ];
}

function catLegend(theme: CrossSectionTheme): LegendEntry[] {
  return [
    { label: t('legend.cat.light'), color: catRiskColor('light', theme), meaning: t('legend.cat.lightDesc') },
    { label: t('legend.cat.moderate'), color: catRiskColor('moderate', theme), meaning: t('legend.cat.moderateDesc') },
    { label: t('legend.cat.severe'), color: catRiskColor('severe', theme), meaning: t('legend.cat.severeDesc') },
  ];
}

function convectiveLegend(theme: CrossSectionTheme): LegendEntry[] {
  const fill = theme.convective.towerFill;
  return [
    { label: t('legend.convective.marginal'), color: fill['marginal'] ?? 'transparent', meaning: t('legend.convective.marginalDesc') },
    { label: t('legend.convective.low'), color: fill['low'] ?? 'transparent', meaning: t('legend.convective.lowDesc') },
    { label: t('legend.convective.moderate'), color: fill['moderate'] ?? 'transparent', meaning: t('legend.convective.moderateDesc') },
    { label: t('legend.convective.high'), color: fill['high'] ?? 'transparent', meaning: t('legend.convective.highDesc') },
    { label: t('legend.convective.extreme'), color: fill['extreme'] ?? 'transparent', meaning: t('legend.convective.extremeDesc') },
  ];
}

/** DD cloud legend, branched by the layer's rendering style:
 *  - natural: puff/gap overlay (coverage encoded as horizontal fill fraction)
 *  - soft / square: solid swatch (alpha already encodes coverage via `cloudFillFromDD`)
 */
function cloudBandsLegend(layerId: string, theme: CrossSectionTheme): LegendEntry[] {
  const style = cloudStyleForLayer(layerId);
  const ovcColor = cloudFillFromDD(0.5, 'ovc', theme);
  const bknColor = cloudFillFromDD(1.5, 'bkn', theme);
  const sctColor = cloudFillFromDD(2.5, 'sct', theme);

  // Ordered least → most coverage, matching every other legend in the set
  // (25/50/75% NWP cover, light→severe risk) so a reader scanning several
  // groups never has to reverse direction mid-key.
  if (style === 'natural') {
    const f = DEFAULT_NATURAL_CONFIG.fillFraction;
    return [
      { label: t('legend.cloud.sct'), color: 'transparent', meaning: t('legend.cloud.sctDesc'),
        hatchStyle: naturalPuffOverlay(sctColor, f.SCT) },
      { label: t('legend.cloud.bkn'), color: 'transparent', meaning: t('legend.cloud.bknDesc'),
        hatchStyle: naturalPuffOverlay(bknColor, f.BKN) },
      { label: t('legend.cloud.ovc'), color: 'transparent', meaning: t('legend.cloud.ovcDesc'),
        hatchStyle: naturalPuffOverlay(ovcColor, f.OVC) },
    ];
  }

  // soft and square render as solid fills — the rgba alpha already encodes
  // coverage. No overlay needed; the swatch reads correctly with just the color.
  return [
    { label: t('legend.cloud.sct'), color: sctColor, meaning: t('legend.cloud.sctDesc') },
    { label: t('legend.cloud.bkn'), color: bknColor, meaning: t('legend.cloud.bknDesc') },
    { label: t('legend.cloud.ovc'), color: ovcColor, meaning: t('legend.cloud.ovcDesc') },
  ];
}

function nwpCloudLegend(layerId: string, theme: CrossSectionTheme): LegendEntry[] {
  const style = cloudStyleForLayer(layerId);
  const colorAt = (pct: number) => nwpCloudFill(pct, theme);

  if (style === 'natural') {
    // Natural NWP fill fraction is `max(minFillFraction, cover%/100)` — with the
    // current config that floors at a full band, so coverage reads through the
    // colour/alpha, not through gaps. Mirror the renderer rather than assuming
    // gaps, so the swatch never promises a pattern the chart won't draw.
    const fracAt = (pct: number) =>
      Math.max(DEFAULT_NATURAL_CONFIG.minFillFraction, Math.min(1, pct / 100));
    return [
      { label: t('legend.nwpCloud.25'), color: 'transparent', meaning: t('legend.nwpCloud.25Desc'),
        hatchStyle: naturalPuffOverlay(colorAt(25), fracAt(25)) },
      { label: t('legend.nwpCloud.50'), color: 'transparent', meaning: t('legend.nwpCloud.50Desc'),
        hatchStyle: naturalPuffOverlay(colorAt(50), fracAt(50)) },
      { label: t('legend.nwpCloud.75'), color: 'transparent', meaning: t('legend.nwpCloud.75Desc'),
        hatchStyle: naturalPuffOverlay(colorAt(75), fracAt(75)) },
    ];
  }

  // soft and square render as solid fills — alpha encodes coverage.
  return [
    { label: t('legend.nwpCloud.25'), color: colorAt(25), meaning: t('legend.nwpCloud.25Desc') },
    { label: t('legend.nwpCloud.50'), color: colorAt(50), meaning: t('legend.nwpCloud.50Desc') },
    { label: t('legend.nwpCloud.75'), color: colorAt(75), meaning: t('legend.nwpCloud.75Desc') },
  ];
}

function inversionLegend(theme: CrossSectionTheme): LegendEntry[] {
  const [r, g, b] = theme.inversion.baseRgb;
  return [
    { label: t('legend.inversion.weak'), color: `rgba(${r}, ${g}, ${b}, ${inversionOpacity(0.5, theme)})`, meaning: t('legend.inversion.weakDesc') },
    { label: t('legend.inversion.moderate'), color: `rgba(${r}, ${g}, ${b}, ${inversionOpacity(2, theme)})`, meaning: t('legend.inversion.moderateDesc') },
    { label: t('legend.inversion.strong'), color: `rgba(${r}, ${g}, ${b}, ${inversionOpacity(4, theme)})`, meaning: t('legend.inversion.strongDesc') },
  ];
}

function nightShadingLegend(theme: CrossSectionTheme): LegendEntry[] {
  const night = theme.nightShading;
  // Shading + the sunset/sunrise boundary markers live in one layer. Marker
  // colours match night-shading.ts (theme-independent: a sun reads as a sun).
  return [
    { label: t('legend.sun.twilight'), color: night.twilight, meaning: t('legend.sun.twilightDesc') },
    { label: t('legend.sun.night'), color: night.night, meaning: t('legend.sun.nightDesc') },
    { label: 'Sunset / Sunrise', color: '#f4b740', meaning: 'Marker where the sun crosses the horizon (elevation 0°)' },
    { label: 'Civil dusk / dawn', color: '#9aa7d0', meaning: 'Marker at sun 6° below the horizon — end/start of usable light' },
  ];
}

function obscurationLegend(theme: CrossSectionTheme): LegendEntry[] {
  const obs = theme.obscuration;
  // Diagonal hatch overlay matches the canvas rendering's 45° hatching.
  const hatch = `repeating-linear-gradient(45deg, ${obs.hatchColor} 0px, ${obs.hatchColor} ${obs.hatchLineWidth}px, transparent ${obs.hatchLineWidth}px, transparent ${obs.hatchSpacingPx}px)`;
  return [
    { label: t('legend.obscuration.lifr'), color: obs.lifr, meaning: t('legend.obscuration.lifrDesc'), hatchStyle: hatch },
    { label: t('legend.obscuration.ifr'), color: obs.ifr, meaning: t('legend.obscuration.ifrDesc'), hatchStyle: hatch },
    { label: t('legend.obscuration.mvfr'), color: obs.mvfr, meaning: t('legend.obscuration.mvfrDesc'), hatchStyle: hatch },
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

/** Line-layer legends. Each entry carries the theme's exact `LineStyle` so the
 *  swatch reproduces width and dash, not just colour — the only way to tell
 *  −10°C from −20°C in themes where the two share a hue (GRAMET). */
function lineLegends(theme: CrossSectionTheme): Record<string, LegendEntry[]> {
  const lineEntry = (label: string, line: LineStyle, meaning: string): LegendEntry => ({
    label, color: line.color, meaning, line,
  });
  const refStyles = referenceLineStyles(theme);
  return {
    'freezing-level': [lineEntry(t('legend.line.freezingLevel'), theme.temperature.freezingLevel, t('legend.line.solidLine'))],
    'minus-10c': [lineEntry(t('legend.line.minus10'), theme.temperature.minus10c, t('legend.line.solidLine'))],
    'minus-20c': [lineEntry(t('legend.line.minus20'), theme.temperature.minus20c, t('legend.line.dashedLine'))],
    'lcl': [lineEntry(t('legend.line.lcl'), theme.stability.lcl, t('legend.line.dashedCloudBase'))],
    'lfc': [lineEntry(t('legend.line.lfc'), theme.stability.lfc, t('legend.line.dashedFreeConvection'))],
    'el': [lineEntry(t('legend.line.el'), theme.stability.el, t('legend.line.dashedStormTop'))],
    'cruise-altitude': [
      lineEntry(t('legend.line.cruise'), refStyles.cruise, t('legend.line.cruiseDesc')),
      lineEntry(t('legend.line.ceiling'), refStyles.ceiling, t('legend.line.ceilingDesc')),
    ],
  };
}

/** Get the visual legend entries for a cross-section layer. */

/** Observed cloud tops (#574): the temperature ramp, plus the two states that
 *  are not temperatures at all.
 *
 *  Reads warm-to-cold like the enhanced-IR scale on satellite imagery, so the
 *  colours mean what a pilot already expects them to mean. Every stop is
 *  labelled in °C because temperature is the measurement; the FL a bar sits at
 *  is derived from it. */
function observedTopsLegend(theme: CrossSectionTheme): LegendEntry[] {
  // The cross-section colours by SHARE, not temperature: the vertical axis
  // already encodes altitude and cloud-top temperature is nearly a function of
  // it, so the colour channel would be spent on something already visible. The
  // map still colours by temperature, where there is no altitude axis.
  const stops = theme.observed.shareStops;
  const pick = (f: number) => stops.reduce(
    (best, s) => (f >= s[0] ? s : best), stops[0],
  );
  // Sampled at the ramp's own breakpoints, and labelled as what the number
  // is: the share of the SKY around the point, not of the cloud found in it.
  // The bands at a point therefore add up to how cloudy the disc was. The
  // first swatch is the drawing floor, so the legend also says where the
  // bands stop — a chart with no band is not a chart with no thin cloud.
  const entries: LegendEntry[] = [0.05, 0.10, 0.22, 0.55].map((f) => ({
    label: `${Math.round(f * 100)}%`,
    color: pick(f)[1],
    meaning: f <= 0.05
      ? 'bands at or below 5% of valid sampled pixels are omitted; the highest top is always marked'
      : f >= 0.55
        ? 'over half the valid sampled pixels had their retrieved cloud top in this geometric-height band'
        : 'share of valid sampled pixels with retrieved tops in this geometric-height band (ft MSL)',
  }));
  entries.push({
    label: 'base / depth unknown',
    color: theme.observed.hatchColor,
    meaning: 'no cloud base or vertical depth is measured: nearby-pixel tops do not establish a shared layer stack; nothing is drawn below the bins',
  });
  entries.push({
    label: 'limited retrieval coverage',
    color: theme.observed.noCoverageColor,
    meaning: 'insufficient coverage — known tops remain visible; NOT a clear sky',
    hatchStyle: `repeating-linear-gradient(45deg, ${theme.observed.noCoverageColor} 0 1px, transparent 1px 4px)`,
  });
  return entries;
}

/** Observed radar & lightning (#574). */
function observedSurfaceLegend(theme: CrossSectionTheme): LegendEntry[] {
  return [
    { label: '20 dBZ', color: '#3cbe5a', meaning: 'light echo' },
    { label: '35 dBZ', color: '#f0d23c', meaning: 'moderate' },
    { label: '45 dBZ', color: '#f08c28', meaning: 'heavy' },
    { label: '55 dBZ', color: '#e13c3c', meaning: 'very heavy' },
    { label: '65 dBZ', color: '#be3cbe', meaning: 'extreme' },
    {
      label: 'limited coverage',
      color: theme.observed.noCoverageColor,
      meaning: 'insufficient radar coverage — known echoes remain visible; NOT "no rain"',
      hatchStyle: `repeating-linear-gradient(45deg, ${theme.observed.noCoverageColor} 0 1px, transparent 1px 4px)`,
    },
  ];
}

export function getLayerLegend(
  layerId: string,
  theme: CrossSectionTheme = getActiveTheme(),
): LegendEntry[] | null {
  // Cloud legends are style-aware: they branch on the layer ID to render
  // natural-puff / soft / square swatches matching the actual rendering.
  const cloudIds = new Set([
    'cloud-bands', 'soft-cloud-bands', 'square-cloud-bands',
  ]);
  const nwpCloudIds = new Set([
    'nwp-cloud-bands', 'soft-nwp-cloud-bands', 'square-nwp-cloud-bands',
  ]);
  if (cloudIds.has(layerId)) return cloudBandsLegend(layerId, theme);
  if (nwpCloudIds.has(layerId)) return nwpCloudLegend(layerId, theme);

  const bandLegends: Record<string, () => LegendEntry[]> = {
    'icing-bands': () => icingLegend(theme),
    'icing-ogimet-nwp-bands': () => icingLegend(theme),
    'ieng-icing-bands': () => icingLegend(theme),
    'sfip-bands': () => icingLegend(theme),
    'sld-bands': sldLegend,
    'cat-bands': () => catLegend(theme),
    'e-shear-bands': () => catLegend(theme),
    'convective-bg': () => convectiveLegend(theme),
    'thermo-convective-bg': () => convectiveLegend(theme),
    'nwp-convective-bg': () => convectiveLegend(theme),
    'inversion-bands': () => inversionLegend(theme),
    'surface-obscuration-bands': () => obscurationLegend(theme),
    'current-conditions': currentConditionsLegend,
    'night-shading': () => nightShadingLegend(theme),
    'observed-tops': () => observedTopsLegend(theme),
    'observed-surface': () => observedSurfaceLegend(theme),
  };

  const bandBuilder = bandLegends[layerId];
  if (bandBuilder) return bandBuilder();

  const lines = lineLegends(theme);
  return lines[layerId] ?? null;
}
