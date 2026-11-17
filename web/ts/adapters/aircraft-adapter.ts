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
  /** Climb / descent (#1005): the preset picked in the form (null = follow the
   *  ICAO type) and overrides on top of it (null = the preset's value). */
  performance_preset?: string | null;
  is_turbo?: boolean;
  climb_ias_kt?: number | null;
  climb_rate_fpm?: number | null;
  descent_rate_fpm?: number | null;
  descent_angle_deg?: number | null;
  /** What the briefing's flight path uses: overrides over the class preset. */
  performance?: AircraftPerformance | null;
  created_at: string;
}

/** Resolved climb / descent performance (#1005), computed on the server. */
export interface AircraftPerformance {
  preset: string;
  source: string;
  climb_ias_kt: number;
  climb_rate_fpm: number;
  ceiling_ft: number;
  descent_mode: 'rate' | 'angle';
  descent_rate_fpm: number | null;
  descent_angle_deg: number | null;
  turbo?: boolean;
}

/** One climb / descent preset, as the aircraft form offers it (#1005). */
export interface PerformancePreset {
  id: string;
  climb_ias_kt: number;
  climb_rate_fpm: number;
  descent_mode: 'rate' | 'angle';
  descent_rate_fpm: number | null;
  descent_angle_deg: number | null;
  turbo: boolean;
}

export interface PerformancePresetsResponse {
  presets: PerformancePreset[];
  /** The preset the ICAO type (and cruise speed) points to. */
  suggested: string;
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
  /** Climb / descent (#1005): preset id ("" = follow the ICAO type) and
   *  overrides; on update, 0 clears an override back to the preset. */
  performance_preset?: string;
  is_turbo?: boolean;
  climb_ias_kt?: number | null;
  climb_rate_fpm?: number | null;
  descent_rate_fpm?: number | null;
  descent_angle_deg?: number | null;
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
  /** Climb / descent (#1005): preset id ("" = follow the ICAO type) and
   *  overrides; on update, 0 clears an override back to the preset. */
  performance_preset?: string;
  is_turbo?: boolean;
  climb_ias_kt?: number | null;
  climb_rate_fpm?: number | null;
  descent_rate_fpm?: number | null;
  descent_angle_deg?: number | null;
}

export async function searchAircraftTypes(query: string): Promise<AircraftType[]> {
  return apiFetch<AircraftType[]>(`/aircraft/types?q=${encodeURIComponent(query)}`);
}

export async function fetchPerformancePresets(
  icaoType: string, cruiseSpeedKt: number | null,
): Promise<PerformancePresetsResponse> {
  const q = new URLSearchParams({ icao_type: icaoType.trim().toUpperCase().slice(0, 4) });
  if (cruiseSpeedKt && cruiseSpeedKt > 0) q.set('cruise_speed_kt', String(cruiseSpeedKt));
  return apiFetch<PerformancePresetsResponse>(`/aircraft/presets?${q}`);
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
