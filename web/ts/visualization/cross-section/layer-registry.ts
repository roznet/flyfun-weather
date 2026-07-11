/** Registry of all available cross-section layers. */

import type { CrossSectionLayer, LayerGroup } from '../types';
import { t } from '../../i18n/i18n';
import { freezingLevelLayer, minus10cLayer, minus20cLayer } from './layers/temperature-lines';
import { cruiseAltitudeLayer } from './layers/reference-lines';
import { icingBandsLayer } from './layers/icing-bands';
import { icingOgimetNwpBandsLayer } from './layers/icing-ogimet-nwp-bands';
import { sfipBandsLayer } from './layers/sfip-bands';
import { lclLayer, lfcLayer, elLayer } from './layers/stability-lines';
import { catBandsLayer } from './layers/cat-bands';
import { inversionBandsLayer } from './layers/inversion-bands';
import { thermoConvectiveBgLayer } from './layers/thermo-convective-bg';
import { nwpConvectiveBgLayer } from './layers/nwp-convective-bg';
import { terrainFillLayer } from './layers/terrain-fill';
import {
  cloudBandsLayer,
  nwpCloudBandsLayer,
  softCloudBandsLayer,
  softNwpCloudBandsLayer,
  squareCloudBandsLayer,
  squareNwpCloudBandsLayer,
} from './layers/cloud-bands-factory';
import { iengIcingBandsLayer } from './layers/ieng-icing-bands';
import { sldBandsLayer } from './layers/sld-bands';
import { eShearBandsLayer } from './layers/e-shear-bands';
import { surfaceObscurationBandsLayer } from './layers/surface-obscuration-bands';
import { currentConditionsLayer } from './layers/current-conditions';
import { frontsMarkersLayer } from './layers/fronts-markers';
import { nightShadingLayer } from './layers/night-shading';
import { highlightLayer } from './layers/highlight-layer';

const ALL_LAYERS: CrossSectionLayer[] = [
  // Rendering order: night → obscuration → clouds → convection → icing → other bands → terrain → lines → reference.
  // Night/twilight shading is first (very back of the stack) so it tints the
  // whole column behind the weather; terrain later masks the below-surface tint.
  nightShadingLayer,
  // Obscuration sits at the bottom of the stack so any DD/NWP cloud
  // bands that extend above the boundary layer overlay it cleanly.
  surfaceObscurationBandsLayer,
  softNwpCloudBandsLayer,
  softCloudBandsLayer,
  squareNwpCloudBandsLayer,
  squareCloudBandsLayer,
  nwpCloudBandsLayer,
  cloudBandsLayer,
  thermoConvectiveBgLayer,
  nwpConvectiveBgLayer,
  icingBandsLayer,
  icingOgimetNwpBandsLayer,
  sfipBandsLayer,
  iengIcingBandsLayer,
  sldBandsLayer,
  catBandsLayer,
  eShearBandsLayer,
  inversionBandsLayer,
  terrainFillLayer,
  // Current conditions sits above terrain (columns rest on the surface) but
  // below the temperature/stability/reference lines so those stay readable.
  currentConditionsLayer,
  // Front markers are vertical lines (not bands) — draw above terrain fill so
  // the marker stays visible over mountainous cross-sections.
  frontsMarkersLayer,
  freezingLevelLayer,
  minus10cLayer,
  minus20cLayer,
  lclLayer,
  lfcLayer,
  elLayer,
  cruiseAltitudeLayer,
  // Advisory highlight (scrim + verdict ribbon, #373) — registered last so it
  // sits at the very top of the stack (the dim wash must overlay everything).
  highlightLayer,
];

export function getAllLayers(): CrossSectionLayer[] {
  return ALL_LAYERS;
}

/** Where the cross-section is being rendered. Used for context-specific
 *  layer defaults: surface obscuration is high-signal in the airport-
 *  profile drawer (where users right-click for low-altitude detail) but
 *  off by default on the briefing page (avoids stacking with low-cloud /
 *  DD bands users already have visible). */
export type CrossSectionContext = 'briefing' | 'airport-profile';

const CONTEXT_OVERRIDES: Record<CrossSectionContext, Record<string, boolean>> = {
  briefing: {},
  'airport-profile': {
    'surface-obscuration-bands': true,
  },
};

export function getDefaultEnabled(context: CrossSectionContext = 'briefing'): Record<string, boolean> {
  const enabled: Record<string, boolean> = {};
  for (const layer of ALL_LAYERS) {
    enabled[layer.id] = layer.defaultEnabled;
  }
  Object.assign(enabled, CONTEXT_OVERRIDES[context]);
  return enabled;
}

export interface LayerGroupInfo {
  group: LayerGroup;
  label: string;
  layers: CrossSectionLayer[];
}

/** Maps preference values to layer IDs for groups that collapse in compact mode. */
const PREFERRED_METHOD_LAYER: Record<string, Record<string, string>> = {
  clouds: {
    // Legacy bare-source forms (pre-natural style). Kept for back-compat
    // with prefs that never went through user_migration 001.
    dd: 'cloud-bands',
    nwp: 'nwp-cloud-bands',
    natural_dd: 'cloud-bands',
    natural_nwp: 'nwp-cloud-bands',
    soft_dd: 'soft-cloud-bands',
    soft_nwp: 'soft-nwp-cloud-bands',
    square_dd: 'square-cloud-bands',
    square_nwp: 'square-nwp-cloud-bands',
  },
  icing: { ogimet_dd: 'icing-bands', ogimet_nwp: 'icing-ogimet-nwp-bands', sfip_nwp: 'sfip-bands', ieng: 'ieng-icing-bands' },
  turbulence: { ri: 'cat-bands', e_shear: 'e-shear-bands' },
  convection: { thermo: 'thermo-convective-bg', nwp: 'nwp-convective-bg' },
};

/** Return the preferred layer for a group based on the user's preference method. */
export function getPreferredLayerForGroup(
  group: LayerGroup,
  layers: CrossSectionLayer[],
  preferredMethod: string | undefined,
): CrossSectionLayer {
  const map = PREFERRED_METHOD_LAYER[group];
  if (map && preferredMethod) {
    const layerId = map[preferredMethod];
    const found = layers.find(l => l.id === layerId);
    if (found) return found;
  }
  return layers.find(l => l.defaultEnabled) ?? layers[0];
}

/**
 * Return layer overrides for entering compact mode: enable only the preferred
 * layer in each compact group, disable the rest. When a group has no known
 * preferred method (empty/missing/unknown), falls back to the group's
 * defaultEnabled layer so compact mode always shows something.
 */
export function getCompactLayerOverrides(
  preferredMethods: Record<string, string>,
): Record<string, boolean> {
  const overrides: Record<string, boolean> = {};
  for (const [group, methodMap] of Object.entries(PREFERRED_METHOD_LAYER)) {
    const groupLayers = ALL_LAYERS.filter((l) => l.group === group);
    const preferred = getPreferredLayerForGroup(
      group as LayerGroup,
      groupLayers,
      preferredMethods[group],
    );
    for (const layerId of Object.values(methodMap)) {
      overrides[layerId] = layerId === preferred.id;
    }
  }
  return overrides;
}

// --- Presets ---

export interface LayerPreset {
  id: string;
  label: string;
  themeId: string;
  enabledLayers: Record<string, boolean>;
}

const GRAMET_ENABLED: Record<string, boolean> = {
  'soft-nwp-cloud-bands': false,
  'soft-cloud-bands': false,
  'square-nwp-cloud-bands': false,
  'square-cloud-bands': false,
  'nwp-cloud-bands': true,        // Natural NWP clouds
  'cloud-bands': false,
  'thermo-convective-bg': false,
  'nwp-convective-bg': true,
  'icing-bands': false,
  'icing-ogimet-nwp-bands': true,
  'sfip-bands': false,
  'ieng-icing-bands': false,
  'sld-bands': false,
  'cat-bands': true,
  'e-shear-bands': false,
  'inversion-bands': false,
  'terrain': true,
  'freezing-level': true,
  'minus-10c': false,
  'minus-20c': false,
  'lcl-line': false,
  'lfc-line': false,
  'el-line': false,
  'cruise-altitude': true,
};

// Windy-style view: light cross-section theme, SFIP-NWP icing, Natural NWP
// clouds, NWP convection. Mirrors GRAMET's overall layer set (CAT, freezing
// level, terrain, cruise) but swaps the cloud/icing methods and the theme.
const WINDY_ENABLED: Record<string, boolean> = {
  'soft-nwp-cloud-bands': false,
  'soft-cloud-bands': false,
  'square-nwp-cloud-bands': false,
  'square-cloud-bands': false,
  'nwp-cloud-bands': true,        // Natural NWP clouds
  'cloud-bands': false,
  'thermo-convective-bg': false,
  'nwp-convective-bg': true,      // NWP convection
  'icing-bands': false,
  'icing-ogimet-nwp-bands': false,
  'sfip-bands': true,             // SFIP-NWP icing
  'ieng-icing-bands': false,
  'sld-bands': false,
  'cat-bands': true,
  'e-shear-bands': false,
  'inversion-bands': false,
  'terrain': true,
  'freezing-level': true,
  'minus-10c': false,
  'minus-20c': false,
  'lcl-line': false,
  'lfc-line': false,
  'el-line': false,
  'cruise-altitude': true,
};

// ForeFlight-style view: high-contrast theme, Square DD clouds, Ogimet-DD
// icing, CAT (Ri) turbulence. Mirrors the other presets' base layers (freezing
// level, terrain, cruise) but uses the all-DD method set with square cloud cells.
const FOREFLIGHT_ENABLED: Record<string, boolean> = {
  'soft-nwp-cloud-bands': false,
  'soft-cloud-bands': false,
  'square-nwp-cloud-bands': false,
  'square-cloud-bands': true,     // Square DD clouds
  'nwp-cloud-bands': false,
  'cloud-bands': false,
  'thermo-convective-bg': false,
  'nwp-convective-bg': true,
  'icing-bands': true,            // Ogimet-DD icing
  'icing-ogimet-nwp-bands': false,
  'sfip-bands': false,
  'ieng-icing-bands': false,
  'sld-bands': false,
  'cat-bands': true,              // CAT (Ri) turbulence
  'e-shear-bands': false,
  'inversion-bands': false,
  'terrain': true,
  'freezing-level': true,
  'minus-10c': false,
  'minus-20c': false,
  'lcl-line': false,
  'lfc-line': false,
  'el-line': false,
  'cruise-altitude': true,
};

// SYNC: the iOS app mirrors these layer presets (GRAMET / Windy / ForeFlight) in
// app/flyfun-weather/flyfun-weather/Views/CrossSection/Layers/CrossSectionPresets.swift
// (translated to iOS layer IDs). Keep the two in lockstep when editing presets.
const PRESETS: Record<string, LayerPreset> = {
  gramet: {
    id: 'gramet',
    label: 'GRAMET',
    themeId: 'gramet',
    enabledLayers: GRAMET_ENABLED,
  },
  windy: {
    id: 'windy',
    label: 'Windy',
    themeId: 'light',
    enabledLayers: WINDY_ENABLED,
  },
  foreflight: {
    id: 'foreflight',
    label: 'ForeFlight',
    themeId: 'high-contrast',
    enabledLayers: FOREFLIGHT_ENABLED,
  },
};

export function getPresets(): LayerPreset[] {
  return Object.values(PRESETS);
}

export function getPreset(id: string): LayerPreset | undefined {
  return PRESETS[id];
}

export function getLayerGroups(): LayerGroupInfo[] {
  const groupMap = new Map<LayerGroup, CrossSectionLayer[]>();
  for (const layer of ALL_LAYERS) {
    let arr = groupMap.get(layer.group);
    if (!arr) { arr = []; groupMap.set(layer.group, arr); }
    arr.push(layer);
  }

  const groupLabels: Record<LayerGroup, string> = {
    terrain: t('viz.group.terrain'),
    temperature: t('viz.group.temperature'),
    clouds: t('viz.group.clouds'),
    icing: t('viz.group.icing'),
    stability: t('viz.group.stability'),
    turbulence: t('viz.group.turbulence'),
    convection: t('viz.group.convection'),
    obscuration: t('viz.group.obscuration'),
    conditions: t('viz.group.conditions'),
    fronts: t('viz.group.fronts'),
    sun: t('viz.group.sun'),
    highlight: t('viz.group.highlight'),
    reference: t('viz.group.reference'),
  };

  // 'terrain' is intentionally omitted: terrain always renders (force-on at
  // render time), so it has no UI toggle. The terrainFillLayer stays in
  // ALL_LAYERS — only its panel group is dropped here.
  // 'highlight' is listed so its toggle CAN render, but the panel hides it via
  // `hiddenGroups` unless an advisory highlight is active with data (#373).
  const order: LayerGroup[] = ['reference', 'temperature', 'clouds', 'obscuration', 'icing', 'stability', 'turbulence', 'convection', 'conditions', 'sun', 'fronts', 'highlight'];

  return order
    .filter((g) => groupMap.has(g))
    .map((g) => ({
      group: g,
      label: groupLabels[g],
      layers: groupMap.get(g)!,
    }));
}
