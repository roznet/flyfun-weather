/** PIREP adapter — submit and query pilot weather reports. */

import { apiFetch } from '../utils';

export interface PirepResponse {
  id: number;
  client_uuid: string | null;
  submitted_at: string;
  observed_at: string;
  latitude: number;
  longitude: number;
  gps_altitude_ft: number | null;
  reported_altitude_ft: number | null;
  in_cloud: boolean | null;
  icing_intensity: string | null;
  icing_type: string | null;
  turbulence_intensity: string | null;
  ceiling_msl_ft: number | null;
  tops_msl_ft: number | null;
  tops_basis: string | null;
  temp_c: number | null;
  wind_dir: number | null;
  wind_speed_kt: number | null;
  remarks: string | null;
  aircraft_type: string | null;
  pack_id: number | null;
  source: string;
  is_own: boolean;
}

export interface PirepListResponse {
  items: PirepResponse[];
  count: number;
}

export interface SubmitPirepRequest {
  client_uuid?: string;
  observed_at: string;
  latitude: number;
  longitude: number;
  gps_altitude_ft?: number | null;
  reported_altitude_ft?: number | null;
  in_cloud?: boolean | null;
  icing_intensity?: string | null;
  icing_type?: string | null;
  turbulence_intensity?: string | null;
  ceiling_msl_ft?: number | null;
  tops_msl_ft?: number | null;
  tops_basis?: string | null;
  temp_c?: number | null;
  wind_dir?: number | null;
  wind_speed_kt?: number | null;
  remarks?: string | null;
  aircraft_id?: number | null;
  pack_id?: number | null;
  source?: string;
}

export interface PirepFilters {
  hazard?: string;
  min_severity?: string;
  altitude_min?: number;
  altitude_max?: number;
  aircraft_type?: string;
}

function buildQueryString(params: Record<string, string | number | undefined>): string {
  const parts: string[] = [];
  for (const [k, v] of Object.entries(params)) {
    if (v !== undefined && v !== null && v !== '') {
      parts.push(`${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`);
    }
  }
  return parts.length ? `?${parts.join('&')}` : '';
}

export async function submitPirep(req: SubmitPirepRequest): Promise<PirepResponse> {
  return apiFetch<PirepResponse>('/pireps', {
    method: 'POST',
    body: JSON.stringify(req),
  });
}

export async function submitPirepsBatch(reqs: SubmitPirepRequest[]): Promise<PirepResponse[]> {
  return apiFetch<PirepResponse[]>('/pireps/batch', {
    method: 'POST',
    body: JSON.stringify(reqs),
  });
}

export async function fetchPirepsByFlight(flightId: string, filters?: PirepFilters): Promise<PirepListResponse> {
  const qs = buildQueryString({ flight_id: flightId, ...filters });
  return apiFetch<PirepListResponse>(`/pireps${qs}`);
}

export async function fetchPirepsByPack(packId: number, filters?: PirepFilters): Promise<PirepListResponse> {
  const qs = buildQueryString({ pack_id: packId, ...filters });
  return apiFetch<PirepListResponse>(`/pireps${qs}`);
}

export async function fetchPirepsByBounds(
  bounds: string, hours: number = 6, filters?: PirepFilters
): Promise<PirepListResponse> {
  const qs = buildQueryString({ bounds, hours, ...filters });
  return apiFetch<PirepListResponse>(`/pireps${qs}`);
}

export async function fetchPirepsByAirport(
  icao: string, hours: number = 6, filters?: PirepFilters
): Promise<PirepListResponse> {
  const qs = buildQueryString({ airport: icao, hours, ...filters });
  return apiFetch<PirepListResponse>(`/pireps${qs}`);
}

export async function fetchPirepsByTimeRange(
  from: string, to: string, filters?: PirepFilters
): Promise<PirepListResponse> {
  const qs = buildQueryString({ from, to, ...filters });
  return apiFetch<PirepListResponse>(`/pireps${qs}`);
}

export async function fetchRecentPireps(hours: number = 6, filters?: PirepFilters): Promise<PirepListResponse> {
  const qs = buildQueryString({ hours, ...filters });
  return apiFetch<PirepListResponse>(`/pireps${qs}`);
}
