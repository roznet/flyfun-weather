/** API communication layer — all backend calls go through here. */

import type {
  CreateFlightRequest,
  DataStatus,
  FlightResponse,
  ForecastSnapshot,
  PackMeta,
  RouteAnalysesManifest,
  RouteInfo,
} from '../store/types';
import { API_BASE, apiFetch } from '../utils';

/** Typed error for refresh stream failures — avoids fragile string matching. */
export class RefreshStreamError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'RefreshStreamError';
  }
}

// --- Routes ---

export async function fetchRoutes(): Promise<RouteInfo[]> {
  return apiFetch<RouteInfo[]>('/routes');
}

export async function fetchRoute(name: string): Promise<RouteInfo> {
  return apiFetch<RouteInfo>(`/routes/${encodeURIComponent(name)}`);
}

// --- Flights ---

export async function fetchFlights(): Promise<FlightResponse[]> {
  return apiFetch<FlightResponse[]>('/flights');
}

export async function fetchFlight(id: string): Promise<FlightResponse> {
  return apiFetch<FlightResponse>(`/flights/${encodeURIComponent(id)}`);
}

export async function createFlight(req: CreateFlightRequest): Promise<FlightResponse> {
  return apiFetch<FlightResponse>('/flights', {
    method: 'POST',
    body: JSON.stringify(req),
  });
}

export async function deleteFlight(id: string): Promise<void> {
  return apiFetch<void>(`/flights/${encodeURIComponent(id)}`, {
    method: 'DELETE',
  });
}

// --- Packs ---

export async function fetchPacks(flightId: string): Promise<PackMeta[]> {
  return apiFetch<PackMeta[]>(`/flights/${encodeURIComponent(flightId)}/packs`);
}

export async function fetchLatestPack(flightId: string): Promise<PackMeta> {
  return apiFetch<PackMeta>(`/flights/${encodeURIComponent(flightId)}/packs/latest`);
}

export async function fetchPack(flightId: string, timestamp: string): Promise<PackMeta> {
  return apiFetch<PackMeta>(
    `/flights/${encodeURIComponent(flightId)}/packs/${encodeURIComponent(timestamp)}`
  );
}

export async function refreshBriefing(flightId: string): Promise<PackMeta> {
  return apiFetch<PackMeta>(
    `/flights/${encodeURIComponent(flightId)}/packs/refresh`,
    { method: 'POST' }
  );
}

export async function fetchFreshness(flightId: string): Promise<DataStatus> {
  return apiFetch<DataStatus>(
    `/flights/${encodeURIComponent(flightId)}/packs/freshness`
  );
}

/** SSE event from the streaming refresh endpoint. */
export interface RefreshStreamEvent {
  type: 'progress' | 'complete' | 'error';
  stage?: string;
  detail?: string | null;
  label?: string;
  progress?: number;
  pack?: PackMeta;
  message?: string;
}

/**
 * Stream a briefing refresh via SSE, calling onEvent for each progress update.
 * Returns the final PackMeta on completion.
 */
export async function refreshBriefingStream(
  flightId: string,
  onEvent: (event: RefreshStreamEvent) => void,
  force?: boolean,
): Promise<PackMeta> {
  const forceParam = force ? '?force=true' : '';
  const url = `${API_BASE}/flights/${encodeURIComponent(flightId)}/packs/refresh/stream${forceParam}`;
  const resp = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
  });

  if (!resp.ok) {
    if (resp.status === 401) {
      window.location.href = '/login.html';
      throw new Error('Session expired');
    }
    const body = await resp.text();
    let detail: string;
    try {
      detail = JSON.parse(body).detail || body;
    } catch {
      detail = body;
    }
    throw new Error(`API ${resp.status}: ${detail}`);
  }

  const reader = resp.body!.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  let finalPack: PackMeta | null = null;

  while (true) {
    const { done, value } = await reader.read();
    if (done) break;

    buffer += decoder.decode(value, { stream: true });

    // Parse SSE frames from buffer
    const frames = buffer.split('\n\n');
    buffer = frames.pop()!; // keep incomplete frame in buffer

    for (const frame of frames) {
      if (!frame.trim()) continue;

      // Extract data line(s) from the SSE frame
      let data = '';
      for (const line of frame.split('\n')) {
        if (line.startsWith('data: ')) {
          data += line.slice(6);
        }
      }
      if (!data) continue;

      try {
        const event: RefreshStreamEvent = JSON.parse(data);
        onEvent(event);

        if (event.type === 'complete' && event.pack) {
          finalPack = event.pack;
        } else if (event.type === 'error') {
          throw new RefreshStreamError(event.message || 'Refresh stream error');
        }
      } catch (e) {
        if (e instanceof RefreshStreamError) throw e;
        // Skip unparseable frames
      }
    }
  }

  if (!finalPack) {
    throw new Error('Refresh stream ended without completion');
  }

  return finalPack;
}

export async function fetchSnapshot(
  flightId: string,
  timestamp: string
): Promise<ForecastSnapshot> {
  return apiFetch<ForecastSnapshot>(
    `/flights/${encodeURIComponent(flightId)}/packs/${encodeURIComponent(timestamp)}/snapshot`
  );
}

// --- Route analyses ---

export async function fetchRouteAnalyses(
  flightId: string,
  timestamp: string,
): Promise<RouteAnalysesManifest> {
  return apiFetch<RouteAnalysesManifest>(
    `/flights/${encodeURIComponent(flightId)}/packs/${encodeURIComponent(timestamp)}/route-analyses`
  );
}

// --- Artifact URLs (for <img> src, etc.) ---

export function grametUrl(flightId: string, timestamp: string): string {
  return `${API_BASE}/flights/${encodeURIComponent(flightId)}/packs/${encodeURIComponent(timestamp)}/gramet`;
}

export function skewtUrl(
  flightId: string,
  timestamp: string,
  icao: string,
  model: string
): string {
  return `${API_BASE}/flights/${encodeURIComponent(flightId)}/packs/${encodeURIComponent(timestamp)}/skewt/${encodeURIComponent(icao)}/${encodeURIComponent(model)}`;
}

export function routeSkewtUrl(
  flightId: string,
  timestamp: string,
  pointIndex: number,
  model: string,
): string {
  return `${API_BASE}/flights/${encodeURIComponent(flightId)}/packs/${encodeURIComponent(timestamp)}/skewt/route/${pointIndex}/${encodeURIComponent(model)}`;
}

export function digestUrl(flightId: string, timestamp: string): string {
  return `${API_BASE}/flights/${encodeURIComponent(flightId)}/packs/${encodeURIComponent(timestamp)}/digest`;
}

export function digestJsonUrl(flightId: string, timestamp: string): string {
  return `${API_BASE}/flights/${encodeURIComponent(flightId)}/packs/${encodeURIComponent(timestamp)}/digest/json`;
}

// --- Report ---

export function reportPdfUrl(flightId: string, timestamp: string): string {
  return `${API_BASE}/flights/${encodeURIComponent(flightId)}/packs/${encodeURIComponent(timestamp)}/report.pdf`;
}

// --- Email ---

export async function sendEmail(flightId: string, timestamp: string): Promise<void> {
  return apiFetch<void>(
    `/flights/${encodeURIComponent(flightId)}/packs/${encodeURIComponent(timestamp)}/email`,
    { method: 'POST' }
  );
}
