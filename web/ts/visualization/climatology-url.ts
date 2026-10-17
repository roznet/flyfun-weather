/** Climatology tab's URL keys (`clim.*`, #796), composed by `maps-main.ts`.
 *
 * `clim.month` empty = the current month (to date); a month no longer in the
 * picker falls back to it. `clim.cat` empty = the dataset's first eligible
 * sub-selection; one the dataset doesn't offer (or that is disabled for a
 * month to date) falls back the same way. */

import type { DatasetKey } from './climatology-datasets';
import { mapViewField } from '../utils/map-view';
import type { UrlValues } from '../utils/url-state';

export type ClimatologySubTab = 'map' | 'top';

export const CLIMATOLOGY_URL_SCHEMA = {
  'clim.sub':     { default: 'map' as ClimatologySubTab, values: ['map', 'top'] as readonly ClimatologySubTab[] },
  'clim.month':   { default: '' },
  'clim.dataset': {
    default: 'category' as DatasetKey,
    values: ['category', 'phenomena', 'wind', 'volatility'] as readonly DatasetKey[],
  },
  'clim.cat':     { default: '' },
  'clim.view':    mapViewField(),
};

export type ClimatologyUrlValues = UrlValues<typeof CLIMATOLOGY_URL_SCHEMA>;
