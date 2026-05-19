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

// --- Leaderboard (Top airports sub-tab) ------------------------------------

export interface LeaderboardRow {
  icao: string;
  n_obs: number;
  value: number | null;
  extra: Record<string, unknown>;
}

export interface LeaderboardResponse {
  month: string;
  is_mtd: boolean;
  as_of_date?: string;
  dataset: string;
  sub: string | null;
  unit: '%' | 'kt' | 'ratio';
  label: string;
  min_n_obs: number;
  rows: LeaderboardRow[];
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

export async function fetchLeaderboard(
  month: string, dataset: string, sub: string | null, limit: number = 20,
): Promise<LeaderboardResponse> {
  const params = new URLSearchParams({ month, dataset, limit: String(limit) });
  if (sub) params.set('sub', sub);
  const resp = await fetch(
    `${apiBase}/climatology/leaderboard?${params.toString()}`,
    { credentials: 'include' },
  );
  if (!resp.ok) {
    const body = await resp.text();
    throw new Error(`Leaderboard ${resp.status}: ${body}`);
  }
  return resp.json();
}
