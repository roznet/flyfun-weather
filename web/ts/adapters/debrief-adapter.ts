/** API client for flight debriefs. */

import type { DebriefReport, DebriefResponse, DebriefStats, OutcomeValue, ConditionTagId, DebriefDecision } from '../store/types';
import { apiFetch } from '../utils';

export interface DebriefRequest {
  decision: DebriefDecision;
  reasons?: ConditionTagId[];
  outcomes?: Partial<Record<ConditionTagId, OutcomeValue>>;
  note?: string | null;
  /** #1023: 'questions' from the Debrief section; omitted = the old form. */
  form?: 'questions' | 'legacy';
}

/** One answer for PUT /debrief/answers. `answer: null` clears it. */
export interface DebriefAnswerRequest {
  question_id: string;
  answer: string | null;
  avoided?: boolean;
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

/** The post-flight report (#1023), built by the server on each call. */
export async function fetchDebriefReport(flightId: string): Promise<DebriefReport> {
  return apiFetch<DebriefReport>(`/flights/${encodeURIComponent(flightId)}/debrief/report`);
}

/** Store answers; the server folds them into the debrief (flown) and
 *  returns the rebuilt report. */
export async function saveDebriefAnswers(
  flightId: string, answers: DebriefAnswerRequest[],
): Promise<DebriefReport> {
  return apiFetch<DebriefReport>(
    `/flights/${encodeURIComponent(flightId)}/debrief/answers`,
    { method: 'PUT', body: JSON.stringify({ answers }) },
  );
}
