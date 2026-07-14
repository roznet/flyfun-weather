/** API adapter for weather overview maps. */

import { API_BASE } from '../utils';

const apiBase = API_BASE;

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
  consensus: ConsensusForecast;
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

export async function fetchAvailableHours(day: number): Promise<{ hours: number[] }> {
  const resp = await fetch(`${apiBase}/maps/forecast/hours?day=${day}`, { credentials: 'include' });
  if (!resp.ok) throw new Error(`Available hours: ${resp.status}`);
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
