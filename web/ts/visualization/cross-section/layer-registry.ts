/** Registry of all available cross-section layers. */

import type { CrossSectionLayer, LayerGroup } from '../types';
import { freezingLevelLayer, minus10cLayer, minus20cLayer } from './layers/temperature-lines';
import { cruiseAltitudeLayer } from './layers/reference-lines';
import { cloudBandsLayer } from './layers/cloud-bands';
import { icingBandsLayer } from './layers/icing-bands';
import { icingOgimetNwpBandsLayer } from './layers/icing-ogimet-nwp-bands';
import { sfipBandsLayer } from './layers/sfip-bands';
import { lclLayer, lfcLayer, elLayer } from './layers/stability-lines';
import { catBandsLayer } from './layers/cat-bands';
import { inversionBandsLayer } from './layers/inversion-bands';
import { convectiveBgLayer } from './layers/convective-bg';
import { terrainFillLayer } from './layers/terrain-fill';
import { nwpCloudBandsLayer } from './layers/nwp-cloud-bands';

const ALL_LAYERS: CrossSectionLayer[] = [
  // Rendering order: bands → terrain (covers below-surface) → lines → reference
  nwpCloudBandsLayer,
  convectiveBgLayer,
  cloudBandsLayer,
  icingBandsLayer,
  icingOgimetNwpBandsLayer,
  sfipBandsLayer,
  catBandsLayer,
  inversionBandsLayer,
  terrainFillLayer,
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

export function getDefaultEnabled(): Record<string, boolean> {
  const enabled: Record<string, boolean> = {};
  for (const layer of ALL_LAYERS) {
    enabled[layer.id] = layer.defaultEnabled;
  }
  return enabled;
}

export interface LayerGroupInfo {
  group: LayerGroup;
  label: string;
  layers: CrossSectionLayer[];
}

/** Maps preference values to layer IDs for groups that collapse in compact mode. */
const PREFERRED_METHOD_LAYER: Record<string, Record<string, string>> = {
  clouds: { dd: 'cloud-bands', nwp: 'nwp-cloud-bands' },
  icing: { ogimet_dd: 'icing-bands', ogimet_nwp: 'icing-ogimet-nwp-bands', sfip_nwp: 'sfip-bands' },
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
 * layer in each compact group, disable the rest.
 */
export function getCompactLayerOverrides(
  preferredMethods: Record<string, string>,
): Record<string, boolean> {
  const overrides: Record<string, boolean> = {};
  for (const [group, methodMap] of Object.entries(PREFERRED_METHOD_LAYER)) {
    const preferredId = methodMap[preferredMethods[group]];
    for (const layerId of Object.values(methodMap)) {
      overrides[layerId] = layerId === preferredId;
    }
  }
  return overrides;
}

export function getLayerGroups(): LayerGroupInfo[] {
  const groupMap = new Map<LayerGroup, CrossSectionLayer[]>();
  for (const layer of ALL_LAYERS) {
    let arr = groupMap.get(layer.group);
    if (!arr) { arr = []; groupMap.set(layer.group, arr); }
    arr.push(layer);
  }

  const groupLabels: Record<LayerGroup, string> = {
    terrain: 'Terrain',
    temperature: 'Temperature',
    clouds: 'Clouds',
    icing: 'Icing',
    stability: 'Stability',
    turbulence: 'Turbulence',
    convection: 'Convection',
    reference: 'Reference',
  };

  const order: LayerGroup[] = ['terrain', 'reference', 'temperature', 'clouds', 'icing', 'stability', 'turbulence', 'convection'];

  return order
    .filter((g) => groupMap.has(g))
    .map((g) => ({
      group: g,
      label: groupLabels[g],
      layers: groupMap.get(g)!,
    }));
}
