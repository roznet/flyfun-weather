/** Profiles adapter — CRUD for flight profiles. */

import { apiFetch } from '../utils';
import type { AdvisoryPreferences } from './preferences-adapter';

export interface ProfileSettings {
  cruise_altitude_ft: number | null;
  flight_ceiling_ft: number | null;
  speed_kt: number | null;
  models: string[] | null;
  advisory_models: string[] | null;
  gramet_enabled: boolean | null;
  llm_digest_enabled: boolean | null;
  icing_severity_enhance: boolean | null;
  advisories: AdvisoryPreferences | null;
}

export interface ProfileResponse {
  id: number;
  name: string;
  is_default: boolean;
  settings: ProfileSettings;
  created_at: string;
  updated_at: string;
}

export async function fetchProfiles(): Promise<ProfileResponse[]> {
  return apiFetch<ProfileResponse[]>('/user/profiles');
}

export async function createProfile(name: string, settings?: Partial<ProfileSettings>): Promise<ProfileResponse> {
  return apiFetch<ProfileResponse>('/user/profiles', {
    method: 'POST',
    body: JSON.stringify({ name, settings }),
  });
}

export async function updateProfile(
  id: number,
  update: { name?: string; settings?: Partial<ProfileSettings>; is_default?: boolean },
): Promise<ProfileResponse> {
  return apiFetch<ProfileResponse>(`/user/profiles/${id}`, {
    method: 'PUT',
    body: JSON.stringify(update),
  });
}

export async function deleteProfile(id: number): Promise<void> {
  return apiFetch<void>(`/user/profiles/${id}`, { method: 'DELETE' });
}

export async function duplicateProfile(id: number, name: string): Promise<ProfileResponse> {
  return apiFetch<ProfileResponse>(`/user/profiles/${id}/duplicate`, {
    method: 'POST',
    body: JSON.stringify({ name }),
  });
}
