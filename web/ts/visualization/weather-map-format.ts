/** Shared color scales + value formatting for the forecast overview map.
 *
 * Extracted from weather-map.ts so both the Leaflet marker layer and the
 * airport summary card color/format identical values from the same source
 * of truth (no duplicated thresholds). weather-map.ts re-exports
 * `ForecastMetric` for existing importers.
 */

import type {
  ForecastAirport, ConsensusForecast, AltRequired,
} from '../adapters/maps-adapter';
import { formatVisibility, formatHeading } from '../units';
import {
  isConsensusMode, computeConsensus, ordinalConsensus, type ConsensusMode,
} from './weather-map-consensus';

// --- Color scales ---

export const CAT_COLORS: Record<string, string> = {
  VFR: '#22c55e', MVFR: '#3b82f6', IFR: '#ef4444', LIFR: '#a855f7',
};

export const RISK_COLORS: Record<string, string> = {
  none: '#22c55e', marginal: '#eab308', low: '#facc15', moderate: '#f97316', high: '#ef4444', extreme: '#991b1b',
};

/** Border color per agreement bucket (consensus modes). Keys match the
 *  values the server bakes into `consensus.agreement[field]`. */
export const AGREEMENT_COLORS: Record<string, string> = {
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

export function cloudCoverColor(pct: number): string {
  const g = Math.round(220 - (pct / 100) * 160);
  return `rgb(${g},${g},${g + 10})`;
}

// --- Forecast metric extraction ---

export type ForecastMetric = 'flight_category' | 'wind_speed_kt' | 'crosswind_kt' | 'headwind_kt' | 'ceiling_ft' | 'cape_jkg' | 'convective_risk' | 'cloud_cover_pct' | 'visibility_m' | 'alternate_needed';

// --- Alternate-required (FAA/EASA) helpers (#249) ---

// Alternate-required treated as an ordinal (no < yes=worse) so the Worst/Majority
// toggle aggregates it the same way as flight category.
const ALT_ORDER: Record<string, number> = { no: 0, yes: 1 };

/** Per-model/aggregate label: None / EASA / FAA / FAA+EASA. */
export function altLabel(f: AltRequired | undefined): string {
  if (!f) return '—';
  if (f.faa && f.easa) return 'FAA+EASA';
  if (f.easa) return 'EASA';
  if (f.faa) return 'FAA';
  return 'None';
}

/** Aggregate per-model flags using the active consensus mode (worst = any model;
 * majority = modal with worst tiebreak), matching the flight-category toggle. */
export function aggAltRequired(airport: ForecastAirport, mode: ConsensusMode): AltRequired | undefined {
  const flags = Object.values(airport.models)
    .map((m) => m?.alt_required)
    .filter((f): f is AltRequired => !!f);
  if (!flags.length) return undefined;
  const reg = (pick: (f: AltRequired) => boolean): boolean =>
    ordinalConsensus(flags.map((f) => (pick(f) ? 'yes' : 'no')), ALT_ORDER, mode) === 'yes';
  return { faa: reg((f) => f.faa), easa: reg((f) => f.easa) };
}

export function altNeededColor(airport: ForecastAirport, model: string): string {
  const f = isConsensusMode(model) ? aggAltRequired(airport, model) : airport.models[model]?.alt_required;
  if (!f) return '#888';
  const n = (f.faa ? 1 : 0) + (f.easa ? 1 : 0);
  return n === 0 ? '#22c55e' : n === 1 ? '#eab308' : '#ef4444'; // green / amber / red
}

export function getConsensus(airport: ForecastAirport, mode: ConsensusMode): ConsensusForecast {
  return computeConsensus(airport, mode);
}

export function getForecastColor(airport: ForecastAirport, metric: ForecastMetric, model: string): string {
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

function fmtWind(speed: number | null | undefined, dir: number | null | undefined, gust: number | null | undefined): string {
  if (speed == null) return '';
  const dirStr = dir != null ? `${formatHeading(dir)}/` : '';
  const gustStr = gust ? `G${Math.round(gust)}` : '';
  return `${dirStr}${Math.round(speed)}${gustStr} kt`;
}

export const METRIC_LABEL: Record<ForecastMetric, string> = {
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

/** Format the value of a metric (no label) for tooltip / card display. */
export function formatMetricValue(data: { [key: string]: any }, metric: ForecastMetric): string {
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

export function getAgreementForMetric(consensus: ConsensusForecast, metric: ForecastMetric): string | null {
  const key = metricAgreementKey(metric);
  return consensus.agreement[key] ?? null;
}
