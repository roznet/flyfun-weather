/** Map viewport in the URL (#796): `lat,lon,zoom`, lat/lon to 2 dp, integer zoom.
 *
 * Each map-bearing tab of the maps page owns one key (`fc.view`, `now.view`,
 * `syn.view`, `hist.view`, `clim.view`), since each has its own Leaflet map
 * and the user may have positioned them differently.
 *
 * The value is kept as the canonical string, with '' meaning "the map's
 * initial view": `createUrlState` elides a field equal to its default by
 * identity, so a string compares cleanly where an object would not, and an
 * untouched map leaves no key in the URL.
 *
 * Only Leaflet's types are imported, so the codec stays unit-testable in
 * vitest's node environment.
 */

import type * as L from 'leaflet';
import type { Codec, UrlField } from './url-state';

export interface MapView {
  lat: number;
  lon: number;
  zoom: number;
}

/** The view every maps-page map opens on (Central Europe). */
export const DEFAULT_MAP_VIEW: MapView = { lat: 48, lon: 10, zoom: 5 };

// Leaflet's own zoom range for the tile layers in use; anything outside is a
// hand-edited or garbage link.
const MIN_ZOOM = 1;
const MAX_ZOOM = 18;

export function parseMapView(raw: string): MapView | null {
  const parts = raw.split(',');
  if (parts.length !== 3) return null;
  const [lat, lon, zoom] = parts.map((p) => (p.trim() === '' ? NaN : Number(p)));
  if (!Number.isFinite(lat) || !Number.isFinite(lon) || !Number.isFinite(zoom)) return null;
  if (lat < -90 || lat > 90 || lon < -180 || lon > 180) return null;
  const z = Math.round(zoom);
  if (z < MIN_ZOOM || z > MAX_ZOOM) return null;
  return { lat, lon, zoom: z };
}

export function formatMapView(v: MapView): string {
  // Normalise -0.00 to 0.00 so the same view always formats the same.
  const fix = (n: number) => (Math.abs(n) < 0.005 ? 0 : n).toFixed(2);
  return `${fix(v.lat)},${fix(v.lon)},${Math.round(v.zoom)}`;
}

/** Parse to the canonical string; garbage is undefined so `createUrlState`
 *  falls back to the default (the map's initial view). */
export const mapViewCodec: Codec<string> = {
  parse: (raw) => {
    const v = parseMapView(raw);
    return v ? formatMapView(v) : undefined;
  },
  format: (v) => v,
};

/** Schema field for a tab's `*.view` key. */
export function mapViewField(): UrlField<string> {
  return { default: '', codec: mapViewCodec };
}

/** The subset of `L.Map` the binding touches (keeps it testable with a stub). */
export type ViewMap = Pick<L.Map, 'getCenter' | 'getZoom' | 'setView' | 'on' | 'off'>;

export interface MapViewBinding {
  /** Current view as the URL value: '' while it equals the default view. */
  current(): string;
  dispose(): void;
}

export interface BindMapViewOptions {
  /** URL-hydrated view ('' = none); applied immediately when it parses. */
  initial?: string;
  /** Called (debounced) after the user moves the map to a different view. */
  onChange: () => void;
  /** The view `current()` elides. Defaults to the map's view at bind time. */
  defaultView?: MapView;
  debounceMs?: number;
}

function viewOf(map: ViewMap): MapView {
  const c = map.getCenter();
  return { lat: c.lat, lon: c.lng, zoom: map.getZoom() };
}

/** Keep a Leaflet map's viewport in step with its tab's `*.view` key.
 *
 * Apply the initial view **after** anything that would move the map on first
 * load (none of the maps page's maps auto-fit to data today), or the shared
 * position is overwritten.
 *
 * `onChange` fires only when the formatted view actually changed: Leaflet
 * also emits `moveend` from `invalidateSize()` (tab shown, panel opened),
 * which keeps the centre, and a page write on that would put state the user
 * never touched (e.g. the forecast tab's smart-default hour) into a bare URL.
 */
export function bindMapView(map: ViewMap, opts: BindMapViewOptions): MapViewBinding {
  const defaultFormatted = formatMapView(opts.defaultView ?? viewOf(map));
  const initial = opts.initial ? parseMapView(opts.initial) : null;
  if (initial) map.setView([initial.lat, initial.lon], initial.zoom, { animate: false });

  const current = (): string => {
    const f = formatMapView(viewOf(map));
    return f === defaultFormatted ? '' : f;
  };

  let notified = current();
  let timer: ReturnType<typeof setTimeout> | null = null;
  const onMoveEnd = () => {
    if (timer) clearTimeout(timer);
    timer = setTimeout(() => {
      timer = null;
      const now = current();
      if (now === notified) return;
      notified = now;
      opts.onChange();
    }, opts.debounceMs ?? 300);
  };
  map.on('moveend', onMoveEnd);

  return {
    current,
    dispose: () => {
      if (timer) clearTimeout(timer);
      map.off('moveend', onMoveEnd);
    },
  };
}
