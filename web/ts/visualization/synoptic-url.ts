/** Synoptic Forecast tab's URL keys (`syn.*`, #796), composed by `maps-main.ts`.
 *
 * Model, init, level and hour are data-driven (the Hewson manifest): a linked
 * value the manifest no longer has falls back the way the pickers already do
 * (first model with data, newest init, 850 hPa or the first level, hour
 * clamped to the run), keeping the other params. Empty `syn.init` = newest.
 *
 * The basemap (OSM / DWD / Met Office chart) is not linked: chart sources
 * depend on the viewer's access, and `syn.view` records the OSM view only. */

import type { ColorScale, HewsonMetric } from './hewson-colormaps';
import { mapViewField } from '../utils/map-view';
import type { UrlValues } from '../utils/url-state';

export const SYNOPTIC_METRICS: readonly HewsonMetric[] = [
  'theta_e', 'gradient', 'neg_laplacian', 'tfp', 'advection', 'tendency',
];
export const SYNOPTIC_FRONT_GATES: readonly string[] = [
  'off', 'default', 'strict', 'sensitive', 'gradient-only',
];

export const SYNOPTIC_URL_SCHEMA = {
  'syn.model':   { default: 'ecmwf' },
  'syn.init':    { default: '' },
  'syn.level':   { default: 850, values: [925, 850, 700] as readonly number[] },
  'syn.metric':  { default: 'tfp' as HewsonMetric, values: SYNOPTIC_METRICS },
  'syn.hour':    { default: 0 },
  // Percent, so the int codec applies (the map takes 0..1).
  'syn.opacity': { default: 50, values: Array.from({ length: 101 }, (_, i) => i) as readonly number[] },
  'syn.scale':   { default: 'default' as ColorScale, values: ['default', 'storm'] as readonly ColorScale[] },
  'syn.fronts':  { default: 'default', values: SYNOPTIC_FRONT_GATES },
  'syn.view':    mapViewField(),
};

export type SynopticUrlValues = UrlValues<typeof SYNOPTIC_URL_SCHEMA>;
