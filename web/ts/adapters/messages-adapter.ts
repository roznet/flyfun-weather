/** Messages adapter — fetch system messages and notification status. */

import { apiFetch } from '../utils';

export interface SystemMessage {
  id: string;
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
