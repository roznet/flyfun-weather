/** API adapter for the Hewson synoptic map (/api/hewson-map). */

import { API_BASE } from '../utils';

const apiBase = API_BASE;

// --- Types ---

export interface HewsonManifestSnapshot {
  init_time: string;          // ISO 8601 with Z
  init_time_unix: number;
  levels: number[];
  stride_hours: number;
  valid_times: string[];      // length = n_hours
  n_hours: number;
  lat_min: number;
  lat_max: number;
  lon_min: number;
  lon_max: number;
  n_lat: number;
  n_lon: number;
}

export interface HewsonManifest {
  models: Record<string, HewsonManifestSnapshot[]>;
}

export interface HewsonSlice {
  model: string;
  init_time: string;
  valid_time: string;
  level: number;
  metric: string;
  hour: number;
  stride_hours: number;
  lat: number[];                       // n_lat
  lon: number[];                       // n_lon
  values: (number | null)[][];         // (n_lat, n_lon)
}

export interface HewsonSliceParams {
  model: string;
  init: string;        // ISO 8601 with Z
  level: number;
  metric: string;
  hour: number;
}

export interface HewsonAllMetricsSlice {
  model: string;
  init_time: string;
  valid_time: string;
  level: number;
  hour: number;
  stride_hours: number;
  lat: number[];
  lon: number[];
  /** Per-metric (n_lat, n_lon) grid. Cells outside the grid mask come back as null. */
  metrics: Record<string, (number | null)[][]>;
  /** Metrics that weren't present in this snapshot (e.g. legacy snapshots). */
  missing_metrics: string[];
}

export interface HewsonAllMetricsParams {
  model: string;
  init: string;
  level: number;
  hour: number;
}

// --- Fetch functions ---

export async function fetchHewsonManifest(): Promise<HewsonManifest> {
  const resp = await fetch(`${apiBase}/hewson-map/manifest`, { credentials: 'include' });
  if (!resp.ok) throw new Error(`Hewson manifest: ${resp.status}`);
  return resp.json();
}

export async function fetchHewsonSlice(p: HewsonSliceParams): Promise<HewsonSlice> {
  const qs = new URLSearchParams({
    model: p.model,
    init: p.init,
    level: String(p.level),
    metric: p.metric,
    hour: String(p.hour),
  });
  const resp = await fetch(`${apiBase}/hewson-map?${qs}`, { credentials: 'include' });
  if (!resp.ok) {
    let detail: string | undefined;
    try { detail = (await resp.json())?.detail; } catch { /* ignore */ }
    throw new Error(detail ?? `Hewson slice: ${resp.status}`);
  }
  return resp.json();
}

export async function fetchHewsonAllMetrics(
  p: HewsonAllMetricsParams,
): Promise<HewsonAllMetricsSlice> {
  const qs = new URLSearchParams({
    model: p.model,
    init: p.init,
    level: String(p.level),
    hour: String(p.hour),
  });
  const resp = await fetch(
    `${apiBase}/hewson-map/all-metrics?${qs}`, { credentials: 'include' },
  );
  if (!resp.ok) {
    let detail: string | undefined;
    try { detail = (await resp.json())?.detail; } catch { /* ignore */ }
    throw new Error(detail ?? `Hewson all-metrics: ${resp.status}`);
  }
  return resp.json();
}
