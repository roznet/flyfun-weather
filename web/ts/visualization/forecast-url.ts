/** Forecast Overview tab's URL keys (`fc.*`), composed by `maps-main.ts`.
 *
 * Do not rename these: the iOS app opens shared `/maps.html` links natively
 * and reads `fc.day/hour/model/metric/apt` by name (`AppState.mapDeepLink`).
 * New keys are safe, it ignores unknown ones. */

import { FORECAST_INDIVIDUAL_MODELS } from '../adapters/maps-adapter';
import { FORECAST_METRICS, type ForecastMetric } from './weather-map-format';
import { mapViewField } from '../utils/map-view';

export const FORECAST_URL_SCHEMA = {
  'fc.day':    { default: 0,  values: [0, 1, 2, 3, 4, 5, 6] as readonly number[] },
  'fc.hour':   { default: 12, values: [6, 9, 12, 15, 18] as readonly number[] },
  'fc.model':  { default: 'worst', values: ['worst', 'majority', 'gfs', 'icon', 'ecmwf'] as readonly string[] },
  'fc.metric': {
    default: 'flight_category' as ForecastMetric,
    values: FORECAST_METRICS as readonly ForecastMetric[],
  },
  // Open airport-profile panel (ICAO) and the panel's selected model.
  // Empty ICAO = no panel open. Panel model defaults differ from the
  // map's per-airport model (panel always needs a real model, not a
  // consensus mode), so they're separate keys.
  'fc.apt':    { default: '' },
  'fc.apModel':{ default: 'ecmwf', values: FORECAST_INDIVIDUAL_MODELS },
  // Panel view: card (default) | cross | skewt. Lets a shared link land
  // straight on the cross-section; bare links open the summary card.
  'fc.apView': { default: 'card', values: ['card', 'cross', 'skewt'] as readonly string[] },
  'fc.view':   mapViewField(),
};
