/** Weather overview map — Leaflet map with airport markers colored by forecast or verification metrics. */

import * as L from 'leaflet';
import type {
  ForecastAirport, ForecastMapResponse, ModelForecast, ConsensusForecast,
  VerificationAirport, VerificationMapResponse,
} from '../adapters/maps-adapter';

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
  good: '#22c55e', moderate: '#f97316', poor: '#ef4444',
};

function windSpeedColor(kt: number): string {
  if (kt < 10) return '#22c55e';
  if (kt < 15) return '#84cc16';
  if (kt < 20) return '#eab308';
  if (kt < 25) return '#f97316';
  if (kt < 35) return '#ef4444';
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

function accuracyColor(pct: number): string {
  if (pct >= 80) return '#22c55e';
  if (pct >= 60) return '#eab308';
  if (pct >= 40) return '#f97316';
  return '#ef4444';
}

function maeColor(value: number, thresholdBad: number): string {
  const ratio = Math.min(value / thresholdBad, 1);
  if (ratio < 0.3) return '#22c55e';
  if (ratio < 0.6) return '#eab308';
  if (ratio < 0.8) return '#f97316';
  return '#ef4444';
}

// --- Forecast metric extraction ---

export type ForecastMetric = 'flight_category' | 'wind_speed_kt' | 'ceiling_ft' | 'cape_jkg' | 'convective_risk' | 'cloud_cover_pct' | 'visibility_m';

function isConsensusMode(model: string): boolean {
  return model === 'worst' || model === 'majority';
}

const CAT_ORDER: Record<string, number> = { VFR: 0, MVFR: 1, IFR: 2, LIFR: 3 };

function computeConsensus(airport: ForecastAirport, mode: string): ConsensusForecast {
  const models = Object.values(airport.models);
  if (models.length === 0) return { flight_category: 'VFR', agreement: 'good' };

  const cats = models.map(m => m.flight_category);

  let consensusCat: string;
  if (mode === 'majority') {
    const counts = new Map<string, number>();
    for (const c of cats) counts.set(c, (counts.get(c) ?? 0) + 1);
    const maxCount = Math.max(...counts.values());
    const tied = [...counts.entries()].filter(([, n]) => n === maxCount).map(([c]) => c);
    consensusCat = tied.reduce((a, b) => (CAT_ORDER[a] ?? 0) >= (CAT_ORDER[b] ?? 0) ? a : b);
  } else {
    consensusCat = cats.reduce((a, b) => (CAT_ORDER[a] ?? 0) >= (CAT_ORDER[b] ?? 0) ? a : b);
  }

  // Use server-computed agreement (model divergence) — same regardless of consensus mode
  const agreement = airport.consensus.agreement;

  // Numeric means (same as server logic)
  const result: ConsensusForecast = { flight_category: consensusCat, agreement };
  for (const field of ['wind_speed_kt', 'ceiling_ft', 'cape_jkg', 'visibility_m'] as const) {
    const vals = models.map(m => m[field]).filter((v): v is number => v != null);
    if (vals.length) (result as any)[field] = Math.round((vals.reduce((a, b) => a + b, 0) / vals.length) * 10) / 10;
  }
  // Wind direction: circular mean
  const dirs = models.map(m => m.wind_dir_deg).filter((v): v is number => v != null);
  if (dirs.length) {
    const sinSum = dirs.reduce((s, d) => s + Math.sin(d * Math.PI / 180), 0);
    const cosSum = dirs.reduce((s, d) => s + Math.cos(d * Math.PI / 180), 0);
    result.wind_dir_deg = ((Math.atan2(sinSum, cosSum) * 180 / Math.PI) + 360) % 360;
  }

  return result;
}

function getConsensus(airport: ForecastAirport, model: string): ConsensusForecast {
  // For 'worst', use the pre-computed server consensus (avoids recomputation)
  if (model === 'worst') return airport.consensus;
  return computeConsensus(airport, model);
}

function getForecastColor(airport: ForecastAirport, metric: ForecastMetric, model: string): string {
  const consensus = isConsensusMode(model);
  const modelData = consensus ? null : airport.models[model];
  const data = consensus ? getConsensus(airport, model) : modelData;
  if (!data) return '#888';

  switch (metric) {
    case 'flight_category':
      return CAT_COLORS[data.flight_category] || '#888';
    case 'wind_speed_kt':
      return windSpeedColor(data.wind_speed_kt ?? 0);
    case 'ceiling_ft':
      return ceilingColor(data.ceiling_ft ?? null);
    case 'cape_jkg':
      return capeColor(data.cape_jkg ?? 0);
    case 'convective_risk': {
      // In consensus mode, pick the worst risk across models
      if (consensus) {
        const riskOrder = ['none', 'marginal', 'low', 'moderate', 'high', 'extreme'];
        let worst = 'none';
        for (const md of Object.values(airport.models)) {
          const r = md.convective_risk || 'none';
          if (riskOrder.indexOf(r) > riskOrder.indexOf(worst)) worst = r;
        }
        return RISK_COLORS[worst] || '#888';
      }
      return RISK_COLORS[modelData?.convective_risk || 'none'] || '#888';
    }
    case 'visibility_m': {
      if (consensus) {
        const vals = Object.values(airport.models).map(m => m.visibility_m).filter((v): v is number => v != null);
        return visibilityColor(vals.length ? Math.min(...vals) : 99999);
      }
      return visibilityColor(modelData?.visibility_m ?? 99999);
    }
    case 'cloud_cover_pct': {
      // In consensus mode, average cloud cover across models
      if (consensus) {
        const vals = Object.values(airport.models).map(m => m.cloud_cover_pct).filter((v): v is number => v != null);
        return cloudCoverColor(vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : 0);
      }
      return cloudCoverColor(modelData?.cloud_cover_pct ?? 0);
    }
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
  if (m == null) return '';
  const sm = m / 1609.34;
  if (sm >= 10) return '>10 SM';
  return `${sm.toFixed(1)} SM`;
}

function fmtWind(speed: number | null | undefined, dir: number | null | undefined, gust: number | null | undefined): string {
  if (speed == null) return '';
  const dirStr = dir != null ? `${String(Math.round(dir)).padStart(3, '0')}/` : '';
  const gustStr = gust ? `G${Math.round(gust)}` : '';
  return `${dirStr}${Math.round(speed)}${gustStr} kt`;
}

function getMetricLine(data: { [key: string]: any }, metric: ForecastMetric): string | null {
  switch (metric) {
    case 'wind_speed_kt':
      return data.wind_speed_kt != null ? `Wind: ${fmtWind(data.wind_speed_kt, data.wind_dir_deg, data.wind_gust_kt)}` : null;
    case 'ceiling_ft':
      return data.ceiling_ft != null ? `Ceiling: ${fmtCeiling(data.ceiling_ft)}` : null;
    case 'visibility_m':
      return data.visibility_m != null ? `Visibility: ${fmtVisibility(data.visibility_m)}` : null;
    case 'cape_jkg':
      return data.cape_jkg != null ? `CAPE: ${Math.round(data.cape_jkg)} J/kg` : null;
    case 'convective_risk':
      return `Convective: ${data.convective_risk || 'none'}`;
    case 'cloud_cover_pct':
      return data.cloud_cover_pct != null ? `Cloud cover: ${Math.round(data.cloud_cover_pct)}%` : null;
    default:
      return null;
  }
}

function getForecastTooltip(airport: ForecastAirport, model: string, metric: ForecastMetric): string {
  const lines: string[] = [`<b>${airport.icao}</b>`];

  if (isConsensusMode(model)) {
    const c = getConsensus(airport, model);
    lines.push(`Category: <b>${c.flight_category}</b> (${c.agreement})`);
    if (metric !== 'flight_category') {
      // For consensus, build metric line from averaged consensus data or worst-of-models
      if (metric === 'convective_risk') {
        const riskOrder = ['none', 'marginal', 'low', 'moderate', 'high', 'extreme'];
        let worst = 'none';
        for (const md of Object.values(airport.models)) {
          const r = md.convective_risk || 'none';
          if (riskOrder.indexOf(r) > riskOrder.indexOf(worst)) worst = r;
        }
        lines.push(`Convective: ${worst}`);
      } else {
        const line = getMetricLine(c, metric);
        if (line) lines.push(line);
      }
    }
    for (const [m, d] of Object.entries(airport.models)) {
      lines.push(`<span style="color:var(--text-muted)">${m.toUpperCase()}: ${d.flight_category}</span>`);
    }
  } else {
    const d = airport.models[model];
    if (!d) { lines.push('No data'); return lines.join('<br>'); }
    lines.push(`Category: <b>${d.flight_category}</b>`);
    if (metric !== 'flight_category') {
      const line = getMetricLine(d, metric);
      if (line) lines.push(line);
    }
  }
  return lines.join('<br>');
}

// --- Verification metric extraction ---

export type VerifMetric = 'category_match_pct' | 'ceiling_mae_ft' | 'wind_mae_kt' | 'temp_mae_c';

function getVerifColor(airport: VerificationAirport, metric: VerifMetric): string {
  switch (metric) {
    case 'category_match_pct':
      return accuracyColor(airport.category_match_pct);
    case 'ceiling_mae_ft':
      return maeColor(airport.ceiling_mae_ft, 1500);
    case 'wind_mae_kt':
      return maeColor(airport.wind_mae_kt, 10);
    case 'temp_mae_c':
      return maeColor(airport.temp_mae_c, 5);
    default:
      return '#888';
  }
}

function getVerifTooltip(airport: VerificationAirport): string {
  return [
    `<b>${airport.icao}</b> (n=${airport.sample_count})`,
    `Category match: ${airport.category_match_pct.toFixed(1)}%`,
    `Ceiling MAE: ${Math.round(airport.ceiling_mae_ft)} ft`,
    `Wind MAE: ${airport.wind_mae_kt.toFixed(1)} kt`,
    `Temp MAE: ${airport.temp_mae_c.toFixed(1)} C`,
    `Ceiling bias: ${airport.ceiling_bias_ft > 0 ? '+' : ''}${Math.round(airport.ceiling_bias_ft)} ft`,
  ].join('<br>');
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
};

// --- Map class ---

export class WeatherMap {
  private container: HTMLElement;
  private map: L.Map | null = null;
  private markersGroup: L.LayerGroup | null = null;
  private legendEl: HTMLElement | null = null;
  private tileLayer: L.TileLayer | null = null;

  constructor(container: HTMLElement) {
    this.container = container;
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
    if (!this.map) return 5;
    const z = this.map.getZoom();
    if (z <= 4) return 3;
    if (z <= 5) return 4;
    if (z <= 6) return 5;
    if (z <= 7) return 6;
    return 8;
  }

  setForecastData(data: ForecastMapResponse, metric: ForecastMetric, model: string): void {
    if (!this.markersGroup || !this.map) return;
    this.markersGroup.clearLayers();

    const r = this.markerRadius();
    for (const apt of data.airports) {
      const color = getForecastColor(apt, metric, model);
      const isConsensus = isConsensusMode(model);
      const border = isConsensus
        ? (AGREEMENT_COLORS[getConsensus(apt, model).agreement] || '#888')
        : color;

      const marker = L.circleMarker([apt.lat, apt.lon], {
        radius: r,
        fillColor: color,
        fillOpacity: 0.85,
        color: border,
        weight: isConsensus ? 2 : 1,
        opacity: 1,
      });

      marker.bindTooltip(getForecastTooltip(apt, model, metric), { className: 'map-tooltip' });
      marker.addTo(this.markersGroup);
    }

    // Update marker sizes on zoom
    this.map.off('zoomend.markers');
    this.map.on('zoomend.markers', () => {
      const nr = this.markerRadius();
      this.markersGroup?.eachLayer((layer) => {
        if (layer instanceof L.CircleMarker) layer.setRadius(nr);
      });
    });

    this.renderLegend(FORECAST_LEGENDS[metric]);
  }

  setVerificationData(data: VerificationMapResponse, metric: VerifMetric): void {
    if (!this.markersGroup || !this.map) return;
    this.markersGroup.clearLayers();

    const r = this.markerRadius();
    const minSamples = 5;

    for (const apt of data.airports) {
      if (apt.sample_count < minSamples) continue;

      const color = getVerifColor(apt, metric);
      // Scale radius by sample count (more data = larger marker)
      const sizeBonus = Math.min(apt.sample_count / 100, 1) * 3;

      const marker = L.circleMarker([apt.lat, apt.lon], {
        radius: r + sizeBonus,
        fillColor: color,
        fillOpacity: 0.8,
        color: color,
        weight: 1,
        opacity: 1,
      });

      marker.bindTooltip(getVerifTooltip(apt), { className: 'map-tooltip' });
      marker.addTo(this.markersGroup);
    }

    this.map.off('zoomend.markers');
    this.map.on('zoomend.markers', () => {
      const nr = this.markerRadius();
      this.markersGroup?.eachLayer((layer) => {
        if (layer instanceof L.CircleMarker) layer.setRadius(nr);
      });
    });

    // Verification legend
    const verifLegends: Record<VerifMetric, { title: string; items: Array<{ color: string; label: string }> }> = {
      category_match_pct: {
        title: 'Category Match %',
        items: [
          { color: '#22c55e', label: '>= 80%' },
          { color: '#eab308', label: '60-80%' },
          { color: '#f97316', label: '40-60%' },
          { color: '#ef4444', label: '< 40%' },
        ],
      },
      ceiling_mae_ft: {
        title: 'Ceiling MAE (ft)',
        items: [
          { color: '#22c55e', label: '< 450' },
          { color: '#eab308', label: '450-900' },
          { color: '#f97316', label: '900-1200' },
          { color: '#ef4444', label: '> 1200' },
        ],
      },
      wind_mae_kt: {
        title: 'Wind MAE (kt)',
        items: [
          { color: '#22c55e', label: '< 3' },
          { color: '#eab308', label: '3-6' },
          { color: '#f97316', label: '6-8' },
          { color: '#ef4444', label: '> 8' },
        ],
      },
      temp_mae_c: {
        title: 'Temp MAE (C)',
        items: [
          { color: '#22c55e', label: '< 1.5' },
          { color: '#eab308', label: '1.5-3' },
          { color: '#f97316', label: '3-4' },
          { color: '#ef4444', label: '> 4' },
        ],
      },
    };
    this.renderLegend(verifLegends[metric]);
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
