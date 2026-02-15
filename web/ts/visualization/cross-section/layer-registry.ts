/** Registry of all available cross-section layers. */

import type { CrossSectionLayer, LayerGroup } from '../types';
import { freezingLevelLayer, minus10cLayer, minus20cLayer } from './layers/temperature-lines';
import { cruiseAltitudeLayer } from './layers/reference-lines';
import { cloudBandsLayer } from './layers/cloud-bands';
import { icingBandsLayer } from './layers/icing-bands';
import { lclLayer, lfcLayer, elLayer } from './layers/stability-lines';
import { catBandsLayer } from './layers/cat-bands';
import { inversionBandsLayer } from './layers/inversion-bands';
import { convectiveBgLayer } from './layers/convective-bg';
import { terrainFillLayer } from './layers/terrain-fill';

const ALL_LAYERS: CrossSectionLayer[] = [
  // Rendering order: terrain → background → bands → lines → reference (back to front)
  terrainFillLayer,
  convectiveBgLayer,
  cloudBandsLayer,
  icingBandsLayer,
  catBandsLayer,
  inversionBandsLayer,
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
