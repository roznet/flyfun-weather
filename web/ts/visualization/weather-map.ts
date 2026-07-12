/** Weather overview map — Leaflet map with airport markers colored by forecast metrics. */

import * as L from 'leaflet';
import type {
  ForecastAirport, ForecastMapResponse, ModelForecast, ConsensusForecast, AltRequired,
} from '../adapters/maps-adapter';
import { formatVisibility, getUnitsRegion, formatHeading } from '../units';

const LIGHT_TILES = 'https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png';
const DARK_TILES = 'https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png';
const ATTR = '&copy; <a href="https://www.openstreetmap.org/copyright">OSM</a>';

function isDark(): boolean {
  return document.documentElement.dataset.theme === 'dark';
}

// --- Color scales ---

const CAT_COLORS: Record<string, string> = {
  VFR: '#22c55e', MVFR: '#3b82f6', IFR: '#ef4444', LIFR: '#a855f7',
};

const RISK_COLORS: Record<string, string> = {
  none: '#22c55e', marginal: '#eab308', low: '#facc15', moderate: '#f97316', high: '#ef4444', extreme: '#991b1b',
};

const AGREEMENT_COLORS: Record<string, string> = {
  consistent: '#22c55e', mixed: '#f97316', divergent: '#ef4444',
};

function windSpeedColor(kt: number): string {
  if (kt < 10) return '#22c55e';
  if (kt < 15) return '#84cc16';
  if (kt < 20) return '#eab308';
  if (kt < 25) return '#f97316';
  if (kt < 35) return '#ef4444';
  return '#991b1b';
}

function crosswindColor(kt: number): string {
  if (kt < 5) return '#22c55e';
  if (kt < 10) return '#84cc16';
  if (kt < 15) return '#eab308';
  if (kt < 20) return '#f97316';
  if (kt < 25) return '#ef4444';
  return '#991b1b';
}

function headwindColor(kt: number): string {
  if (kt < 10) return '#22c55e';
  if (kt < 15) return '#84cc16';
  if (kt < 20) return '#eab308';
  if (kt < 25) return '#f97316';
  if (kt < 30) return '#ef4444';
  return '#991b1b';
}

function ceilingColor(ft: number | null): string {
  if (ft === null) return '#888';
  if (ft < 500) return '#a855f7';  // LIFR
  if (ft < 1000) return '#ef4444'; // IFR
  if (ft < 3000) return '#3b82f6'; // MVFR
  return '#22c55e';                 // VFR
}

function capeColor(jkg: number): string {
  if (jkg < 100) return '#22c55e';
  if (jkg < 500) return '#eab308';
  if (jkg < 1000) return '#f97316';
  if (jkg < 2000) return '#ef4444';
  return '#991b1b';
}

function visibilityColor(m: number): string {
  const sm = m / 1609.34;
  if (sm >= 5) return '#22c55e';   // VFR
  if (sm >= 3) return '#3b82f6';   // MVFR
  if (sm >= 1) return '#ef4444';   // IFR
  return '#a855f7';                 // LIFR
}

function cloudCoverColor(pct: number): string {
  const g = Math.round(220 - (pct / 100) * 160);
  return `rgb(${g},${g},${g + 10})`;
}

// --- Forecast metric extraction ---

export type ForecastMetric = 'flight_category' | 'wind_speed_kt' | 'crosswind_kt' | 'headwind_kt' | 'ceiling_ft' | 'cape_jkg' | 'convective_risk' | 'cloud_cover_pct' | 'visibility_m' | 'alternate_needed';

// --- Alternate-required (FAA/EASA) helpers (#249) ---

/** Per-model/aggregate label: None / EASA / FAA / FAA+EASA. */
function altLabel(f: AltRequired | undefined): string {
  if (!f) return '—';
  if (f.faa && f.easa) return 'FAA+EASA';
  if (f.easa) return 'EASA';
  if (f.faa) return 'FAA';
  return 'None';
}

/** Aggregate per-model flags using the active consensus mode (worst = any model;
 * majority = modal with worst tiebreak), matching the flight-category toggle. */
function aggAltRequired(airport: ForecastAirport, mode: ConsensusMode): AltRequired | undefined {
  const flags = Object.values(airport.models)
    .map((m) => m?.alt_required)
    .filter((f): f is AltRequired => !!f);
  if (!flags.length) return undefined;
  const reg = (pick: (f: AltRequired) => boolean): boolean =>
    ordinalConsensus(flags.map((f) => (pick(f) ? 'yes' : 'no')), ALT_ORDER, mode) === 'yes';
  return { faa: reg((f) => f.faa), easa: reg((f) => f.easa) };
}

function altNeededColor(airport: ForecastAirport, model: string): string {
  const f = isConsensusMode(model) ? aggAltRequired(airport, model) : airport.models[model]?.alt_required;
  if (!f) return '#888';
  const n = (f.faa ? 1 : 0) + (f.easa ? 1 : 0);
  return n === 0 ? '#22c55e' : n === 1 ? '#eab308' : '#ef4444'; // green / amber / red
}

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

import { isConsensusMode, computeConsensus, ordinalConsensus, type ConsensusMode } from './weather-map-consensus';

// Alternate-required treated as an ordinal (no < yes=worse) so the Worst/Majority
// toggle aggregates it the same way as flight category.
const ALT_ORDER: Record<string, number> = { no: 0, yes: 1 };

function getConsensus(airport: ForecastAirport, mode: ConsensusMode): ConsensusForecast {
  return computeConsensus(airport, mode);
}

function getForecastColor(airport: ForecastAirport, metric: ForecastMetric, model: string): string {
  const data = isConsensusMode(model) ? getConsensus(airport, model) : airport.models[model];
  if (!data) return '#888';

  switch (metric) {
    case 'flight_category':
      return CAT_COLORS[data.flight_category] || '#888';
    case 'wind_speed_kt':
      return windSpeedColor(data.wind_speed_kt ?? 0);
    case 'crosswind_kt':
      return data.crosswind_kt != null ? crosswindColor(data.crosswind_kt) : '#888';
    case 'headwind_kt':
      return data.headwind_kt != null ? headwindColor(data.headwind_kt) : '#888';
    case 'ceiling_ft':
      return ceilingColor(data.ceiling_ft ?? null);
    case 'cape_jkg':
      return capeColor(data.cape_jkg ?? 0);
    case 'convective_risk':
      return RISK_COLORS[data.convective_risk || 'none'] || '#888';
    case 'visibility_m':
      return visibilityColor(data.visibility_m ?? 99999);
    case 'cloud_cover_pct':
      return cloudCoverColor(data.cloud_cover_pct ?? 0);
    case 'alternate_needed':
      return altNeededColor(airport, model);
    default:
      return '#888';
  }
}

function fmtCeiling(ft: number | null | undefined): string {
  if (ft == null) return '';
  if (ft >= 10000) return 'CAVOK';
  return `${Math.round(ft)} ft`;
}

function fmtVisibility(m: number | null | undefined): string {
  return formatVisibility(m);
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

function fmtWind(speed: number | null | undefined, dir: number | null | undefined, gust: number | null | undefined): string {
  if (speed == null) return '';
  const dirStr = dir != null ? `${formatHeading(dir)}/` : '';
  const gustStr = gust ? `G${Math.round(gust)}` : '';
  return `${dirStr}${Math.round(speed)}${gustStr} kt`;
}

const METRIC_LABEL: Record<ForecastMetric, string> = {
  flight_category: 'Category',
  wind_speed_kt: 'Wind',
  crosswind_kt: 'Xwind',
  headwind_kt: 'Headwind',
  ceiling_ft: 'Ceiling',
  cape_jkg: 'CAPE',
  convective_risk: 'Convective',
  visibility_m: 'Visibility',
  cloud_cover_pct: 'Cloud cover',
  alternate_needed: 'Alternate required?',
};

/** Format the value of a metric (no label) for tooltip display. */
function formatMetricValue(data: { [key: string]: any }, metric: ForecastMetric): string {
  switch (metric) {
    case 'flight_category':
      return data.flight_category ?? '—';
    case 'wind_speed_kt':
      return data.wind_speed_kt != null ? fmtWind(data.wind_speed_kt, data.wind_dir_deg, data.wind_gust_kt) : '—';
    case 'crosswind_kt':
    case 'headwind_kt': {
      const v = data[metric];
      if (v == null) return '—';
      const gust = metric === 'crosswind_kt' ? data.gust_crosswind_kt : data.gust_headwind_kt;
      const gustStr = gust != null ? ` (G${Math.round(gust)})` : '';
      const rwyStr = data.best_runway_id ? ` RWY ${data.best_runway_id}` : '';
      return `${Math.round(v)}${gustStr} kt${rwyStr}`;
    }
    case 'ceiling_ft':
      return data.ceiling_ft != null ? fmtCeiling(data.ceiling_ft) : '—';
    case 'visibility_m':
      return data.visibility_m != null ? fmtVisibility(data.visibility_m) : '—';
    case 'cape_jkg':
      return data.cape_jkg != null ? `${Math.round(data.cape_jkg)} J/kg` : '—';
    case 'convective_risk':
      return data.convective_risk || 'none';
    case 'cloud_cover_pct':
      return data.cloud_cover_pct != null ? `${Math.round(data.cloud_cover_pct)}%` : '—';
    case 'alternate_needed':
      return altLabel(data.alt_required);
    default:
      return '—';
  }
}

/** Get the agreement key in the consensus dict for a given metric. */
function metricAgreementKey(metric: ForecastMetric): string {
  switch (metric) {
    case 'flight_category': return 'flight_category';
    case 'wind_speed_kt': return 'wind_speed_kt';
    case 'crosswind_kt': return 'wind_speed_kt';  // closest proxy
    case 'headwind_kt': return 'wind_speed_kt';   // closest proxy
    case 'ceiling_ft': return 'ceiling_ft';
    case 'cape_jkg': return 'cape_jkg';
    case 'convective_risk': return 'cape_jkg';  // closest proxy
    case 'visibility_m': return 'visibility_m';
    case 'cloud_cover_pct': return 'cloud_cover_pct';
    default: return 'flight_category';
  }
}

function getAgreementForMetric(consensus: ConsensusForecast, metric: ForecastMetric): string | null {
  const key = metricAgreementKey(metric);
  return consensus.agreement[key] ?? null;
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
      });

      marker.bindTooltip(getForecastTooltip(apt, model, metric), { className: 'map-tooltip' });
      // Right-click → open airport profile panel. Native contextmenu is
      // suppressed so the browser menu doesn't fight Leaflet's event.
      marker.on('contextmenu', (e: L.LeafletMouseEvent) => {
        L.DomEvent.preventDefault(e.originalEvent);
        L.DomEvent.stopPropagation(e.originalEvent);
        this.contextHandler?.(apt.icao);
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
