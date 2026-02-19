/** Shared utilities for the WeatherBrief web app. */

import { logout, type CurrentUser } from './adapters/auth-adapter';

// --- HTML escaping ---

export function escapeHtml(text: string): string {
  return text
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#x27;');
}

// --- Shared user info rendering ---

export function renderUserInfo(user: CurrentUser): void {
  const container = document.getElementById('user-info');
  if (!container) return;
  const adminLink = user.is_admin
    ? '<a href="/admin.html" class="btn-settings" title="Admin">Admin</a>'
    : '';
  container.innerHTML = `
    ${adminLink}
    <span class="user-name">${escapeHtml(user.name)}</span>
    <button class="btn-logout" id="logout-btn">Sign out</button>
  `;
  document.getElementById('logout-btn')?.addEventListener('click', () => logout());
}

// --- DOM helpers ---

/** Shorthand for document.getElementById with non-null assertion. */
export function $(id: string): HTMLElement {
  return document.getElementById(id)!;
}

// --- Formatting helpers ---

/** Format a date string for display (e.g. "Sat, 14 Feb 2026"). */
export function formatDate(iso: string): string {
  try {
    const d = new Date(iso.includes('T') ? iso : iso + 'T00:00:00Z');
    return d.toLocaleDateString('en-GB', {
      weekday: 'short', day: 'numeric', month: 'short', year: 'numeric',
      timeZone: 'UTC',
    });
  } catch {
    return iso;
  }
}

/** Format time as 4-digit UTC (e.g. "0800Z"). */
export function formatTime(hour: number): string {
  return `${hour.toString().padStart(2, '0')}00Z`;
}

/** Format altitude for display (e.g. "FL085" or "5000ft"). */
export function formatAlt(ft: number): string {
  if (ft >= 10000) return `FL${Math.round(ft / 100)}`;
  return `${ft}ft`;
}

// --- Model catalog (populated from /api/models at startup) ---

export interface ModelCatalogEntry { key: string; name: string; default: boolean; }

let _catalog: ModelCatalogEntry[] = [];

export function initModelCatalog(catalog: ModelCatalogEntry[]): void {
  _catalog = catalog;
}

export function allModelKeys(): string[] {
  return _catalog.map(m => m.key);
}

export function defaultModelKeys(): string[] {
  return _catalog.filter(m => m.default).map(m => m.key);
}

/** Get display name for a model key (falls back to uppercase). */
export function modelLabel(model: string): string {
  return _catalog.find(m => m.key === model)?.name ?? model.toUpperCase();
}

// --- Windy URL builder ---

/** Models that have a dedicated Windy view. Others fall back to ECMWF (default). */
const WINDY_MODEL_MAP: Record<string, string> = {
  gfs: 'gfs',
  icon: 'icon',
};

/** Build a Windy meteogram URL for the given coordinates, time, and model.
 *
 * GFS/ICON: https://www.windy.com/{lat}/{lon}/{model}/meteogram?{model},{date}-{hour},{lat},{lon},{zoom},i:pressure,p:metars
 * Others:   https://www.windy.com/{lat}/{lon}/meteogram?{date}-{hour},{lat},{lon},{zoom},i:pressure,p:metars
 */
export function buildWindyUrl(
  lat: number,
  lon: number,
  time: string | Date,
  model?: string,
  zoom: number = 8,
): string {
  const iso = typeof time === 'string' ? (time.endsWith('Z') || /[+-]\d{2}:?\d{2}$/.test(time) ? time : time + 'Z') : '';
  const d = typeof time === 'string' ? new Date(iso) : time;
  const pad = (n: number) => n.toString().padStart(2, '0');
  const timePart = `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}-${pad(d.getUTCHours())}`;
  const latStr = lat.toFixed(3);
  const lonStr = lon.toFixed(3);
  const windyModel = model ? WINDY_MODEL_MAP[model] : undefined;
  const pathModel = windyModel ? `${windyModel}/` : '';
  const queryModel = windyModel ? `${windyModel},` : '';
  return `https://www.windy.com/${latStr}/${lonStr}/${pathModel}meteogram?${queryModel}${timePart},${latStr},${lonStr},${zoom},i:pressure,p:metars`;
}

/** Auto-dismiss timeout for status messages (ms). */
export const STATUS_DISMISS_MS = 3000;

// --- Shared API fetch ---

export const API_BASE = '/api';

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...init?.headers },
    ...init,
  });
  if (!resp.ok) {
    if (resp.status === 401) {
      window.location.href = '/login.html';
      throw new Error('Session expired');
    }
    if (resp.status === 403) {
      window.location.href = '/login.html';
      throw new Error('Account suspended');
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
  if (resp.status === 204) return undefined as T;
  return resp.json();
}
