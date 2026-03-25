/** Messages adapter — fetch system messages and notification status. */

import { apiFetch } from '../utils';

export interface SystemMessage {
  id: number;
  date: string;
  title: string;
  body: string;
  category: 'feature' | 'change' | 'fix';
}

export interface MessagesStatus {
  unseen_count: number;
  latest_message_date: string | null;
}

export async function fetchMessages(): Promise<SystemMessage[]> {
  return apiFetch<SystemMessage[]>('/messages');
}

export async function fetchMessagesStatus(): Promise<MessagesStatus> {
  return apiFetch<MessagesStatus>('/messages/status');
}

export async function markMessagesSeen(): Promise<void> {
  return apiFetch<void>('/messages/seen', { method: 'POST' });
}

// --- Admin CRUD ---

export interface MessageCreate {
  date: string;
  title: string;
  body: string;
  category: string;
}

export interface MessageUpdate {
  date?: string;
  title?: string;
  body?: string;
  category?: string;
}

export async function adminListMessages(): Promise<SystemMessage[]> {
  return apiFetch<SystemMessage[]>('/admin/messages');
}

export async function adminCreateMessage(msg: MessageCreate): Promise<SystemMessage> {
  return apiFetch<SystemMessage>('/admin/messages', {
    method: 'POST',
    body: JSON.stringify(msg),
  });
}

export async function adminUpdateMessage(id: number, msg: MessageUpdate): Promise<SystemMessage> {
  return apiFetch<SystemMessage>(`/admin/messages/${id}`, {
    method: 'PUT',
    body: JSON.stringify(msg),
  });
}

export async function adminDeleteMessage(id: number): Promise<void> {
  return apiFetch<void>(`/admin/messages/${id}`, { method: 'DELETE' });
}
