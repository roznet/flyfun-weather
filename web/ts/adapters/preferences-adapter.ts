/** Preferences adapter — fetch, save, and clear autorouter credentials. */

import { apiFetch, type ModelCatalogEntry } from '../utils';
import type { AdvisoryParameterDef, AdvisoryCatalogEntry } from '../types/advisories';

export type { AdvisoryParameterDef, AdvisoryCatalogEntry };

export interface FlightDefaults {
  cruise_altitude_ft: number | null;
  flight_ceiling_ft: number | null;
  models: string[] | null;
  advisory_models: string[] | null;
}

export interface DigestConfig {
  config_name: string | null;
}

export interface AdvisoryPreferences {
  enabled: Record<string, boolean> | null;
  params: Record<string, Record<string, number>> | null;
  aggregation?: 'worst' | 'majority';
}

export interface PreferencesResponse {
  defaults: FlightDefaults;
  digest_config: DigestConfig;
  advisories: AdvisoryPreferences;
  has_autorouter_creds: boolean;
  autorouter_mode: 'oauth' | 'password';
  gramet_enabled: boolean;
  llm_digest_enabled: boolean;
  icing_severity_enhance: boolean;
  icing_method: string;
  cloud_method: string;
  convective_method: string;
  locale: string;
  units_region: string;
  display_currency: string;
  synoptic_forecast_map_enabled: boolean;
  defer_email_for_model_update: boolean;
  pirep_can_view: boolean;
  pirep_can_publish: boolean;
  donations_enabled: boolean; // global: Stripe configured (gates the donate link)
}

export interface PreferencesUpdate {
  defaults?: FlightDefaults;
  digest_config?: DigestConfig;
  advisories?: AdvisoryPreferences;
  autorouter_username?: string;
  autorouter_password?: string;
  gramet_enabled?: boolean;
  llm_digest_enabled?: boolean;
  icing_severity_enhance?: boolean;
  locale?: string;
  units_region?: string;
  display_currency?: string;
  synoptic_forecast_map_enabled?: boolean;
  defer_email_for_model_update?: boolean;
}

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

export async function unlinkAutorouter(): Promise<{ linked: boolean }> {
  const resp = await fetch('/autorouter/unlink', { method: 'POST' });
  if (!resp.ok) {
    const text = await resp.text();
    throw new Error(`API ${resp.status}: ${text}`);
  }
  return resp.json();
}

export async function completeSetup(): Promise<void> {
  return apiFetch<void>('/user/preferences/setup-complete', {
    method: 'POST',
  });
}

export async function fetchAdvisoryCatalog(): Promise<AdvisoryCatalogEntry[]> {
  return apiFetch<AdvisoryCatalogEntry[]>('/user/preferences/advisories/catalog');
}

export async function fetchModelCatalog(): Promise<ModelCatalogEntry[]> {
  return apiFetch<ModelCatalogEntry[]>('/models');
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
  // Durable, all-time flag: has this user ever run a timing scan? Backs the
  // flexibility-explainer first-time gate. Count is surfaced for future use.
  time_scan_used: boolean;
  time_scan_count: number;
}

export async function fetchUsageSummary(): Promise<UsageSummary> {
  return apiFetch<UsageSummary>('/user/usage');
}
