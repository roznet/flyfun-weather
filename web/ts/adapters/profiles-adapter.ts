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
  auto_front_detection: boolean | null;
  compute_alternates: boolean | null;
  icing_method: string | null;
  cloud_method: string | null;
  convective_method: string | null;
  flight_rules: string | null;
  digest_guidance: string | null;
  advisories: AdvisoryPreferences | null;
  /** Setup-interview answers (#387, slice 3): {question_id: option_id}. Stored
   *  for idempotent re-runs — the assistant pre-selects these. */
  interview?: Record<string, string> | null;
}

export interface ProfileResponse {
  id: number;
  name: string;
  is_default: boolean;
  settings: ProfileSettings;
  system_template_key: string | null;
  created_at: string;
  updated_at: string;
}

export interface SystemTemplate {
  key: string;
  name: string;
  description: string;
  settings: ProfileSettings;
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

export async function resetProfileToTemplate(id: number): Promise<ProfileResponse> {
  return apiFetch<ProfileResponse>(`/user/profiles/${id}/reset`, { method: 'POST' });
}

export async function fetchSystemTemplates(locale = 'en'): Promise<SystemTemplate[]> {
  return apiFetch<SystemTemplate[]>(`/user/profiles/system-templates?locale=${locale}`);
}

export interface DigestGuidancePreset {
  key: string;
  name: string;
  description: string;
}

export async function fetchDigestGuidancePresets(locale = 'en'): Promise<DigestGuidancePreset[]> {
  return apiFetch<DigestGuidancePreset[]>(`/user/profiles/digest-guidance-presets?locale=${locale}`);
}

export async function fetchDigestGuidanceText(key: string): Promise<string> {
  const resp = await apiFetch<{ key: string; text: string }>(`/user/profiles/digest-guidance-presets/${key}/text`);
  return resp.text;
}
