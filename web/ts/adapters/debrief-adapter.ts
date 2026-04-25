/** API client for flight debriefs. */

import type { DebriefResponse, DebriefStats, OutcomeValue, ConditionTagId, DebriefDecision } from '../store/types';
import { apiFetch } from '../utils';

export interface DebriefRequest {
  decision: DebriefDecision;
  reasons?: ConditionTagId[];
  outcomes?: Partial<Record<ConditionTagId, OutcomeValue>>;
  note?: string | null;
}

export async function fetchDebrief(flightId: string): Promise<DebriefResponse | null> {
  try {
    return await apiFetch<DebriefResponse>(`/flights/${encodeURIComponent(flightId)}/debrief`);
  } catch (e: unknown) {
    if (e instanceof Error && e.message.startsWith('API 404:')) return null;
    throw e;
  }
}

export async function saveDebrief(flightId: string, body: DebriefRequest): Promise<DebriefResponse> {
  return apiFetch<DebriefResponse>(
    `/flights/${encodeURIComponent(flightId)}/debrief`,
    { method: 'PUT', body: JSON.stringify(body) },
  );
}

export async function deleteDebrief(flightId: string): Promise<void> {
  await apiFetch<void>(
    `/flights/${encodeURIComponent(flightId)}/debrief`,
    { method: 'DELETE' },
  );
}

export async function fetchDebriefStats(windowDays = 90): Promise<DebriefStats> {
  return apiFetch<DebriefStats>(`/debriefs/stats?window_days=${windowDays}`);
}
