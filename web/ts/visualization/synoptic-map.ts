/** Synoptic map — Leaflet map + HewsonGridLayer + colorbar legend.
 *
 * Sibling of WeatherMap (visualization/weather-map.ts) but slim: no airport
 * markers, no per-metric color matrix — just the gridded canvas overlay.
 * Lives in the "Synoptic Forecast" tab on the forecast page.
 */

import * as L from 'leaflet';
import type { HewsonAllMetricsSlice, HewsonSlice } from '../adapters/hewson-map-adapter';
import { COLORMAPS, gradientCss, type HewsonMetric } from './hewson-colormaps';
import { HewsonGridLayer } from './hewson-grid-layer';

const HEWSON_METRIC_ORDER: HewsonMetric[] = [
  'theta_e', 'gradient', 'neg_laplacian', 'tfp', 'advection', 'tendency',
];

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
  // Hover state — populated via setHoverGrid(); cleared when no grid is loaded.
  private hoverGrid: HewsonAllMetricsSlice | null = null;
  private hoverEl: HTMLElement | null = null;

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

    // Cursor-following tooltip — wired once at init, hidden until hoverGrid
    // is populated by setHoverGrid().
    this.hoverEl = document.createElement('div');
    this.hoverEl.className = 'synoptic-hover-tip';
    this.hoverEl.style.display = 'none';
    this.container.appendChild(this.hoverEl);

    this.map.on('mousemove', this.handleMouseMove);
    this.map.on('mouseout', this.hideHover);
    // Mobile: tap to show the values at that point. The next mousemove
    // (e.g. on map drag) re-positions; the next mouseout hides.
    this.map.on('click', this.handleMouseMove);

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

  /** Update the (vmin, vmax) of the current slice without re-fetching.
   * Refreshes both the canvas and the legend. ``metric`` is needed for the
   * legend title / unit; pass the metric currently rendered. */
  setVRange(metric: HewsonMetric, vmin: number, vmax: number): void {
    this.gridLayer?.setVRange(vmin, vmax);
    this.renderLegend(metric, vmin, vmax);
  }

  clear(): void {
    this.gridLayer?.clear();
    this.removeLegend();
    this.hoverGrid = null;
    this.hideHover();
  }

  /** Cache the all-metrics grid for cursor-tooltip lookups. Pass null to
   * disable hover (e.g. between hour changes while a refetch is in flight). */
  setHoverGrid(grid: HewsonAllMetricsSlice | null): void {
    this.hoverGrid = grid;
    if (!grid) this.hideHover();
  }

  // -------------------------------------------------------------------------
  // Cursor tooltip
  // -------------------------------------------------------------------------

  private handleMouseMove = (e: L.LeafletMouseEvent): void => {
    if (!this.hoverGrid || !this.hoverEl) return;
    const grid = this.hoverGrid;

    // Snap to the nearest grid cell. lat/lon arrays are uniform (0.25°
    // by default) so a direct linear lookup is cheap and exact.
    const lat = grid.lat;
    const lon = grid.lon;
    const dLat = lat.length >= 2 ? Math.abs(lat[1] - lat[0]) : 0.25;
    const dLon = lon.length >= 2 ? Math.abs(lon[1] - lon[0]) : 0.25;

    const i = Math.round((e.latlng.lat - lat[0]) / (lat[lat.length - 1] - lat[0]) * (lat.length - 1));
    const j = Math.round((e.latlng.lng - lon[0]) / (lon[lon.length - 1] - lon[0]) * (lon.length - 1));
    if (i < 0 || i >= lat.length || j < 0 || j >= lon.length) {
      this.hideHover();
      return;
    }

    // Check at least one metric has a finite value here — otherwise we're
    // in a terrain-mask hole or off-data, so don't show a tooltip.
    let anyFinite = false;
    for (const m of HEWSON_METRIC_ORDER) {
      const grid2d = grid.metrics[m];
      const v = grid2d?.[i]?.[j];
      if (v !== null && v !== undefined && Number.isFinite(v)) {
        anyFinite = true;
        break;
      }
    }
    if (!anyFinite) {
      this.hideHover();
      return;
    }

    // Build the tooltip rows.
    const cellLat = lat[i];
    const cellLon = lon[j];
    const rows = HEWSON_METRIC_ORDER
      .filter((m) => grid.metrics[m] !== undefined)
      .map((m) => {
        const v = grid.metrics[m][i][j];
        const spec = COLORMAPS[m];
        const label = spec?.label.split(' ')[0] ?? m;  // "θe", "|∇θe|", ...
        const unit = spec?.unit ?? '';
        const text = (v === null || !Number.isFinite(v))
          ? '—'
          : `${formatValue(m, v as number)} ${unit}`;
        return `<div class="synoptic-hover-row"><span class="synoptic-hover-label">${escapeHtml(label)}</span><span class="synoptic-hover-value">${escapeHtml(text)}</span></div>`;
      })
      .join('');

    const dms = (v: number, pos: string, neg: string) =>
      `${Math.abs(v).toFixed(2)}°${v >= 0 ? pos : neg}`;
    const header = `${dms(cellLat, 'N', 'S')}, ${dms(cellLon, 'E', 'W')}<br><span class="synoptic-hover-cell">cell @ ${cellLat.toFixed(2)}, ${cellLon.toFixed(2)}</span>`;

    this.hoverEl.innerHTML = `<div class="synoptic-hover-header">${header}</div>${rows}`;
    this.hoverEl.style.display = 'block';

    // Position relative to the container — Leaflet's containerPoint is the
    // cursor position in the map container's local coordinate system.
    const cp = e.containerPoint;
    const tipW = this.hoverEl.offsetWidth;
    const tipH = this.hoverEl.offsetHeight;
    const containerRect = this.container.getBoundingClientRect();
    // Default offset: 12 px to the right and below the cursor. Flip when
    // the cursor approaches the right or bottom edge so the tooltip stays
    // fully visible.
    let x = cp.x + 12;
    let y = cp.y + 12;
    if (x + tipW > containerRect.width - 6) x = cp.x - 12 - tipW;
    if (y + tipH > containerRect.height - 6) y = cp.y - 12 - tipH;
    this.hoverEl.style.left = `${x}px`;
    this.hoverEl.style.top = `${y}px`;
  };

  private hideHover = (): void => {
    if (this.hoverEl) this.hoverEl.style.display = 'none';
  };

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

/** Round to a metric-appropriate number of decimals for the tooltip. */
function formatValue(metric: HewsonMetric, v: number): string {
  // θe magnitudes ~ 280–340 K; show 1 decimal.
  // Everything else is small (K/h, K/100km, K/(100km)²) — 2 decimals.
  const decimals = metric === 'theta_e' ? 1 : 2;
  return v.toFixed(decimals);
}
