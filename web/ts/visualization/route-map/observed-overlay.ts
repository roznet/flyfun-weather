/** Observed-conditions overlay for the briefing route map (#574).
 *
 * Three things, in one layer group:
 *
 *  1. **Corridor buffers** — the sampled discs drawn as a translucent band, so
 *     "within 20 NM of the route" is a shape on the map rather than a number
 *     in a tooltip.
 *  2. **The newest frame**, clipped to that corridor's bounding box, as a
 *     single `imageOverlay`. Not tiles, not an animation, and no time slider:
 *     the question this answers is "is that cell on my route right now?", and
 *     a looping tiled radar product is a different, much more expensive thing.
 *  3. **Lightning as points, faded by age.** A ten-minute accumulation drawn
 *     flat would suggest every flash happened at once; fading by each flash's
 *     own time keeps the trail readable as a trail.
 *
 * The age badge is not decoration. A DBZH composite uses scans from the prior
 * ten minutes plus delivery lag, so an echo on screen can be ~15 min old — about
 * 30 NM of own-ship at 120 kt — and the badge is the only thing on the map
 * that says so. It reports the frame's own valid time, never a synthesised
 * "as of" shared with the other sources.
 */

import * as L from 'leaflet';
import type { VizObserved, VizRouteData } from '../types';
import {
  FLASH_TRAIL_MINUTES,
  boxParams,
  corridorBox,
  flashOpacity,
  formatBadge,
  overlayUrl as overlayUrlForBox,
  type LatLonBox,
} from './observed-overlay-geometry';

const CORRIDOR_COLOR = '#2563eb';
const FLASH_COLOR = '#7c3aed';

export { flashOpacity, corridorBox } from './observed-overlay-geometry';

export interface ObservedOverlayOptions {
  /** Which gridded source to draw, or `null` to draw no imagery. */
  imagerySource: string | null;
  /** Opacity of the imagery, 0–1. */
  imageryOpacity?: number;
  /** Corridor width (NM) whose buffer is outlined. */
  radiusNm: number;
  /** Loaded bytes with provenance retained by the owning renderer. */
  imageUrl?: string;
}

export interface ObservedFlashPoint {
  lat: number;
  lon: number;
  time: string;
}

export interface ObservedFlashResult {
  flashes: ObservedFlashPoint[];
  field: import('./observed-overlay-geometry').ObservedBadgeField;
}

/** Route corridor box as Leaflet bounds. */
export function corridorBounds(data: VizRouteData, radiusNm: number): L.LatLngBounds | null {
  const box = corridorBox(data.points, radiusNm);
  if (!box) return null;
  return L.latLngBounds([box.south, box.west], [box.north, box.east]);
}

function boundsToBox(bounds: L.LatLngBounds): LatLonBox {
  return {
    south: bounds.getSouth(),
    west: bounds.getWest(),
    north: bounds.getNorth(),
    east: bounds.getEast(),
  };
}

/** Age badge for whichever source the map is currently drawing. */
export function badgeText(observed: VizObserved, source: string | null): string {
  const field =
    source === 'eumetsat_ctth' || source === 'eumetsat_ctth_temp' ? observed.cloudTops
      : source === 'opera_rate' ? observed.rainRate
      : source === 'opera_dbzh' ? observed.reflectivity
      : source === 'eumetsat_li' ? observed.lightning : null;
  return formatBadge(field);
}

/** Overlay URL for one source over one bounding box. */
export function overlayUrl(source: string, bounds: L.LatLngBounds): string {
  return overlayUrlForBox(source, boundsToBox(bounds));
}

/**
 * Draw (or redraw) the observed overlay into `group`.
 *
 * Returns the badge text the caller should show, or `''` when there is
 * nothing observed to label.
 */
export function renderObservedOverlay(
  group: L.LayerGroup,
  map: L.Map,
  data: VizRouteData,
  options: ObservedOverlayOptions,
  flashes: readonly ObservedFlashPoint[],
  now: Date = new Date(),
): string {
  group.clearLayers();
  const observed = data.observed;
  if (!observed) return '';

  const bounds = corridorBounds(data, options.radiusNm);
  if (!bounds) return '';

  // 1. The frame itself, under everything else.
  if (options.imageUrl) {
    L.imageOverlay(options.imageUrl, bounds, {
      opacity: options.imageryOpacity ?? 0.75,
      interactive: false,
    }).addTo(group);
  }

  // 2. The corridor the numbers describe.
  L.rectangle(bounds, {
    color: CORRIDOR_COLOR,
    weight: 1,
    opacity: 0.45,
    fill: false,
    dashArray: '5,4',
    interactive: false,
  }).addTo(group);

  // 3. Lightning, oldest first so recent flashes draw on top.
  addObservedFlashes(group, flashes, now);

  return badgeText(observed, options.imagerySource);
}

/** Redraw only the clock-sensitive lightning markers, leaving raster layers intact. */
export function renderObservedFlashes(
  group: L.LayerGroup,
  flashes: readonly ObservedFlashPoint[],
  now: Date = new Date(),
): void {
  group.clearLayers();
  addObservedFlashes(group, flashes, now);
}

function addObservedFlashes(
  group: L.LayerGroup,
  flashes: readonly ObservedFlashPoint[],
  now: Date,
): void {
  const dated = flashes
    .map((f) => ({ flash: f, ageMinutes: (now.getTime() - new Date(f.time).getTime()) / 60000 }))
    .filter((f) => Number.isFinite(f.ageMinutes) && f.ageMinutes < FLASH_TRAIL_MINUTES)
    .sort((a, b) => b.ageMinutes - a.ageMinutes);
  for (const { flash, ageMinutes } of dated) {
    const opacity = flashOpacity(ageMinutes);
    if (opacity <= 0) continue;
    L.circleMarker([flash.lat, flash.lon], {
      radius: 3,
      color: FLASH_COLOR,
      fillColor: FLASH_COLOR,
      fillOpacity: opacity,
      opacity,
      weight: 1,
      interactive: false,
    }).addTo(group);
  }
}

/** Fetch lightning points inside the corridor. Failure is not fatal: the
 *  imagery and corridor still draw, and the badge still says how old they are. */
export async function fetchObservedFlashes(
  bounds: L.LatLngBounds,
): Promise<ObservedFlashResult> {
  const response = await fetch(`/api/observed/flashes?${boxParams(boundsToBox(bounds))}`);
  if (!response.ok) throw new Error('Observed lightning unavailable');
  const payload = await response.json();
  return {
    flashes: payload.flashes ?? [],
    field: { source: 'eumetsat_li', label: 'Lightning', validTime: payload.newest_valid_time ?? '', ageMinutes: 0,
      windowMinutes: payload.window_minutes ?? 0, attribution: payload.attribution?.text ?? '' },
  };
}

/** One colour stop of a source's ramp, as the server renders it. */
export interface ObservedLegendStop {
  value: number;
  color: string;
}

export interface ObservedSourceStatus {
  source: string;
  label: string;
  units: string;
  legend: ObservedLegendStop[];
}

let statusCache: Map<string, ObservedSourceStatus> | null = null;

/** Per-source legends, fetched once and cached.
 *
 *  From the server rather than a client-side copy of the ramps: `legend_for`
 *  exists precisely "so it cannot drift from the render", and until now nothing
 *  consumed it. A second table here would be a second thing to keep in step
 *  with the renderer, and the map's whole job is to show what was measured.
 */
export async function fetchObservedLegends(): Promise<Map<string, ObservedSourceStatus>> {
  if (statusCache) return statusCache;
  try {
    const response = await fetch('/api/observed/status');
    if (!response.ok) return new Map();
    const payload = await response.json();
    statusCache = new Map(
      (payload.sources ?? []).map((s: ObservedSourceStatus) => [s.source, s]),
    );
    return statusCache!;
  } catch {
    // A missing legend costs the scale, not the overlay.
    return new Map();
  }
}

/** Format a ramp value for the legend, per source.
 *
 *  Temperature arrives in kelvin because that is what the granule stores; a
 *  pilot reads celsius. Geometric heights arrive in metres and are shown in ft MSL.
 */
export function formatLegendValue(source: string, value: number, units: string): string {
  if (source === 'eumetsat_ctth_temp') return `${Math.round(value - 273.15)}°C`;
  if (source === 'eumetsat_ctth') return `${Math.round(value * 3.28084)} ft MSL`;
  if (units === 'mm/h') return `${value}`;
  return `${value}`;
}
