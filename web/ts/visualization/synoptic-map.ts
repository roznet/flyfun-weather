/** Synoptic map — Leaflet map + HewsonGridLayer + colorbar legend.
 *
 * Sibling of WeatherMap (visualization/weather-map.ts) but slim: no airport
 * markers, no per-metric color matrix — just the gridded canvas overlay.
 * Lives in the "Synoptic Forecast" tab on the forecast page.
 *
 * Two basemap modes:
 *   - 'osm'   — OSM/CARTO tiles (default), Web Mercator.
 *   - 'chart' — a DWD / Met Office surface chart as the basemap, via
 *               `L.CRS.Simple` + an image overlay, with the Hewson grid and
 *               front polylines re-projected into the chart's polar-stereo
 *               pixel space so they line up with the chart's isobars/fronts.
 *
 * CRS.Simple note: with image-overlay bounds `[[0,0],[h,w]]`, a chart pixel
 * `(x, y)` aligns with the image when fed to Leaflet as `L.latLng(h - y, x)`
 * (the Y axis is flipped relative to image pixels). All chart-mode coordinate
 * conversions go through `toLatLng` / the inverse in `handleMouseMove`.
 */

import * as L from 'leaflet';
import type { HewsonAllMetricsSlice, HewsonFront, HewsonSlice } from '../adapters/hewson-map-adapter';
import { COLORMAPS, gradientCss, type HewsonMetric } from './hewson-colormaps';
import { HewsonGridLayer } from './hewson-grid-layer';
import type { ChartProjection } from './chart-projection';

// Gate-detected front-axis colours (match the CLI DWD overlay: blue cold,
// red warm, purple quasi-stationary).
const FRONT_COLORS: Record<string, string> = {
  cold: '#0a5adc',
  warm: '#dc0000',
  'quasi-stationary': '#960096',
};

const HEWSON_METRIC_ORDER: HewsonMetric[] = [
  'theta_e', 'gradient', 'neg_laplacian', 'tfp', 'advection', 'tendency',
];

const LIGHT_TILES = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png';
const DARK_TILES = 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png';
// CARTO's terms require their attribution alongside OSM when serving from
// basemaps.cartocdn.com. Mirror the per-theme split used in
// route-map-inset.ts / route-map/renderer.ts.
const LIGHT_ATTR = '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>';
const DARK_ATTR = '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/">CARTO</a>';

function isDark(): boolean {
  return document.documentElement.dataset.theme === 'dark';
}

export type BasemapMode = 'osm' | 'chart';

/** Everything SynopticMap needs to render a chart basemap. */
export interface ChartBasemapSpec {
  /** Chart image URL (PNG / GIF). */
  url: string;
  /** Forward/inverse projection + native pixel size for this chart type. */
  projection: ChartProjection;
  /** Attribution HTML for the chart source. */
  attributionHtml: string;
}

interface GeoBounds {
  minLat: number;
  maxLat: number;
  minLon: number;
  maxLon: number;
}

export class SynopticMap {
  private container: HTMLElement;
  private map: L.Map | null = null;
  private tileLayer: L.TileLayer | null = null;
  private imageOverlay: L.ImageOverlay | null = null;
  private gridLayer: HewsonGridLayer | null = null;
  private frontsLayer: L.LayerGroup | null = null;
  private legendEl: HTMLElement | null = null;

  private mode: BasemapMode = 'osm';
  private chartSpec: ChartBasemapSpec | null = null;
  private chartAttr: string | null = null;

  // Re-applied across basemap rebuilds.
  private lastSlice: { slice: HewsonSlice; vmin?: number; vmax?: number } | null = null;
  private lastFronts: HewsonFront[] = [];
  private opacity = 0.5;

  // Hover state — populated via setHoverGrid(); cleared when no grid is loaded.
  private hoverGrid: HewsonAllMetricsSlice | null = null;
  private hoverEl: HTMLElement | null = null;
  private themeWired = false;

  constructor(container: HTMLElement) {
    this.container = container;
  }

  init(): void {
    if (this.map) return;
    if (!this.hoverEl) {
      this.hoverEl = document.createElement('div');
      this.hoverEl.className = 'synoptic-hover-tip';
      this.hoverEl.style.display = 'none';
      this.container.appendChild(this.hoverEl);
    }
    this.wireThemeOnce();
    this.buildMap(null);
  }

  invalidateSize(): void {
    this.map?.invalidateSize();
  }

  // -------------------------------------------------------------------------
  // Basemap mode
  // -------------------------------------------------------------------------

  /** Switch basemap. Preserves the geographic area in view across the swap
   * (Leaflet can't change CRS after init, so the map is rebuilt). For 'chart'
   * mode a spec is required; switching the chart *image* within the same
   * source should use {@link updateChartImage} (cheaper, no rebuild). */
  setBasemap(mode: BasemapMode, spec?: ChartBasemapSpec): void {
    if (mode === 'chart' && !spec) return;
    const geo = this.getGeoBounds();
    this.mode = mode;
    this.chartSpec = mode === 'chart' ? spec! : null;
    this.rebuild(geo);
  }

  /** Swap the chart image without rebuilding the map (e.g. on hour change
   * within the same source). No-op outside chart mode. */
  updateChartImage(url: string): void {
    if (this.mode !== 'chart' || !this.chartSpec || !this.imageOverlay) return;
    this.chartSpec = { ...this.chartSpec, url };
    this.imageOverlay.setUrl(url);
  }

  /** Tear down and rebuild the Leaflet map for the current mode, re-applying
   * the slice / fronts / opacity and fitting to `geo` (if given). */
  private rebuild(geo: GeoBounds | null): void {
    if (this.map) {
      this.map.remove();
      this.map = null;
      this.tileLayer = null;
      this.imageOverlay = null;
      this.gridLayer = null;
      this.frontsLayer = null;
    }
    this.removeLegend();
    this.buildMap(geo);

    // Re-apply rendered state onto the fresh layers.
    if (this.lastSlice) {
      this.gridLayer?.setSlice(this.lastSlice.slice, this.lastSlice.vmin, this.lastSlice.vmax);
      this.renderLegend(this.lastSlice.slice.metric as HewsonMetric, this.lastSlice.vmin, this.lastSlice.vmax);
    }
    this.gridLayer?.setOpacity(this.opacity);
    if (this.lastFronts.length) this.drawFronts(this.lastFronts);
  }

  private buildMap(geo: GeoBounds | null): void {
    if (this.mode === 'chart' && this.chartSpec) {
      this.buildChartMap(this.chartSpec, geo);
    } else {
      this.buildOsmMap(geo);
    }

    this.gridLayer = new HewsonGridLayer();
    if (this.mode === 'chart' && this.chartSpec) {
      const h = this.chartSpec.projection.nativeSize[1];
      const proj = this.chartSpec.projection;
      // Grid layer feeds L.latLng(p.y, p.x); flip Y here so it lands on the
      // image (see CRS.Simple note at the top of the file).
      this.gridLayer.setProjector((lat, lon) => {
        const p = proj.forward(lat, lon);
        return { x: p.x, y: h - p.y };
      });
    }
    this.gridLayer.addTo(this.map!);

    this.frontsLayer = L.layerGroup().addTo(this.map!);

    this.map!.on('mousemove', this.handleMouseMove);
    this.map!.on('mouseout', this.hideHover);
    // Mobile: tap to show the values at that point.
    this.map!.on('click', this.handleMouseMove);
  }

  private buildOsmMap(geo: GeoBounds | null): void {
    this.map = L.map(this.container, { center: [48, 10], zoom: 5, zoomControl: true });
    const dark = isDark();
    this.tileLayer = L.tileLayer(dark ? DARK_TILES : LIGHT_TILES, {
      attribution: dark ? DARK_ATTR : LIGHT_ATTR,
      maxZoom: 18,
    }).addTo(this.map);
    if (geo) this.fitGeoBounds(geo);
  }

  private buildChartMap(spec: ChartBasemapSpec, geo: GeoBounds | null): void {
    const [w, h] = spec.projection.nativeSize;
    this.map = L.map(this.container, {
      crs: L.CRS.Simple,
      minZoom: -5,
      maxZoom: 4,
      zoomControl: true,
    });
    const bounds = L.latLngBounds([[0, 0], [h, w]]);
    // The chart image must sit *below* the Hewson grid pane (z 350) and the
    // front polylines (overlayPane, z 400). Leaflet's default overlayPane (400)
    // would paint the chart over the grid, so give it a dedicated low pane.
    if (!this.map.getPane('chartBasemapPane')) {
      const p = this.map.createPane('chartBasemapPane');
      p.style.zIndex = '250';
    }
    this.imageOverlay = L.imageOverlay(spec.url, bounds, { pane: 'chartBasemapPane' }).addTo(this.map);
    this.map.setMaxBounds(bounds.pad(0.5));

    this.chartAttr = spec.attributionHtml;
    this.map.attributionControl.addAttribution(this.chartAttr);

    if (geo) this.fitGeoBounds(geo);
    else this.map.fitBounds(bounds);
  }

  // -------------------------------------------------------------------------
  // Coordinate helpers
  // -------------------------------------------------------------------------

  /** Chart pixel (x, y) -> Leaflet LatLng under CRS.Simple (Y flipped so it
   * lands on the image overlay placed at bounds [[0,0],[h,w]]). */
  private chartLatLng(x: number, y: number): L.LatLng {
    const h = this.chartSpec!.projection.nativeSize[1];
    return L.latLng(h - y, x);
  }

  /** WGS84 (lat, lon) -> a Leaflet LatLng for the active CRS. */
  private toLatLng(lat: number, lon: number): L.LatLng {
    if (this.mode === 'chart' && this.chartSpec) {
      const p = this.chartSpec.projection.forward(lat, lon);
      return this.chartLatLng(p.x, p.y);
    }
    return L.latLng(lat, lon);
  }

  /** A Leaflet LatLng (active CRS) -> WGS84 {lat, lon}, or null if it doesn't
   * map to a valid geographic point (chart edges). */
  private fromLatLng(ll: L.LatLng): { lat: number; lon: number } | null {
    if (this.mode === 'chart' && this.chartSpec) {
      const h = this.chartSpec.projection.nativeSize[1];
      const out = this.chartSpec.projection.inverse(ll.lng, h - ll.lat);
      if (!Number.isFinite(out.lat) || !Number.isFinite(out.lon)) return null;
      return out;
    }
    return { lat: ll.lat, lon: ll.lng };
  }

  private getGeoBounds(): GeoBounds | null {
    if (!this.map) return null;
    const b = this.map.getBounds();
    const corners = [b.getNorthWest(), b.getNorthEast(), b.getSouthEast(), b.getSouthWest()];

    let geos: { lat: number; lon: number }[];
    if (this.mode === 'chart' && this.chartSpec) {
      // Corners are chart-pixel space (lat=h-y, lng=x). Clamp to the chart
      // extent before inverse-projecting — a view panned/zoomed past the chart
      // edge would otherwise inverse-project to nonsense (e.g. lat near 0).
      const [w, h] = this.chartSpec.projection.nativeSize;
      const proj = this.chartSpec.projection;
      geos = corners
        .map((c) => {
          const x = Math.max(0, Math.min(w, c.lng));
          const y = Math.max(0, Math.min(h, h - c.lat));
          return proj.inverse(x, y);
        })
        .filter((g) => Number.isFinite(g.lat) && Number.isFinite(g.lon));
    } else {
      geos = corners.map((c) => ({ lat: c.lat, lon: c.lng }));
    }
    if (!geos.length) return null;
    return {
      minLat: Math.min(...geos.map((g) => g.lat)),
      maxLat: Math.max(...geos.map((g) => g.lat)),
      minLon: Math.min(...geos.map((g) => g.lon)),
      maxLon: Math.max(...geos.map((g) => g.lon)),
    };
  }

  private fitGeoBounds(geo: GeoBounds): void {
    if (!this.map) return;
    const cornersGeo: [number, number][] = [
      [geo.minLat, geo.minLon],
      [geo.minLat, geo.maxLon],
      [geo.maxLat, geo.minLon],
      [geo.maxLat, geo.maxLon],
    ];

    if (this.mode === 'chart' && this.chartSpec) {
      // Project the geo bbox to chart pixels and clamp to the chart extent, so
      // a wide OSM view (Atlantic/Africa) doesn't shrink the chart to a dot —
      // we never zoom out past the chart itself.
      const [w, h] = this.chartSpec.projection.nativeSize;
      const proj = this.chartSpec.projection;
      let minX = Infinity, minY = Infinity, maxX = -Infinity, maxY = -Infinity;
      for (const [la, lo] of cornersGeo) {
        const p = proj.forward(la, lo);
        minX = Math.min(minX, p.x); maxX = Math.max(maxX, p.x);
        minY = Math.min(minY, p.y); maxY = Math.max(maxY, p.y);
      }
      minX = Math.max(0, Math.min(w, minX)); maxX = Math.max(0, Math.min(w, maxX));
      minY = Math.max(0, Math.min(h, minY)); maxY = Math.max(0, Math.min(h, maxY));
      if (maxX - minX < 1 || maxY - minY < 1) {
        // Geo bbox doesn't overlap the chart — show the whole chart.
        this.map.fitBounds(L.latLngBounds([[0, 0], [h, w]]), { animate: false });
        return;
      }
      const bb = L.latLngBounds([this.chartLatLng(minX, minY), this.chartLatLng(maxX, maxY)]);
      this.map.fitBounds(bb, { animate: false });
      return;
    }

    this.map.fitBounds(
      L.latLngBounds([[geo.minLat, geo.minLon], [geo.maxLat, geo.maxLon]]),
      { animate: false },
    );
  }

  // -------------------------------------------------------------------------
  // Slice / legend / fronts
  // -------------------------------------------------------------------------

  /** Replace the rendered slice. Builds/refreshes the legend. */
  setSlice(slice: HewsonSlice, vmin?: number, vmax?: number): void {
    this.lastSlice = { slice, vmin, vmax };
    if (!this.gridLayer) return;
    this.gridLayer.setSlice(slice, vmin, vmax);
    this.renderLegend(slice.metric as HewsonMetric, vmin, vmax);
  }

  setOpacity(o: number): void {
    this.opacity = Math.max(0, Math.min(1, o));
    this.gridLayer?.setOpacity(this.opacity);
  }

  /** Update the (vmin, vmax) of the current slice without re-fetching. */
  setVRange(metric: HewsonMetric, vmin: number, vmax: number): void {
    if (this.lastSlice) this.lastSlice = { ...this.lastSlice, vmin, vmax };
    this.gridLayer?.setVRange(vmin, vmax);
    this.renderLegend(metric, vmin, vmax);
  }

  clear(): void {
    this.lastSlice = null;
    this.lastFronts = [];
    this.gridLayer?.clear();
    this.clearFronts();
    this.removeLegend();
    this.hoverGrid = null;
    this.hideHover();
  }

  /** Draw gate-detected front polylines (replacing any already shown). */
  setFronts(fronts: HewsonFront[]): void {
    this.lastFronts = fronts;
    this.drawFronts(fronts);
  }

  private drawFronts(fronts: HewsonFront[]): void {
    if (!this.frontsLayer) return;
    this.frontsLayer.clearLayers();
    for (const f of fronts) {
      // API gives GeoJSON [lon, lat]; project into the active CRS.
      const latlngs = f.coordinates.map(([lon, lat]) => this.toLatLng(lat, lon));
      if (latlngs.length < 2) continue;
      const color = FRONT_COLORS[f.kind] ?? FRONT_COLORS['quasi-stationary'];
      const line = L.polyline(latlngs, {
        color, weight: 4, opacity: 0.9, lineJoin: 'round',
      });
      line.bindTooltip(
        `${f.kind} front · ${Math.round(f.length_km)} km · ` +
        `|∇θe| ${f.mean_gradient.toFixed(1)} · Δθe ${f.mean_delta_theta_e.toFixed(1)} K`,
        { sticky: true },
      );
      line.addTo(this.frontsLayer);
    }
  }

  clearFronts(): void {
    this.lastFronts = [];
    this.frontsLayer?.clearLayers();
  }

  /** Cache the all-metrics grid for cursor-tooltip lookups. */
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

    // Recover WGS84 lat/lon — in chart mode e.latlng is chart-pixel space.
    const geo = this.fromLatLng(e.latlng);
    if (!geo) {
      this.hideHover();
      return;
    }

    // Snap to the nearest grid cell. lat/lon arrays are uniform (0.25°
    // by default) so a direct linear index calculation is cheap and exact.
    const lat = grid.lat;
    const lon = grid.lon;
    const i = Math.round((geo.lat - lat[0]) / (lat[lat.length - 1] - lat[0]) * (lat.length - 1));
    const j = Math.round((geo.lon - lon[0]) / (lon[lon.length - 1] - lon[0]) * (lon.length - 1));
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
        // Defensive: a malformed response with fewer rows than lat.length
        // would otherwise throw a TypeError inside a Leaflet mouse handler.
        const row = grid.metrics[m]?.[i];
        const v = row !== undefined ? row[j] : null;
        const spec = COLORMAPS[m];
        const label = spec?.label.split(' ')[0] ?? m;  // "θe", "|∇θe|", ...
        const unit = spec?.unit ?? '';
        const text = (v === null || v === undefined || !Number.isFinite(v))
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

    // Position relative to the container.
    const cp = e.containerPoint;
    const tipW = this.hoverEl.offsetWidth;
    const tipH = this.hoverEl.offsetHeight;
    const containerRect = this.container.getBoundingClientRect();
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

  private wireThemeOnce(): void {
    if (this.themeWired) return;
    this.themeWired = true;
    document.addEventListener('theme-changed', () => {
      // Charts have no dark variant — only OSM tiles swap on theme change.
      if (this.mode !== 'osm' || !this.tileLayer || !this.map) return;
      const dark = isDark();
      this.tileLayer.setUrl(dark ? DARK_TILES : LIGHT_TILES);
      this.tileLayer.getAttribution = () => dark ? DARK_ATTR : LIGHT_ATTR;
      this.map.attributionControl.removeAttribution(LIGHT_ATTR);
      this.map.attributionControl.removeAttribution(DARK_ATTR);
      this.map.attributionControl.addAttribution(dark ? DARK_ATTR : LIGHT_ATTR);
    });
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
