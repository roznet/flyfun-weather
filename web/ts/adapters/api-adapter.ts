/** API communication layer — all backend calls go through here. */

import type {
  CreateFlightRequest,
  FlightResponse,
  ForecastSnapshot,
  PackMeta,
  RouteAnalysesManifest,
  RouteInfo,
} from '../store/types';

const API_BASE = '/api';

async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...init?.headers },
    ...init,
  });
  if (!resp.ok) {
    const body = await resp.text();
    let detail: string;
    try {
      detail = JSON.parse(body).detail || body;
    } catch {
      detail = body;
    }
    throw new Error(`API ${resp.status}: ${detail}`);
  }
  if (resp.status === 204) return undefined as T;
  return resp.json();
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
