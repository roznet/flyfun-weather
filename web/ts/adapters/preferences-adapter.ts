/** Preferences adapter — fetch, save, and clear autorouter credentials. */

import { apiFetch, type BookingConfig, type ModelCatalogEntry } from '../utils';
import type {
  AdvisoryParameterDef,
  AdvisoryCatalogEntry,
  AdvisoryCategory,
  AdvisoryCatalogResponse,
  Interview,
} from '../types/advisories';

export type { AdvisoryParameterDef, AdvisoryCatalogEntry, AdvisoryCategory, AdvisoryCatalogResponse, Interview };

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
  // Briefing-refresh notifications (ios-app-briefing-notifications.md).
  notify_email: boolean;
  notify_push: boolean;
  notify_scope: 'auto' | 'all' | 'off';
  notify_change_only: boolean;
  // One-time fail-safe notice: email was auto-re-enabled after the user's last
  // push device was removed while email was off (dismiss with notify_decay_notice=false).
  notify_decay_notice: boolean;
  // Registered APNs device count — push is only actionable with ≥1 device.
  push_device_count: number;
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
  notify_email?: boolean;
  notify_push?: boolean;
  notify_scope?: 'auto' | 'all' | 'off';
  notify_change_only?: boolean;
  notify_decay_notice?: boolean; // only meaningful as false, to dismiss the notice
}

/**
 * "Briefing updates" 3-stop — folds notify_scope + notify_change_only into one
 * control (ios-app-briefing-notifications.md). Off = never; changes = only when
 * the assessment/outlook moves; every = every completion. Kept identical to the
 * iOS fold so the two clients stay consistent.
 */
export type BriefingUpdates = 'off' | 'changes' | 'every';

export function foldBriefingUpdates(scope: string, changeOnly: boolean): BriefingUpdates {
  if (scope === 'off') return 'off';
  return changeOnly ? 'changes' : 'every';
}

export function unfoldBriefingUpdates(
  v: BriefingUpdates,
): { notify_scope: 'all' | 'off'; notify_change_only: boolean } {
  if (v === 'off') return { notify_scope: 'off', notify_change_only: true };
  return { notify_scope: 'all', notify_change_only: v === 'changes' };
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

/**
 * Fetch the advisory catalog (#387). The server returns advisories already in
 * display order plus an ordered category list. Tolerates the legacy bare-array
 * response (pre-#387 server) by synthesizing an empty category list, so the
 * settings page falls back to grouping by first-seen category.
 */
export async function fetchAdvisoryCatalog(): Promise<AdvisoryCatalogResponse> {
  const resp = await apiFetch<AdvisoryCatalogResponse | AdvisoryCatalogEntry[]>(
    '/user/preferences/advisories/catalog',
  );
  if (Array.isArray(resp)) {
    return { advisories: resp, categories: [] };
  }
  return resp;
}

/** Fetch the declarative setup-interview structure (#387, slice 3). */
export async function fetchAdvisoryInterview(): Promise<Interview> {
  return apiFetch<Interview>('/user/preferences/advisories/interview');
}

export async function fetchModelCatalog(): Promise<ModelCatalogEntry[]> {
  return apiFetch<ModelCatalogEntry[]>('/models');
}

export async function fetchBookingConfig(): Promise<BookingConfig> {
  return apiFetch<BookingConfig>('/models/config');
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
