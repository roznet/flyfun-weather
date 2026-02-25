/** Route map renderer — Leaflet-based geographic visualization of weather along the route. */

import * as L from 'leaflet';
import type { VizRouteData } from '../types';
import type { MapMetric } from './metrics';
import { computeSegmentStyles } from './segment-style';

export class RouteMapRenderer {
  private container: HTMLElement;
  private map: L.Map | null = null;
  private segmentGroup: L.LayerGroup | null = null;
  private waypointGroup: L.LayerGroup | null = null;
  private highlightMarker: L.CircleMarker | null = null;

  private data: VizRouteData | null = null;
  private colorMetric: MapMetric | null = null;
  private widthMetric: MapMetric | null = null;
  private altitudeFt = 0;
  private selectedPointIndex = -1;
  private initialized = false;

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

  setSelectedPointIndex(index: number): void {
    this.selectedPointIndex = index;
    this.updateHighlight();
  }

  render(): void {
    if (!this.data || this.data.points.length < 2) return;
    if (this.container.clientWidth === 0 || this.container.clientHeight === 0) return;

    this.ensureMap();
    this.renderSegments();
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

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>',
      maxZoom: 18,
    }).addTo(this.map);

    this.segmentGroup = L.layerGroup().addTo(this.map);
    this.waypointGroup = L.layerGroup().addTo(this.map);

    // Fit to route bounds
    if (this.data && this.data.points.length > 0) {
      const lats = this.data.points.map((p) => p.lat);
      const lons = this.data.points.map((p) => p.lon);
      const bounds = L.latLngBounds(
        [Math.min(...lats), Math.min(...lons)],
        [Math.max(...lats), Math.max(...lons)],
      );
      this.map.fitBounds(bounds, { padding: [30, 30] });
    }

    this.initialized = true;
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

  private renderWaypoints(): void {
    if (!this.data || !this.waypointGroup || !this.map) return;

    this.waypointGroup.clearLayers();

    for (const wp of this.data.waypointMarkers) {
      const marker = L.circleMarker([wp.lat, wp.lon], {
        radius: 5,
        color: '#1a1a2e',
        fillColor: '#ffffff',
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
