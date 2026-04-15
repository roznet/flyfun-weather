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
  total_users: number;
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
  pack_timestamp: string;
  category: string;
  comment: string;
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

export async function fetchAdminFeedback(status?: string): Promise<FeedbackEntry[]> {
  const qs = status ? `?status=${encodeURIComponent(status)}` : '';
  return apiFetch<FeedbackEntry[]>(`/feedback/admin${qs}`);
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
  optimistic_2: number;
  optimistic_3: number;
  pessimistic_1: number;
  pessimistic_2: number;
  pessimistic_3: number;
}

export interface WindAdvisoryStats {
  model: string;
  accuracy_pct: number | null;
}

export interface MissedWarning {
  icao: string;
  observation_time: string;
  model: string;
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
