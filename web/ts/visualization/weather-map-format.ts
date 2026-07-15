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
  isConsensusMode, ordinalConsensus, type ConsensusMode,
} from './weather-map-consensus';
import catalog from '../data/map-metrics-catalog.json';

// --- Served catalog: colours, thresholds, labels, legends (B2, #419) ---
//
// The colour ramps and thresholds are no longer hardcoded here — they live in
// `web/ts/data/map-metrics-catalog.json`, imported below and ALSO served
// ETag-cacheable to iOS via `/api/help` (`maps` section). A threshold change is
// a one-line JSON edit both clients pick up, so the same weather can never show
// in two different colours on two devices. This module is now the JSON's
// interpreter: it evaluates the band/categorical scales the catalog describes.

interface BandStop { lt?: number; gte?: number; color: string; }
interface ThresholdScale {
  kind: 'threshold_asc' | 'threshold_desc';
  stops: BandStop[];
  default: string;
  null_color?: string;
  convert?: 'm_to_sm';
}
interface GrayRampScale { kind: 'gray_ramp'; base: number; span: number; blue_boost: number; }
type BandScale = ThresholdScale | GrayRampScale;

interface MetricColorSpec {
  kind: 'categorical' | 'band' | 'alternate_needed';
  scale?: string;
  field?: string;
  default?: string;
  fallback: string | number | null;
}
interface MapLegendItem { color: string; label: string; }
export interface MapLegend { title: string; items: MapLegendItem[]; }
interface MetricSpec { label: string; color: MetricColorSpec; legend: MapLegend; }
interface MapMetricsCatalog {
  version: number;
  scales: {
    categorical: Record<string, Record<string, string>>;
    bands: Record<string, BandScale>;
  };
  metrics: Record<string, MetricSpec>;
}

const CATALOG = catalog as unknown as MapMetricsCatalog;
const CATEGORICAL = CATALOG.scales.categorical;
const BANDS = CATALOG.scales.bands;

/** The metrics the map can colour by. Single source for both the `ForecastMetric`
 *  type and the runtime list, so the two cannot drift; `mapsUrlState` validates
 *  `fc.metric` against it too. */
export const FORECAST_METRICS = [
  'flight_category', 'alternate_needed', 'wind_speed_kt', 'crosswind_kt',
  'headwind_kt', 'ceiling_ft', 'visibility_m', 'cape_jkg', 'convective_risk',
  'cloud_cover_pct',
] as const;

/** Check the catalog describes every metric with a scale that actually exists.
 *
 * The JSON is imported and cast to its interface, so TypeScript cannot see
 * inside it: dropping or misspelling a metric key — or pointing one at a scale
 * that isn't there — compiles fine and would only fail when a marker renders
 * (`spec.color` on `undefined`), taking out the whole map in production. Since
 * the point of the catalog is that a threshold is "a one-line JSON edit both
 * clients pick up", that edit has to fail loudly and early instead.
 *
 * Exported pure so a unit test can assert the shipped catalog is well-formed —
 * that test is the build-time gate; the module-load throw below is the backstop.
 */
export function validateMapMetricsCatalog(cat: MapMetricsCatalog): string[] {
  const problems: string[] = [];
  for (const metric of FORECAST_METRICS) {
    const spec = cat.metrics?.[metric];
    if (!spec) { problems.push(`metric '${metric}' missing from catalog`); continue; }
    if (!spec.label) problems.push(`metric '${metric}' has no label`);
    if (!spec.legend?.items?.length) problems.push(`metric '${metric}' has no legend items`);

    const color = spec.color;
    if (!color) { problems.push(`metric '${metric}' has no color spec`); continue; }
    switch (color.kind) {
      case 'categorical':
        if (!color.scale || !cat.scales?.categorical?.[color.scale]) {
          problems.push(`metric '${metric}' → unknown categorical scale '${color.scale}'`);
        }
        break;
      case 'band':
        if (!color.scale || !cat.scales?.bands?.[color.scale]) {
          problems.push(`metric '${metric}' → unknown band scale '${color.scale}'`);
        }
        if (!color.field) problems.push(`metric '${metric}' band scale has no data field`);
        break;
      case 'alternate_needed':
        break;
      default:
        problems.push(`metric '${metric}' has unknown color kind '${(color as { kind: string }).kind}'`);
    }
  }
  return problems;
}

const CATALOG_PROBLEMS = validateMapMetricsCatalog(CATALOG);
if (CATALOG_PROBLEMS.length) {
  throw new Error(`map-metrics-catalog.json is malformed:\n  ${CATALOG_PROBLEMS.join('\n  ')}`);
}

/** Muted grey for missing data (unchanged from the previous hardcoded value). */
const MUTED = '#888';

// Re-exported categorical scales, sourced from the catalog so callers that
// colour category / risk / agreement directly (markers, card, legends) share
// the one table with the marker layer.
export const CAT_COLORS: Record<string, string> = CATEGORICAL.flight_category;
export const RISK_COLORS: Record<string, string> = CATEGORICAL.convective_risk;
/** Border color per agreement bucket (consensus modes). Keys match the
 *  values the server bakes into `consensus.agreement[field]`. */
export const AGREEMENT_COLORS: Record<string, string> = CATEGORICAL.agreement;

function grayRamp(scale: GrayRampScale, pct: number): string {
  const g = Math.round(scale.base - (pct / 100) * scale.span);
  return `rgb(${g},${g},${g + scale.blue_boost})`;
}

export function cloudCoverColor(pct: number): string {
  return grayRamp(BANDS.cloud_cover_pct as GrayRampScale, pct);
}

/** Evaluate a value→colour band scale from the catalog.
 *
 * A missing value resolves in the same order the old per-metric functions did:
 * a `null_color` (ceiling) wins; otherwise a `null` fallback → muted grey
 * (crosswind/headwind), and a numeric fallback (wind/CAPE 0, visibility 99999,
 * cloud 0) is substituted before banding. `threshold_asc` picks the first stop
 * the value is below; `threshold_desc` (visibility, after m→SM) the first stop
 * the value is at-or-above. */
function bandColor(scaleKey: string, raw: number | null | undefined, fallback: string | number | null): string {
  const scale = BANDS[scaleKey];
  let v: number | null = raw ?? null;
  if (v == null) {
    const nullColor = (scale as ThresholdScale).null_color;
    if (nullColor) return nullColor;
    if (typeof fallback !== 'number') return MUTED;
    v = fallback;
  }
  if (scale.kind === 'gray_ramp') return grayRamp(scale, v);
  let val = v;
  if (scale.convert === 'm_to_sm') val = val / 1609.34;
  if (scale.kind === 'threshold_desc') {
    for (const s of scale.stops) if (val >= (s.gte as number)) return s.color;
    return scale.default;
  }
  for (const s of scale.stops) if (val < (s.lt as number)) return s.color;
  return scale.default;
}

// --- Forecast metric extraction ---

export type ForecastMetric = typeof FORECAST_METRICS[number];

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

/** The consensus block for the active mode. Both Worst (`consensus`) and
 *  Majority (`consensus_majority`) are baked server-side (#419) so the client
 *  carries no consensus math — it just picks the block. Falls back to the
 *  worst block if a majority block is missing (e.g. an older cached payload). */
export function getConsensus(airport: ForecastAirport, mode: ConsensusMode): ConsensusForecast {
  const baked = mode === 'majority' ? airport.consensus_majority : airport.consensus;
  return baked ?? airport.consensus;
}

export function getForecastColor(airport: ForecastAirport, metric: ForecastMetric, model: string): string {
  const spec = CATALOG.metrics[metric];
  const data = isConsensusMode(model) ? getConsensus(airport, model) : airport.models[model];
  if (!data) return MUTED;

  const color = spec.color;
  switch (color.kind) {
    case 'alternate_needed':
      return altNeededColor(airport, model);
    case 'categorical': {
      // For categorical metrics the scale name is also the data field
      // ('flight_category' / 'convective_risk'). `default` maps a blank value
      // to a sentinel key (convective '' → 'none').
      const scaleMap = CATEGORICAL[color.scale!];
      let key = (data as unknown as Record<string, unknown>)[color.scale!] as string | undefined;
      if (color.default != null && !key) key = color.default;
      return (key != null ? scaleMap[key] : undefined) ?? (color.fallback as string);
    }
    case 'band':
      return bandColor(color.scale!, (data as unknown as Record<string, number | null>)[color.field!], color.fallback);
    default:
      return MUTED;
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
  const dirStr = dir != null ? `${formatHeading(dir)}@` : '';
  const gustStr = gust ? `G${Math.round(gust)}` : '';
  return `${dirStr}${Math.round(speed)}${gustStr} kt`;
}

export const METRIC_LABEL: Record<ForecastMetric, string> = Object.fromEntries(
  Object.entries(CATALOG.metrics).map(([k, m]) => [k, m.label]),
) as Record<ForecastMetric, string>;

/** Per-metric legend (title + colour rows), from the served catalog. The
 *  marker layer renders these; visibility keeps a region-aware override for
 *  its labels (SM vs km) in `weather-map.ts`. */
export const MAP_LEGENDS: Record<ForecastMetric, MapLegend> = Object.fromEntries(
  Object.entries(CATALOG.metrics).map(([k, m]) => [k, m.legend]),
) as Record<ForecastMetric, MapLegend>;

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
      const gustStr = gust != null ? `G${Math.round(gust)}` : '';
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
