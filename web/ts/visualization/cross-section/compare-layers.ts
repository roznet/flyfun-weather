/** Registry of layers available in compare mode and their rendering type. */

import type { VizPoint, AltitudeLines } from '../types';

export type CompareLayerType = 'band' | 'line';

export interface ComparableLayer {
  id: string;
  name: string;
  group: string;
  type: CompareLayerType;
  /** For line layers: extract the altitude value from a VizPoint. */
  lineAccessor?: (p: VizPoint) => number | null;
  lineStyle?: { color: string; width: number; dash?: number[] };
  envelopeFill?: string;
}

const COMPARABLE_LAYERS: ComparableLayer[] = [
  // --- Band layers (reuse existing CrossSectionLayer.render via offscreen compositing) ---

  // Clouds
  { id: 'cloud-bands', name: 'DD Clouds', group: 'Clouds', type: 'band' },
  { id: 'nwp-cloud-bands', name: 'NWP Clouds', group: 'Clouds', type: 'band' },

  // Icing
  { id: 'icing-bands', name: 'Ogimet-DD Icing', group: 'Icing', type: 'band' },
  { id: 'icing-ogimet-nwp-bands', name: 'Ogimet-NWP Icing', group: 'Icing', type: 'band' },
  { id: 'sfip-bands', name: 'SFIP Icing', group: 'Icing', type: 'band' },

  // Turbulence
  { id: 'cat-bands', name: 'CAT', group: 'Turbulence', type: 'band' },
  { id: 'inversion-bands', name: 'Inversions', group: 'Turbulence', type: 'band' },

  // Convection
  { id: 'thermo-convective-bg', name: 'Thermo Convective', group: 'Convection', type: 'band' },
  { id: 'nwp-convective-bg', name: 'NWP Convective', group: 'Convection', type: 'band' },

  // --- Line layers (envelope rendering: min/max band + mean line) ---

  // Temperature
  {
    id: 'freezing-level', name: '0°C (Freezing)', group: 'Temperature', type: 'line',
    lineAccessor: (p) => p.altitudeLines.freezingLevelFt,
    lineStyle: { color: '#2563eb', width: 2 },
    envelopeFill: 'rgba(37, 99, 235, 0.15)',
  },
  {
    id: 'minus-10c-level', name: '-10°C', group: 'Temperature', type: 'line',
    lineAccessor: (p) => p.altitudeLines.minus10cLevelFt,
    lineStyle: { color: '#7c3aed', width: 1.5, dash: [6, 3] },
    envelopeFill: 'rgba(124, 58, 237, 0.12)',
  },
  {
    id: 'minus-20c-level', name: '-20°C', group: 'Temperature', type: 'line',
    lineAccessor: (p) => p.altitudeLines.minus20cLevelFt,
    lineStyle: { color: '#9333ea', width: 1.5, dash: [6, 3] },
    envelopeFill: 'rgba(147, 51, 234, 0.12)',
  },

  // Stability
  {
    id: 'lcl-line', name: 'LCL', group: 'Stability', type: 'line',
    lineAccessor: (p) => p.altitudeLines.lclAltitudeFt,
    lineStyle: { color: '#059669', width: 1.5, dash: [4, 4] },
    envelopeFill: 'rgba(5, 150, 105, 0.12)',
  },
  {
    id: 'lfc-line', name: 'LFC', group: 'Stability', type: 'line',
    lineAccessor: (p) => p.altitudeLines.lfcAltitudeFt,
    lineStyle: { color: '#d97706', width: 1.5, dash: [4, 4] },
    envelopeFill: 'rgba(217, 119, 6, 0.12)',
  },
  {
    id: 'el-line', name: 'EL', group: 'Stability', type: 'line',
    lineAccessor: (p) => p.altitudeLines.elAltitudeFt,
    lineStyle: { color: '#dc2626', width: 1.5, dash: [4, 4] },
    envelopeFill: 'rgba(220, 38, 38, 0.12)',
  },
];

const LAYER_MAP = new Map(COMPARABLE_LAYERS.map((l) => [l.id, l]));

export function getComparableLayer(id: string): ComparableLayer | undefined {
  return LAYER_MAP.get(id);
}

export interface ComparableLayerGroup {
  group: string;
  layers: ComparableLayer[];
}

export function getComparableLayerGroups(): ComparableLayerGroup[] {
  const groupMap = new Map<string, ComparableLayer[]>();
  for (const l of COMPARABLE_LAYERS) {
    let arr = groupMap.get(l.group);
    if (!arr) { arr = []; groupMap.set(l.group, arr); }
    arr.push(l);
  }
  const result: ComparableLayerGroup[] = [];
  for (const [group, layers] of groupMap) {
    result.push({ group, layers });
  }
  return result;
}

export { COMPARABLE_LAYERS };
