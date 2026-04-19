/** PIREP map — Leaflet map with PIREP markers, severity colors, and fog-of-war overlay.
 *
 * The fog-of-war overlay darkens the entire map to communicate that absence
 * of PIREPs means "unknown", not "clear". Circular holes are cut around each
 * PIREP location (~20 nm radius) so the underlying map is visible there.
 */

import * as L from 'leaflet';
import type { PirepResponse } from '../adapters/pirep-adapter';
import { SEVERITY_COLORS, maxSeverity, hazardIconsRaw as hazardIcon } from '../utils/pirep-helpers';
import { renderPirepDetailCard } from '../managers/pirep-ui';

const LIGHT_TILES = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png';
const DARK_TILES = 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png';
const ATTR = '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>';

/** Fog-of-war radius around each PIREP in metres (~20 nm). */
const FOG_HOLE_RADIUS_M = 37040;
/** Overlay darkness: 0 = transparent, 1 = opaque. */
const FOG_OPACITY = 0.45;

function isDark(): boolean {
  return document.documentElement.dataset.theme === 'dark';
}

function ageOpacity(observedAt: string): number {
  const ageHrs = (Date.now() - new Date(observedAt).getTime()) / 3600000;
  if (ageHrs <= 3) return 1.0;
  if (ageHrs <= 6) return 0.7;
  return 0.4;
}

/* ---------- Fog-of-war canvas overlay ---------- */

/**
 * A canvas layer that paints a dark overlay with circular holes punched out
 * at each PIREP location. Uses `destination-out` compositing for the cutouts
 * and a radial gradient so edges feather smoothly.
 */
class FogOfWarLayer extends L.Layer {
  private canvas: HTMLCanvasElement | null = null;
  private pireps: PirepResponse[] = [];
  private pane: HTMLElement | null = null;

  setPireps(pireps: PirepResponse[]): void {
    this.pireps = pireps;
    this.redraw();
  }

  onAdd(map: L.Map): this {
    // Create a custom pane between tiles and markers
    if (!map.getPane('fogPane')) {
      this.pane = map.createPane('fogPane');
      this.pane.style.zIndex = '350'; // above tiles (200), below markers (600)
      this.pane.style.pointerEvents = 'none';
    } else {
      this.pane = map.getPane('fogPane')!;
    }

    this.canvas = L.DomUtil.create('canvas', '', this.pane) as HTMLCanvasElement;
    this.canvas.style.position = 'absolute';
    this.canvas.style.top = '0';
    this.canvas.style.left = '0';

    map.on('move zoom viewreset resize', this.redraw, this);
    this.redraw();
    return this;
  }

  onRemove(map: L.Map): this {
    map.off('move zoom viewreset resize', this.redraw, this);
    if (this.canvas && this.canvas.parentNode) {
      this.canvas.parentNode.removeChild(this.canvas);
    }
    this.canvas = null;
    return this;
  }

  private redraw = (): void => {
    const map = (this as any)._map as L.Map | undefined;
    if (!map || !this.canvas) return;

    const size = map.getSize();
    const dpr = window.devicePixelRatio || 1;
    this.canvas.width = size.x * dpr;
    this.canvas.height = size.y * dpr;
    this.canvas.style.width = size.x + 'px';
    this.canvas.style.height = size.y + 'px';

    // Align canvas with the map's pixel origin
    const topLeft = map.containerPointToLayerPoint([0, 0]);
    L.DomUtil.setPosition(this.canvas, topLeft);

    const ctx = this.canvas.getContext('2d')!;
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);

    // 1. Fill the entire canvas with dark overlay
    ctx.globalCompositeOperation = 'source-over';
    ctx.fillStyle = `rgba(0, 0, 0, ${FOG_OPACITY})`;
    ctx.fillRect(0, 0, size.x, size.y);

    // 2. Cut holes at each PIREP using destination-out
    ctx.globalCompositeOperation = 'destination-out';
    for (const p of this.pireps) {
      const pt = map.latLngToContainerPoint([p.latitude, p.longitude]);

      // Convert metre radius to pixels at this zoom / latitude
      const radiusPx = this.metresToPixels(map, p.latitude, FOG_HOLE_RADIUS_M);

      // Radial gradient for soft feathered edges
      const grad = ctx.createRadialGradient(pt.x, pt.y, 0, pt.x, pt.y, radiusPx);
      grad.addColorStop(0, 'rgba(0,0,0,1)');     // fully transparent centre
      grad.addColorStop(0.6, 'rgba(0,0,0,1)');   // solid out to 60%
      grad.addColorStop(1, 'rgba(0,0,0,0)');      // feather to edge

      ctx.fillStyle = grad;
      ctx.beginPath();
      ctx.arc(pt.x, pt.y, radiusPx, 0, Math.PI * 2);
      ctx.fill();
    }
  };

  /** Convert a ground distance in metres to screen pixels for a given latitude. */
  private metresToPixels(map: L.Map, lat: number, metres: number): number {
    const latlng = L.latLng(lat, 0);
    const point = map.latLngToContainerPoint(latlng);
    // Shift by `metres` east and measure pixel distance
    const degPerMetre = 1 / (111320 * Math.cos(lat * Math.PI / 180));
    const latlng2 = L.latLng(lat, degPerMetre * metres);
    const point2 = map.latLngToContainerPoint(latlng2);
    return Math.abs(point2.x - point.x);
  }
}

export class PirepMap {
  private container: HTMLElement;
  private map: L.Map | null = null;
  private markersGroup: L.LayerGroup | null = null;
  private fogLayer: FogOfWarLayer | null = null;

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

    this.fogLayer = new FogOfWarLayer();
    this.fogLayer.addTo(this.map);

    this.markersGroup = L.layerGroup().addTo(this.map);

    // Disclaimer
    const disclaimer = (L.control as any)({ position: 'bottomleft' });
    disclaimer.onAdd = () => {
      const div = L.DomUtil.create('div', 'pirep-map-disclaimer');
      div.innerHTML = 'Darkened areas: <strong>no reports</strong> — not clear skies.';
      return div;
    };
    disclaimer.addTo(this.map);
  }

  setData(pireps: PirepResponse[]): void {
    if (!this.map) this.init();

    this.markersGroup!.clearLayers();
    this.fogLayer!.setPireps(pireps);

    for (const p of pireps) {
      const severity = maxSeverity(p);
      const color = SEVERITY_COLORS[severity] || '#999';
      const opacity = ageOpacity(p.observed_at);

      // Marker
      const icon = L.divIcon({
        className: 'pirep-marker',
        html: `<div class="pirep-marker-inner" style="background:${color};opacity:${opacity}">${hazardIcon(p)}</div>`,
        iconSize: [28, 28],
        iconAnchor: [14, 14],
      });

      const marker = L.marker([p.latitude, p.longitude], { icon }).addTo(this.markersGroup!);
      marker.bindPopup(renderPirepDetailCard(p), { maxWidth: 300 });
    }

    // Auto-fit bounds if we have data
    if (pireps.length > 0) {
      const bounds = L.latLngBounds(pireps.map(p => [p.latitude, p.longitude]));
      this.map!.fitBounds(bounds.pad(0.2), { maxZoom: 8 });
    }
  }

  /** Call when the container becomes visible (tab switch) to fix tile rendering. */
  invalidateSize(): void {
    if (this.map) {
      setTimeout(() => this.map!.invalidateSize(), 100);
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
