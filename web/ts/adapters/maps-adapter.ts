/** API adapter for weather overview maps. */

import { API_BASE } from '../utils';

const apiBase = API_BASE;

// --- Forecast types ---

export interface ForecastAirport {
  icao: string;
  lat: number;
  lon: number;
  models: Record<string, ModelForecast>;
  consensus: ConsensusForecast;
}

export interface ModelForecast {
  ceiling_ft: number | null;
  visibility_m: number | null;
  wind_speed_kt: number | null;
  wind_dir_deg: number | null;
  wind_gust_kt: number | null;
  cloud_cover_pct: number | null;
  cape_jkg: number | null;
  convective_risk: string;
  temperature_c: number | null;
  flight_category: string;
}

export interface ConsensusForecast {
  flight_category: string;
  agreement: string;
  wind_speed_kt?: number;
  wind_dir_deg?: number;
  ceiling_ft?: number;
  cape_jkg?: number;
  visibility_m?: number;
}

export interface ForecastMapResponse {
  forecast_time: string;
  model_init_times: Record<string, string>;
  airports: ForecastAirport[];
}

// --- Verification types ---

export interface VerificationAirport {
  icao: string;
  lat: number;
  lon: number;
  sample_count: number;
  category_match_pct: number;
  ceiling_mae_ft: number;
  wind_mae_kt: number;
  temp_mae_c: number;
  vis_mae_m: number;
  ceiling_bias_ft: number;
}

export interface VerificationMapResponse {
  period_since: string;
  period_until: string;
  model: string;
  days_out: number;
  airports: VerificationAirport[];
}

// --- Fetch functions ---

export async function fetchForecastMap(day: number, hour: number, mode: string = 'worst'): Promise<ForecastMapResponse> {
  const resp = await fetch(`${apiBase}/maps/forecast?day=${day}&hour=${hour}&mode=${mode}`, { credentials: 'include' });
  if (!resp.ok) throw new Error(`Forecast map: ${resp.status}`);
  return resp.json();
}

export async function fetchVerificationMap(
  period: string, model: string, daysOut: number,
): Promise<VerificationMapResponse> {
  const resp = await fetch(
    `${apiBase}/maps/verification?period=${period}&model=${model}&days_out=${daysOut}`,
    { credentials: 'include' },
  );
  if (!resp.ok) throw new Error(`Verification map: ${resp.status}`);
  return resp.json();
}

export async function fetchAvailableHours(day: number): Promise<{ hours: number[] }> {
  const resp = await fetch(`${apiBase}/maps/forecast/hours?day=${day}`, { credentials: 'include' });
  if (!resp.ok) throw new Error(`Available hours: ${resp.status}`);
  return resp.json();
}
