/** Weather overview map — Leaflet map with airport markers colored by forecast metrics. */

import * as L from 'leaflet';
import type {
  ForecastAirport, ForecastMapResponse,
} from '../adapters/maps-adapter';
import { getUnitsRegion } from '../units';
import { isConsensusMode, type ConsensusMode } from './weather-map-consensus';
import {
  getForecastColor, getConsensus, getAgreementForMetric, formatMetricValue,
  METRIC_LABEL, altLabel, aggAltRequired, AGREEMENT_COLORS,
  CAT_COLORS, RISK_COLORS, cloudCoverColor,
  type ForecastMetric,
} from './weather-map-format';

// Re-exported so existing importers (maps-main.ts) keep resolving the type here.
export type { ForecastMetric };

const LIGHT_TILES = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png';
const DARK_TILES = 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png';
const ATTR = '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>';

function isDark(): boolean {
  return document.documentElement.dataset.theme === 'dark';
}

// --- Alternate-required (FAA/EASA) tooltip ---

function altNeededTooltip(airport: ForecastAirport, model: string): string {
  const lines: string[] = [`<b>${airport.icao}</b>`];
  if (isConsensusMode(model)) {
    lines.push(`Alternate required: <b>${altLabel(aggAltRequired(airport, model))}</b>`);
    for (const [m, d] of Object.entries(airport.models)) {
      lines.push(`<span style="color:var(--text-muted)">${m.toUpperCase()}: ${altLabel(d?.alt_required)}</span>`);
    }
  } else {
    lines.push(`Alternate required: <b>${altLabel(airport.models[model]?.alt_required)}</b>`);
  }
  return lines.join('<br>');
}

/** Region-aware legend rows for the visibility metric (flight-category bands). */
function visibilityLegendItems(): Array<{ color: string; label: string }> {
  if (getUnitsRegion() === 'us') {
    return [
      { color: '#22c55e', label: '>= 5 SM (VFR)' },
      { color: '#3b82f6', label: '3-5 SM (MVFR)' },
      { color: '#ef4444', label: '1-3 SM (IFR)' },
      { color: '#a855f7', label: '< 1 SM (LIFR)' },
    ];
  }
  // km equivalents of the SM breakpoints visibilityColor() actually uses
  // (5/3/1 SM = 8.05/4.83/1.61 km) so the legend matches the dot colors.
  return [
    { color: '#22c55e', label: '>= 8.0 km (VFR)' },
    { color: '#3b82f6', label: '4.8-8.0 km (MVFR)' },
    { color: '#ef4444', label: '1.6-4.8 km (IFR)' },
    { color: '#a855f7', label: '< 1.6 km (LIFR)' },
  ];
}

function getForecastTooltip(airport: ForecastAirport, model: string, metric: ForecastMetric): string {
  if (metric === 'alternate_needed') return altNeededTooltip(airport, model);

  const lines: string[] = [`<b>${airport.icao}</b>`];
  const label = METRIC_LABEL[metric];

  if (isConsensusMode(model)) {
    const c = getConsensus(airport, model);
    const modeName = model === 'worst' ? 'Worst' : 'Majority';

    lines.push(`${modeName} ${label.toLowerCase()}: <b>${formatMetricValue(c, metric)}</b>`);

    const agr = getAgreementForMetric(c, metric);
    if (agr) lines.push(`Models: ${agr}`);

    for (const [m, d] of Object.entries(airport.models)) {
      lines.push(`<span style="color:var(--text-muted)">${m.toUpperCase()}: ${formatMetricValue(d, metric)}</span>`);
    }
  } else {
    const d = airport.models[model];
    if (!d) { lines.push('No data'); return lines.join('<br>'); }
    lines.push(`${label}: <b>${formatMetricValue(d, metric)}</b>`);
  }
  return lines.join('<br>');
}

// --- Legend definitions ---

const FORECAST_LEGENDS: Record<ForecastMetric, { title: string; items: Array<{ color: string; label: string }> }> = {
  flight_category: {
    title: 'Flight Category',
    items: [
      { color: CAT_COLORS.VFR, label: 'VFR' },
      { color: CAT_COLORS.MVFR, label: 'MVFR' },
      { color: CAT_COLORS.IFR, label: 'IFR' },
      { color: CAT_COLORS.LIFR, label: 'LIFR' },
    ],
  },
  wind_speed_kt: {
    title: 'Wind Speed (kt)',
    items: [
      { color: '#22c55e', label: '< 10' },
      { color: '#84cc16', label: '10-15' },
      { color: '#eab308', label: '15-20' },
      { color: '#f97316', label: '20-25' },
      { color: '#ef4444', label: '25-35' },
      { color: '#991b1b', label: '35+' },
    ],
  },
  crosswind_kt: {
    title: 'Best Rwy Crosswind (kt)',
    items: [
      { color: '#22c55e', label: '< 5' },
      { color: '#84cc16', label: '5-10' },
      { color: '#eab308', label: '10-15' },
      { color: '#f97316', label: '15-20' },
      { color: '#ef4444', label: '20-25' },
      { color: '#991b1b', label: '25+' },
    ],
  },
  headwind_kt: {
    title: 'Best Rwy Headwind (kt)',
    items: [
      { color: '#22c55e', label: '< 10' },
      { color: '#84cc16', label: '10-15' },
      { color: '#eab308', label: '15-20' },
      { color: '#f97316', label: '20-25' },
      { color: '#ef4444', label: '25-30' },
      { color: '#991b1b', label: '30+' },
    ],
  },
  ceiling_ft: {
    title: 'Ceiling (ft)',
    items: [
      { color: '#22c55e', label: '> 3000 (VFR)' },
      { color: '#3b82f6', label: '1000-3000 (MVFR)' },
      { color: '#ef4444', label: '500-1000 (IFR)' },
      { color: '#a855f7', label: '< 500 (LIFR)' },
    ],
  },
  cape_jkg: {
    title: 'CAPE (J/kg)',
    items: [
      { color: '#22c55e', label: '< 100' },
      { color: '#eab308', label: '100-500' },
      { color: '#f97316', label: '500-1000' },
      { color: '#ef4444', label: '1000-2000' },
      { color: '#991b1b', label: '2000+' },
    ],
  },
  convective_risk: {
    title: 'Convective Risk',
    items: Object.entries(RISK_COLORS).map(([k, c]) => ({ color: c, label: k })),
  },
  visibility_m: {
    title: 'Visibility',
    items: [
      { color: '#22c55e', label: '>= 5 SM (VFR)' },
      { color: '#3b82f6', label: '3-5 SM (MVFR)' },
      { color: '#ef4444', label: '1-3 SM (IFR)' },
      { color: '#a855f7', label: '< 1 SM (LIFR)' },
    ],
  },
  cloud_cover_pct: {
    title: 'Cloud Cover',
    items: [
      { color: cloudCoverColor(0), label: 'Clear' },
      { color: cloudCoverColor(25), label: '25%' },
      { color: cloudCoverColor(50), label: '50%' },
      { color: cloudCoverColor(75), label: '75%' },
      { color: cloudCoverColor(100), label: 'Overcast' },
    ],
  },
  alternate_needed: {
    title: 'Alternate required? (model estimate)',
    items: [
      { color: '#22c55e', label: 'Neither (FAA & EASA ok)' },
      { color: '#eab308', label: 'One regime (FAA or EASA)' },
      { color: '#ef4444', label: 'Both (FAA & EASA)' },
      { color: '#888', label: 'No data' },
    ],
  },
};

// --- Map class ---

/** Right-click handler — receives only the ICAO. The map already has the
 *  coords via the marker, but the host re-resolves them server-side
 *  (via `_resolve_airport_coords`) so passing them here would be dead
 *  payload. If a future host wants to skip that lookup, extend the
 *  signature then. */
export type AirportContextHandler = (icao: string) => void;

export class WeatherMap {
  private container: HTMLElement;
  private map: L.Map | null = null;
  private markersGroup: L.LayerGroup | null = null;
  private legendEl: HTMLElement | null = null;
  private tileLayer: L.TileLayer | null = null;
  private zoomHandler: (() => void) | null = null;
  private contextHandler: AirportContextHandler | null = null;
  private clickHandler: AirportContextHandler | null = null;
  private highlightedIcao: string | null = null;
  /** Marker registry. Stores the original style alongside the marker so
   *  `setHighlightedIcao(null)` can restore the exact pre-highlight
   *  appearance — consensus mode uses weight=2 + agreement-color border,
   *  individual-model mode uses weight=1, and a single restored value
   *  would silently change the visual encoding. */
  private icaoToMarker: Map<string, { marker: L.CircleMarker; weight: number; color: string }> = new Map();

  constructor(container: HTMLElement) {
    this.container = container;
  }

  /** Register a callback fired when the user right-clicks an airport marker.
   *  The forecast tab uses this to open the airport-profile detail panel. */
  setAirportContextHandler(handler: AirportContextHandler | null): void {
    this.contextHandler = handler;
  }

  /** Register a callback fired when the user left-clicks (or taps) an
   *  airport marker — the primary, touch-friendly way to open the
   *  airport summary panel. Right-click stays wired as an alias. */
  setAirportClickHandler(handler: AirportContextHandler | null): void {
    this.clickHandler = handler;
  }

  /** Visually highlight one airport (typically the one currently shown
   *  in the detail panel). Pass null to clear the highlight. */
  setHighlightedIcao(icao: string | null): void {
    if (this.highlightedIcao === icao) return;
    if (this.highlightedIcao) {
      const prev = this.icaoToMarker.get(this.highlightedIcao);
      if (prev) prev.marker.setStyle({ weight: prev.weight, color: prev.color });
    }
    this.highlightedIcao = icao;
    if (icao) {
      const entry = this.icaoToMarker.get(icao);
      if (entry) {
        entry.marker.setStyle({ weight: 4, color: '#fbbf24' });
        entry.marker.bringToFront();
      }
    }
  }

  init(): void {
    if (this.map) return;

    const dark = isDark();
    this.map = L.map(this.container, {
      center: [48, 10],   // Central Europe
      zoom: 5,
      zoomControl: true,
    });

    this.tileLayer = L.tileLayer(dark ? DARK_TILES : LIGHT_TILES, {
      attribution: ATTR,
      maxZoom: 18,
    }).addTo(this.map);

    this.markersGroup = L.layerGroup().addTo(this.map);

    // Theme switching
    document.addEventListener('theme-changed', () => {
      if (!this.map || !this.tileLayer) return;
      const url = isDark() ? DARK_TILES : LIGHT_TILES;
      this.tileLayer.setUrl(url);
    });
  }

  private markerRadius(): number {
    if (!this.map) return 7;
    const z = this.map.getZoom();
    if (z <= 4) return 5;
    if (z <= 5) return 6;
    if (z <= 6) return 7;
    if (z <= 7) return 9;
    return 11;
  }

  private attachZoomHandler(): void {
    if (!this.map) return;
    if (this.zoomHandler) this.map.off('zoomend', this.zoomHandler);
    this.zoomHandler = () => {
      const nr = this.markerRadius();
      this.markersGroup?.eachLayer((layer) => {
        if (layer instanceof L.CircleMarker) layer.setRadius(nr);
      });
    };
    this.map.on('zoomend', this.zoomHandler);
  }

  setForecastData(data: ForecastMapResponse, metric: ForecastMetric, model: string): void {
    if (!this.markersGroup || !this.map) return;
    this.markersGroup.clearLayers();
    this.icaoToMarker.clear();

    const r = this.markerRadius();
    const consensusMode: ConsensusMode | null = isConsensusMode(model) ? model : null;
    for (const apt of data.airports) {
      const color = getForecastColor(apt, metric, model);
      const border = consensusMode
        ? (AGREEMENT_COLORS[getAgreementForMetric(getConsensus(apt, consensusMode), metric) ?? ''] || '#888')
        : color;
      const baseWeight = consensusMode ? 2 : 1;

      const marker = L.circleMarker([apt.lat, apt.lon], {
        radius: r,
        fillColor: color,
        fillOpacity: 0.85,
        color: border,
        weight: baseWeight,
        opacity: 1,
        // Signal the marker is interactive (opens the summary panel).
        className: 'wx-marker-clickable',
      });

      marker.bindTooltip(getForecastTooltip(apt, model, metric), { className: 'map-tooltip' });
      // Left-click / tap → open the airport summary panel. This is the
      // primary, touch-friendly entry point (hover + right-click don't
      // exist on touch devices).
      marker.on('click', (e: L.LeafletMouseEvent) => {
        L.DomEvent.stopPropagation(e.originalEvent);
        this.clickHandler?.(apt.icao);
      });
      // Right-click → same panel, kept as a desktop alias. Native
      // contextmenu is suppressed so the browser menu doesn't fight
      // Leaflet's event.
      marker.on('contextmenu', (e: L.LeafletMouseEvent) => {
        L.DomEvent.preventDefault(e.originalEvent);
        L.DomEvent.stopPropagation(e.originalEvent);
        (this.clickHandler ?? this.contextHandler)?.(apt.icao);
      });
      marker.addTo(this.markersGroup);
      this.icaoToMarker.set(apt.icao, { marker, weight: baseWeight, color: border });
    }

    this.attachZoomHandler();
    const legend = metric === 'visibility_m'
      ? { ...FORECAST_LEGENDS.visibility_m, items: visibilityLegendItems() }
      : FORECAST_LEGENDS[metric];
    this.renderLegend(legend);

    // Re-apply highlight if the currently-highlighted airport is still on the map.
    if (this.highlightedIcao) {
      const entry = this.icaoToMarker.get(this.highlightedIcao);
      if (entry) {
        entry.marker.setStyle({ weight: 4, color: '#fbbf24' });
        entry.marker.bringToFront();
      }
    }
  }

  private renderLegend(legend: { title: string; items: Array<{ color: string; label: string }> }): void {
    // Remove old legend
    if (this.legendEl) {
      this.legendEl.remove();
      this.legendEl = null;
    }

    const wrapper = this.container.parentElement;
    if (!wrapper) return;

    const el = document.createElement('div');
    el.className = 'map-legend';
    el.innerHTML = `
      <div class="map-legend-title">${legend.title}</div>
      ${legend.items.map(i => `
        <div class="legend-item">
          <span class="legend-dot" style="background:${i.color}"></span>
          <span>${i.label}</span>
        </div>
      `).join('')}
    `;
    wrapper.appendChild(el);
    this.legendEl = el;
  }

  invalidateSize(): void {
    this.map?.invalidateSize();
  }

  destroy(): void {
    if (this.legendEl) { this.legendEl.remove(); this.legendEl = null; }
    if (this.map) { this.map.remove(); this.map = null; }
  }
}
