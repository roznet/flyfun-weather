/** Preferences adapter — fetch, save, and clear autorouter credentials. */

export interface FlightDefaults {
  cruise_altitude_ft: number | null;
  flight_ceiling_ft: number | null;
  models: string[] | null;
}

export interface DigestConfig {
  config_name: string | null;
}

export interface PreferencesResponse {
  defaults: FlightDefaults;
  digest_config: DigestConfig;
  has_autorouter_creds: boolean;
}

export interface PreferencesUpdate {
  defaults?: FlightDefaults;
  digest_config?: DigestConfig;
  autorouter_username?: string;
  autorouter_password?: string;
}

import { apiFetch } from '../utils';

export async function fetchPreferences(): Promise<PreferencesResponse> {
  return apiFetch<PreferencesResponse>('/user/preferences');
}

export async function savePreferences(update: PreferencesUpdate): Promise<PreferencesResponse> {
  return apiFetch<PreferencesResponse>('/user/preferences', {
    method: 'PUT',
    body: JSON.stringify(update),
  });
}

export async function clearAutorouterCreds(): Promise<void> {
  return apiFetch<void>('/user/preferences/autorouter', {
    method: 'DELETE',
  });
}

// --- Usage ---

export interface ServiceUsage {
  used: number;
  limit: number;
}

export interface TodayUsage {
  briefings: number;
  open_meteo: ServiceUsage;
  gramet: ServiceUsage;
  llm_digest: ServiceUsage;
}

export interface MonthUsage {
  briefings: number;
  gramet: number;
  llm_digest: number;
  total_tokens: number;
}

export interface UsageSummary {
  today: TodayUsage;
  month: MonthUsage;
}

export async function fetchUsageSummary(): Promise<UsageSummary> {
  return apiFetch<UsageSummary>('/user/usage');
}
