/** Synoptic map — Leaflet map + HewsonGridLayer + colorbar legend.
 *
 * Sibling of WeatherMap (visualization/weather-map.ts) but slim: no airport
 * markers, no per-metric color matrix — just the gridded canvas overlay.
 * Lives in the "Synoptic Forecast" tab on the forecast page.
 */

import * as L from 'leaflet';
import type { HewsonSlice } from '../adapters/hewson-map-adapter';
import { COLORMAPS, gradientCss, type HewsonMetric } from './hewson-colormaps';
import { HewsonGridLayer } from './hewson-grid-layer';

const LIGHT_TILES = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png';
const DARK_TILES = 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png';
const ATTR = '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>';

function isDark(): boolean {
  return document.documentElement.dataset.theme === 'dark';
}

export class SynopticMap {
  private container: HTMLElement;
  private map: L.Map | null = null;
  private tileLayer: L.TileLayer | null = null;
  private gridLayer: HewsonGridLayer | null = null;
  private legendEl: HTMLElement | null = null;

  constructor(container: HTMLElement) {
    this.container = container;
  }

  init(): void {
    if (this.map) return;

    this.map = L.map(this.container, {
      center: [48, 10],
      zoom: 5,
      zoomControl: true,
    });

    this.tileLayer = L.tileLayer(isDark() ? DARK_TILES : LIGHT_TILES, {
      attribution: ATTR,
      maxZoom: 18,
    }).addTo(this.map);

    this.gridLayer = new HewsonGridLayer();
    this.gridLayer.addTo(this.map);

    document.addEventListener('theme-changed', () => {
      if (!this.tileLayer) return;
      this.tileLayer.setUrl(isDark() ? DARK_TILES : LIGHT_TILES);
    });
  }

  invalidateSize(): void {
    this.map?.invalidateSize();
  }

  /** Replace the rendered slice. Builds/refreshes the legend. */
  setSlice(slice: HewsonSlice, vmin?: number, vmax?: number): void {
    if (!this.gridLayer) return;
    this.gridLayer.setSlice(slice, vmin, vmax);
    this.renderLegend(slice.metric as HewsonMetric, vmin, vmax);
  }

  setOpacity(o: number): void {
    this.gridLayer?.setOpacity(o);
  }

  setVRange(vmin: number, vmax: number): void {
    this.gridLayer?.setVRange(vmin, vmax);
  }

  clear(): void {
    this.gridLayer?.clear();
    this.removeLegend();
  }

  private renderLegend(
    metric: HewsonMetric,
    vmin?: number,
    vmax?: number,
  ): void {
    const spec = COLORMAPS[metric];
    if (!spec) return;
    const lo = vmin ?? spec.defaultVmin;
    const hi = vmax ?? spec.defaultVmax;
    const mid = (lo + hi) / 2;

    if (!this.legendEl) {
      this.legendEl = document.createElement('div');
      this.legendEl.className = 'synoptic-legend';
      this.container.appendChild(this.legendEl);
    }

    const fmt = (v: number) =>
      Math.abs(v) >= 100 ? v.toFixed(0) : v.toFixed(1);

    this.legendEl.innerHTML = `
      <div class="synoptic-legend-title">${escapeHtml(spec.label)}</div>
      <div class="synoptic-legend-bar" style="background:${gradientCss(metric)}"></div>
      <div class="synoptic-legend-ticks">
        <span>${fmt(lo)}</span>
        <span>${fmt(mid)}</span>
        <span>${fmt(hi)} ${escapeHtml(spec.unit)}</span>
      </div>
    `;
  }

  private removeLegend(): void {
    if (this.legendEl?.parentNode) {
      this.legendEl.parentNode.removeChild(this.legendEl);
    }
    this.legendEl = null;
  }
}

function escapeHtml(s: string): string {
  return s.replace(/[&<>"']/g, (c) =>
    ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]!),
  );
}
