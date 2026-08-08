/** Admin adapter — user management and agent API calls. */

import { apiFetch } from '../utils';

export interface AdminUserUsage {
  briefings: number;
  gramet: number;
  llm_digest: number;
  total_tokens: number;
}

export interface AdminUser {
  id: string;
  email: string;
  display_name: string;
  provider: string;
  type: 'human' | 'agent';
  approved: boolean;
  created_at: string | null;
  last_login_at: string | null;
  last_active_at: string | null;
  usage: AdminUserUsage;
  disk_usage_bytes: number;
  // Agent-only fields
  token_count?: number;
  active_tokens?: number;
  token_last_used?: string | null;
}

export interface AdminSummary {
  /** Every account ever created — not windowed by ``period``. */
  total_users: number;
  /** Accounts that generated at least one briefing within ``period``. */
  active_users: number;
  total_briefings: number;
  total_tokens: number;
  total_disk_bytes: number;
}

export type AdminPeriod = '30d' | 'all';

export interface AdminUsersResponse {
  period: AdminPeriod;
  summary: AdminSummary;
  total_humans: number;
  users: AdminUser[];
}

export interface CreateAgentResponse {
  user_id: string;
  token: string;
  name: string;
}

export interface CreateTokenResponse {
  token: string;
  token_id: number;
}

export async function fetchAdminUsers(period: AdminPeriod = '30d', limit = 25, offset = 0): Promise<AdminUsersResponse> {
  return apiFetch<AdminUsersResponse>(`/admin/users?period=${period}&limit=${limit}&offset=${offset}`);
}

export async function approveUser(userId: string): Promise<void> {
  await apiFetch<unknown>(`/admin/users/${userId}/approve`, { method: 'POST' });
}

export async function createAgent(name: string): Promise<CreateAgentResponse> {
  return apiFetch<CreateAgentResponse>('/admin/agents', {
    method: 'POST',
    body: JSON.stringify({ name }),
  });
}

export async function createAgentToken(userId: string, name?: string): Promise<CreateTokenResponse> {
  return apiFetch<CreateTokenResponse>(`/admin/agents/${userId}/tokens`, {
    method: 'POST',
    body: JSON.stringify({ name: name || '' }),
  });
}

export async function revokeAgent(userId: string): Promise<void> {
  await apiFetch<unknown>(`/admin/agents/${userId}`, { method: 'DELETE' });
}

export async function revokeAgentToken(userId: string, tokenId: number): Promise<void> {
  await apiFetch<unknown>(`/admin/agents/${userId}/tokens/${tokenId}`, { method: 'DELETE' });
}

// --- Connected apps (dynamically-registered OAuth clients) ---

export interface ConnectedApp {
  name: string;
  scopes: string[];
  /** Distinct users with at least one active (non-revoked) token. */
  users: number;
  /** Distinct users ever connected, including revoked-only. */
  users_total: number;
  tokens_active: number;
  tokens_total: number;
  /** Number of OAuth client registrations merged under this app name. */
  registrations: number;
  last_used: string | null;
  registered: string | null;
}

export interface ConnectedAppsResponse {
  apps: ConnectedApp[];
}

export async function fetchConnectedApps(): Promise<ConnectedAppsResponse> {
  return apiFetch<ConnectedAppsResponse>('/admin/connected-apps');
}

// --- User Costs ---

export interface UserCostUser {
  id: string;
  email: string;
  display_name: string;
  approved: boolean;
  provider: string;
  created_at: string | null;
  last_login_at: string | null;
  last_active_at: string | null;
}

export interface UserCostSummary {
  cost_this_week_usd: number;
  cost_this_month_usd: number;
  total_cost_usd: number;
  total_briefings: number;
  avg_cost_per_briefing_usd: number;
}

export interface UserCostTransaction {
  id: number;
  timestamp: string;
  cost_usd: number;
  category: string;
  description: string;
  breakdown: Record<string, number> | null;
  flight_id: string | null;
}

export interface UserCostFlight {
  flight_id: string;
  route_name: string;
  target_date: string;
  target_time_utc: number;
  cruise_altitude_ft: number;
  created_at: string | null;
}

export interface UserCostBreakdown {
  token_cost_usd: number;
  infra_share_usd: number;
  subscription_share_usd: number;
  storage_cost_usd: number;
  margin_usd: number;
  total_usd: number;
  /** What prompt caching saved. Absent when every charged briefing predates
   *  the field — absent means unknown, not a measured $0.00. Already inside
   *  token_cost_usd, so never add it to a total. */
  cache_saving_usd?: number;
}

export interface UserCostsResponse {
  user: UserCostUser;
  summary: UserCostSummary;
  transactions: UserCostTransaction[];
  recent_flights: UserCostFlight[];
  cost_breakdown: UserCostBreakdown;
}

export async function fetchUserCosts(userId: string, limit = 50): Promise<UserCostsResponse> {
  return apiFetch<UserCostsResponse>(`/admin/users/${encodeURIComponent(userId)}/costs?limit=${limit}`);
}

// --- Cost config + program cost report ---

export interface CostConfigData {
  token_cost_per_1k_input: number;
  token_cost_per_1k_output: number;
  droplet_monthly_usd: number;
  misc_monthly_usd: number;
  subscriptions_monthly_usd: number;
  subscription_details: Record<string, number> | null;
  disk_cost_per_gb_monthly: number;
  estimated_monthly_briefings: number;
  margin_percent: number;
}

export interface CostConfigVersion {
  id: number;
  active_from: string;
  active_until: string | null;
  config: CostConfigData;
}

export interface CostReportFixedLine {
  label: string;
  monthly_usd: number;
  prorated_usd: number;
}

export interface CostReport {
  window_days: number;
  fixed_lines: CostReportFixedLine[];
  fixed_monthly_usd: number;
  fixed_prorated_usd: number;
  variable_token_usd: number;
  variable_storage_usd: number;
  variable_usd: number;
  /** Prompt-cache saving over the window. `null` when no ledger row in the
   *  window records it (all pre-date the field); already inside
   *  variable_token_usd, so it is reporting-only. */
  cache_saving_usd?: number | null;
  subtotal_usd: number;
  margin_percent: number;
  margin_usd: number;
  total_usd: number;
  num_briefings: number;
  num_users: number;
  cost_per_briefing_usd: number;
  cost_per_user_usd: number;
  config_id: number;
}

export async function fetchCostReport(window: '7d' | '30d' = '30d'): Promise<CostReport | null> {
  return apiFetch<CostReport | null>(`/admin/cost-report?window=${window}`);
}

export async function fetchCostConfig(): Promise<CostConfigVersion | null> {
  return apiFetch<CostConfigVersion | null>('/admin/cost-config');
}

export async function fetchCostConfigHistory(): Promise<CostConfigVersion[]> {
  return apiFetch<CostConfigVersion[]>('/admin/cost-config/history');
}

export async function updateCostConfig(body: Partial<CostConfigData>): Promise<CostConfigVersion> {
  return apiFetch<CostConfigVersion>('/admin/cost-config', {
    method: 'PUT',
    body: JSON.stringify(body),
  });
}

// --- Hub (cross-app) ---

export interface HubServiceCost {
  cost_usd: number;
  count: number;
}

export interface HubUser {
  id: string;
  email: string;
  display_name: string;
  approved: boolean;
  services: Record<string, HubServiceCost>;
  total_cost_usd: number;
  total_actions: number;
  last_active: string | null;
}

export interface HubResponse {
  period: string;
  app_registry: Record<string, string | null>;
  users: HubUser[];
  totals: { cost_usd: number; actions: number; users: number };
}

export async function fetchHubUsers(period = '30d'): Promise<HubResponse> {
  return apiFetch<HubResponse>(`/admin/hub/users?period=${period}`);
}

// --- Feedback ---

export type FeedbackStatus = 'pending' | 'ready' | 'replied' | 'ignored';

export interface FeedbackEntry {
  id: number;
  user_email: string;
  user_name: string;
  flight_id: string;
  route_name: string;
  waypoints: string[];
  pack_timestamp: string;
  category: string;
  comment: string;
  sentiment: 'up' | 'down' | null;
  target: string | null;
  contact_ok: boolean;
  created_at: string | null;
  status: FeedbackStatus;
  classification: string | null;
  ai_analysis: string | null;
  admin_reply: string | null;
  admin_notes: string | null;
  confidence: number | null;
  replied_at: string | null;
  processed_at: string | null;
}

export type FeedbackKind = 'feedback' | 'ratings';

export async function fetchAdminFeedback(status?: string, kind?: FeedbackKind): Promise<FeedbackEntry[]> {
  const params = new URLSearchParams();
  if (status) params.set('status', status);
  if (kind) params.set('kind', kind);
  const qs = params.toString();
  return apiFetch<FeedbackEntry[]>(`/feedback/admin${qs ? `?${qs}` : ''}`);
}

export async function updateFeedbackStatus(id: number, status: FeedbackStatus): Promise<void> {
  await apiFetch(`/feedback/admin/${id}/status`, {
    method: 'PUT',
    body: JSON.stringify({ status }),
  });
}

export async function saveFeedbackReply(id: number, reply: string): Promise<void> {
  await apiFetch(`/feedback/admin/${id}/reply`, {
    method: 'PUT',
    body: JSON.stringify({ reply }),
  });
}

export async function sendFeedbackReply(id: number, reply?: string): Promise<{ sent_to: string }> {
  return apiFetch(`/feedback/admin/${id}/send`, {
    method: 'POST',
    body: JSON.stringify(reply != null ? { reply } : {}),
  });
}

export async function reopenFeedback(id: number): Promise<void> {
  await apiFetch(`/feedback/admin/${id}/reopen`, { method: 'POST' });
}

export async function saveFeedbackNotes(id: number, notes: string): Promise<void> {
  await apiFetch(`/feedback/admin/${id}/notes`, {
    method: 'PUT',
    body: JSON.stringify({ notes }),
  });
}

// --- Performance Metrics ---

export interface AdminMetricsWindow {
  total_refreshes: number;
  avg_elapsed_seconds: number | null;
  p95_elapsed_seconds: number | null;
  avg_queue_wait_seconds: number | null;
  max_queue_wait_seconds: number | null;
  by_trigger: Record<string, number>;
}

export interface AdminMetrics {
  current: { active_refreshes: number; queued_refreshes: number };
  last_24h: AdminMetricsWindow;
  last_7d: AdminMetricsWindow;
  last_30d: AdminMetricsWindow;
}

export async function fetchAdminMetrics(): Promise<AdminMetrics> {
  return apiFetch<AdminMetrics>('/admin/metrics');
}

// --- API Usage ---

export interface ApiUsageBucket {
  service: string;
  pipeline: string;
  total_calls: number;
}

export interface ApiUsagePeriod {
  label: string;
  by_service: ApiUsageBucket[];
  total_calls: number;
}

export interface ApiUsageResponse {
  current_month: ApiUsagePeriod;
  last_30d: ApiUsagePeriod;
  all_time: ApiUsagePeriod;
}

export async function fetchApiUsage(): Promise<ApiUsageResponse> {
  return apiFetch<ApiUsageResponse>('/admin/api-usage');
}

// --- Verification Stats ---

export interface VerificationActivity {
  flights_verified: number;
  flights_completed: number;
  airports_observed: number;
  observations_collected: number;
  cycles_run: number;
  avg_cycle_duration_ms: number | null;
}

export interface CategoryAccuracyRow {
  model: string;
  days_out: number;
  accuracy_pct: number | null;
  sample_count: number;
}

export interface NotableMiss {
  icao: string;
  observation_time: string;
  model: string;
  days_out: number;
  obs_category: string;
  model_category: string;
  ceiling_delta_ft: number | null;
  direction: string;
  severity: number;
}

export interface CategoryBiasStats {
  model: string;
  days_out: number;
  total_scores: number;
  optimistic_1: number;
  // 2 = "2 or more levels optimistic" (collapsed in verification_daily_stats)
  optimistic_2: number;
  pessimistic_1: number;
  pessimistic_2: number;
}

export interface WindAdvisoryStats {
  model: string;
  accuracy_pct: number | null;
  sample_count: number;
}

/** Gust accuracy under both conditionings — they are never blended (#491).
 *
 * Forecast-flagged: `n_flagged` hours the forecast called a gust,
 * `flagged_over_peak_kt` = mean(forecast gust − realised peak) on those hours,
 * `over_warn_ratio` = flagged ÷ hours the airport actually gusted.
 * Obs-flagged: `n_gust` hours the airport gusted, with MAE and signed bias.
 */
export interface GustAccuracyStats {
  model: string;
  days_out: number;
  n: number;
  n_gust: number;
  gust_mae_kt: number | null;
  gust_bias_kt: number | null;
  n_flagged: number;
  flagged_over_peak_kt: number | null;
  n_obs_gust: number;
  n_flag_hit: number;
  over_warn_ratio: number | null;
}

export interface MissedWarning {
  icao: string;
  observation_time: string;
  model: string;
  days_out: number;
  obs_wind_advisory: string;
  model_wind_advisory: string;
}

export interface VerificationDigest {
  period_label: string;
  activity: VerificationActivity;
  category_accuracy_today: CategoryAccuracyRow[];
  category_accuracy_7d: CategoryAccuracyRow[];
  notable_misses: NotableMiss[];
  category_bias: CategoryBiasStats[];
  wind_advisory: WindAdvisoryStats[];
  /** Absent on cache entries written before #491 — treat as empty. */
  gust_accuracy?: GustAccuracyStats[];
  missed_warnings: MissedWarning[];
}

export type VerificationPeriod = '24h' | '7d' | '30d';
export type VerificationSource = 'flight' | 'standalone';

export async function fetchVerificationStats(
  period: VerificationPeriod = '24h',
  source: VerificationSource = 'flight',
  country?: string,
  icao?: string,
): Promise<VerificationDigest> {
  let url = `/admin/verification?period=${period}&source=${source}`;
  if (icao) url += `&icao=${encodeURIComponent(icao)}`;
  else if (country) url += `&country=${encodeURIComponent(country)}`;
  return apiFetch<VerificationDigest>(url);
}

export async function fetchVerificationAirports(): Promise<Record<string, string[]>> {
  return apiFetch<Record<string, string[]>>('/admin/verification/airports');
}

/** Clear the calling admin's setup_completed flag so the welcome wizard replays
 * on the next visit to /flights. Admin-only by design — used for testing the
 * first-time experience.
 */
export async function resetMyOnboarding(): Promise<void> {
  return apiFetch<void>('/admin/onboarding/reset', { method: 'POST' });
}
