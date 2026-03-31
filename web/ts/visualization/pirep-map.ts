/** PIREP map — Leaflet map with PIREP markers, severity colors, and awareness circles. */

import * as L from 'leaflet';
import type { PirepResponse } from '../adapters/pirep-adapter';
import { renderPirepDetailCard } from '../managers/pirep-ui';

const LIGHT_TILES = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png';
const DARK_TILES = 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png';
const ATTR = '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>';

const SEVERITY_COLORS: Record<string, string> = {
  none: '#4caf50',
  trace: '#fbc02d',
  light: '#fbc02d',
  moderate: '#ff9800',
  severe: '#f44336',
};

function isDark(): boolean {
  return document.documentElement.dataset.theme === 'dark';
}

function maxSeverity(p: PirepResponse): string {
  const order = ['none', 'trace', 'light', 'moderate', 'severe'];
  let max = 0;
  if (p.icing_intensity) max = Math.max(max, order.indexOf(p.icing_intensity));
  if (p.turbulence_intensity) max = Math.max(max, order.indexOf(p.turbulence_intensity));
  return order[max] || 'none';
}

function hazardIcon(p: PirepResponse): string {
  const parts: string[] = [];
  if (p.icing_intensity && p.icing_intensity !== 'none') parts.push('&#10052;');
  if (p.turbulence_intensity && p.turbulence_intensity !== 'none') parts.push('&#9084;');
  if (p.in_cloud || p.ceiling_msl_ft != null || p.tops_msl_ft != null) parts.push('&#9729;');
  if (parts.length === 0) parts.push('&#10003;');
  return parts.join('');
}

function ageOpacity(observedAt: string): number {
  const ageMin = (Date.now() - new Date(observedAt).getTime()) / 60000;
  if (ageMin <= 30) return 1.0;
  if (ageMin <= 90) return 0.7;
  return 0.4;
}

export class PirepMap {
  private container: HTMLElement;
  private map: L.Map | null = null;
  private markersGroup: L.LayerGroup | null = null;
  private circlesGroup: L.LayerGroup | null = null;

  constructor(container: HTMLElement) {
    this.container = container;
  }

  init(): void {
    if (this.map) return;

    const dark = isDark();
    this.map = L.map(this.container, {
      center: [48, 10],  // Central Europe
      zoom: 5,
      zoomControl: true,
    });

    L.tileLayer(dark ? DARK_TILES : LIGHT_TILES, {
      attribution: ATTR,
      maxZoom: 18,
    }).addTo(this.map);

    this.markersGroup = L.layerGroup().addTo(this.map);
    this.circlesGroup = L.layerGroup().addTo(this.map);

    // Disclaimer
    const disclaimer = L.control({ position: 'bottomleft' });
    disclaimer.onAdd = () => {
      const div = L.DomUtil.create('div', 'pirep-map-disclaimer');
      div.innerHTML = 'Areas without reports are <strong>unknown</strong>, not clear.';
      return div;
    };
    disclaimer.addTo(this.map);
  }

  setData(pireps: PirepResponse[]): void {
    if (!this.map) this.init();

    this.markersGroup.clearLayers();
    this.circlesGroup.clearLayers();

    for (const p of pireps) {
      const severity = maxSeverity(p);
      const color = SEVERITY_COLORS[severity] || '#999';
      const opacity = ageOpacity(p.observed_at);

      // Awareness circle (~30km radius ≈ 0.27 deg)
      L.circle([p.latitude, p.longitude], {
        radius: 30000,
        color: color,
        fillColor: color,
        fillOpacity: 0.08 * opacity,
        weight: 1,
        opacity: 0.3 * opacity,
        interactive: false,
      }).addTo(this.circlesGroup);

      // Marker
      const icon = L.divIcon({
        className: 'pirep-marker',
        html: `<div class="pirep-marker-inner" style="background:${color};opacity:${opacity}">${hazardIcon(p)}</div>`,
        iconSize: [28, 28],
        iconAnchor: [14, 14],
      });

      const marker = L.marker([p.latitude, p.longitude], { icon }).addTo(this.markersGroup);
      marker.bindPopup(renderPirepDetailCard(p), { maxWidth: 300 });
    }

    // Auto-fit bounds if we have data
    if (pireps.length > 0) {
      const bounds = L.latLngBounds(pireps.map(p => [p.latitude, p.longitude]));
      this.map.fitBounds(bounds.pad(0.2), { maxZoom: 8 });
    }
  }

  /** Call when the container becomes visible (tab switch) to fix tile rendering. */
  invalidateSize(): void {
    if (this.map) {
      setTimeout(() => this.map.invalidateSize(), 100);
    }
  }

  /** Get current viewport bounds as "sw_lat,sw_lon,ne_lat,ne_lon" string. */
  getBounds(): string | null {
    if (!this.map) return null;
    const b = this.map.getBounds();
    return `${b.getSouth()},${b.getWest()},${b.getNorth()},${b.getEast()}`;
  }

  destroy(): void {
    if (this.map) {
      this.map.remove();
      this.map = null;
    }
  }
}
