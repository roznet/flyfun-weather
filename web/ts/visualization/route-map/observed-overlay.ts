/** Observed-conditions overlay for the briefing route map (#574).
 *
 * Three things, in one layer group:
 *
 *  1. **Corridor buffers** — the sampled discs drawn as a translucent band, so
 *     "within 20 NM of the route" is a shape on the map rather than a number
 *     in a tooltip.
 *  2. **The newest frame.** Radar is drawn as Europe-wide Web Mercator tiles
 *     keyed by the frame's stamp (#652), so the map can be panned and a tile
 *     URL always means one frame; cloud tops are still a single
 *     `imageOverlay` clipped to the corridor box. Optionally, the satellite
 *     infrared image is drawn under the radar, as Windy does. No animation
 *     yet — the stamp-keyed URLs are what a loop (#653) will step through.
 *  3. **Lightning as points, faded by age.** A ten-minute accumulation drawn
 *     flat would suggest every flash happened at once; fading by each flash's
 *     own time keeps the trail readable as a trail.
 *
 * The age badge is not decoration. A DBZH composite is a rolling ten-minute
 * maximum plus delivery lag, so an echo on screen can be ~15 min old — about
 * 30 NM of own-ship at 120 kt — and the badge is the only thing on the map
 * that says so. It reports the frame's own valid time, never a synthesised
 * "as of" shared with the other sources.
 */

import * as L from 'leaflet';
import type { VizObserved, VizRouteData } from '../types';
import {
  FLASH_TRAIL_MINUTES,
  SATELLITE_SOURCE,
  boxParams,
  corridorBox,
  currentFrame,
  flashOpacity,
  formatBadge,
  frameBadgeField,
  frameTileUrl,
  isTiledSource,
  staleBadge,
  overlayUrl as overlayUrlForBox,
  type LatLonBox,
  type ObservedFrame,
  type ObservedFramesInfo,
} from './observed-overlay-geometry';

const CORRIDOR_COLOR = '#2563eb';
const FLASH_COLOR = '#7c3aed';
/** The satellite underlay is a picture of the whole sky; a fixed opacity lets
 *  the muted basemap's coastlines read through it, and the radar opacity
 *  slider stays about the radar. */
const SATELLITE_OPACITY = 0.8;
// Tile layers stack in Leaflet's tile pane: basemap (1) < satellite < radar.
const SATELLITE_Z = 5;
const RADAR_Z = 6;

export { flashOpacity, corridorBox } from './observed-overlay-geometry';

export interface ObservedOverlayOptions {
  /** Which gridded source to draw, or `null` to draw no imagery. */
  imagerySource: string | null;
  /** Opacity of the imagery, 0–1. */
  imageryOpacity?: number;
  /** Corridor width (NM) whose buffer is outlined. */
  radiusNm: number;
  /** Frame listings per tiled source (`/api/observed/frames/{source}`). A
   *  tiled source with no listing falls back to the corridor image. */
  frames?: ReadonlyMap<string, ObservedFramesInfo>;
  /** Draw the satellite infrared underlay. */
  showSatellite?: boolean;
}

export interface ObservedFlashPoint {
  lat: number;
  lon: number;
  time: string;
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
    source === 'eumetsat_ctth' ? observed.cloudTops
      : source === 'opera_rate' ? observed.rainRate
      : observed.reflectivity ?? observed.lightning;
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
  // Tile layers persist across renders (the map re-renders on every altitude
  // drag); everything else is redrawn. Clear the rest now, before this render
  // adds its own; `wanted` collects this render's tiles for `reconcileTiles`.
  clearNonTileLayers(group);
  const wanted: TileSpec[] = [];
  const finish = (badge: string): string => {
    reconcileTiles(group, wanted);
    return badge;
  };
  const observed = data.observed;
  if (!observed) return finish('');

  const bounds = corridorBounds(data, options.radiusNm);
  if (!bounds) return finish('');

  const badges: string[] = [];

  // 1. Satellite infrared, under the radar.
  if (options.showSatellite) {
    const info = options.frames?.get(SATELLITE_SOURCE);
    const frame = currentFrame(info);
    if (info && frame) {
      wanted.push(tileSpec(info, frame, SATELLITE_OPACITY, SATELLITE_Z));
      badges.push(formatBadge(frameBadgeField(info, frame, now)));
    } else {
      badges.push(staleBadge(info, now));
    }
  }

  // 2. The radar / cloud-top frame itself.
  if (options.imagerySource) {
    const info = isTiledSource(options.imagerySource)
      ? options.frames?.get(options.imagerySource)
      : undefined;
    const frame = currentFrame(info);
    if (info && frame) {
      wanted.push(tileSpec(info, frame, options.imageryOpacity ?? 0.75, RADAR_Z));
      badges.unshift(formatBadge(frameBadgeField(info, frame, now)));
    } else if (!isTiledSource(options.imagerySource) || !info) {
      // Cloud tops, or a radar listing that has not arrived / failed: the
      // corridor image, labelled from the briefing's own sample.
      L.imageOverlay(overlayUrl(options.imagerySource, bounds), bounds, {
        opacity: options.imageryOpacity ?? 0.75,
        interactive: false,
      }).addTo(group);
      badges.unshift(badgeText(observed, options.imagerySource));
    }
    // A listing that says "stale" draws nothing: an old frame must not pass
    // for the present sky, which is also what the corridor endpoint's 410 does.
    // The badge says so, so it does not read as "no echoes".
    if (info && !frame) badges.unshift(staleBadge(info, now));
  } else {
    badges.unshift(badgeText(observed, options.imagerySource));
  }

  // 3. The corridor the numbers describe.
  L.rectangle(bounds, {
    color: CORRIDOR_COLOR,
    weight: 1,
    opacity: 0.45,
    fill: false,
    dashArray: '5,4',
    interactive: false,
  }).addTo(group);

  // 4. Lightning, oldest first so recent flashes draw on top.
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

  return finish(badges.filter(Boolean).join('\n'));
}

interface TileSpec {
  url: string;
  opacity: number;
  zIndex: number;
  minNativeZoom: number;
  maxNativeZoom: number;
}

function tileSpec(
  info: ObservedFramesInfo,
  frame: ObservedFrame,
  opacity: number,
  zIndex: number,
): TileSpec {
  return {
    url: frameTileUrl(info, frame),
    opacity,
    zIndex,
    minNativeZoom: info.min_zoom,
    maxNativeZoom: info.max_zoom,
  };
}

// Live tile layers per group, keyed by URL (= source + frame stamp).
const liveTiles = new WeakMap<L.LayerGroup, Map<string, L.TileLayer>>();

// Tile layers on their way out: an old frame kept until its successor loads.
const retiringTiles = new WeakMap<L.LayerGroup, Set<L.Layer>>();

function clearNonTileLayers(group: L.LayerGroup): void {
  const tiles = new Set<L.Layer>(liveTiles.get(group)?.values() ?? []);
  const retiring = retiringTiles.get(group);
  group.eachLayer((layer) => {
    if (!tiles.has(layer) && !retiring?.has(layer)) group.removeLayer(layer);
  });
}

/** Keep tile layers whose URL is still wanted (updating opacity), add new
 *  ones, drop the rest. Recreating tile layers on every render would
 *  re-request and flash every tile on each altitude-slider drag; a new frame
 *  is a new URL, so it still swaps. Touches tile layers only. */
function reconcileTiles(group: L.LayerGroup, wanted: TileSpec[]): void {
  const live = liveTiles.get(group) ?? new Map<string, L.TileLayer>();
  liveTiles.set(group, live);
  const keep = new Set<L.Layer>();
  const added: { layer: L.TileLayer; zIndex: number }[] = [];
  for (const spec of wanted) {
    let layer = live.get(spec.url);
    if (layer && group.hasLayer(layer)) {
      layer.setOpacity(spec.opacity);
    } else {
      layer = tileLayerFor(spec);
      live.set(spec.url, layer);
      layer.addTo(group);
      added.push({ layer, zIndex: spec.zIndex });
    }
    keep.add(layer);
  }
  for (const [url, layer] of live) {
    if (keep.has(layer)) continue;
    live.delete(url);
    // A new frame of the same layer (same z-index) replaces this one: keep
    // the old frame on screen until the new tiles have loaded, so the map
    // does not blank once per radar cycle. Removed at the latest after
    // FRAME_SWAP_TIMEOUT_MS (a successor whose tiles all fail never fires
    // `load`). Re-renders in between leave it alone (`retiringTiles`).
    const successor = added.find((a) => a.zIndex === layer.options.zIndex);
    if (successor) {
      const retiring = retiringTiles.get(group) ?? new Set<L.Layer>();
      retiringTiles.set(group, retiring);
      retiring.add(layer);
      const drop = (): void => {
        retiring.delete(layer);
        group.removeLayer(layer);
      };
      successor.layer.once('load', drop);
      setTimeout(drop, FRAME_SWAP_TIMEOUT_MS);
    } else {
      group.removeLayer(layer);
    }
  }
}

const FRAME_SWAP_TIMEOUT_MS = 10_000;

function tileLayerFor(spec: TileSpec): L.TileLayer {
  return L.tileLayer(spec.url, {
    opacity: spec.opacity,
    zIndex: spec.zIndex,
    // Leaflet scales the nearest native zoom outside this range rather than
    // requesting tiles the server refuses.
    minNativeZoom: spec.minNativeZoom,
    maxNativeZoom: spec.maxNativeZoom,
    maxZoom: 18,
    // No Leaflet attribution: the badge already carries each layer's own
    // (per-frame) attribution, and these strings are long enough to wrap the
    // attribution bar over the badge.
  });
}

const FRAMES_TTL_MS = 60_000;
const framesCache = new Map<string, { at: number; info: ObservedFramesInfo | null }>();

/** Frame listing for a tiled source, cached briefly (a new radar frame lands
 *  every 5 min). `null` when unavailable: the caller falls back or omits. */
export async function fetchObservedFrames(
  source: string,
  now: number = Date.now(),
): Promise<ObservedFramesInfo | null> {
  const hit = framesCache.get(source);
  if (hit && now - hit.at < FRAMES_TTL_MS) return hit.info;
  let info: ObservedFramesInfo | null = null;
  try {
    const response = await fetch(`/api/observed/frames/${encodeURIComponent(source)}`);
    if (response.ok) info = (await response.json()) as ObservedFramesInfo;
  } catch {
    info = null;
  }
  framesCache.set(source, { at: now, info });
  return info;
}

/** Cached listing if fresh, without fetching (for synchronous renders). */
export function cachedObservedFrames(source: string, now: number = Date.now()): ObservedFramesInfo | null | undefined {
  const hit = framesCache.get(source);
  if (!hit || now - hit.at >= FRAMES_TTL_MS) return undefined;
  return hit.info;
}

/** Fetch lightning points inside the corridor. Failure is not fatal: the
 *  imagery and corridor still draw, and the badge still says how old they are. */
export async function fetchObservedFlashes(
  bounds: L.LatLngBounds,
): Promise<ObservedFlashPoint[]> {
  const response = await fetch(`/api/observed/flashes?${boxParams(boundsToBox(bounds))}`);
  if (!response.ok) return [];
  const payload = await response.json();
  return (payload.flashes ?? []) as ObservedFlashPoint[];
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
 *  pilot reads celsius. Heights arrive in metres and are read as flight levels.
 */
export function formatLegendValue(source: string, value: number, units: string): string {
  if (source === 'eumetsat_ctth_temp') return `${Math.round(value - 273.15)}°C`;
  if (source === 'eumetsat_ctth') return `FL${Math.round((value * 3.28084) / 100)}`;
  if (units === 'mm/h') return `${value}`;
  return `${value}`;
}
