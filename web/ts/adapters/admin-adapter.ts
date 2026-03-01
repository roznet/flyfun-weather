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

export async function fetchAdminUsers(period: AdminPeriod = '30d'): Promise<AdminUsersResponse> {
  return apiFetch<AdminUsersResponse>(`/admin/users?period=${period}`);
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
  credits_used_today: number;
  credits_used_month: number;
  total_credits_charged: number;
  total_briefings: number;
  avg_cost_per_briefing: number;
}

export interface UserCostTransaction {
  id: number;
  timestamp: string;
  amount: number;
  balance_after: number;
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
  credit_balance: number;
  summary: UserCostSummary;
  transactions: UserCostTransaction[];
  recent_flights: UserCostFlight[];
  cost_breakdown: UserCostBreakdown;
}

export async function fetchUserCosts(userId: string, limit = 50): Promise<UserCostsResponse> {
  return apiFetch<UserCostsResponse>(`/admin/users/${encodeURIComponent(userId)}/costs?limit=${limit}`);
}

// --- Feedback ---

export interface FeedbackEntry {
  id: number;
  user_email: string;
  user_name: string;
  flight_id: string;
  pack_timestamp: string;
  category: string;
  comment: string;
  created_at: string | null;
}

export async function fetchAdminFeedback(): Promise<FeedbackEntry[]> {
  return apiFetch<FeedbackEntry[]>('/feedback/admin');
}
