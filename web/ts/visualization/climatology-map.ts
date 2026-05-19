/** Climatology map — Leaflet circle markers colored by a single per-airport value.
 *
 * Generic over dataset: takes a ``Scale`` (color stops + legend labels) and a
 * popup-HTML builder per render. The same map instance serves all 4 dataset
 * variants in views #1–#4; switching datasets is just ``setScale`` + ``setData``.
 */

import * as L from 'leaflet';

const LIGHT_TILES = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png';
const DARK_TILES = 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png';
const ATTR = '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>';

function isDark(): boolean {
  return document.documentElement.dataset.theme === 'dark';
}

// --- Color palette (named so dataset scales stay readable) -----------------

export const COLORS = {
  green:   '#22c55e',
  lime:    '#84cc16',
  yellow:  '#eab308',
  orange:  '#f97316',
  red:     '#ef4444',
  darkred: '#991b1b',
  grey:    '#888',
} as const;

// --- Scale definitions -----------------------------------------------------

export interface Stop {
  /** Inclusive lower bound. The first stop that ``value >= bound`` wins. */
  bound: number;
  color: string;
}

export interface Scale {
  title: string;
  unit: string;
  /** Stops ordered worst→best for unfavorable metrics, best→worst for VFR.
   *  ``pctColor`` walks them top-to-bottom and returns the first match. */
  stops: Stop[];
}

export function pctColor(value: number, scale: Scale): string {
  for (const s of scale.stops) {
    if (value >= s.bound) return s.color;
  }
  return COLORS.grey;
}

/** Build legend rows (color + label) from a scale's stops. */
export function legendRows(scale: Scale): { color: string; label: string }[] {
  const stops = scale.stops;
  const rows: { color: string; label: string }[] = [];
  for (let i = 0; i < stops.length; i++) {
    const s = stops[i];
    const next = stops[i - 1];
    const formatBound = (v: number) =>
      v >= 10 || v === 0 ? `${v}` : v.toFixed(1);
    if (i === 0) {
      rows.push({ color: s.color, label: `≥ ${formatBound(s.bound)}${scale.unit}` });
    } else {
      const prev = stops[i - 1].bound;
      rows.push({
        color: s.color,
        label: `${formatBound(s.bound)}–${formatBound(prev)}${scale.unit}`,
      });
    }
    void next;
  }
  return rows;
}

// --- Map class -------------------------------------------------------------

export interface MapAirport {
  icao: string;
  lat: number;
  lon: number;
  value: number | null;
  [extra: string]: unknown;
}

export type PopupBuilder = (a: MapAirport) => string;

export class ClimatologyMap {
  private map: L.Map;
  private tileLayer: L.TileLayer;
  private markersLayer: L.LayerGroup;
  private themeObserver: MutationObserver;
  private airports: MapAirport[] = [];
  private scale: Scale | null = null;
  private buildPopup: PopupBuilder = () => '';

  constructor(containerId: string) {
    const el = document.getElementById(containerId);
    if (!el) throw new Error(`No element #${containerId}`);

    this.map = L.map(el, {
      center: [48, 10],
      zoom: 5,
      worldCopyJump: true,
    });
    this.tileLayer = L.tileLayer(isDark() ? DARK_TILES : LIGHT_TILES, {
      attribution: ATTR, maxZoom: 10,
    }).addTo(this.map);
    this.markersLayer = L.layerGroup().addTo(this.map);

    this.themeObserver = new MutationObserver(() => {
      this.tileLayer.setUrl(isDark() ? DARK_TILES : LIGHT_TILES);
    });
    this.themeObserver.observe(document.documentElement, {
      attributes: true, attributeFilter: ['data-theme'],
    });
  }

  /** Switch the color scale and popup builder.
   *
   * Does NOT re-render — the caller is expected to follow with ``setData``
   * carrying matching airport rows. Eagerly re-rendering here would apply
   * the new popup builder to stale airports from the previous dataset,
   * which fails when popups expect dataset-specific fields (e.g. the
   * volatility popup reading ``n_obs`` from wind data that doesn't carry it).
   */
  setScale(scale: Scale, buildPopup: PopupBuilder): void {
    this.scale = scale;
    this.buildPopup = buildPopup;
  }

  setData(airports: MapAirport[]): void {
    this.airports = airports;
    this.render();
  }

  private render(): void {
    this.markersLayer.clearLayers();
    const scale = this.scale;
    for (const a of this.airports) {
      const color = a.value !== null && scale ? pctColor(a.value, scale) : COLORS.grey;
      const marker = L.circleMarker([a.lat, a.lon], {
        radius: 5,
        fillColor: color,
        color: '#111',
        weight: 0.6,
        opacity: 1,
        fillOpacity: a.value === null ? 0.4 : 0.9,
      });
      marker.bindPopup(this.buildPopup(a));
      marker.addTo(this.markersLayer);
    }
  }

  invalidateSize(): void {
    this.map.invalidateSize();
  }

  destroy(): void {
    this.themeObserver.disconnect();
    this.map.remove();
  }
}
