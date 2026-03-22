/** Route graph metric registry — extensible definitions for plottable values. */

import type { VizPoint } from '../types';
import { t } from '../../i18n/i18n';

export type RenderType = 'line' | 'bar';

export interface RouteGraphMetric {
  /** Unique identifier used in settings persistence. */
  readonly id: string;
  /** Display name shown in dropdowns. */
  readonly label: string;
  /** Unit string shown on the Y-axis (e.g., "kt", "°C", "mm"). */
  readonly unit: string;
  /** How to render: smooth line or filled bars. */
  readonly renderType: RenderType;
  /** Primary color for the line/bar. */
  readonly color: string;
  /** Extract the numeric value from a VizPoint. Returns null if unavailable. */
  getValue(point: VizPoint): number | null;
  /** Optional: format value for tooltip. Defaults to rounding to 1 decimal. */
  formatValue?: (v: number) => string;
  /** Optional: fixed Y-axis range [min, max]. Auto-scaled from data if omitted. */
  suggestedRange?: [number, number];
  /** Draw a reference line at y=0 (useful for head/tailwind). */
  showZeroLine?: boolean;
  /** Labels drawn above/below the zero line: [aboveLabel, belowLabel]. */
  zeroLineLabels?: [string, string];
}

/**
 * Metric registry — add new metrics here. They automatically appear in dropdowns
 * and can be rendered without any changes to the renderer or controls.
 */
export const ROUTE_GRAPH_METRICS: readonly RouteGraphMetric[] = [
  {
    id: 'headwind',
    label: 'Head/Tailwind',
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
    label: 'Crosswind',
    unit: 'kt',
    renderType: 'line',
    color: '#7c3aed',
    showZeroLine: true,
    getValue: (p) => p.crosswindKt,
    formatValue: (v) => `${Math.abs(v).toFixed(0)} kt`,
  },
  {
    id: 'temperature',
    label: 'Temperature (2m)',
    unit: '°C',
    renderType: 'line',
    color: '#dc2626',
    showZeroLine: true,
    getValue: (p) => p.temperatureC,
    formatValue: (v) => `${v.toFixed(1)}°C`,
  },
  {
    id: 'precipitation',
    label: 'Precipitation',
    unit: 'mm',
    renderType: 'bar',
    color: '#0ea5e9',
    getValue: (p) => p.precipitationMm,
    formatValue: (v) => `${v.toFixed(1)} mm`,
    suggestedRange: [0, 5],
  },
  {
    id: 'cloud-cover',
    label: 'Cloud Cover',
    unit: '%',
    renderType: 'bar',
    color: '#6b7280',
    getValue: (p) => p.cloudCoverTotalPct,
    suggestedRange: [0, 100],
    formatValue: (v) => `${Math.round(v)}%`,
  },
  {
    id: 'cape',
    label: 'CAPE',
    unit: 'J/kg',
    renderType: 'bar',
    color: '#f59e0b',
    getValue: (p) => p.capeSurfaceJkg,
    suggestedRange: [0, 1000],
    formatValue: (v) => `${Math.round(v)} J/kg`,
  },
  {
    id: 'freezing-level',
    label: 'Freezing Level',
    unit: 'ft',
    renderType: 'line',
    color: '#06b6d4',
    getValue: (p) => p.altitudeLines.freezingLevelFt,
    formatValue: (v) => `${Math.round(v).toLocaleString()} ft`,
  },
  {
    id: 'ceiling-dd',
    label: 'Ceiling DD',
    unit: 'ft AGL',
    renderType: 'line',
    color: '#8b5cf6',
    getValue: (p) => {
      if (p.soundingCeilingFt == null) return null;
      const agl = Math.max(0, p.soundingCeilingFt - p.terrainElevationFt);
      return agl > 5000 ? null : agl;
    },
    formatValue: (v) => `${Math.round(v).toLocaleString()} ft AGL`,
  },
  {
    id: 'ceiling-nwp',
    label: 'Ceiling NWP',
    unit: 'ft AGL',
    renderType: 'line',
    color: '#d946ef',
    getValue: (p) => {
      if (p.nwpCloudDiag?.ceilingFt == null) return null;
      const agl = Math.max(0, p.nwpCloudDiag.ceilingFt - p.terrainElevationFt);
      return agl > 5000 ? null : agl;
    },
    formatValue: (v) => `${Math.round(v).toLocaleString()} ft AGL`,
  },
];

/** Sentinel value for "no metric selected" (used for the optional right Y-axis). */
export const METRIC_NONE = 'none';

/** Look up a metric by id. Returns undefined for METRIC_NONE or unknown ids. */
export function getMetricById(id: string): RouteGraphMetric | undefined {
  if (id === METRIC_NONE) return undefined;
  return ROUTE_GRAPH_METRICS.find((m) => m.id === id);
}

/** Get all metric options for dropdown (including a "None" option for the right axis). */
export function getMetricOptions(includeNone: boolean): Array<{ id: string; label: string }> {
  const options: Array<{ id: string; label: string }> = [];
  if (includeNone) {
    options.push({ id: METRIC_NONE, label: t('graph.none') });
  }
  for (const m of ROUTE_GRAPH_METRICS) {
    options.push({ id: m.id, label: t('graph.' + m.id) });
  }
  return options;
}
