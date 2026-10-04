/** API adapter for weather overview maps. */

import { API_BASE } from '../utils';

const apiBase = API_BASE;

/** The individual forecast models the airport-forecast snapshot backend
 *  populates and the full map's `fc.model` URL key accepts (gfs/icon/ecmwf).
 *  Single source shared by maps-main.ts and the briefing forecast overlay so
 *  the list can't drift across copies. */
export const FORECAST_INDIVIDUAL_MODELS: readonly string[] = ['gfs', 'icon', 'ecmwf'];

// --- Forecast types ---

/** FAA/EASA "is a destination alternate required?" flags (#249, NWP path). */
export interface AltRequired {
  faa: boolean;
  easa: boolean;
}

export interface ForecastAirport {
  icao: string;
  lat: number;
  lon: number;
  approach_type?: string | null;
  models: Record<string, ModelForecast>;
  /** Worst-mode consensus, baked server-side. */
  consensus: ConsensusForecast;
  /** Majority-mode consensus, baked server-side (#419). Optional so an older
   *  cached payload without it still decodes; `getConsensus` falls back to
   *  `consensus` when absent. */
  consensus_majority?: ConsensusForecast;
}

export interface ModelForecast {
  ceiling_ft: number | null;
  visibility_m: number | null;
  alt_required?: AltRequired;
  wind_speed_kt: number | null;
  wind_dir_deg: number | null;
  wind_gust_kt: number | null;
  crosswind_kt: number | null;
  headwind_kt: number | null;
  best_runway_id: string | null;
  gust_crosswind_kt: number | null;
  gust_headwind_kt: number | null;
  cloud_cover_pct: number | null;
  cape_jkg: number | null;
  convective_risk: string;
  temperature_c: number | null;
  flight_category: string;
}

export interface ConsensusForecast {
  flight_category: string;
  agreement: Record<string, string>;
  wind_speed_kt?: number;
  wind_dir_deg?: number;
  crosswind_kt?: number;
  headwind_kt?: number;
  ceiling_ft?: number;
  cape_jkg?: number;
  visibility_m?: number;
  cloud_cover_pct?: number;
  convective_risk?: string;
}

export interface ForecastMapResponse {
  forecast_time: string;
  model_init_times: Record<string, string>;
  airports: ForecastAirport[];
}

// --- Fetch functions ---

export async function fetchForecastMap(day: number, hour: number): Promise<ForecastMapResponse> {
  const resp = await fetch(`${apiBase}/maps/forecast?day=${day}&hour=${hour}`, { credentials: 'include' });
  if (!resp.ok) throw new Error(`Forecast map: ${resp.status}`);
  return resp.json();
}

/** What a given day actually holds. The grid is not rectangular: the far days
 *  carry fewer models (ICON's ceiling GRIB stops at 120h) and the last day
 *  fewer hours (ECMWF only delivers 6-hourly steps past 144h). */
export interface DayAvailability {
  day: number;
  date: string;
  available: boolean;
  hours: number[];
  models: string[];
}

export async function fetchAvailableDays(): Promise<{ days: DayAvailability[]; max_day: number }> {
  const resp = await fetch(`${apiBase}/maps/forecast/days`, { credentials: 'include' });
  if (!resp.ok) throw new Error(`Available days: ${resp.status}`);
  return resp.json();
}

// --- Historical map (#629) ---
//
// Every selection rule (which METAR, which TAF reading, which model run and
// valid time, why a source is empty) is decided server-side in
// `tasks/historical_map.py`; the client only picks a block and colours it.

/** A METAR as a map source: the model value keys plus the report itself. */
export interface HistoricalMetar extends Partial<ModelForecast> {
  flight_category: string;
  observation_time: string;
  age_min: number;
  report_type: string;
  raw: string | null;
  weather: string[];
  dewpoint_c: number | null;
  qnh: number | null;
}

/** A TAF read at the selected instant (prevailing + worst temporary group). */
export interface HistoricalTaf extends Partial<Omit<ModelForecast, 'flight_category'>> {
  flight_category: string | null;
  prevailing_category: string | null;
  temporary_category: string | null;
  temporary_type: string | null;
  trend_type: string | null;
  significant_weather: string[];
  issue_time: string | null;
  raw: string;
}

export interface HistoricalModelForecast extends ModelForecast {
  valid_time: string;
  model_init_time: string;
}

export interface HistoricalAirport extends Omit<ForecastAirport, 'models' | 'consensus' | 'consensus_majority'> {
  models: Record<string, HistoricalModelForecast>;
  /** Consensus over the NWP models only; null when no model has data. */
  consensus: ConsensusForecast | null;
  consensus_majority: ConsensusForecast | null;
  observed: { metar?: HistoricalMetar; taf?: HistoricalTaf };
}

/** Per-source provenance: what it is based on, or why it has nothing. */
export interface HistoricalSourceInfo {
  available: boolean;
  /** 'beyond_horizon' | 'no_valid_time' | 'no_run' (models only). */
  reason?: string;
  count?: number;
  max_age_min?: number;
  valid_time?: string;
  model_init_time?: string;
  fetched_at?: string;
  lead_hours?: number;
}

export interface HistoricalMapResponse {
  at: string;
  lead_days: number;
  run_cutoff: string;
  sources: Record<string, HistoricalSourceInfo>;
  airports: HistoricalAirport[];
}

export interface HistoricalRange {
  latest: string;
  earliest_observation: string | null;
  earliest_model: string | null;
  step_minutes: number;
  model_sample_hours: number[];
  metar_max_age_min: number;
  model_max_age_min: number;
  models: string[];
  observed_sources: string[];
  leads: Array<{ lead_days: number; models: string[] }>;
}

export async function fetchHistoricalMap(at: string, lead: number): Promise<HistoricalMapResponse> {
  const params = new URLSearchParams({ at, lead: String(lead) });
  const resp = await fetch(`${apiBase}/maps/historical?${params}`, { credentials: 'include' });
  if (!resp.ok) throw new Error(`Historical map: ${resp.status}`);
  return resp.json();
}

export async function fetchHistoricalRange(): Promise<HistoricalRange> {
  const resp = await fetch(`${apiBase}/maps/historical/range`, { credentials: 'include' });
  if (!resp.ok) throw new Error(`Historical range: ${resp.status}`);
  return resp.json();
}

/** `/maps/now` (#656): the latest METAR per airport right now — the
 *  historical airport shape with only `observed.metar`, read at the current
 *  instant rather than the 30-min grid. */
export interface NowMapResponse {
  at: string;
  sources: { metar: HistoricalSourceInfo };
  airports: HistoricalAirport[];
}

export async function fetchNowMap(): Promise<NowMapResponse> {
  const resp = await fetch(`${apiBase}/maps/now`, { credentials: 'include' });
  if (!resp.ok) throw new Error(`Now map: ${resp.status}`);
  return resp.json();
}
