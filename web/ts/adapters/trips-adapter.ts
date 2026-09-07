/** API communication for flight trips (#602). */

import type {
  TripRefreshStatus,
  TripResponse,
  TripSummary,
} from '../store/types';
import { apiFetch } from '../utils';

export async function fetchTrips(): Promise<TripResponse[]> {
  return apiFetch<TripResponse[]>('/trips');
}

export async function fetchTrip(id: string): Promise<TripResponse> {
  return apiFetch<TripResponse>(`/trips/${encodeURIComponent(id)}`);
}

export async function fetchTripSummary(id: string): Promise<TripSummary> {
  return apiFetch<TripSummary>(`/trips/${encodeURIComponent(id)}/summary`);
}

/** Group flights into a new trip. A 1-leg trip is valid. */
export async function createTrip(
  flightIds: string[], name?: string,
): Promise<TripResponse> {
  return apiFetch<TripResponse>('/trips', {
    method: 'POST',
    body: JSON.stringify({ flight_ids: flightIds, name: name ?? null }),
  });
}

export async function addTripLegs(
  tripId: string, flightIds: string[],
): Promise<TripResponse> {
  return apiFetch<TripResponse>(`/trips/${encodeURIComponent(tripId)}/legs`, {
    method: 'POST',
    body: JSON.stringify({ flight_ids: flightIds }),
  });
}

/** Unlink a leg — never a delete. The flight and its packs survive. */
export async function removeTripLeg(
  tripId: string, flightId: string,
): Promise<void> {
  await apiFetch<unknown>(
    `/trips/${encodeURIComponent(tripId)}/legs/${encodeURIComponent(flightId)}`,
    { method: 'DELETE' },
  );
}

export async function updateTrip(
  tripId: string,
  patch: Partial<{
    name: string;
    notes: string;
    auto_refresh: boolean;
    auto_refresh_hour: number;
    notify_override: 'default' | 'notify' | 'mute';
  }>,
): Promise<TripResponse> {
  return apiFetch<TripResponse>(`/trips/${encodeURIComponent(tripId)}`, {
    method: 'PATCH',
    body: JSON.stringify(patch),
  });
}

/** Delete the trip container. Its legs survive, unlinked. */
export async function deleteTrip(tripId: string): Promise<void> {
  await apiFetch<unknown>(`/trips/${encodeURIComponent(tripId)}`, {
    method: 'DELETE',
  });
}

/**
 * Kick off a trip refresh. The server drives it one leg at a time and keeps
 * going if the tab closes — the client only polls for progress.
 */
export async function refreshTrip(tripId: string): Promise<TripRefreshStatus> {
  return apiFetch<TripRefreshStatus>(
    `/trips/${encodeURIComponent(tripId)}/refresh`, { method: 'POST' },
  );
}

export async function fetchTripRefreshStatus(
  tripId: string,
): Promise<TripRefreshStatus> {
  return apiFetch<TripRefreshStatus>(
    `/trips/${encodeURIComponent(tripId)}/refresh/status`,
  );
}

export interface TripAiSummary {
  trip_id: string;
  text: string | null;
  generated_at: string | null;
  unavailable_reason:
    | 'ai_disabled' | 'no_legs' | 'generation_failed' | 'guardrail_rejected'
    | null;
}

/** Fetch (regenerating only when stale) the trip's AI paragraph. */
export async function fetchTripAiSummary(tripId: string): Promise<TripAiSummary> {
  return apiFetch<TripAiSummary>(
    `/trips/${encodeURIComponent(tripId)}/ai-summary`, { method: 'POST' },
  );
}
