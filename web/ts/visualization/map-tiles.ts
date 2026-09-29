/** Base-map tiles shared by every Leaflet map, per theme.
 *
 * Dark uses Stadia's Alidade Smooth Dark: CARTO's `dark_all` (the previous
 * dark tiles) now answers every request with an "API KEY REQUIRED" placeholder.
 * Stadia authenticates by the page's domain (registered in the Stadia client
 * dashboard), so no key appears here — an unregistered origin gets a 401 tile.
 */

import * as L from 'leaflet';

const LIGHT_TILES = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png';
const DARK_TILES = 'https://tiles.stadiamaps.com/tiles/alidade_smooth_dark/{z}/{x}/{y}{r}.png';
const LIGHT_ATTR = '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>';
const DARK_ATTR = '&copy; <a href="https://stadiamaps.com/">Stadia Maps</a> &copy; <a href="https://openmaptiles.org/">OpenMapTiles</a> &copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>';

export function baseTileUrl(dark: boolean): string {
  return dark ? DARK_TILES : LIGHT_TILES;
}

export function baseTileAttribution(dark: boolean): string {
  return dark ? DARK_ATTR : LIGHT_ATTR;
}

/** Base tile layer for the given theme (not yet added to a map). */
export function createBaseTileLayer(dark: boolean, maxZoom = 18): L.TileLayer {
  return L.tileLayer(baseTileUrl(dark), { attribution: baseTileAttribution(dark), maxZoom });
}

/** Swap a base layer to the other theme, keeping the attribution control in step. */
export function applyBaseTileTheme(map: L.Map, layer: L.TileLayer, dark: boolean): void {
  const prev = layer.options.attribution;
  const next = baseTileAttribution(dark);
  layer.options.attribution = next;
  layer.setUrl(baseTileUrl(dark));
  if (prev !== next && map.attributionControl) {
    if (prev) map.attributionControl.removeAttribution(prev);
    map.attributionControl.addAttribution(next);
  }
}
