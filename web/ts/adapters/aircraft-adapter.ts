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
  /** True only on the "My aircraft" stand-in the server synthesises so every
   *  user always has a default aircraft (#777). Any PUT clears it; a POST while
   *  it is the user's only aircraft replaces it server-side. */
  is_placeholder: boolean;
  created_at: string;
}

/** Display label for an aircraft in a picker: "TAIL — Type (nickname)".
 *  A placeholder carries a meaningless type (ZZZZ), so it shows its nickname. */
export function aircraftLabel(ac: AircraftResponse): string {
  if (ac.is_placeholder) return ac.nickname || ac.type_name;
  const base = ac.tail_number ? `${ac.tail_number} — ${ac.type_name}` : ac.type_name;
  return ac.nickname ? `${base} (${ac.nickname})` : base;
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
