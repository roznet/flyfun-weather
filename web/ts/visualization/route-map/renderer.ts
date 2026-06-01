/** Route map renderer — Leaflet-based geographic visualization of weather along the route. */

import * as L from 'leaflet';
import type { VizRouteData } from '../types';
import type { MapMetric } from './metrics';
import { computeSegmentStyles } from './segment-style';
import { isDarkTheme } from '../interaction-utils';
import { frontColor, frontTooltip, frontOfftrackTooltip, FRONT_INTENSITY_WEIGHT } from '../front-style';

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
  private highlightMarker: L.CircleMarker | null = null;

  private data: VizRouteData | null = null;
  private colorMetric: MapMetric | null = null;
  private widthMetric: MapMetric | null = null;
  private altitudeFt = 0;
  private showFronts = false;
  private selectedPointIndex = -1;
  private initialized = false;
  private currentTileTheme: 'light' | 'dark' = 'light';

  constructor(container: HTMLElement) {
    this.container = container;
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

  setSelectedPointIndex(index: number): void {
    this.selectedPointIndex = index;
    this.updateHighlight();
  }

  render(): void {
    if (!this.data || this.data.points.length < 2) return;
    if (this.container.clientWidth === 0 || this.container.clientHeight === 0) return;

    this.ensureMap();
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
    if (this.map) {
      this.map.remove();
      this.map = null;
    }
    this.segmentGroup = null;
    this.frontsGroup = null;
    this.waypointGroup = null;
    this.highlightMarker = null;
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
  private renderFronts(): void {
    if (!this.data || !this.frontsGroup || !this.map) return;
    this.frontsGroup.clearLayers();
    const fronts = this.data.fronts;
    if (!this.showFronts || !fronts) return;

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
