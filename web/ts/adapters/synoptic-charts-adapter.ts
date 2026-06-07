/** Adapter for the flight-independent /api/synoptic-charts endpoints, used by
 * the Synoptic Forecast tab to render a DWD / Met Office chart as the basemap
 * under the Hewson overlay. */

import type { ChartProjectionKey } from '../visualization/chart-projection-constants';

export interface SynopticChartEntry {
  id: string;
  offset_h: number;
  chart_type: string; // 'analysis' | 'icon' | 'colour'
  native_size: [number, number] | null;
  valid_time: string | null; // ISO 8601 Z
}

export interface SynopticChartSource {
  slug: string; // 'dwd' | 'metoffice'
  label: string;
  run_cycle: string;
  issued_at: string | null;
  attribution_html: string;
  charts: SynopticChartEntry[];
}

export interface SynopticChartsManifest {
  sources: SynopticChartSource[];
}

export async function fetchSynopticChartsManifest(): Promise<SynopticChartsManifest> {
  const resp = await fetch('/api/synoptic-charts/manifest', { credentials: 'include' });
  if (!resp.ok) throw new Error(`synoptic-charts manifest HTTP ${resp.status}`);
  return resp.json();
}

/** Bytes URL for a specific (source, run_cycle, chart_id). */
export function synopticChartUrl(slug: string, runCycle: string, chartId: string): string {
  return `/api/synoptic-charts/${encodeURIComponent(slug)}/${encodeURIComponent(runCycle)}/${encodeURIComponent(chartId)}`;
}

/** The ChartProjectionKey for a source+chart pair, matching the generated
 * constants ('dwd-analysis' | 'dwd-icon' | 'metoffice-colour'). */
export function projectionKeyFor(slug: string, chartType: string): ChartProjectionKey {
  return `${slug}-${chartType}` as ChartProjectionKey;
}

/** Pick the chart whose valid time is closest to `targetMs`. Tie-break toward
 * the earlier offset (matches the server's select_default_chart_id). Returns
 * null when the source has no charts. */
export function pickChartForValidTime(
  source: SynopticChartSource,
  targetMs: number,
): { chart: SynopticChartEntry; gapHours: number } | null {
  if (!source.charts.length) return null;
  let best: SynopticChartEntry | null = null;
  let bestGapMs = Infinity;
  for (const c of source.charts) {
    if (!c.valid_time) continue;
    const gap = Math.abs(new Date(c.valid_time).getTime() - targetMs);
    if (gap < bestGapMs || (gap === bestGapMs && best !== null && c.offset_h < best.offset_h)) {
      best = c;
      bestGapMs = gap;
    }
  }
  if (!best) return null;
  return { chart: best, gapHours: bestGapMs / 3_600_000 };
}
