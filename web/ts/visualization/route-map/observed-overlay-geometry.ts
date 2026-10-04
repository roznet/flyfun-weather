/** Pure geometry and labelling for the observed map overlay (#574).
 *
 * Split out of `observed-overlay.ts` so it carries no Leaflet import: Leaflet
 * touches `window` at module load, which makes anything that imports it
 * untestable in a node environment. The rules encoded here — how wide the
 * corridor box is, how a flash fades, what the age badge says — are exactly
 * the parts worth testing, so they live where tests can reach them.
 */

/** Lightning older than this is dropped rather than drawn nearly-invisible. */
export const FLASH_TRAIL_MINUTES = 60;

export interface LatLonBox {
  south: number;
  west: number;
  north: number;
  east: number;
}

export interface ObservedBadgeField {
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
 * "Radar reflectivity 14:05Z · 12 min old · 10 min rolling max · <attribution>"
 *
 * Every clause is load-bearing. The valid time is the frame's own, never a
 * synthesised instant shared with the other sources; the age is what turns
 * "there is a cell there" into "there was a cell there twelve minutes ago";
 * and the rolling-window note is what stops a 10-minute maximum being read as
 * a snapshot. At 120 kt those minutes are tens of nautical miles.
 */
export function formatBadge(field: ObservedBadgeField | null): string {
  if (!field) return '';
  const stamp = new Date(field.validTime);
  const hhmm = Number.isNaN(stamp.getTime())
    ? '--:--'
    : `${String(stamp.getUTCHours()).padStart(2, '0')}:${String(stamp.getUTCMinutes()).padStart(2, '0')}Z`;
  const age = field.ageMinutes < 1 ? 'just now' : `${Math.round(field.ageMinutes)} min old`;
  const rolling = field.windowMinutes > 0
    ? ` · ${Math.round(field.windowMinutes)} min rolling max`
    : '';
  const attribution = field.attribution ? ` · ${field.attribution}` : '';
  return `${field.label} ${hhmm} · ${age}${rolling}${attribution}`;
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

/** True when this selection  draws lightning points instead of a raster. */
export function isPointsOverlay(id: string): boolean {
  return OBSERVED_OVERLAY_OPTIONS.some((o) => o.id === id && o.points === true);
}

// --- Tiled layers (#652) ------------------------------------------------------

/** Radar sources drawn as Europe-wide tiles rather than a corridor image.
 *  Cloud tops stay on the corridor image (the server renders them per box). */
export const TILED_SOURCES: readonly string[] = ['opera_dbzh', 'opera_rate'];

/** The satellite infrared underlay (EUMETView, proxied by the server). */
export const SATELLITE_SOURCE = 'satellite_ir';

export function isTiledSource(id: string | null | undefined): boolean {
  return !!id && TILED_SOURCES.includes(id);
}

/** One frame of a tiled layer, as `/api/observed/frames/{source}` lists it. */
export interface ObservedFrame {
  stamp: string;
  valid_time: string;
  age_minutes: number;
}

/** `/api/observed/frames/{source}`. Every retained frame, newest first — the
 *  map draws `frames[0]`; a loop (#653) steps through the rest. */
export interface ObservedFramesInfo {
  source: string;
  label: string;
  frames: ObservedFrame[];
  stale: boolean;
  window_minutes: number;
  attribution: { text?: string | null } | null;
  tile_url_template: string;
  min_zoom: number;
  max_zoom: number;
}

/** Tile URL for one frame. The stamp is in the path, so the URL is the frame:
 *  a different frame is a different URL, never a re-fetch of the same one. */
export function frameTileUrl(info: ObservedFramesInfo, frame: ObservedFrame): string {
  return info.tile_url_template.replace('{stamp}', encodeURIComponent(frame.stamp));
}

/** The frame the map should draw now, or `null` when there is nothing current
 *  — a stale radar frame is not drawn as if it were the present sky. */
export function currentFrame(info: ObservedFramesInfo | null | undefined): ObservedFrame | null {
  if (!info || info.stale || !info.frames.length) return null;
  return info.frames[0];
}

/** Badge line for a layer whose newest frame is too old to draw, so a pilot
 *  can tell "radar hidden because stale" from "no echoes". `''` when there is
 *  nothing to say (no listing, or a current frame). */
export function staleBadge(info: ObservedFramesInfo | null | undefined, now: Date = new Date()): string {
  if (!info || !info.stale) return '';
  const newest = info.frames[0];
  if (!newest) return `${info.label}: no current frame`;
  const valid = new Date(newest.valid_time);
  if (Number.isNaN(valid.getTime())) return `${info.label}: no current frame`;
  const hhmm = `${String(valid.getUTCHours()).padStart(2, '0')}:${String(valid.getUTCMinutes()).padStart(2, '0')}Z`;
  const age = Math.round((now.getTime() - valid.getTime()) / 60000);
  return `${info.label}: not shown — newest frame ${hhmm} is ${age} min old`;
}

/** Badge fields for a drawn frame, from the frame itself (not the briefing's
 *  sample, which may be an older frame than the one on screen). */
export function frameBadgeField(
  info: ObservedFramesInfo,
  frame: ObservedFrame,
  now: Date = new Date(),
): ObservedBadgeField {
  const valid = new Date(frame.valid_time);
  const ageMinutes = Number.isNaN(valid.getTime())
    ? frame.age_minutes
    : Math.max(0, (now.getTime() - valid.getTime()) / 60000);
  return {
    label: info.label,
    validTime: frame.valid_time,
    ageMinutes,
    windowMinutes: info.window_minutes ?? 0,
    attribution: info.attribution?.text ?? '',
  };
}

/** The observed layer to draw for the pilot's pick.
 *
 *  `''` ("None") is a choice, not a missing value: it draws nothing. Only a
 *  pick this briefing did not collect falls back — to reflectivity, else rain
 *  rate, else nothing. (The fallback used to test the pick for truthiness, so
 *  "None" fell back to radar.)
 */
export function resolveObservedSelection(
  chosen: string | null | undefined,
  available: Readonly<Record<string, boolean>>,
): string {
  if (chosen === '') return '';
  if (chosen && available[chosen]) return chosen;
  return available.opera_dbzh ? 'opera_dbzh' : available.opera_rate ? 'opera_rate' : '';
}

/** The flashes endpoint's box limit for a whole-map request (server
 *  `MAX_FLASH_SPAN_DEG`, #656's "Now" tab). */
export const MAX_FLASH_SPAN_DEG = 80;

/** Clamp a viewport to what the flashes endpoint accepts, around its centre. */
export function flashBox(view: LatLonBox, maxSpan: number = MAX_FLASH_SPAN_DEG): LatLonBox {
  const clamp = (lo: number, hi: number): [number, number] => {
    if (hi - lo <= maxSpan) return [lo, hi];
    const mid = (lo + hi) / 2;
    return [mid - maxSpan / 2, mid + maxSpan / 2];
  };
  const [south, north] = clamp(Math.max(-89, view.south), Math.min(89, view.north));
  const [west, east] = clamp(view.west, view.east);
  return { south, west, north, east };
}
