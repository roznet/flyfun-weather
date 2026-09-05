/** Route map renderer — Leaflet-based geographic visualization of weather along the route. */

import * as L from 'leaflet';
import {
  renderObservedOverlay,
  renderObservedFlashes,
  formatLegendValue,
  type ObservedFlashPoint,
  type ObservedSourceStatus,
  corridorBounds,
  overlayUrl,
} from './observed-overlay';
import { fetchObservedImage, formatBadge, type ObservedBadgeField } from './observed-overlay-geometry';
import { ObservedImageRequests } from './observed-request-state';
import { observeDisplayClock } from '../observed-time';
import { escapeHtml } from '../../utils';
import type { VizRouteData } from '../types';
import type { MapMetric } from './metrics';
import { computeSegmentStyles } from './segment-style';
import { isDarkTheme } from '../interaction-utils';
import { frontColor, frontKindLabel, frontTooltip, frontOfftrackTooltip, FRONT_INTENSITY_WEIGHT } from '../front-style';
import type { HewsonFront } from '../../adapters/hewson-map-adapter';
import type { FrontKind } from '../../types/fronts';
import type { ForecastMapResponse } from '../../adapters/maps-adapter';
import { getForecastColor, type ForecastMetric } from '../weather-map-format';
import { getForecastTooltip, forecastLegend } from '../weather-map';

/** A front-axis polyline tagged with the pressure level it was extracted at, so
 *  the map can draw a model's boundaries across all stored levels (a low warm
 *  front at 850/925 plus a mid cold front at 700) and style them by altitude. */
export interface MapFrontLine extends HewsonFront {
  level_hPa: number;
}

// Drop axes whose every vertex is farther than this from the route — keeps the
// overlay to boundaries near the track, not the whole European domain.
const FRONT_CORRIDOR_KM = 120;
// Fainter with altitude so a 925 hPa warm front reads as lower than a 700 line.
const FRONT_LEVEL_OPACITY: Record<number, number> = { 700: 0.9, 850: 0.72, 925: 0.6 };

function haversineKm(aLat: number, aLon: number, bLat: number, bLon: number): number {
  const toRad = (d: number): number => (d * Math.PI) / 180;
  const dLat = toRad(bLat - aLat);
  const dLon = toRad(bLon - aLon);
  const s = Math.sin(dLat / 2) ** 2
    + Math.cos(toRad(aLat)) * Math.cos(toRad(bLat)) * Math.sin(dLon / 2) ** 2;
  return 2 * 6371 * Math.asin(Math.min(1, Math.sqrt(s)));
}

const LIGHT_TILES = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png';
const DARK_TILES = 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png';
const LIGHT_ATTR = '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>';
const DARK_ATTR = '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/">CARTO</a>';

export class RouteMapRenderer {
  private container: HTMLElement;
  private map: L.Map | null = null;
  private tileLayer: L.TileLayer | null = null;
  private segmentGroup: L.LayerGroup | null = null;
  private waypointGroup: L.LayerGroup | null = null;
  private frontsGroup: L.LayerGroup | null = null;
  private airportForecastGroup: L.LayerGroup | null = null;
  // Observed conditions (#574): the newest frame, the corridor it describes,
  // and age-faded lightning. Nothing here animates and there is no time
  // slider — deliberately out of scope, not a first cut.
  private observedGroup: L.LayerGroup | null = null;
  private observedFlashGroup: L.LayerGroup | null = null;
  private observedLabelsEl: HTMLElement | null = null;
  private observedBadgeEl: HTMLElement | null = null;
  private observedSource: string | null = null;
  private observedOpacity = 0.75;
  private observedLegendEl: HTMLElement | null = null;
  private observedLegends: Map<string, ObservedSourceStatus> | null = null;
  private observedFlashes: ObservedFlashPoint[] = [];
  private observedFlashField: ObservedBadgeField | null = null;
  private readonly observedImages = new ObservedImageRequests(
    fetchObservedImage,
    (blob) => URL.createObjectURL(blob),
    (url) => URL.revokeObjectURL(url),
  );
  private stopObservedClock: () => void;
  private highlightMarker: L.CircleMarker | null = null;
  private forecastLegendEl: HTMLElement | null = null;
  private forecastZoomHandler: (() => void) | null = null;

  private data: VizRouteData | null = null;
  private colorMetric: MapMetric | null = null;
  private widthMetric: MapMetric | null = null;
  private altitudeFt = 0;
  private showFronts = false;
  private frontLines: MapFrontLine[] | null = null;
  // Airport forecast overlay (#424): per-airport markers for the snapshot time
  // nearest the flight, coloured by the same served catalog as the full
  // forecast map. Data holds every model; switching model/metric is a recolour.
  private showForecastOverlay = false;
  private forecastData: ForecastMapResponse | null = null;
  private forecastMetric: ForecastMetric = 'flight_category';
  private forecastModel = 'ecmwf';
  // Bumped only when the data reference actually changes, so the render
  // signature below stays stable across the many re-renders that don't touch
  // the overlay (e.g. altitude-slider drags) and the ~620-marker rebuild is
  // skipped. `null` sig forces the first draw.
  private forecastDataVersion = 0;
  private lastForecastSig: string | null = null;
  private selectedPointIndex = -1;
  private initialized = false;
  private currentTileTheme: 'light' | 'dark' = 'light';

  constructor(container: HTMLElement) {
    this.container = container;
    this.stopObservedClock = observeDisplayClock(() => {
      // Clock-sensitive points and labels only; never touch the raster group.
      this.renderObservedFlashClock();
      if (this.observedBadgeEl) this.updateObservedBadge(this.currentObservedBadge());
    });
  }

  setData(data: VizRouteData): void {
    this.data = data;
    this.altitudeFt = data.cruiseAltitudeFt;
  }

  setColorMetric(metric: MapMetric | null): void {
    this.colorMetric = metric;
  }

  setWidthMetric(metric: MapMetric | null): void {
    this.widthMetric = metric;
  }

  setAltitude(altitudeFt: number): void {
    this.altitudeFt = altitudeFt;
  }

  /** Toggle the experimental Hewson front overlay (#196). */
  setShowFronts(show: boolean): void {
    this.showFronts = show;
  }

  /** Gated 2-D front axes for the selected model across all stored levels
   *  (`GET /api/hewson-map/fronts`, same FrontGateConfig as the advisory).
   *  Fetched async by briefing-main and handed in; `null` clears them. Call
   *  `refreshFronts()` after to redraw. */
  setFrontLines(lines: MapFrontLine[] | null): void {
    this.frontLines = lines;
  }

  /** Redraw just the fronts layer (after `setFrontLines`), without a full render. */
  refreshFronts(): void {
    this.renderFronts();
  }

  /** Which observed source to draw as imagery (#574), or `null` for none.
   *  Lightning is drawn as points regardless — it is not a raster. */
  setObservedSource(source: string | null): void {
    this.observedSource = source;
  }

  /** Opacity of the observed imagery, 0–1. */
  setObservedOpacity(opacity: number): void {
    this.observedOpacity = Math.max(0, Math.min(1, opacity));
  }

  /** Per-source colour ramps for the on-map scale, from the server. */
  setObservedLegends(legends: Map<string, ObservedSourceStatus>): void {
    this.observedLegends = legends;
  }

  /** Lightning points for the corridor, fetched async by briefing-main.
   *  Each carries its own time so the overlay can fade the trail by age. */
  setObservedFlashes(flashes: ObservedFlashPoint[], field: ObservedBadgeField | null = null): void {
    this.observedFlashes = flashes;
    this.observedFlashField = field;
  }

  /** Redraw just the observed overlay, after new flashes or a source change. */
  refreshObserved(): void {
    this.renderObserved();
  }

  /** Allow one raster retry after an explicit briefing refresh. */
  retryObservedFetch(): void {
    this.observedImages.retryFailed();
  }

  /** Toggle the airport forecast overlay (#424). */
  setShowForecastOverlay(show: boolean): void {
    this.showForecastOverlay = show;
  }

  /** The forecast-map snapshot for the flight time (all models per airport),
   *  fetched async by briefing-main. `null` clears it. Call
   *  `refreshForecastOverlay()` after to redraw. */
  setForecastData(data: ForecastMapResponse | null): void {
    // Bump the version only on a real change so repeated cache-hit calls with
    // the same object reference don't force a redraw (see renderForecastOverlay).
    if (this.forecastData !== data) {
      this.forecastData = data;
      this.forecastDataVersion++;
    }
  }

  /** Which forecast metric colours the overlay markers. Recolour is a redraw. */
  setForecastMetric(metric: ForecastMetric): void {
    this.forecastMetric = metric;
  }

  /** Individual model (gfs/icon/ecmwf) the overlay reads — mirrors the
   *  briefing's model selector. Consensus modes are not offered here (they
   *  live on the full forecast map). */
  setForecastModel(model: string): void {
    this.forecastModel = model;
  }

  /** Redraw just the airport overlay layer + its legend, without a full render. */
  refreshForecastOverlay(): void {
    this.renderForecastOverlay();
  }

  setSelectedPointIndex(index: number): void {
    this.selectedPointIndex = index;
    this.updateHighlight();
  }

  render(): void {
    if (!this.data || this.data.points.length < 2) return;
    if (this.container.clientWidth === 0 || this.container.clientHeight === 0) return;

    this.ensureMap();
    this.renderObserved();
    this.renderForecastOverlay();
    this.renderSegments();
    this.renderFronts();
    this.renderWaypoints();
    this.updateHighlight();
  }

  /** Call after layout transitions to fix Leaflet sizing. */
  invalidateSize(): void {
    if (this.map) {
      this.map.invalidateSize();
    }
  }

  /** Highlight a segment by point index (for hover sync from other panels). */
  highlightSegment(index: number): void {
    if (!this.map || !this.data) return;

    if (this.highlightMarker) {
      this.highlightMarker.remove();
      this.highlightMarker = null;
    }

    if (index < 0 || index >= this.data.points.length) return;

    const pt = this.data.points[index];
    this.highlightMarker = L.circleMarker([pt.lat, pt.lon], {
      radius: 8,
      color: '#2563eb',
      fillColor: '#2563eb',
      fillOpacity: 0.4,
      weight: 2,
    }).addTo(this.map);
  }

  /** Get the point index nearest to a lat/lon. */
  getNearestPointIndex(lat: number, lon: number): number {
    if (!this.data) return 0;
    let bestIdx = 0;
    let bestDist = Infinity;
    for (let i = 0; i < this.data.points.length; i++) {
      const p = this.data.points[i];
      const d = (p.lat - lat) ** 2 + (p.lon - lon) ** 2;
      if (d < bestDist) {
        bestDist = d;
        bestIdx = i;
      }
    }
    return bestIdx;
  }

  destroy(): void {
    this.stopObservedClock();
    this.observedImages.destroy();
    if (this.forecastLegendEl) { this.forecastLegendEl.remove(); this.forecastLegendEl = null; }
    if (this.observedLegendEl) { this.observedLegendEl.remove(); this.observedLegendEl = null; }
    if (this.map) {
      this.map.remove();
      this.map = null;
    }
    if (this.observedBadgeEl) { this.observedBadgeEl.remove(); this.observedBadgeEl = null; }
    if (this.observedLabelsEl) { this.observedLabelsEl.remove(); this.observedLabelsEl = null; }
    this.observedGroup = null;
    this.observedFlashGroup = null;
    this.observedFlashes = [];
    this.segmentGroup = null;
    this.frontsGroup = null;
    this.airportForecastGroup = null;
    this.waypointGroup = null;
    this.highlightMarker = null;
    this.forecastZoomHandler = null;
    this.lastForecastSig = null;
    this.initialized = false;
  }

  // --- Private ---

  private ensureMap(): void {
    if (this.initialized && this.map) return;

    this.map = L.map(this.container, {
      zoomControl: true,
      attributionControl: true,
    });

    const dark = isDarkTheme();
    this.currentTileTheme = dark ? 'dark' : 'light';
    this.tileLayer = L.tileLayer(dark ? DARK_TILES : LIGHT_TILES, {
      attribution: dark ? DARK_ATTR : LIGHT_ATTR,
      maxZoom: 18,
    }).addTo(this.map);

    // Switch tiles when theme changes
    window.addEventListener('theme-changed', ((e: CustomEvent<string>) => {
      this.updateTiles(e.detail === 'dark');
    }) as EventListener);

    // Observed imagery is the backdrop: it is a picture of the sky, and
    // everything the briefing computed must stay legible over it.
    this.observedGroup = L.layerGroup().addTo(this.map);
    this.observedFlashGroup = L.layerGroup().addTo(this.map);
    // Airport forecast overlay sits at the bottom of the stack so the route
    // segments, fronts and waypoints always draw on top of the airport dots.
    this.airportForecastGroup = L.layerGroup().addTo(this.map);
    this.segmentGroup = L.layerGroup().addTo(this.map);
    this.frontsGroup = L.layerGroup().addTo(this.map);
    this.waypointGroup = L.layerGroup().addTo(this.map);

    // Fit to route bounds
    if (this.data && this.data.points.length > 0) {
      let minLat = Infinity, maxLat = -Infinity, minLon = Infinity, maxLon = -Infinity;
      for (const p of this.data.points) {
        if (p.lat < minLat) minLat = p.lat;
        if (p.lat > maxLat) maxLat = p.lat;
        if (p.lon < minLon) minLon = p.lon;
        if (p.lon > maxLon) maxLon = p.lon;
      }
      const bounds = L.latLngBounds([minLat, minLon], [maxLat, maxLon]);
      this.map.fitBounds(bounds, { padding: [30, 30] });
    }

    this.initialized = true;
  }

  private updateTiles(dark: boolean): void {
    const target = dark ? 'dark' : 'light';
    if (!this.map || !this.tileLayer || this.currentTileTheme === target) return;
    this.currentTileTheme = target;
    this.tileLayer.setUrl(dark ? DARK_TILES : LIGHT_TILES);
    this.map.attributionControl.remove();
    L.control.attribution().addTo(this.map);
    this.tileLayer.getAttribution = () => dark ? DARK_ATTR : LIGHT_ATTR;
    this.renderWaypoints();
  }

  private renderSegments(): void {
    if (!this.data || !this.segmentGroup || !this.map) return;

    this.segmentGroup.clearLayers();

    const styles = computeSegmentStyles(
      this.data.points,
      this.colorMetric,
      this.widthMetric,
      this.altitudeFt,
    );

    const points = this.data.points;
    for (let i = 0; i < styles.length; i++) {
      const style = styles[i];
      const p1 = points[i];
      const p2 = points[i + 1];

      const line = L.polyline(
        [[p1.lat, p1.lon], [p2.lat, p2.lon]],
        {
          color: style.color,
          weight: style.weight,
          opacity: 0.9,
          lineCap: 'round',
          lineJoin: 'round',
        },
      );

      // Store segment index for interaction
      (line as any)._segmentIndex = i;

      this.segmentGroup.addLayer(line);
    }
  }

  /** Experimental Hewson front overlay (#196): a marker per on-track crossing
   *  (colored by kind, sized by intensity) plus an off-track marker for the
   *  nearest closing front. Advisory-only, free-atmosphere boundaries. */
  private renderObserved(): void {
    if (!this.observedGroup || !this.map || !this.data) return;
    const observed = this.data.observed;
    const source = this.observedSource;
    const field = !source ? null : source.startsWith('eumetsat_ctth') ? observed?.cloudTops
      : source === 'opera_rate' ? observed?.rainRate : source === 'opera_dbzh' ? observed?.reflectivity : null;
    const bounds = corridorBounds(this.data, observed?.radiusNm ?? 20);
    const imageRequest = field && source && bounds ? overlayUrl(source, bounds) : null;
    const key = imageRequest ? `${imageRequest}|${field!.validTime}` : null;
    if (imageRequest && field && key) {
      const requestField = source === 'eumetsat_ctth_temp'
        ? { ...field, label: 'Cloud-top temperature' }
        : field;
      this.observedImages.select(key, imageRequest, requestField, () => this.renderObserved());
    } else {
      this.observedImages.clear();
    }
    const image = this.observedImages.current();
    renderObservedOverlay(
      this.observedGroup,
      this.map,
      this.data,
      {
        imagerySource: this.observedSource,
        imageryOpacity: this.observedOpacity,
        radiusNm: this.data.observed?.radiusNm ?? 20,
        imageUrl: image.url ?? undefined,
      },
      [],
    );
    this.renderObservedFlashClock();
    this.updateObservedBadge(this.currentObservedBadge());
    this.updateObservedLegend();
  }

  private currentObservedBadge(): string {
    if (this.observedSource === 'eumetsat_li') return this.observedFlashField
      ? formatBadge(this.observedFlashField) : 'Lightning unavailable or loading · age unknown';
    if (!this.observedSource) return '';
    const image = this.observedImages.current();
    return image.field ? formatBadge(image.field) : 'Observed image unavailable or loading · age unknown';
  }

  private renderObservedFlashClock(): void {
    if (!this.observedFlashGroup) return;
    renderObservedFlashes(this.observedFlashGroup, this.observedFlashes);
  }

  /** Shared normal-flow stack: long timestamps/attribution must push the legend
   *  upward rather than wrapping underneath its fixed corner position. */
  private observedLabelContainer(): HTMLElement {
    if (!this.observedLabelsEl) {
      this.observedLabelsEl = document.createElement('div');
      this.observedLabelsEl.className = 'map-observed-labels';
      this.container.appendChild(this.observedLabelsEl);
    }
    return this.observedLabelsEl;
  }

  /** The age badge rides on the map itself, not in a side panel: it labels a
   *  specific picture, and the picture is what the pilot is looking at. */
  private updateObservedBadge(text: string): void {
    if (!text) {
      if (this.observedBadgeEl) { this.observedBadgeEl.remove(); this.observedBadgeEl = null; }
      return;
    }
    if (!this.observedBadgeEl) {
      this.observedBadgeEl = document.createElement('div');
      this.observedBadgeEl.className = 'map-observed-badge';
      this.observedLabelContainer().appendChild(this.observedBadgeEl);
    }
    this.observedBadgeEl.textContent = text;
  }

  /** Colour scale for whatever observed layer is drawn, in the map's corner.
   *
   *  The synoptic grid layer carries one and these did not, so a pilot could
   *  see a green pixel and have no way to learn what value it meant. Built
   *  from the server's own ramp, never a client copy. */
  private updateObservedLegend(): void {
    const status = this.observedSource ? this.observedLegends?.get(this.observedSource) : null;
    if (!status || !status.legend?.length) {
      if (this.observedLegendEl) { this.observedLegendEl.remove(); this.observedLegendEl = null; }
      return;
    }
    if (!this.observedLegendEl) {
      this.observedLegendEl = document.createElement('div');
      this.observedLegendEl.className = 'map-observed-legend';
      this.observedLabelContainer().appendChild(this.observedLegendEl);
    }
    const stops = status.legend;
    const swatches = stops
      .map((s) => `<span class="mol-swatch" style="background:${s.color}"></span>`)
      .join('');
    // First and last only: a label per stop is unreadable at this size, and
    // the ends are what set the range.
    const lo = formatLegendValue(status.source, stops[0].value, status.units);
    const hi = formatLegendValue(status.source, stops[stops.length - 1].value, status.units);
    this.observedLegendEl.innerHTML =
      `<div class="mol-title">${escapeHtml(status.label)}</div>`
      + `<div class="mol-ramp">${swatches}</div>`
      + `<div class="mol-ends"><span>${escapeHtml(lo)}</span><span>${escapeHtml(hi)}</span></div>`;
  }

  private renderFronts(): void {
    if (!this.frontsGroup || !this.map) return;
    this.frontsGroup.clearLayers();
    if (!this.showFronts) return;

    // Gated front axes (the 2-D TFP=0 extractor, same FrontGateConfig as the
    // advisory) for the selected model, across all stored levels — so a low warm
    // front (850/925) gets a line just like the mid cold front (700). Clipped to
    // a corridor around the route to drop the rest of the European domain.
    // Drawn first so the route-crossing markers sit on top; opacity fades with
    // altitude (lower hPa = lighter). Switching the model re-fetches.
    if (this.frontLines) {
      for (const fl of this.frontLines) {
        if (fl.coordinates.length < 2 || !this.lineNearRoute(fl.coordinates)) continue;
        // Endpoint returns GeoJSON [lon, lat]; Leaflet wants [lat, lon].
        const latlngs = fl.coordinates.map(([lon, lat]) => [lat, lon] as [number, number]);
        const color = frontColor(fl.kind as FrontKind);
        const line = L.polyline(latlngs, {
          color,
          weight: 3,
          opacity: FRONT_LEVEL_OPACITY[fl.level_hPa] ?? 0.8,
          dashArray: fl.kind === 'quasi-stationary' ? '6,5' : undefined,
        });
        line.bindTooltip(
          `${frontKindLabel(fl.kind as FrontKind)} · ${fl.level_hPa} hPa · ${Math.round(fl.length_km)} km`,
          { sticky: true, className: 'map-waypoint-tooltip' },
        );
        this.frontsGroup.addLayer(line);
      }
    }

    const fronts = this.data?.fronts;
    if (!fronts) return;

    for (const c of fronts.crossings) {
      const color = frontColor(c.kind);
      const marker = L.circleMarker([c.lat, c.lon], {
        radius: 4 + (FRONT_INTENSITY_WEIGHT[c.intensity] ?? 2),
        color,
        fillColor: color,
        fillOpacity: 0.85,
        weight: 2,
      });
      marker.bindTooltip(frontTooltip(c), {
        direction: 'top',
        offset: [0, -8],
        className: 'map-waypoint-tooltip',
      });
      this.frontsGroup.addLayer(marker);
    }

    // Off-track nearest front, only when it is closing on the route.
    const n = fronts.nearest;
    if (n && !n.on_track && n.trend === 'closing') {
      const marker = L.circleMarker([n.lat, n.lon], {
        radius: 6,
        color: '#6b7280',
        fillColor: '#9ca3af',
        fillOpacity: 0.5,
        weight: 2,
        dashArray: '3,3',
      });
      marker.bindTooltip(
        frontOfftrackTooltip(n.distance_km, n.closing_km_per_h),
        { direction: 'top', offset: [0, -8], className: 'map-waypoint-tooltip' },
      );
      this.frontsGroup.addLayer(marker);
    }
  }

  /** Airport forecast overlay (#424): one circle marker per watchlist airport
   *  in the snapshot, coloured by the selected metric for the briefing's
   *  selected model. Reuses the forecast map's served colour catalog, tooltip
   *  and legend so the two views can never disagree. All ~620 airports are
   *  drawn — zoom does the spatial filtering. */
  private renderForecastOverlay(): void {
    if (!this.airportForecastGroup || !this.map) return;

    // Skip the ~620-marker teardown/rebuild when nothing about the overlay
    // changed since the last draw. renderVisualization re-runs on every
    // map-affecting state change (notably each altitude-slider input tick, which
    // has nothing to do with the airport overlay), and both render() and
    // updateForecastOverlay call this per pass — without this guard that heavy
    // DOM churn would run twice per tick and stutter the drag. Zoom-driven
    // radius changes are handled separately by the zoom handler (no rebuild).
    const sig = this.showForecastOverlay && this.forecastData
      ? `on|${this.forecastMetric}|${this.forecastModel}|${this.forecastDataVersion}`
      : 'off';
    if (sig === this.lastForecastSig) return;
    this.lastForecastSig = sig;

    this.airportForecastGroup.clearLayers();

    if (!this.showForecastOverlay || !this.forecastData) {
      this.removeForecastLegend();
      this.attachForecastZoomHandler();
      return;
    }

    const r = this.forecastMarkerRadius();
    const metric = this.forecastMetric;
    const model = this.forecastModel;
    for (const apt of this.forecastData.airports) {
      const color = getForecastColor(apt, metric, model);
      const marker = L.circleMarker([apt.lat, apt.lon], {
        radius: r,
        fillColor: color,
        fillOpacity: 0.85,
        color,
        weight: 1,
        opacity: 1,
      });
      marker.bindTooltip(getForecastTooltip(apt, model, metric), { className: 'wx-forecast-tooltip' });
      this.airportForecastGroup.addLayer(marker);
    }

    this.attachForecastZoomHandler();
    this.renderForecastLegend();
  }

  /** Marker radius scaled to zoom for legibility — one step smaller than the
   *  full forecast map's so the airport dots stay secondary to the route. */
  private forecastMarkerRadius(): number {
    if (!this.map) return 5;
    const z = this.map.getZoom();
    if (z <= 4) return 4;
    if (z <= 5) return 5;
    if (z <= 6) return 6;
    if (z <= 7) return 8;
    return 10;
  }

  private attachForecastZoomHandler(): void {
    if (!this.map) return;
    if (this.forecastZoomHandler) {
      this.map.off('zoomend', this.forecastZoomHandler);
      this.forecastZoomHandler = null;
    }
    if (!this.showForecastOverlay) return;
    this.forecastZoomHandler = () => {
      const nr = this.forecastMarkerRadius();
      this.airportForecastGroup?.eachLayer((layer) => {
        if (layer instanceof L.CircleMarker) layer.setRadius(nr);
      });
    };
    this.map.on('zoomend', this.forecastZoomHandler);
  }

  private removeForecastLegend(): void {
    if (this.forecastLegendEl) { this.forecastLegendEl.remove(); this.forecastLegendEl = null; }
  }

  /** Small floating legend inside the map (top-right) — distinct from the
   *  route-segment gradient legend that sits below the map, so the two don't
   *  collide. Reuses the served catalog rows via `forecastLegend`. */
  private renderForecastLegend(): void {
    this.removeForecastLegend();
    const legend = forecastLegend(this.forecastMetric);
    const el = document.createElement('div');
    el.className = 'wx-forecast-legend';
    el.innerHTML = `
      <div class="wx-forecast-legend-title">${legend.title}</div>
      ${legend.items.map((i) => `
        <div class="wx-forecast-legend-item">
          <span class="wx-forecast-legend-dot" style="background:${i.color}"></span>
          <span>${i.label}</span>
        </div>
      `).join('')}
    `;
    // Appended into the Leaflet container so it floats over the tiles; stop the
    // map from panning when the user interacts with the box.
    this.container.appendChild(el);
    L.DomEvent.disableClickPropagation(el);
    this.forecastLegendEl = el;
  }

  /** True if any vertex of the axis is within FRONT_CORRIDOR_KM of any route
   *  point — the corridor clip that keeps the overlay near the track. Without a
   *  route (no points), don't filter. */
  private lineNearRoute(coords: [number, number][]): boolean {
    const pts = this.data?.points;
    if (!pts || pts.length === 0) return true;
    for (const [lon, lat] of coords) {
      for (const p of pts) {
        if (haversineKm(lat, lon, p.lat, p.lon) <= FRONT_CORRIDOR_KM) return true;
      }
    }
    return false;
  }

  private renderWaypoints(): void {
    if (!this.data || !this.waypointGroup || !this.map) return;

    this.waypointGroup.clearLayers();

    for (const wp of this.data.waypointMarkers) {
      const dark = isDarkTheme();
      const marker = L.circleMarker([wp.lat, wp.lon], {
        radius: 5,
        color: dark ? '#e4e4e8' : '#1a1a2e',
        fillColor: dark ? '#1e1e2a' : '#ffffff',
        fillOpacity: 1,
        weight: 2,
      });

      marker.bindTooltip(wp.icao, {
        permanent: false,
        direction: 'top',
        offset: [0, -8],
        className: 'map-waypoint-tooltip',
      });

      this.waypointGroup.addLayer(marker);
    }
  }

  private updateHighlight(): void {
    if (!this.map || !this.data) return;

    if (this.highlightMarker) {
      this.highlightMarker.remove();
      this.highlightMarker = null;
    }

    if (this.selectedPointIndex >= 0 && this.selectedPointIndex < this.data.points.length) {
      const pt = this.data.points[this.selectedPointIndex];
      this.highlightMarker = L.circleMarker([pt.lat, pt.lon], {
        radius: 8,
        color: '#2563eb',
        fillColor: '#ffffff',
        fillOpacity: 0.8,
        weight: 3,
      }).addTo(this.map);
    }
  }

  /** Expose map for interaction attachment. */
  getMap(): L.Map | null {
    return this.map;
  }

  getSegmentGroup(): L.LayerGroup | null {
    return this.segmentGroup;
  }
}
