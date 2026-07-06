/** API communication layer — all backend calls go through here. */

import type {
  CreateFlightRequest,
  DataStatus,
  ElevationProfile,
  FlightResponse,
  ForecastSnapshot,
  PackMeta,
  RealtimeRefreshResult,
  RouteAnalysesManifest,
  RouteObservations,
  RouteSigmets,
} from '../store/types';
import type { AltitudeTableResult, RouteAdvisoriesManifest } from '../types/advisories';
import type { RouteFrontsManifest } from '../types/fronts';
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

export interface FlightsPage {
  flights: FlightResponse[];
  /** Full count of the user's "past" flights, regardless of pagination, read
   *  from the X-Past-Total response header. Lets the page decide whether to
   *  show a "Show more" affordance. Falls back to the returned past count when
   *  the header is absent (older server). */
  pastTotal: number;
}

/** Fetch the flights list. ``pastLimit`` paginates only the past section;
 *  future + recent are always returned in full. Omit ``pastLimit`` for the
 *  full, unpaginated list. */
export async function fetchFlights(opts?: {
  pastLimit?: number;
  pastOffset?: number;
}): Promise<FlightsPage> {
  const params = new URLSearchParams();
  if (opts?.pastLimit != null) params.set('past_limit', String(opts.pastLimit));
  if (opts?.pastOffset != null) params.set('past_offset', String(opts.pastOffset));
  const qs = params.toString();

  let pastTotal: number | null = null;
  const flights = await apiFetch<FlightResponse[]>(
    `/flights${qs ? `?${qs}` : ''}`,
    undefined,
    (resp) => {
      const header = resp.headers.get('X-Past-Total');
      if (header != null) {
        const parsed = parseInt(header, 10);
        if (!Number.isNaN(parsed)) pastTotal = parsed;
      }
    },
  );

  return {
    flights,
    pastTotal: pastTotal ?? flights.filter((f) => f.section === 'past').length,
  };
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
  // Timing-scenario Flexibility mode; omit for no change. 'alternate'
  // requires an alt_departure_time (server 422s otherwise).
  flexibility?: 'none' | 'alternate' | 'same_day' | 'prev_day' | 'next_day';
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

// --- Autorouter recent-routes import (issue #151) ---

export interface AutorouterRouteSummary {
  routeid: string;
  departure: string;
  destination: string;
  departure_name: string | null;
  destination_name: string | null;
  departure_time: string | null;
  fplan: string;
  route_distance_nm: number | null;
  aircraft_description: string | null;
  callsign: string | null;
}

export interface AutorouterRoutesResponse {
  routes: AutorouterRouteSummary[];
}

export class AutorouterNotLinkedError extends Error {
  constructor() {
    super('autorouter_not_linked');
    this.name = 'AutorouterNotLinkedError';
  }
}

export async function fetchAutorouterRoutes(limit = 25): Promise<AutorouterRoutesResponse> {
  try {
    return await apiFetch<AutorouterRoutesResponse>(
      `/flights/autorouter-routes?limit=${encodeURIComponent(String(limit))}`,
    );
  } catch (err) {
    // apiFetch surfaces the FastAPI {detail} payload in the error message.
    if (err instanceof Error && err.message.includes('autorouter_not_linked')) {
      throw new AutorouterNotLinkedError();
    }
    throw err;
  }
}

// --- Route interpretation ---

export interface InterpretRouteResponse {
  original_tokens: string[];
  interpreted: string[];
  /** Tokens we couldn't place on the map: typos or DB-context misses. */
  skipped: string[];
  /** Tokens recognised but rejected as too far off the direct leg. */
  off_route: string[];
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
  status: 'queued' | 'already_fresh' | 'realtime' | 'pending_coverage';
  flight_id: string;
  message: string;
  // Tiered refresh gate detail — present when the request was gated to a
  // real-time-only refresh or skipped entirely (never 'full' here; a full
  // decision proceeds to the pipeline instead).
  mode?: 'realtime' | 'none';
  reason?: string;
  eta_useful?: string | null;
  observations?: RouteObservations | null;
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
  // Null on a `complete` event that produced no pack — the pending-coverage
  // no-op (beyond-horizon flight). Present with a pack on a normal completion.
  pack?: PackMeta | null;
  message?: string;
  elapsed_seconds?: number;
  // Present on the `complete` event when the refresh was gated instead of
  // running a full pipeline. Two shapes share this field: the tiered gate emits
  // a full `RefreshDecision` (needed/n_eligible/…); the pending-coverage no-op
  // emits only mode/reason + `available_date`. Hence the tiered fields and
  // `available_date` are both optional.
  refresh_decision?: {
    mode: 'full' | 'realtime' | 'none';
    reason: string;
    needed?: number;
    n_eligible?: number;
    n_updated?: number;
    days_out?: number;
    eta_useful?: string | null;
    available_date?: string;  // pending-coverage no-op only
  };
  // Freshly fetched observations on the realtime gate path — mirrors the
  // non-streaming RefreshAccepted.observations so SSE consumers don't need a
  // separate reload. Null on the `none` path and full-pipeline completes.
  observations?: RouteObservations | null;
  // Freshly fetched route SIGMETs on the realtime gate path.
  sigmets?: RouteSigmets | null;
}

/**
 * Stream a briefing refresh via SSE, calling onEvent for each progress update.
 * Returns the final PackMeta on completion, or `null` when the server completed
 * with a gated no-op that produced no pack (e.g. a pending-coverage flight whose
 * date no model reaches yet — the `complete` event carries `pack: null`).
 * Throws only on an error event or a genuinely dropped stream (no `complete`).
 */
export async function refreshBriefingStream(
  flightId: string,
  onEvent: (event: RefreshStreamEvent) => void,
  force?: boolean,
  asOfDate?: string,
  notifyEmail?: boolean,
): Promise<PackMeta | null> {
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
  let sawComplete = false;

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

        if (event.type === 'complete') {
          // `complete` is the terminal event whether or not it carries a pack.
          // Gated no-ops (pending coverage) send `pack: null`; a normal refresh
          // sends the final pack. Record completion either way so a null-pack
          // completion is a defined terminal state, not mistaken for a drop.
          finalPack = event.pack ?? null;
          sawComplete = true;
        } else if (event.type === 'error') {
          throw new RefreshStreamError(event.message || 'Refresh stream error');
        }
      } catch (e) {
        if (e instanceof RefreshStreamError) throw e;
        // Skip unparseable frames
      }
    }
  }

  // No `complete` event at all → the stream genuinely dropped. (A completion
  // that carried no pack still set sawComplete and returns null below.)
  if (!sawComplete) {
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

/** Fetch the experimental front-detection artifact (route_fronts.json).
 *  404s (and is therefore expected to reject) whenever the experimental
 *  "Auto Front Detection" pref was off at generation time — callers fetch it
 *  non-blocking and treat rejection as "no fronts". */
export async function fetchRouteFronts(
  flightId: string,
  timestamp: string,
): Promise<RouteFrontsManifest> {
  return apiFetch<RouteFrontsManifest>(
    `/flights/${encodeURIComponent(flightId)}/packs/${encodeURIComponent(timestamp)}/route-fronts`
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

export type FlexibilityMode = 'none' | 'alternate' | 'same_day' | 'prev_day' | 'next_day';

export interface TimeScanStatusDTO {
  status: 'pending' | 'running' | 'done' | 'failed' | 'skipped';
  flexibility: FlexibilityMode;
  reason: string;
  updated_at: string;
}

export interface TimeConfirmationDTO {
  models_checked: string[];
  assessment: string;
  assessment_reason: string;
  /** Per-model "id=STATUS, ..." breakdown; a model with nothing flagged is absent. */
  per_model_reasons?: Record<string, string>;
  better_than_baseline: boolean;
  improves: string[];
  worsens: string[];
  confirmed_at: string;
}

export interface TimeCandidateDTO {
  departure_time: string;
  departure_shift_hours: number;
  valid_times: string[];  // per-route-point ETAs the grade actually read
  assessment: string;            // GREEN / AMBER / RED
  assessment_reason: string;
  models_used: string[];
  improves: string[];
  worsens: string[];
  margin: number;
  confidence: 'confirmed_in_window' | 'ecmwf_only' | 'confirmed';
  is_baseline: boolean;
  is_alternate: boolean;
  confirmed: TimeConfirmationDTO | null;
  confirm_pending: boolean;
}

export interface TimeWindowScanDTO {
  flexibility: FlexibilityMode;
  baseline: { departure_time: string; assessment: string; assessment_reason: string };
  window: { start: string; end: string; daylight_clipped: boolean; horizon_clipped: boolean; past_clipped?: boolean } | null;
  candidates: TimeCandidateDTO[];
  refused_times: string[];
  generated_at: string;
}

export interface TimeOptionsResponse {
  status: TimeScanStatusDTO | null;
  scan: TimeWindowScanDTO | null;
}

/** Timing-scenario scan (Flexibility) — status + result, poll-friendly.
 *  The scan runs as a background job after the briefing, so the client polls
 *  this after briefing_ready until status is terminal (done/failed/skipped).
 *  404s when the flight has Flexibility "none" and no scan was ever run. */
export async function fetchTimeOptions(
  flightId: string,
  timestamp: string,
): Promise<TimeOptionsResponse> {
  return apiFetch<TimeOptionsResponse>(
    `/flights/${encodeURIComponent(flightId)}/packs/${encodeURIComponent(timestamp)}/time-options`
  );
}

/** Re-queue the timing scan for a pack — used after "Set as alternate time"
 *  so the changed alternate gets graded and the alt artifacts re-persist. */
export async function rescanTimeOptions(
  flightId: string,
  timestamp: string,
): Promise<{ status: string }> {
  return apiFetch<{ status: string }>(
    `/flights/${encodeURIComponent(flightId)}/packs/${encodeURIComponent(timestamp)}/time-options/rescan`,
    { method: 'POST' },
  );
}

/** Queue the on-tap multi-model check of one provisional candidate (202);
 *  the result lands on the candidate via the regular time-options poll. */
export async function confirmTimeOption(
  flightId: string,
  timestamp: string,
  departureTime: string,
): Promise<{ status: string }> {
  return apiFetch<{ status: string }>(
    `/flights/${encodeURIComponent(flightId)}/packs/${encodeURIComponent(timestamp)}/time-options/confirm`,
    { method: 'POST', body: JSON.stringify({ departure_time: departureTime }) },
  );
}

export interface RoutePointWindOverlay {
  point_index: number;
  wind_components: Record<string, {
    wind_speed_kt: number;
    wind_direction_deg: number;
    track_deg: number;
    headwind_kt: number;
    crosswind_kt: number;
  }>;
}

export interface RouteWindOverlay {
  cruise_altitude_ft: number;
  points: RoutePointWindOverlay[];
}

export interface RecalculateAdvisoriesResult {
  manifest: RouteAdvisoriesManifest;
  wind_overlay: RouteWindOverlay | null;
}

export async function recalculateAdvisories(
  flightId: string,
  timestamp: string,
  cruiseAltitudeFt?: number,
): Promise<RecalculateAdvisoriesResult> {
  const qs = cruiseAltitudeFt != null ? `?cruise_altitude_ft=${cruiseAltitudeFt}` : '';
  return apiFetch<RecalculateAdvisoriesResult>(
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

/** Fetch the altitude table precomputed at refresh (#259) — cheap, no sweep.
 *  404s for packs that predate the precompute; callers fall back to the POST
 *  sweep endpoint (`fetchAltitudeTable`) in that case. */
export async function fetchAltitudeTableCached(
  flightId: string,
  timestamp: string,
): Promise<AltitudeTableResult> {
  return apiFetch<AltitudeTableResult>(
    `/flights/${encodeURIComponent(flightId)}/packs/${encodeURIComponent(timestamp)}/advisories/altitude-table`,
    { method: 'GET' },
  );
}

// --- On-demand AI summary (digest-only, no full refresh) ---

/** Generate the AI summary for an existing pack whose profile had AI off.
 *  Runs only the LLM digest against existing pack data; returns the updated
 *  pack meta (has_digest=true). */
export async function generateDigest(
  flightId: string,
  timestamp: string,
): Promise<PackMeta> {
  return apiFetch<PackMeta>(
    `/flights/${encodeURIComponent(flightId)}/packs/${encodeURIComponent(timestamp)}/digest/generate`,
    { method: 'POST' },
  );
}

// --- Observations + SIGMET refresh ---

export async function refreshObservations(
  flightId: string,
  timestamp: string,
): Promise<RealtimeRefreshResult> {
  return apiFetch<RealtimeRefreshResult>(
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

// --- Met Office surface-pressure charts ---

export function metofficeChartUrl(flightId: string, timestamp: string, chartId: string): string {
  return `${API_BASE}/flights/${encodeURIComponent(flightId)}/packs/${encodeURIComponent(timestamp)}/metoffice-chart/${encodeURIComponent(chartId)}`;
}

export function metofficeChartOverlayUrl(flightId: string, timestamp: string): string {
  return `${API_BASE}/flights/${encodeURIComponent(flightId)}/packs/${encodeURIComponent(timestamp)}/metoffice-chart-overlay`;
}

// --- Report ---

export function reportPdfUrl(flightId: string, timestamp: string): string {
  return `${API_BASE}/flights/${encodeURIComponent(flightId)}/packs/${encodeURIComponent(timestamp)}/report.pdf`;
}

/** Fetch the flight as a cross-app FlightExchange JSON payload (the format
 *  MyGAR / Manifest6 and other flyfun apps import). Returns the raw envelope
 *  dict so callers can serialize it to a downloadable file. */
export async function fetchFlightExchange(flightId: string): Promise<unknown> {
  return apiFetch<unknown>(`/flights/${encodeURIComponent(flightId)}/export`);
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
  sentiment?: 'up' | 'down' | null;
  target?: 'digest' | 'general' | null;
  contact_ok?: boolean;
}

export async function submitFeedback(req: FeedbackRequest): Promise<{ id: number; status: string }> {
  return apiFetch<{ id: number; status: string }>('/feedback', {
    method: 'POST',
    body: JSON.stringify(req),
  });
}
