/** Admin adapter — user management API calls. */

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
  approved: boolean;
  created_at: string | null;
  last_login_at: string | null;
  usage_month: AdminUserUsage;
  disk_usage_bytes: number;
}

export interface AdminSummary {
  total_users: number;
  total_briefings: number;
  total_tokens: number;
  total_disk_bytes: number;
}

export interface AdminUsersResponse {
  summary: AdminSummary;
  users: AdminUser[];
}

export async function fetchAdminUsers(): Promise<AdminUsersResponse> {
  return apiFetch<AdminUsersResponse>('/admin/users');
}

export async function approveUser(userId: string): Promise<void> {
  await apiFetch<unknown>(`/admin/users/${userId}/approve`, { method: 'POST' });
}
