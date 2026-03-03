/** Lightweight Leaflet map showing a route polyline + waypoint markers.
 *  No weather overlays — just geographic context for the flight detail page. */

import * as L from 'leaflet';
import type { WaypointInfo } from '../adapters/api-adapter';
import { isDarkTheme } from '../visualization/interaction-utils';

const LIGHT_TILES = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png';
const DARK_TILES = 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png';
const LIGHT_ATTR = '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>';
const DARK_ATTR = '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a> &copy; <a href="https://carto.com/">CARTO</a>';

export class RouteMapInset {
  private container: HTMLElement;
  private map: L.Map | null = null;
  private tileLayer: L.TileLayer | null = null;
  private routeLayer: L.Polyline | null = null;
  private markerGroup: L.LayerGroup | null = null;
  private themeListener: ((e: Event) => void) | null = null;

  constructor(container: HTMLElement) {
    this.container = container;
  }

  /** Render the route on the map. */
  render(waypoints: WaypointInfo[]): void {
    if (waypoints.length === 0) return;

    this.ensureMap();
    if (!this.map) return;

    // Clear previous layers
    if (this.routeLayer) { this.routeLayer.remove(); this.routeLayer = null; }
    if (this.markerGroup) { this.markerGroup.clearLayers(); }

    const latlngs: L.LatLngExpression[] = waypoints.map(w => [w.lat, w.lon]);

    // Route polyline
    this.routeLayer = L.polyline(latlngs, {
      color: '#2563eb',
      weight: 3,
      opacity: 0.8,
    }).addTo(this.map);

    // Waypoint markers
    if (!this.markerGroup) {
      this.markerGroup = L.layerGroup().addTo(this.map);
    }

    for (const wp of waypoints) {
      const marker = L.circleMarker([wp.lat, wp.lon], {
        radius: 5,
        color: '#2563eb',
        fillColor: '#fff',
        fillOpacity: 1,
        weight: 2,
      });
      marker.bindTooltip(wp.icao, {
        permanent: waypoints.length <= 6,
        direction: 'top',
        offset: [0, -8],
        className: 'map-waypoint-tooltip',
      });
      marker.addTo(this.markerGroup);
    }

    // Fit bounds with padding
    const bounds = L.latLngBounds(latlngs);
    this.map.fitBounds(bounds, { padding: [30, 30] });
  }

  /** Fix map sizing after container resize. */
  invalidateSize(): void {
    if (this.map) this.map.invalidateSize();
  }

  /** Clean up map and listeners. */
  destroy(): void {
    if (this.themeListener) {
      window.removeEventListener('theme-changed', this.themeListener);
      this.themeListener = null;
    }
    if (this.map) {
      this.map.remove();
      this.map = null;
    }
  }

  private ensureMap(): void {
    if (this.map) return;
    if (this.container.clientWidth === 0 || this.container.clientHeight === 0) return;

    this.map = L.map(this.container, {
      zoomControl: true,
      attributionControl: true,
      scrollWheelZoom: false,
    });

    const dark = isDarkTheme();
    this.tileLayer = L.tileLayer(dark ? DARK_TILES : LIGHT_TILES, {
      attribution: dark ? DARK_ATTR : LIGHT_ATTR,
      maxZoom: 18,
    }).addTo(this.map);

    // Listen for theme changes
    this.themeListener = ((e: CustomEvent<string>) => {
      this.updateTiles(e.detail === 'dark');
    }) as EventListener;
    window.addEventListener('theme-changed', this.themeListener);
  }

  private updateTiles(dark: boolean): void {
    if (!this.map || !this.tileLayer) return;
    this.tileLayer.setUrl(dark ? DARK_TILES : LIGHT_TILES);
    this.tileLayer.options.attribution = dark ? DARK_ATTR : LIGHT_ATTR;
  }
}
