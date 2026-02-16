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

/** NWP model display names. */
export const MODEL_DISPLAY_NAMES: Record<string, string> = {
  gfs: 'GFS',
  ecmwf: 'ECMWF',
  icon: 'ICON',
  ukmo: 'UKMO',
  meteofrance: 'Météo-France',
};

/** Get display name for a model key (falls back to uppercase). */
export function modelLabel(model: string): string {
  return MODEL_DISPLAY_NAMES[model] ?? model.toUpperCase();
}

/** All available NWP model keys. */
export const ALL_MODELS = ['gfs', 'ecmwf', 'icon', 'ukmo', 'meteofrance'] as const;

/** Default models for new users. */
export const DEFAULT_MODELS = ['gfs', 'ecmwf', 'icon'];

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
