/** Admin adapter — user management API calls. */

import { apiFetch } from '../utils';

export interface AdminUserUsageToday {
  briefings: number;
  open_meteo: number;
  gramet: number;
  llm_digest: number;
}

export interface AdminUserUsageMonth {
  briefings: number;
  total_tokens: number;
}

export interface AdminUser {
  id: string;
  email: string;
  display_name: string;
  provider: string;
  approved: boolean;
  created_at: string | null;
  last_login_at: string | null;
  usage_today: AdminUserUsageToday;
  usage_month: AdminUserUsageMonth;
}

export async function fetchAdminUsers(): Promise<AdminUser[]> {
  return apiFetch<AdminUser[]>('/admin/users');
}

export async function approveUser(userId: string): Promise<void> {
  await apiFetch<unknown>(`/admin/users/${userId}/approve`, { method: 'POST' });
}
