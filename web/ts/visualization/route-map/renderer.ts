/** Route map renderer — Leaflet-based geographic visualization of weather along the route. */

import * as L from 'leaflet';
import type { VizRouteData } from '../types';
import type { ResolvedAdvisoryFocus } from '../advisory-focus';
import {
  ROUTE_EVIDENCE_HALO_WEIGHT,
  collectRouteMapFitCoordinates,
  focusRegionsInPaintOrder,
  routeEvidenceDashArray,
  routePointBounds,
} from '../advisory-focus';
import type { MapMetric } from './metrics';
import { computeSegmentStyles } from './segment-style';
import { isDarkTheme } from '../interaction-utils';
import { frontColor, frontKindLabel, frontTooltip, frontOfftrackTooltip, FRONT_INTENSITY_WEIGHT } from '../front-style';
import type { HewsonFront } from '../../adapters/hewson-map-adapter';
import type { FrontKind } from '../../types/fronts';

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
const EVIDENCE_PANE = 'wb-advisory-evidence';
const EVIDENCE_COLORS: Record<ResolvedAdvisoryFocus['regions'][number]['severity'], string> = {
  green: '#16a34a',
  amber: '#d97706',
  red: '#dc2626',
};

function validCoordinate(lat: number, lon: number): boolean {
  return Number.isFinite(lat)
    && Number.isFinite(lon)
    && lat >= -90
    && lat <= 90
    && lon >= -180
    && lon <= 180;
}

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
  private evidenceGroup: L.LayerGroup | null = null;
  private segmentGroup: L.LayerGroup | null = null;
  private waypointGroup: L.LayerGroup | null = null;
  private frontsGroup: L.LayerGroup | null = null;
  private highlightMarker: L.CircleMarker | null = null;

  private data: VizRouteData | null = null;
  private advisoryFocus: ResolvedAdvisoryFocus | null = null;
  private colorMetric: MapMetric | null = null;
  private widthMetric: MapMetric | null = null;
  private altitudeFt = 0;
  private showFronts = false;
  private frontLines: MapFrontLine[] | null = null;
  private selectedPointIndex = -1;
  private initialized = false;
  private currentTileTheme: 'light' | 'dark' = 'light';
  private readonly themeChangedHandler: EventListener = (event) => {
    this.updateTiles((event as CustomEvent<string>).detail === 'dark');
  };

  constructor(container: HTMLElement) {
    this.container = container;
  }

  setData(data: VizRouteData): void {
    this.data = data;
    this.altitudeFt = data.cruiseAltitudeFt;
  }

  setAdvisoryFocus(focus: ResolvedAdvisoryFocus | null): void {
    this.advisoryFocus = focus;
    this.renderEvidence();
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

  setSelectedPointIndex(index: number): void {
    this.selectedPointIndex = index;
    this.updateHighlight();
  }

  render(): void {
    if (!this.data || this.data.points.length < 2) return;
    if (this.container.clientWidth === 0 || this.container.clientHeight === 0) return;

    this.ensureMap();
    this.renderEvidence();
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

  /** Fit the route, advisory evidence, and front axes when explicitly requested. */
  fitRouteAndFronts(): void {
    const coordinates: Array<[number, number]> = [];
    const addCoordinate = (lat: number, lon: number): void => {
      if (validCoordinate(lat, lon)) coordinates.push([lat, lon]);
    };

    const fitCoordinates = collectRouteMapFitCoordinates({
      routePoints: this.data?.points ?? [],
      focus: this.advisoryFocus,
      showFronts: this.showFronts,
      frontAxes: this.frontLines ?? [],
      frontCrossings: this.data?.fronts?.crossings ?? [],
      nearestFront: this.data?.fronts?.nearest ?? null,
      frontAxisNearRoute: (axis) => this.lineNearRoute(axis),
    });
    for (const point of fitCoordinates) {
      addCoordinate(point.lat, point.lon);
    }

    if (coordinates.length === 0) return;
    this.ensureMap();
    if (!this.map) return;
    const bounds = L.latLngBounds(coordinates);
    if (!bounds.isValid()) return;
    this.map.fitBounds(bounds, { padding: [30, 30] });
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
    window.removeEventListener('theme-changed', this.themeChangedHandler);
    this.evidenceGroup?.clearLayers();
    if (this.map) {
      this.map.remove();
      this.map = null;
    }
    this.tileLayer = null;
    this.evidenceGroup = null;
    this.segmentGroup = null;
    this.frontsGroup = null;
    this.waypointGroup = null;
    this.highlightMarker = null;
    this.advisoryFocus = null;
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
    window.addEventListener('theme-changed', this.themeChangedHandler);

    const evidencePane = this.map.getPane(EVIDENCE_PANE) ?? this.map.createPane(EVIDENCE_PANE);
    evidencePane.style.zIndex = '390';
    evidencePane.style.pointerEvents = 'none';

    this.evidenceGroup = L.layerGroup().addTo(this.map);
    this.segmentGroup = L.layerGroup().addTo(this.map);
    this.frontsGroup = L.layerGroup().addTo(this.map);
    this.waypointGroup = L.layerGroup().addTo(this.map);

    const initialRouteBounds = routePointBounds(this.data?.points ?? []);
    if (initialRouteBounds) {
      const bounds = L.latLngBounds(initialRouteBounds[0], initialRouteBounds[1]);
      if (bounds.isValid()) this.map.fitBounds(bounds, { padding: [30, 30] });
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

  private renderEvidence(): void {
    if (!this.evidenceGroup) return;
    this.evidenceGroup.clearLayers();
    const focus = this.advisoryFocus;
    if (!focus?.active.highlightSurfaces.includes('route-map')) return;

    for (const region of focusRegionsInPaintOrder(focus.regions)) {
      if (
        region.mapPath.length < 2
        || !region.mapPath.every((point) => validCoordinate(point.lat, point.lon))
      ) {
        continue;
      }
      const line = L.polyline(
        region.mapPath.map((point) => [point.lat, point.lon] as [number, number]),
        {
          pane: EVIDENCE_PANE,
          interactive: false,
          color: EVIDENCE_COLORS[region.severity],
          weight: ROUTE_EVIDENCE_HALO_WEIGHT,
          opacity: 0.38,
          dashArray: routeEvidenceDashArray(focus.locationState),
          lineCap: 'round',
          lineJoin: 'round',
        },
      );
      this.evidenceGroup.addLayer(line);
    }
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

  /** True if any vertex of the axis is within FRONT_CORRIDOR_KM of any route
   *  point — the corridor clip that keeps the overlay near the track. Without a
   *  route (no points), don't filter. */
  private lineNearRoute(coords: ReadonlyArray<ReadonlyArray<number>>): boolean {
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
