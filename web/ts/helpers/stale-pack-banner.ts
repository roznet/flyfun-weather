/** Pure decision logic for the stale-pack banner.
 *
 * The banner fires on briefing.html when the loaded pack's altitude or
 * departure no longer matches the live flight — i.e. the pilot edited
 * the flight after the pack was generated. Owner-only (non-owners
 * can't trigger a refresh).
 *
 * Kept in a separate module from briefing-ui.ts so unit tests can
 * import the function without dragging in Leaflet/DOM dependencies.
 */

import type { FlightResponse, RouteAnalysesManifest } from '../store/types';
import { formatAlt, formatDepartureTime } from '../utils';
import { t } from '../i18n/i18n';

export interface StalePackBannerState {
  altChanged: boolean;
  timeChanged: boolean;
  /** Altitude changed but time/route didn't → cheap recalc path, not a full refresh. */
  altOnly: boolean;
  message: string;
}

export function computeStalePackBanner(
  flight: FlightResponse | null,
  manifest: RouteAnalysesManifest | null,
  isOwner: boolean,
): StalePackBannerState | null {
  if (!flight || !manifest || !isOwner) return null;

  const altChanged = manifest.cruise_altitude_ft !== flight.cruise_altitude_ft;
  const flightDepIso = new Date(flight.departure_time).toISOString();
  const manifestDepIso = new Date(manifest.departure_time).toISOString();
  const timeChanged = flightDepIso !== manifestDepIso;

  if (!altChanged && !timeChanged) return null;

  const parts: string[] = [];
  if (altChanged) {
    parts.push(t('stalePack.altChanged', {
      packAlt: formatAlt(manifest.cruise_altitude_ft),
      flightAlt: formatAlt(flight.cruise_altitude_ft),
    }));
  }
  if (timeChanged) {
    parts.push(t('stalePack.timeChanged', {
      packTime: formatDepartureTime(manifest.departure_time),
      flightTime: formatDepartureTime(flight.departure_time),
    }));
  }

  // Altitude-only changes route to the cheap recalc path (#259): re-evaluating
  // advisories at the new altitude is ~free and never needs new model data, so
  // the full-pipeline "Refresh" (which no-ops when no newer GRIB exists) would
  // be misleading. A time/route change genuinely needs a re-fetch.
  const altOnly = altChanged && !timeChanged;
  const prompt = altOnly ? t('stalePack.altOnlyPrompt') : t('stalePack.refreshPrompt');

  return {
    altChanged,
    timeChanged,
    altOnly,
    message: `${parts.join(' ')} ${prompt}`,
  };
}
