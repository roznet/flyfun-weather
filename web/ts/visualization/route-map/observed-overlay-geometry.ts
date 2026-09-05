/** Pure geometry and labelling for the observed map overlay (#574).
 *
 * Split out of `observed-overlay.ts` so it carries no Leaflet import: Leaflet
 * touches `window` at module load, which makes anything that imports it
 * untestable in a node environment. The rules encoded here — how wide the
 * corridor box is, how a flash fades, what the age badge says — are exactly
 * the parts worth testing, so they live where tests can reach them.
 */

/** Lightning older than this is dropped rather than drawn nearly-invisible. */
import { observationTimeText, observationWindowText } from '../observed-time';

export const FLASH_TRAIL_MINUTES = 60;

export interface LatLonBox {
  south: number;
  west: number;
  north: number;
  east: number;
}

export interface ObservedBadgeField {
  source?: string;
  label: string;
  validTime: string;
  ageMinutes: number;
  windowMinutes: number;
  attribution: string;
}

/** Route bounding box padded by the corridor width, in degrees. */
export function corridorBox(
  points: ReadonlyArray<{ lat: number; lon: number }>,
  radiusNm: number,
): LatLonBox | null {
  if (points.length === 0) return null;
  let minLat = Infinity, maxLat = -Infinity, minLon = Infinity, maxLon = -Infinity;
  for (const p of points) {
    if (p.lat < minLat) minLat = p.lat;
    if (p.lat > maxLat) maxLat = p.lat;
    if (p.lon < minLon) minLon = p.lon;
    if (p.lon > maxLon) maxLon = p.lon;
  }
  const padLat = (radiusNm * 1.852) / 111.0;
  // Longitude degrees shrink with latitude; pad using the widest latitude on
  // the route so the box never clips the corridor at its northern end.
  const cosLat = Math.max(
    0.2,
    Math.cos((Math.max(Math.abs(minLat), Math.abs(maxLat)) * Math.PI) / 180),
  );
  const padLon = padLat / cosLat;
  return {
    south: minLat - padLat,
    west: minLon - padLon,
    north: maxLat + padLat,
    east: maxLon + padLon,
  };
}

/** Opacity for a flash of a given age. Linear fade to nothing at the trail end. */
export function flashOpacity(ageMinutes: number): number {
  if (!Number.isFinite(ageMinutes) || ageMinutes < 0) return 0;
  if (ageMinutes <= 0) return 0.9;
  if (ageMinutes >= FLASH_TRAIL_MINUTES) return 0;
  return 0.9 * (1 - ageMinutes / FLASH_TRAIL_MINUTES);
}

/** Query string for the overlay and flashes endpoints. */
export function boxParams(box: LatLonBox): string {
  return new URLSearchParams({
    south: box.south.toFixed(4),
    west: box.west.toFixed(4),
    north: box.north.toFixed(4),
    east: box.east.toFixed(4),
  }).toString();
}

/** Overlay URL for one source over one bounding box. */
export function overlayUrl(source: string, box: LatLonBox): string {
  return `/api/observed/overlay/${source}.png?${boxParams(box)}`;
}

/**
 * "Radar reflectivity 14:05Z · 12 min old · 10 min acquisition window · <attribution>"
 *
 * Every clause is load-bearing. The valid time is the frame's own, never a
 * synthesised instant shared with the other sources; the age is what turns
 * "there is a cell there" into "there was a cell there twelve minutes ago";
 * and the acquisition-window note distinguishes contributing scan times from
 * the composite nominal time. It is not a maximum over earlier composites.
 */
export function formatBadge(field: ObservedBadgeField | null): string {
  if (!field) return '';
  const rolling = observationWindowText(field.source ?? '', field.windowMinutes);
  const attribution = field.attribution ? ` · ${field.attribution}` : '';
  return `${field.label} ${observationTimeText(field.validTime)}${rolling}${attribution}`;
}

/** Fetch bytes and their provenance together; a missing header means unknown. */
export async function fetchObservedImage(url: string, field: ObservedBadgeField): Promise<{ blob: Blob; field: ObservedBadgeField }> {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`Observed image unavailable (${response.status})`);
  const attribution = response.headers.get('X-Observed-Attribution') ?? '';
  const rawWindow = Number(response.headers.get('X-Observed-Window-Minutes'));
  const windowMinutes = Number.isFinite(rawWindow) && rawWindow > 0 ? rawWindow : 0;
  return {
    blob: await response.blob(),
    field: {
      ...field,
      validTime: response.headers.get('X-Observed-Valid-Time') ?? '',
      windowMinutes,
      attribution: decodeURIComponent(attribution),
    },
  };
}

/** The observed layers the map can draw, in menu order.
 *
 *  One at a time by design: these are different measurements of the same sky
 *  (echo, intensity, top height, top temperature, discharges) and stacking
 *  them would make it impossible to say which measurement a colour came from.
 *
 *  `needs` names the payload field that has to be present for the option to be
 *  offered — an option that would render an empty PNG is worse than an absent
 *  one, because the pilot cannot tell "nothing there" from "not collected".
 */
export const OBSERVED_OVERLAY_OPTIONS: Array<{
  id: string;
  labelKey: string;
  needs: 'reflectivity' | 'rainRate' | 'cloudTops' | 'lightning' | null;
  /** Points rather than a raster. */
  points?: boolean;
}> = [
  { id: '', labelKey: 'viz.observed.none', needs: null },
  { id: 'opera_dbzh', labelKey: 'viz.observed.reflectivity', needs: 'reflectivity' },
  { id: 'opera_rate', labelKey: 'viz.observed.rainRate', needs: 'rainRate' },
  { id: 'eumetsat_ctth', labelKey: 'viz.observed.cloudTops', needs: 'cloudTops' },
  { id: 'eumetsat_ctth_temp', labelKey: 'viz.observed.cloudTemp', needs: 'cloudTops' },
  { id: 'eumetsat_li', labelKey: 'viz.observed.lightning', needs: 'lightning', points: true },
];

/** Resolve the saved selection against the fields carried by this briefing. */
export function resolveObservedOverlay(
  chosen: string,
  available: { reflectivity: boolean; rainRate: boolean; cloudTops: boolean; lightning: boolean },
): string {
  // Empty is an explicit "None", not a missing preference.
  if (chosen === '') return '';
  const option = OBSERVED_OVERLAY_OPTIONS.find((candidate) => candidate.id === chosen);
  if (option?.needs && available[option.needs]) return chosen;
  return available.reflectivity ? 'opera_dbzh' : available.rainRate ? 'opera_rate' : '';
}

/** True when this selection  draws lightning points instead of a raster. */
export function isPointsOverlay(id: string): boolean {
  return OBSERVED_OVERLAY_OPTIONS.some((o) => o.id === id && o.points === true);
}
