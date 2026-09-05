/** Route graph metric registry — extensible definitions for plottable values. */

import type { VizPoint } from '../types';
import { t } from '../../i18n/i18n';
import { getUnitsRegion, qnhUnitLabel, qnhDisplayValue } from '../../units';

export type RenderType = 'line' | 'bar';

export interface RouteGraphMetric {
  /** Unique identifier used in settings persistence. */
  readonly id: string;
  /** Unit string shown on the Y-axis (e.g., "kt", "°C", "mm"). */
  readonly unit: string;
  /** How to render: smooth line or filled bars. */
  readonly renderType: RenderType;
  /** Primary color for the line/bar. */
  readonly color: string;
  /**
   * Extract the numeric value from a VizPoint. Returns null — and *only* null —
   * when the value is genuinely unavailable. A known value that happens to sit
   * off the top of the axis is not null; see `aboveScale`.
   */
  getValue(point: VizPoint): number | null;
  /** Optional: format value for tooltip. Defaults to rounding to 1 decimal. */
  formatValue?: (v: number) => string;
  /** Optional: fixed Y-axis range [min, max]. Auto-scaled from data if omitted. */
  suggestedRange?: [number, number];
  /**
   * Treat values above `suggestedRange`'s max as a distinct *above-scale* state
   * instead of data: the axis is pinned to the range exactly (no padding, no
   * expansion, so the top tick *is* the cap) and such points render as a capped
   * marker on the top edge rather than a gap. Requires `suggestedRange`.
   *
   * This is a presentation state, not a claim about the weather — the value is
   * known and is never clipped or rewritten, it just doesn't fit the axis.
   */
  aboveScale?: boolean;
  /**
   * Optional: true when the sensor behind this metric does not cover the
   * point at all (#574). Only observed metrics implement it; forecast metrics
   * have no such state and leave it undefined.
   */
  isNoCoverage?: (point: VizPoint) => boolean;
  /** Draw a reference line at y=0 (useful for head/tailwind). */
  showZeroLine?: boolean;
  /** Labels drawn above/below the zero line: [aboveLabel, belowLabel]. */
  zeroLineLabels?: [string, string];
}

/**
 * Display cap for the ceiling metrics, in ft AGL. Ceilings above this are of no
 * practical interest to plot point-by-point, so the axis stops here and higher
 * ceilings render as above-scale. Purely a display bound — it does not change
 * any meteorological value or threshold.
 */
const CEILING_AGL_CAP_FT = 5000;

/**
 * Metric registry — add new metrics here. They automatically appear in dropdowns
 * and can be rendered without any changes to the renderer or controls.
 */
export const ROUTE_GRAPH_METRICS: readonly RouteGraphMetric[] = [
  // --- Observed (#574) ---
  // Measurements, not forecasts. They sit alongside their modelled siblings
  // (`precipitation`) on purpose: putting the two on the same axis is how a
  // pilot sees where the model and the radar disagree, and phase 1 leaves
  // that judgement to them rather than computing a verdict.
  {
    id: 'observed-rain-rate',
    unit: 'mm/h',
    renderType: 'bar',
    color: '#0891b2',
    getValue: (p) => p.observedRateMmH,
    isNoCoverage: (p) => p.observedRadarNoCoverage,
    formatValue: (v) => `${v.toFixed(1)} mm/h`,
  },
  {
    id: 'observed-flash-rate',
    unit: '/1000km²/min',
    renderType: 'bar',
    color: '#7c3aed',
    getValue: (p) => p.observedFlashRate,
    formatValue: (v) => (v === 0 ? 'none' : v.toFixed(2)),
  },
  {
    id: 'headwind',
    unit: 'kt',
    renderType: 'line',
    color: '#2563eb',
    showZeroLine: true,
    get zeroLineLabels(): [string, string] { return [t('graph.headwindUp'), t('graph.tailwindDown')]; },
    getValue: (p) => p.headwindKt,
    formatValue: (v) => {
      const abs = Math.abs(v).toFixed(0);
      return v >= 0 ? `${abs} kt HW` : `${abs} kt TW`;
    },
  },
  {
    id: 'crosswind',
    unit: 'kt',
    renderType: 'line',
    color: '#7c3aed',
    showZeroLine: true,
    getValue: (p) => p.crosswindKt,
    formatValue: (v) => `${Math.abs(v).toFixed(0)} kt`,
  },
  {
    id: 'temperature',
    unit: '°C',
    renderType: 'line',
    color: '#dc2626',
    showZeroLine: true,
    getValue: (p) => p.temperatureC,
    formatValue: (v) => `${v.toFixed(1)}°C`,
  },
  {
    id: 'isa-dev',
    unit: '°C',
    renderType: 'line',
    color: '#ea580c',
    // ISA deviation at the elected cruise level (actual − ISA standard).
    // Zero line = on-ISA; above = warmer than standard (higher density
    // altitude, degraded TAS/climb), below = colder. Useful for performance.
    showZeroLine: true,
    get zeroLineLabels(): [string, string] { return [t('graph.isaWarmer'), t('graph.isaColder')]; },
    getValue: (p) => p.isaDevC,
    // Derive the sign from the *rounded* value so a small negative deviation
    // (e.g. −0.3) reads "ISA±0", never "ISA−0"; same guard for the °C part.
    formatValue: (v) => {
      const dev = Math.round(v);
      const isa = dev === 0 ? '±0' : `${dev > 0 ? '+' : '−'}${Math.abs(dev)}`;
      const c = Math.abs(v).toFixed(1);
      const cSign = c === '0.0' ? '±' : v > 0 ? '+' : '−';
      return `ISA${isa} (${cSign}${c}°C)`;
    },
  },
  {
    id: 'precipitation',
    unit: 'mm',
    renderType: 'bar',
    color: '#0ea5e9',
    getValue: (p) => p.precipitationMm,
    formatValue: (v) => `${v.toFixed(1)} mm`,
    suggestedRange: [0, 5],
  },
  {
    id: 'cloud-cover',
    unit: '%',
    renderType: 'bar',
    color: '#6b7280',
    getValue: (p) => p.cloudCoverTotalPct,
    suggestedRange: [0, 100],
    formatValue: (v) => `${Math.round(v)}%`,
  },
  {
    id: 'cape',
    unit: 'J/kg',
    renderType: 'bar',
    color: '#f59e0b',
    getValue: (p) => p.capeSurfaceJkg,
    suggestedRange: [0, 1000],
    formatValue: (v) => `${Math.round(v)} J/kg`,
  },
  {
    id: 'cin',
    unit: 'J/kg',
    renderType: 'bar',
    color: '#0d9488',
    // CIN is convention-negative (energy that inhibits convection), so bars
    // hang below the zero line — the inhibition "cap" reading next to CAPE.
    // zero sits at the top of the range, so draw it explicitly for reference.
    showZeroLine: true,
    getValue: (p) => p.cinSurfaceJkg,
    suggestedRange: [-300, 0],
    formatValue: (v) => `${Math.round(v)} J/kg`,
  },
  {
    id: 'qnh',
    // Region-aware unit (hPa for Europe, inHg for the US). The getter is read
    // at render time so the axis label matches getValue's converted units.
    get unit(): string { return qnhUnitLabel(); },
    renderType: 'line',
    color: '#475569',
    // VizPoint carries canonical hPa; convert to the display region's units so
    // axis ticks/scale and the value agree (hPa ~1013, inHg ~29.92).
    getValue: (p) => (p.qnhHpa == null ? null : qnhDisplayValue(p.qnhHpa)),
    formatValue: (v) =>
      getUnitsRegion() === 'us' ? `${v.toFixed(2)} inHg` : `${Math.round(v)} hPa`,
  },
  {
    id: 'freezing-level',
    unit: 'ft',
    renderType: 'line',
    color: '#06b6d4',
    getValue: (p) => p.altitudeLines.freezingLevelFt,
    formatValue: (v) => `${Math.round(v).toLocaleString()} ft`,
  },
  {
    id: 'ceiling-dd',
    unit: 'ft AGL',
    renderType: 'line',
    color: '#8b5cf6',
    // A ceiling well above the route is the best possible news, so it must not
    // return null — that is reserved for "no sounding", and the two used to be
    // indistinguishable (gap in the line, tooltip "N/A"). Above the cap is an
    // above-scale state instead; see `aboveScale`.
    getValue: (p) => {
      if (p.soundingCeilingFt == null) return null;
      return Math.max(0, p.soundingCeilingFt - p.terrainElevationFt);
    },
    formatValue: (v) => `${Math.round(v).toLocaleString()} ft AGL`,
    suggestedRange: [0, CEILING_AGL_CAP_FT],
    aboveScale: true,
  },
  {
    id: 'ceiling-nwp',
    unit: 'ft AGL',
    renderType: 'line',
    color: '#d946ef',
    getValue: (p) => {
      if (p.nwpCloudDiag?.ceilingFt == null) return null;
      return Math.max(0, p.nwpCloudDiag.ceilingFt - p.terrainElevationFt);
    },
    formatValue: (v) => `${Math.round(v).toLocaleString()} ft AGL`,
    suggestedRange: [0, CEILING_AGL_CAP_FT],
    aboveScale: true,
  },
];

/**
 * A metric's value at one route point, in three states rather than two.
 *
 * `unavailable` means we have no value. `above-scale` means we have one and it
 * is off the top of the axis — the distinction the chart previously collapsed,
 * rendering "ceiling is excellent" identically to "no data".
 */
export type MetricSample =
  | { readonly kind: 'value'; readonly value: number; readonly partialCoverage?: boolean }
  | { readonly kind: 'above-scale'; readonly value: number }
  | { readonly kind: 'unavailable' }
  // The sensor does not look here (#574). Distinct from `unavailable`, which
  // is "we have no number", and emphatically distinct from `value: 0`: a
  // radar coverage hole rendered as a gap reads as "no rain", and about half
  // the OPERA grid is such a hole.
  | { readonly kind: 'no-coverage' };

const UNAVAILABLE_SAMPLE: MetricSample = { kind: 'unavailable' };
const NO_COVERAGE_SAMPLE: MetricSample = { kind: 'no-coverage' };

/**
 * Classify a metric's value at a point. The single place the three states are
 * derived — renderer, axes and tooltip all read this rather than re-deriving
 * the cap, so they cannot drift apart.
 */
export function sampleMetric(metric: RouteGraphMetric, point: VizPoint): MetricSample {
  // Checked before the value: for an observed metric a missing number means
  // "the sensor saw nothing" only when it was looking, and the metric alone
  // knows which of the two it is.
  const v = metric.getValue(point);
  if (metric.isNoCoverage?.(point)) {
    return v != null && v > 0 ? { kind: 'value', value: v, partialCoverage: true } : NO_COVERAGE_SAMPLE;
  }
  if (v === null) return UNAVAILABLE_SAMPLE;
  if (metric.aboveScale && metric.suggestedRange && v > metric.suggestedRange[1]) {
    return { kind: 'above-scale', value: v };
  }
  return { kind: 'value', value: v };
}

/**
 * Tooltip text for a sample, unit-aware via the metric's own `formatValue`.
 * Above-scale reads "> <cap>"; only a genuinely absent value reads "N/A".
 */
export function formatSample(metric: RouteGraphMetric, sample: MetricSample): string {
  const fmt = (v: number): string => (metric.formatValue ? metric.formatValue(v) : v.toFixed(1));
  switch (sample.kind) {
    case 'value':
      return fmt(sample.value) + (sample.partialCoverage ? ' (partial coverage)' : '');
    case 'above-scale':
      // Formatted from the cap, not the value: we are reporting the axis limit
      // we can show, not disclosing a number we declined to plot.
      return `> ${fmt(metric.suggestedRange![1])}`;
    case 'unavailable':
      return 'N/A';
    case 'no-coverage':
      return t('graph.noCoverage');
  }
}

/** Sentinel value for "no metric selected" (used for the optional right Y-axis). */
export const METRIC_NONE = 'none';

/** Look up a metric by id. Returns undefined for METRIC_NONE or unknown ids. */
export function getMetricById(id: string): RouteGraphMetric | undefined {
  if (id === METRIC_NONE) return undefined;
  return ROUTE_GRAPH_METRICS.find((m) => m.id === id);
}

/**
 * Localized display name for a metric (used by both the dropdown and the
 * hover tooltip). Most metrics map straight to their `graph.<id>` i18n key;
 * QNH is region-aware — "Altimeter" in the US (inHg) vs "QNH" in Europe (hPa),
 * matching the unit shown on its axis.
 */
export function getMetricLabel(id: string): string {
  if (id === 'qnh') {
    return getUnitsRegion() === 'us' ? t('graph.altimeter') : t('graph.qnh');
  }
  return t('graph.' + id);
}

/** Get all metric options for dropdown (including a "None" option for the right axis). */
export function getMetricOptions(includeNone: boolean): Array<{ id: string; label: string }> {
  const options: Array<{ id: string; label: string }> = [];
  if (includeNone) {
    options.push({ id: METRIC_NONE, label: t('graph.none') });
  }
  for (const m of ROUTE_GRAPH_METRICS) {
    options.push({ id: m.id, label: getMetricLabel(m.id) });
  }
  return options;
}
