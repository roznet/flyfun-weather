/** "Now" tab's URL keys (`now.*`, #796) and its layer set, kept free of
 *  Leaflet so the codec is unit-testable.
 *
 * Layers precedence on load: the URL's `now.layers` beats the viewer's saved
 * set (`localStorage`), so a shared link shows what the sender saw; no key
 * means the viewer's saved set, else the defaults. The key is elided only when
 * the set equals `DEFAULT_NOW_LAYERS`, so a sender with custom layers always
 * carries them. (A sender on the defaults sends no key, so a viewer with a
 * custom saved set sees their own: the price of keeping a bare URL bare.) */

import { mapViewField } from '../utils/map-view';
import type { Codec, UrlValues } from '../utils/url-state';

export type NowLayer = 'airports' | 'radar' | 'satellite' | 'lightning' | 'cells' | 'rain';
export const NOW_LAYERS: readonly NowLayer[] = ['airports', 'radar', 'satellite', 'lightning', 'cells', 'rain'];
export type NowLayers = Record<NowLayer, boolean>;

export const DEFAULT_NOW_LAYERS: Readonly<NowLayers> = {
  airports: true, radar: true, satellite: true, lightning: true, cells: true,
  // Rain areas are off by default, as in the prototype: dozens of frontal
  // outlines would bury the cores.
  rain: false,
};

/** Every layer off. A distinct token, since '' is "no key". */
const NONE = 'none';

/** Enabled layers in `NOW_LAYERS` order ('none' when all are off), or '' when
 *  the set is the default one. */
export function encodeNowLayers(layers: NowLayers): string {
  if (NOW_LAYERS.every((k) => layers[k] === DEFAULT_NOW_LAYERS[k])) return '';
  const on = NOW_LAYERS.filter((k) => layers[k]);
  return on.length ? on.join(',') : NONE;
}

/** The set a `now.layers` value names, or null when it names nothing valid
 *  (absent, garbage): the caller then falls back to the viewer's saved set.
 *  Unknown names next to known ones are dropped, so a link from a build with
 *  an extra layer still opens. */
export function decodeNowLayers(raw: string): NowLayers | null {
  if (raw === NONE) return Object.fromEntries(NOW_LAYERS.map((k) => [k, false])) as NowLayers;
  const names = new Set(raw.split(',').map((s) => s.trim()));
  if (!NOW_LAYERS.some((k) => names.has(k))) return null;
  return Object.fromEntries(NOW_LAYERS.map((k) => [k, names.has(k)])) as NowLayers;
}

const nowLayersCodec: Codec<string> = {
  // Canonicalise on read (order, unknown names); garbage falls back to ''.
  parse: (raw) => {
    const layers = decodeNowLayers(raw);
    if (!layers) return undefined;
    const s = encodeNowLayers(layers);
    // A link that spells out the default set must not collapse to '' (no key),
    // or it would lose to the viewer's localStorage. Keep the explicit list.
    return s || NOW_LAYERS.filter((k) => layers[k]).join(',');
  },
  format: (v) => v,
};

export const NOW_URL_SCHEMA = {
  'now.layers': { default: '', codec: nowLayersCodec },
  'now.view':   mapViewField(),
  // Open airport panel (ICAO), mirroring fc.apt / hist.apt.
  'now.apt':    { default: '' },
};

export type NowUrlValues = UrlValues<typeof NOW_URL_SCHEMA>;
