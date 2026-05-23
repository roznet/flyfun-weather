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

const ALL_LAYERS: CrossSectionLayer[] = [
  // Rendering order: obscuration → clouds → convection → icing → other bands → terrain → lines → reference.
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
  freezingLevelLayer,
  minus10cLayer,
  minus20cLayer,
  lclLayer,
  lfcLayer,
  elLayer,
  cruiseAltitudeLayer,
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
  'soft-nwp-cloud-bands': true,
  'soft-cloud-bands': false,
  'square-nwp-cloud-bands': false,
  'square-cloud-bands': false,
  'nwp-cloud-bands': false,
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

const PRESETS: Record<string, LayerPreset> = {
  gramet: {
    id: 'gramet',
    label: 'GRAMET',
    themeId: 'gramet',
    enabledLayers: GRAMET_ENABLED,
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
    reference: t('viz.group.reference'),
  };

  // 'terrain' is intentionally omitted: terrain always renders (force-on at
  // render time), so it has no UI toggle. The terrainFillLayer stays in
  // ALL_LAYERS — only its panel group is dropped here.
  const order: LayerGroup[] = ['reference', 'temperature', 'clouds', 'obscuration', 'icing', 'stability', 'turbulence', 'convection', 'conditions'];

  return order
    .filter((g) => groupMap.has(g))
    .map((g) => ({
      group: g,
      label: groupLabels[g],
      layers: groupMap.get(g)!,
    }));
}
