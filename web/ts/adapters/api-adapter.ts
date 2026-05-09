/** API communication layer — all backend calls go through here. */

import type {
  CreateFlightRequest,
  DataStatus,
  ElevationProfile,
  FlightResponse,
  ForecastSnapshot,
  PackMeta,
  RouteAnalysesManifest,
  RouteObservations,
} from '../store/types';
import type { AltitudeTableResult, RouteAdvisoriesManifest } from '../types/advisories';
import type { SoundingProfileData } from '../visualization/skewt/types';
import { API_BASE, apiFetch, redirectToLogin } from '../utils';

/** Typed error for refresh stream failures — avoids fragile string matching. */
export class RefreshStreamError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'RefreshStreamError';
  }
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

export interface BulkDeleteResponse {
  deleted: string[];
  not_found: string[];
}

export async function bulkDeleteFlights(ids: string[]): Promise<BulkDeleteResponse> {
  return apiFetch<BulkDeleteResponse>('/flights/bulk-delete', {
    method: 'POST',
    body: JSON.stringify({ ids }),
  });
}

export interface MoveFlightRequest {
  departure_time?: string;
  waypoints?: string[];
  cruise_altitude_ft?: number;
  flight_ceiling_ft?: number;
  flight_duration_hours?: number;
  raw_route?: string;
}

export async function moveFlight(
  flightId: string,
  req: MoveFlightRequest,
): Promise<FlightResponse> {
  return apiFetch<FlightResponse>(
    `/flights/${encodeURIComponent(flightId)}/move`,
    {
      method: 'POST',
      body: JSON.stringify(req),
    },
  );
}

export interface UpdateFlightRequest {
  profile_id?: number;
  aircraft_id?: number;
  departure_time?: string;
  alt_departure_time?: string | null;
  cruise_altitude_ft?: number;
  flight_ceiling_ft?: number;
  flight_duration_hours?: number;
  waypoints?: string[];
  // Pair with `waypoints` to keep the original Field-15 input in sync.
  // Omit on a direct waypoint edit and the server clears the stored
  // raw_route (the old string no longer matches the new chip list).
  // No `| null` — the server treats null and missing identically; the
  // type would imply a "null clears" path that doesn't exist.
  raw_route?: string;
}

export interface UpdateFlightResponse extends FlightResponse {
  invalidation: 'none' | 'advisories_only' | 'refetch_needed';
}

export async function updateFlight(
  flightId: string,
  req: UpdateFlightRequest,
): Promise<UpdateFlightResponse> {
  return apiFetch<UpdateFlightResponse>(
    `/flights/${encodeURIComponent(flightId)}`,
    {
      method: 'PATCH',
      body: JSON.stringify(req),
    },
  );
}

export async function updateAutoRefresh(
  flightId: string,
  req: { auto_refresh: boolean; auto_refresh_hour?: number | null },
): Promise<FlightResponse> {
  return apiFetch<FlightResponse>(
    `/flights/${encodeURIComponent(flightId)}/auto-refresh`,
    {
      method: 'PATCH',
      body: JSON.stringify(req),
    },
  );
}

export async function updatePrivacy(
  flightId: string,
  isPrivate: boolean,
): Promise<FlightResponse> {
  return apiFetch<FlightResponse>(
    `/flights/${encodeURIComponent(flightId)}/privacy`,
    {
      method: 'PATCH',
      body: JSON.stringify({ private: isPrivate }),
    },
  );
}

export interface SubscribeResponse {
  flight_id: string;
  user_id: string;
  created: boolean;
}

export async function subscribeFlight(flightId: string): Promise<SubscribeResponse> {
  return apiFetch<SubscribeResponse>(
    `/flights/${encodeURIComponent(flightId)}/subscribe`,
    { method: 'POST' },
  );
}

export async function unsubscribeFlight(flightId: string): Promise<void> {
  return apiFetch<void>(
    `/flights/${encodeURIComponent(flightId)}/subscribe`,
    { method: 'DELETE' },
  );
}

/** Subscribe and refetch the flight in one call. Callers (three different
 *  stores) wrap this in their own loading/error state. */
export async function subscribeAndRefetch(flightId: string): Promise<FlightResponse> {
  await subscribeFlight(flightId);
  return fetchFlight(flightId);
}

/** Unsubscribe and refetch the flight in one call. */
export async function unsubscribeAndRefetch(flightId: string): Promise<FlightResponse> {
  await unsubscribeFlight(flightId);
  return fetchFlight(flightId);
}

export interface WaypointInfo {
  icao: string;
  name: string;
  lat: number;
  lon: number;
  timezone: string | null;
}

export interface RouteDistanceResponse {
  total_distance_nm: number;
  waypoints: WaypointInfo[];
}

export async function fetchRouteDistance(waypoints: string[]): Promise<RouteDistanceResponse> {
  return apiFetch<RouteDistanceResponse>('/flights/route-distance', {
    method: 'POST',
    body: JSON.stringify({ waypoints }),
  });
}

// --- FPL parsing ---

export interface ParseFplResponse {
  waypoints: string[];
  date: string | null;
  time_utc: string | null;
  altitude_ft: number | null;
  duration_hours: number | null;
  flight_rules: string | null;
  aircraft_type: string | null;
  raw_route: string | null;
  error: string | null;
}

export async function parseFpl(fplText: string): Promise<ParseFplResponse> {
  return apiFetch<ParseFplResponse>('/flights/parse-fpl', {
    method: 'POST',
    body: JSON.stringify({ fpl_text: fplText }),
  });
}

// --- Route interpretation ---

export interface InterpretRouteResponse {
  original_tokens: string[];
  interpreted: string[];
  skipped: string[];
  waypoints: WaypointInfo[];
}

export async function interpretRoute(rawRoute: string): Promise<InterpretRouteResponse> {
  return apiFetch<InterpretRouteResponse>('/flights/interpret-route', {
    method: 'POST',
    body: JSON.stringify({ raw_route: rawRoute }),
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

export interface RefreshAccepted {
  status: 'queued' | 'already_fresh';
  flight_id: string;
  message: string;
}

export async function refreshBriefing(flightId: string): Promise<RefreshAccepted> {
  return apiFetch<RefreshAccepted>(
    `/flights/${encodeURIComponent(flightId)}/packs/refresh`,
    { method: 'POST' }
  );
}

export async function fetchFreshness(flightId: string): Promise<DataStatus> {
  return apiFetch<DataStatus>(
    `/flights/${encodeURIComponent(flightId)}/packs/freshness`
  );
}

/** SSE event from the streaming refresh endpoint.
 *
 * Event types in stream order (best case):
 *   progress* → briefing_ready → progress(llm_digest) → complete
 *
 * `briefing_ready` carries a provisional `pack` with `has_digest=false`;
 * the visible briefing artifacts (snapshot, advisories, GRAMET) are on
 * disk and ready to render. `complete` carries the final `pack` with
 * `has_digest=true` (or false if the digest stage failed). Older servers
 * that don't emit `briefing_ready` still work — clients just see the
 * existing `progress* → complete` flow.
 */
export interface RefreshStreamEvent {
  type: 'progress' | 'briefing_ready' | 'complete' | 'error';
  stage?: string;
  detail?: string | null;
  label?: string;
  progress?: number;
  pack?: PackMeta;
  message?: string;
  elapsed_seconds?: number;
}

/**
 * Stream a briefing refresh via SSE, calling onEvent for each progress update.
 * Returns the final PackMeta on completion.
 */
export async function refreshBriefingStream(
  flightId: string,
  onEvent: (event: RefreshStreamEvent) => void,
  force?: boolean,
  asOfDate?: string,
  notifyEmail?: boolean,
): Promise<PackMeta> {
  const params = new URLSearchParams();
  if (force) params.set('force', 'true');
  if (asOfDate) params.set('as_of_date', asOfDate);
  if (notifyEmail) params.set('notify_email', 'true');
  const qs = params.toString();
  const url = `${API_BASE}/flights/${encodeURIComponent(flightId)}/packs/refresh/stream${qs ? '?' + qs : ''}`;
  const resp = await fetch(url, {
    method: 'POST',
    credentials: 'same-origin',
  });

  if (!resp.ok) {
    if (resp.status === 401) {
      redirectToLogin();
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

// --- Route advisories ---

export async function fetchRouteAdvisories(
  flightId: string,
  timestamp: string,
): Promise<RouteAdvisoriesManifest> {
  return apiFetch<RouteAdvisoriesManifest>(
    `/flights/${encodeURIComponent(flightId)}/packs/${encodeURIComponent(timestamp)}/advisories`
  );
}

export async function fetchAltAdvisories(
  flightId: string,
  timestamp: string,
): Promise<RouteAdvisoriesManifest> {
  return apiFetch<RouteAdvisoriesManifest>(
    `/flights/${encodeURIComponent(flightId)}/packs/${encodeURIComponent(timestamp)}/advisories/alt`
  );
}

export async function computeAltAdvisories(
  flightId: string,
  timestamp: string,
): Promise<RouteAdvisoriesManifest> {
  return apiFetch<RouteAdvisoriesManifest>(
    `/flights/${encodeURIComponent(flightId)}/packs/${encodeURIComponent(timestamp)}/advisories/alt/compute`,
    { method: 'POST' },
  );
}

export async function recalculateAdvisories(
  flightId: string,
  timestamp: string,
  cruiseAltitudeFt?: number,
): Promise<RouteAdvisoriesManifest> {
  const qs = cruiseAltitudeFt != null ? `?cruise_altitude_ft=${cruiseAltitudeFt}` : '';
  return apiFetch<RouteAdvisoriesManifest>(
    `/flights/${encodeURIComponent(flightId)}/packs/${encodeURIComponent(timestamp)}/advisories/recalculate${qs}`,
    { method: 'POST' },
  );
}

export async function fetchAltitudeTable(
  flightId: string,
  timestamp: string,
  stepFt: number = 2000,
): Promise<AltitudeTableResult> {
  const qs = `?step_ft=${stepFt}`;
  return apiFetch<AltitudeTableResult>(
    `/flights/${encodeURIComponent(flightId)}/packs/${encodeURIComponent(timestamp)}/advisories/altitude-table${qs}`,
    { method: 'POST' },
  );
}

// --- Observations refresh ---

export async function refreshObservations(
  flightId: string,
  timestamp: string,
): Promise<RouteObservations> {
  return apiFetch<RouteObservations>(
    `/flights/${encodeURIComponent(flightId)}/packs/${encodeURIComponent(timestamp)}/observations/refresh`,
    { method: 'POST' },
  );
}

// --- Elevation profile ---

export async function fetchElevationProfile(
  flightId: string,
  timestamp: string,
): Promise<ElevationProfile> {
  return apiFetch<ElevationProfile>(
    `/flights/${encodeURIComponent(flightId)}/packs/${encodeURIComponent(timestamp)}/elevation`
  );
}

// --- Artifact URLs (for <img> src, etc.) ---

export function grametUrl(flightId: string, timestamp: string): string {
  return `${API_BASE}/flights/${encodeURIComponent(flightId)}/packs/${encodeURIComponent(timestamp)}/gramet`;
}

export function grametPngUrl(flightId: string, timestamp: string): string {
  return `${API_BASE}/flights/${encodeURIComponent(flightId)}/packs/${encodeURIComponent(timestamp)}/gramet.png`;
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

/** Fetch sounding profile JSON for client-side Skew-T rendering. */
export async function fetchSoundingProfile(
  flightId: string,
  timestamp: string,
  pointIndex: number,
  model: string,
): Promise<SoundingProfileData> {
  const path = `/flights/${encodeURIComponent(flightId)}/packs/${encodeURIComponent(timestamp)}/sounding-profile/${pointIndex}/${encodeURIComponent(model)}`;
  return apiFetch<SoundingProfileData>(path);
}

export function hodographUrl(
  flightId: string,
  timestamp: string,
  icao: string,
  model: string,
): string {
  return `${API_BASE}/flights/${encodeURIComponent(flightId)}/packs/${encodeURIComponent(timestamp)}/hodograph/${encodeURIComponent(icao)}/${encodeURIComponent(model)}`;
}

export function routeHodographUrl(
  flightId: string,
  timestamp: string,
  pointIndex: number,
  model: string,
): string {
  return `${API_BASE}/flights/${encodeURIComponent(flightId)}/packs/${encodeURIComponent(timestamp)}/hodograph/route/${pointIndex}/${encodeURIComponent(model)}`;
}

export function digestUrl(flightId: string, timestamp: string): string {
  return `${API_BASE}/flights/${encodeURIComponent(flightId)}/packs/${encodeURIComponent(timestamp)}/digest`;
}

export function digestJsonUrl(flightId: string, timestamp: string): string {
  return `${API_BASE}/flights/${encodeURIComponent(flightId)}/packs/${encodeURIComponent(timestamp)}/digest/json`;
}

// --- DWD Overview ---

export function dwdOverviewUrl(flightId: string, timestamp: string): string {
  return `${API_BASE}/flights/${encodeURIComponent(flightId)}/packs/${encodeURIComponent(timestamp)}/dwd-overview`;
}

export function dwdChartUrl(flightId: string, timestamp: string, chartId: string): string {
  return `${API_BASE}/flights/${encodeURIComponent(flightId)}/packs/${encodeURIComponent(timestamp)}/dwd-chart/${encodeURIComponent(chartId)}`;
}

export function dwdChartOverlayUrl(flightId: string, timestamp: string): string {
  return `${API_BASE}/flights/${encodeURIComponent(flightId)}/packs/${encodeURIComponent(timestamp)}/dwd-chart-overlay`;
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

// --- Refresh status ---

export interface RefreshEntry {
  flight_id: string;
  status: 'queued' | 'refreshing';
  triggered_by: string;
  stage: string | null;
  detail: string | null;
  queued_at: string;
}

export interface RefreshStatusResponse {
  active: boolean;
  status?: string;
  stage?: string | null;
  label?: string | null;
  detail?: string | null;
  triggered_by?: string;
  queued_at?: string;
}

export async function fetchRefreshStatus(flightId: string): Promise<RefreshStatusResponse> {
  return apiFetch<RefreshStatusResponse>(
    `/flights/${encodeURIComponent(flightId)}/packs/refresh/status`
  );
}

export interface RefreshStats {
  avg_elapsed_seconds: number | null;
  sample_size: number;
}

export async function fetchRefreshStats(): Promise<RefreshStats> {
  return apiFetch<RefreshStats>('/refresh/stats');
}

export async function fetchActiveRefreshes(): Promise<RefreshEntry[]> {
  return apiFetch<RefreshEntry[]>('/refresh/active');
}

// --- Feedback ---

export interface FeedbackRequest {
  flight_id: string;
  pack_timestamp: string;
  category: string;
  comment: string;
}

export async function submitFeedback(req: FeedbackRequest): Promise<{ id: number; status: string }> {
  return apiFetch<{ id: number; status: string }>('/feedback', {
    method: 'POST',
    body: JSON.stringify(req),
  });
}
