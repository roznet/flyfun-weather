/** API adapter for climatology / patterns views (issue #155). */

import { API_BASE } from '../utils';

const apiBase = API_BASE;

// --- Shared envelope (views #2-#4) -----------------------------------------

export interface ClimatologyAirport {
  icao: string;
  lat: number;
  lon: number;
  // ``value`` is the metric being colored; null when no data.
  value: number | null;
  // Extra count fields are dataset-specific. Carried as-is for popup display.
  [extra: string]: unknown;
}

export interface ClimatologyMapEnvelope {
  month: string;
  is_mtd: boolean;
  as_of_date?: string;
  dataset: 'category' | 'phenomena' | 'wind' | 'volatility';
  metric: string;
  unit: '%' | 'kt' | 'ratio';
  airports: ClimatologyAirport[];
}

// --- View #1: Category (pre-existing shape, slightly different) ------------

export interface CategoryAirport {
  icao: string;
  lat: number;
  lon: number;
  n_obs: number;
  n_vfr: number;
  n_mvfr: number;
  n_ifr: number;
  n_lifr: number;
  pct_vfr?: number;
  pct_mvfr?: number;
  pct_ifr?: number;
  pct_lifr?: number;
}

export interface CategoryMapResponse {
  month: string;
  is_mtd: boolean;
  as_of_date?: string;
  airports: CategoryAirport[];
}

// --- View #4 leaderboard ---------------------------------------------------

export interface VolatilityRow {
  icao: string;
  n_obs: number;
  n_changes: number;
  value: number;
}

export interface VolatilityLeaderboardResponse {
  month: string;
  is_mtd: boolean;
  as_of_date?: string;
  min_n_obs: number;
  rows: VolatilityRow[];
}

// --- Fetchers --------------------------------------------------------------

export async function fetchCategoryMap(month: string): Promise<CategoryMapResponse> {
  const resp = await fetch(
    `${apiBase}/climatology/category?month=${encodeURIComponent(month)}`,
    { credentials: 'include' },
  );
  if (!resp.ok) throw new Error(`Climatology category: ${resp.status}`);
  return resp.json();
}

export async function fetchPhenomenaMap(
  month: string, kind: 'ts' | 'fog',
): Promise<ClimatologyMapEnvelope> {
  const resp = await fetch(
    `${apiBase}/climatology/phenomena?month=${encodeURIComponent(month)}&kind=${kind}`,
    { credentials: 'include' },
  );
  if (!resp.ok) throw new Error(`Climatology phenomena: ${resp.status}`);
  return resp.json();
}

export type WindMetric = 'over25' | 'p95' | 'gust';

export async function fetchWindMap(
  month: string, metric: WindMetric,
): Promise<ClimatologyMapEnvelope> {
  const resp = await fetch(
    `${apiBase}/climatology/wind?month=${encodeURIComponent(month)}&metric=${metric}`,
    { credentials: 'include' },
  );
  if (!resp.ok) {
    const body = await resp.text();
    throw new Error(`Climatology wind ${resp.status}: ${body}`);
  }
  return resp.json();
}

export async function fetchVolatilityMap(month: string): Promise<ClimatologyMapEnvelope> {
  const resp = await fetch(
    `${apiBase}/climatology/volatility?month=${encodeURIComponent(month)}`,
    { credentials: 'include' },
  );
  if (!resp.ok) throw new Error(`Climatology volatility: ${resp.status}`);
  return resp.json();
}

export async function fetchVolatilityLeaderboard(
  month: string, limit: number = 20,
): Promise<VolatilityLeaderboardResponse> {
  const resp = await fetch(
    `${apiBase}/climatology/volatility/top?month=${encodeURIComponent(month)}&limit=${limit}`,
    { credentials: 'include' },
  );
  if (!resp.ok) throw new Error(`Volatility leaderboard: ${resp.status}`);
  return resp.json();
}
