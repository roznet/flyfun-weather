/** Route-map metrics (#998) — what colours and sizes the briefing map's route
 *  line, read from the served catalog `web/ts/data/route-map-metrics-catalog.json`.
 *
 *  The catalog is the single source for the metric list, labels, groups,
 *  colour scales, widths, legends and the default, and is served verbatim to
 *  the apps under `route_maps` in `/api/help/catalog`. This module only knows
 *  how to read a value off a viz point (the `value` extractors below); a
 *  catalog metric whose extractor this bundle does not know is skipped rather
 *  than drawn empty.
 *
 *  SYNC: iOS `Views/Map/MapMetrics.swift`, Android `ui/map/MapMetrics.kt` carry
 *  the same extractors under the same names. */

import type { VizPoint } from '../types';
import { bandScaleColor, type BandScale } from '../weather-map-format';
import { t } from '../../i18n/i18n';
import catalogJson from '../../data/route-map-metrics-catalog.json';

// --- Catalog shape ---

export type RouteMetricGroup = 'at_altitude' | 'along_route';

interface RouteLegendItem { value: number; label: string; i18n_key?: string }
type RouteFormat =
  | { kind: 'number'; decimals: number; prefix?: string; suffix?: string; multiply?: number }
  | { kind: 'rank'; labels: string[] };

interface RouteMetricSpec {
  key: string;
  label: string;
  i18n_key: string;
  unit: string;
  group: RouteMetricGroup;
  uses_altitude: boolean;
  value: string;
  aggregation: 'average' | 'max';
  scale: string;
  width: { thin_at: number; thick_at: number };
  format: RouteFormat;
  legend: RouteLegendItem[];
}

export interface RouteMapCatalog {
  version: number;
  default_metric: string;
  no_data_color: string;
  groups: Array<{ key: RouteMetricGroup; label: string; i18n_key: string }>;
  scales: { bands: Record<string, BandScale> };
  metrics: RouteMetricSpec[];
}

export const ROUTE_MAP_CATALOG = catalogJson as unknown as RouteMapCatalog;

// --- Value extractors (the only per-metric code) ---

/** Risk ordinal shared by icing / CAT / convective. */
const RISK_ORDER: Record<string, number> = {
  none: 0, marginal: 1, light: 1, low: 1, moderate: 2, high: 3, severe: 3, extreme: 4,
};

function worstRiskAtAlt(
  zones: Array<{ baseFt: number; topFt: number; risk: string }>,
  altFt: number,
): number {
  let worst = 0;
  for (const z of zones) {
    if (altFt >= z.baseFt && altFt <= z.topFt) {
      const r = RISK_ORDER[z.risk] ?? 0;
      if (r > worst) worst = r;
    }
  }
  return worst;
}

function sfipAtAlt(
  zones: Array<{ baseFt: number; topFt: number; meanSfip100: number | null }>,
  altFt: number,
): number | null {
  let best: number | null = null;
  for (const z of zones) {
    if (altFt >= z.baseFt && altFt <= z.topFt && z.meanSfip100 !== null) {
      if (best === null || z.meanSfip100 > best) best = z.meanSfip100;
    }
  }
  return best;
}

/** Coverage weight for cloud presence (sct=0.3, bkn=0.7, ovc=1.0). */
const COVERAGE_WEIGHT: Record<string, number> = { sct: 0.3, bkn: 0.7, ovc: 1.0 };

function cloudAtAlt(
  layers: Array<{ baseFt: number; topFt: number; coverage: string }>,
  altFt: number,
): number {
  let maxCov = 0;
  for (const l of layers) {
    if (altFt >= l.baseFt && altFt <= l.topFt) {
      const w = COVERAGE_WEIGHT[l.coverage.toLowerCase()] ?? 0.5;
      if (w > maxCov) maxCov = w;
    }
  }
  return maxCov * 100;
}

/** Temperature at an altitude from the isotherm levels (0 / −10 / −20 °C),
 *  interpolated between them and extrapolated at ~2 °C per 1000 ft. */
function tempAtAltitude(p: VizPoint, altFt: number): number | null {
  const alt = p.altitudeLines;
  const fz = alt.freezingLevelFt;
  if (fz == null) return null;
  const refs: Array<[number, number]> = [[fz, 0]];
  if (alt.minus10cLevelFt != null) refs.push([alt.minus10cLevelFt, -10]);
  if (alt.minus20cLevelFt != null) refs.push([alt.minus20cLevelFt, -20]);
  refs.sort((a, b) => a[0] - b[0]);
  if (altFt <= refs[0][0]) return refs[0][1] + (refs[0][0] - altFt) * 2 / 1000;
  const last = refs[refs.length - 1];
  if (altFt >= last[0]) return last[1] - (altFt - last[0]) * 2 / 1000;
  for (let i = 0; i < refs.length - 1; i++) {
    if (altFt >= refs[i][0] && altFt <= refs[i + 1][0]) {
      const frac = (altFt - refs[i][0]) / (refs[i + 1][0] - refs[i][0]);
      return refs[i][1] + frac * (refs[i + 1][1] - refs[i][1]);
    }
  }
  return null;
}

function agreementRank(s: string): number {
  if (s === 'poor') return 2;
  if (s === 'moderate') return 1;
  return 0;
}

type Extractor = (p: VizPoint, altFt: number) => number | null;

/** Catalog `value` → how to read it off a viz point. Same names on iOS and
 *  Android. Add one here (and there) before listing a metric in the catalog. */
export const ROUTE_VALUE_EXTRACTORS: Readonly<Record<string, Extractor>> = {
  cloud_cover_total_pct: (p) => p.cloudCoverTotalPct,
  cloud_cover_low_pct: (p) => p.cloudCoverLowPct,
  convective_risk_rank: (p) => RISK_ORDER[p.convectiveRisk] ?? 0,
  // A point without wind has windSpeedKt null while headwindKt/crosswindKt
  // read 0 (data-extract): show "no data", not calm (as Android).
  headwind_kt: (p) => (p.windSpeedKt == null ? null : Math.max(0, p.headwindKt)),
  tailwind_kt: (p) => (p.windSpeedKt == null ? null : Math.max(0, -p.headwindKt)),
  wind_speed_kt: (p) => p.windSpeedKt ?? null,
  crosswind_kt: (p) => (p.windSpeedKt == null ? null : Math.abs(p.crosswindKt)),
  cape_surface_jkg: (p) => p.capeSurfaceJkg,
  nwp_ceiling_ft: (p) => p.nwpCloudDiag?.ceilingFt ?? null,
  visibility_m: (p) => p.visibilityM ?? null,
  model_agreement_rank: (p) => agreementRank(p.worstModelAgreement),
  temperature_at_level_c: (p, alt) => tempAtAltitude(p, alt),
  icing_risk_at_level_rank: (p, alt) => worstRiskAtAlt(p.icingZones, alt),
  sfip_at_level: (p, alt) => sfipAtAlt(p.sfipZones, alt),
  cat_risk_at_level_rank: (p, alt) => worstRiskAtAlt(p.catLayers, alt),
  cloud_at_level_pct: (p, alt) => cloudAtAlt(p.cloudLayers, alt),
};

// --- Metric objects ---

/** Route-line weight range (px); `getWidth` maps a value into it. */
export const ROUTE_WEIGHT_MIN = 3;
export const ROUTE_WEIGHT_MAX = 18;

export interface LegendStop { value: number; label: string; color: string }

export interface MapMetric {
  readonly id: string;
  /** Translated label (read at call time, so a language switch applies). */
  readonly label: string;
  readonly unit: string;
  readonly group: RouteMetricGroup;
  readonly altitudeDependent: boolean;
  getValue(point: VizPoint, altitudeFt?: number): number | null;
  /** The segment's value from its two endpoints (`max` for hazards). */
  segmentValue(a: VizPoint, b: VizPoint, altitudeFt?: number): number | null;
  getColor(value: number): string;
  getWidth(value: number): number;
  formatValue(value: number): string;
  readonly legendStops: LegendStop[];
}

function translated(key: string | undefined, fallback: string): string {
  if (!key) return fallback;
  const s = t(key);
  return s && s !== key ? s : fallback;
}

/** Fraction 0 (thinnest) … 1 (thickest) for a value on a metric's width spec. */
export function widthFraction(spec: { thin_at: number; thick_at: number }, v: number): number {
  const span = spec.thick_at - spec.thin_at;
  if (span === 0) return 0;
  return Math.min(1, Math.max(0, (v - spec.thin_at) / span));
}

function formatWith(fmt: RouteFormat, v: number): string {
  if (fmt.kind === 'rank') {
    const i = Math.max(0, Math.min(fmt.labels.length - 1, Math.round(v)));
    return fmt.labels[i] ?? String(v);
  }
  const n = v * (fmt.multiply ?? 1);
  return `${fmt.prefix ?? ''}${n.toFixed(fmt.decimals)}${fmt.suffix ?? ''}`;
}

function buildMetric(spec: RouteMetricSpec, cat: RouteMapCatalog): MapMetric | null {
  const extract = ROUTE_VALUE_EXTRACTORS[spec.value];
  const scale = cat.scales.bands[spec.scale];
  if (!extract || !scale) return null;
  const color = (v: number) => bandScaleColor(scale, v);
  const getValue = (p: VizPoint, alt?: number) => extract(p, alt ?? 0);
  return {
    id: spec.key,
    get label() { return translated(spec.i18n_key, spec.label); },
    unit: spec.unit,
    group: spec.group,
    altitudeDependent: spec.uses_altitude,
    getValue,
    segmentValue(a, b, alt) {
      const va = getValue(a, alt);
      const vb = getValue(b, alt);
      if (va === null) return vb;
      if (vb === null) return va;
      return spec.aggregation === 'max' ? Math.max(va, vb) : (va + vb) / 2;
    },
    getColor: color,
    getWidth: (v) => ROUTE_WEIGHT_MIN + widthFraction(spec.width, v) * (ROUTE_WEIGHT_MAX - ROUTE_WEIGHT_MIN),
    formatValue: (v) => formatWith(spec.format, v),
    get legendStops() {
      return spec.legend.map((item) => ({
        value: item.value,
        label: translated(item.i18n_key, item.label),
        color: color(item.value),
      }));
    },
  };
}

/** Problems with a catalog, for the unit test and the module-load backstop. */
export function validateRouteMapCatalog(cat: RouteMapCatalog): string[] {
  const problems: string[] = [];
  const keys = new Set<string>();
  for (const m of cat.metrics ?? []) {
    if (keys.has(m.key)) problems.push(`duplicate metric '${m.key}'`);
    keys.add(m.key);
    if (!cat.scales?.bands?.[m.scale]) problems.push(`metric '${m.key}' → unknown scale '${m.scale}'`);
    if (!ROUTE_VALUE_EXTRACTORS[m.value]) problems.push(`metric '${m.key}' → unknown value '${m.value}'`);
    if (!m.legend?.length) problems.push(`metric '${m.key}' has no legend`);
  }
  if (!keys.has(cat.default_metric)) problems.push(`default '${cat.default_metric}' is not a metric`);
  return problems;
}

const PROBLEMS = validateRouteMapCatalog(ROUTE_MAP_CATALOG);
if (PROBLEMS.length) {
  throw new Error(`route-map-metrics-catalog.json is malformed:\n  ${PROBLEMS.join('\n  ')}`);
}

export const MAP_METRICS: readonly MapMetric[] = ROUTE_MAP_CATALOG.metrics
  .map((m) => buildMetric(m, ROUTE_MAP_CATALOG))
  .filter((m): m is MapMetric => m !== null);

/** The catalog's default metric — one place to change it for every client. */
export const DEFAULT_MAP_METRIC_ID = ROUTE_MAP_CATALOG.default_metric;

/** Colour of a segment whose value is missing. */
export const ROUTE_NO_DATA_COLOR = ROUTE_MAP_CATALOG.no_data_color;

export function getMapMetricById(id: string): MapMetric | undefined {
  return MAP_METRICS.find((m) => m.id === id);
}

/** A stored id resolved to a metric: an unknown (renamed, dropped) id falls
 *  back to the catalog default rather than drawing nothing. */
export function resolveMapMetric(id: string | null | undefined): MapMetric {
  return (id ? getMapMetricById(id) : undefined)
    ?? getMapMetricById(DEFAULT_MAP_METRIC_ID)
    ?? MAP_METRICS[0];
}

/** The SHOWING menu: groups in catalog order, each with its metrics. */
export function mapMetricGroups(): Array<{ key: RouteMetricGroup; label: string; metrics: MapMetric[] }> {
  return ROUTE_MAP_CATALOG.groups
    .map((g) => ({
      key: g.key,
      label: translated(g.i18n_key, g.label),
      metrics: MAP_METRICS.filter((m) => m.group === g.key),
    }))
    .filter((g) => g.metrics.length > 0);
}
