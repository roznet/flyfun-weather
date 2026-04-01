/** Aircraft adapter — CRUD for user aircraft and ICAO type search. */

import { apiFetch } from '../utils';

export interface AircraftType {
  icao: string;
  manufacturer: string;
  model: string;
  category: string | null;
}

export interface AircraftResponse {
  id: number;
  icao_type: string;
  type_name: string;
  tail_number: string | null;
  nickname: string | null;
  is_ifr: boolean;
  is_fiki: boolean;
  cruise_speed_kt: number | null;
  ceiling_ft: number | null;
  is_default: boolean;
  created_at: string;
}

export interface CreateAircraftRequest {
  icao_type: string;
  tail_number?: string | null;
  nickname?: string | null;
  is_ifr?: boolean;
  is_fiki?: boolean;
  cruise_speed_kt?: number | null;
  ceiling_ft?: number | null;
  is_default?: boolean;
}

export interface UpdateAircraftRequest {
  icao_type?: string;
  tail_number?: string | null;
  nickname?: string | null;
  is_ifr?: boolean;
  is_fiki?: boolean;
  cruise_speed_kt?: number | null;
  ceiling_ft?: number | null;
  is_default?: boolean;
}

export async function searchAircraftTypes(query: string): Promise<AircraftType[]> {
  return apiFetch<AircraftType[]>(`/aircraft/types?q=${encodeURIComponent(query)}`);
}

export async function fetchAircraft(): Promise<AircraftResponse[]> {
  return apiFetch<AircraftResponse[]>('/aircraft');
}

export async function createAircraft(req: CreateAircraftRequest): Promise<AircraftResponse> {
  return apiFetch<AircraftResponse>('/aircraft', {
    method: 'POST',
    body: JSON.stringify(req),
  });
}

export async function updateAircraft(id: number, req: UpdateAircraftRequest): Promise<AircraftResponse> {
  return apiFetch<AircraftResponse>(`/aircraft/${id}`, {
    method: 'PUT',
    body: JSON.stringify(req),
  });
}

export async function deleteAircraft(id: number): Promise<void> {
  return apiFetch<void>(`/aircraft/${id}`, { method: 'DELETE' });
}
