/** Shared utilities for the WeatherBrief web app. */

import { logout } from './adapters/auth-adapter';

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

export function renderUserInfo(name: string): void {
  const container = document.getElementById('user-info');
  if (!container) return;
  container.innerHTML = `
    <span class="user-name">${escapeHtml(name)}</span>
    <button class="btn-logout" id="logout-btn">Sign out</button>
  `;
  document.getElementById('logout-btn')?.addEventListener('click', () => logout());
}

// --- Shared API fetch ---

const API_BASE = '/api';

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
