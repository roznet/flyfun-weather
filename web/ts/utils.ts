/** Shared utilities for the Flyfun Weather web app. */

import { logout, type CurrentUser } from './adapters/auth-adapter';
import { t, getAcceptLanguage, getLocale } from './i18n/i18n';

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

export function renderUserInfo(user: CurrentUser, currentPage?: string): void {
  const container = document.getElementById('user-info');
  if (!container) return;

  const navItems: { label: string; href: string; page: string; adminOnly?: boolean; pirepOnly?: boolean; badgeId?: string }[] = [
    { label: t('nav.flights'),  href: '/',               page: 'flights' },
    { label: 'PIREPs',          href: '/pireps.html',    page: 'pireps', pirepOnly: true },
    { label: 'Forecast',        href: '/maps.html',      page: 'maps' },
    { label: t('nav.settings'), href: '/settings.html',  page: 'settings' },
    { label: t('nav.help'),     href: '/help.html',      page: 'help', badgeId: 'nav-messages-badge' },
    { label: t('nav.admin'),    href: '/admin.html',     page: 'admin', adminOnly: true },
  ];

  // Render nav without PIREP initially; it will appear once preferences load
  const renderNav = (pirepEnabled: boolean) => {
    const links = navItems
      .filter(item => !item.adminOnly || user.is_admin)
      .filter(item => !item.pirepOnly || pirepEnabled)
      .map(item => {
        const badge = item.badgeId ? `<span class="nav-dot" id="${item.badgeId}"></span>` : '';
        if (item.page === currentPage) {
          return `<span class="btn-settings nav-current">${item.label}${badge}</span>`;
        }
        return `<a href="${item.href}" class="btn-settings" title="${item.label}">${item.label}${badge}</a>`;
      })
      .join('\n');

    container.innerHTML = `
      ${links}
      <span class="user-name">${escapeHtml(user.name)}</span>
      <button class="btn-logout" id="logout-btn">${t('nav.signOut')}</button>
    `;
    document.getElementById('logout-btn')?.addEventListener('click', () => logout());
  };

  // Initial render (no PIREP tab)
  renderNav(currentPage === 'pireps');

  // Fire-and-forget: check pirep permission and re-render if enabled
  checkPirepNav(currentPage).then(enabled => {
    if (enabled) renderNav(true);
  });

  // Fire-and-forget: check for unseen messages and show badge
  checkMessagesBadge();
}

/** Check if the user has PIREP viewing enabled. */
async function checkPirepNav(currentPage?: string): Promise<boolean> {
  // If we're already on the PIREPs page, always show the tab
  if (currentPage === 'pireps') return true;
  try {
    const { fetchPreferences } = await import('./adapters/preferences-adapter');
    const prefs = await fetchPreferences();
    return prefs.pirep_can_view;
  } catch {
    return false;
  }
}

/** Check for unseen messages and show/hide the nav badge dot. */
export async function checkMessagesBadge(): Promise<void> {
  try {
    const { fetchMessagesStatus } = await import('./adapters/messages-adapter');
    const status = await fetchMessagesStatus();
    const dot = document.getElementById('nav-messages-badge');
    if (dot) {
      dot.classList.toggle('visible', status.unseen_count > 0);
    }
  } catch {
    // Silently ignore — badge is non-critical
  }
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
    const locale = getLocale() === 'en' ? 'en-GB' : getLocale();
    return d.toLocaleDateString(locale, {
      weekday: 'short', day: 'numeric', month: 'short', year: 'numeric',
      timeZone: 'UTC',
    });
  } catch {
    return iso;
  }
}

/** Format time as 4-digit UTC (e.g. "0800Z" or "0930Z"). */
export function formatTime(hour: number, minute: number = 0): string {
  return `${hour.toString().padStart(2, '0')}${minute.toString().padStart(2, '0')}Z`;
}

/** Extract hours+minutes from an ISO datetime string and format as "0930Z". */
export function formatDepartureTime(iso: string): string {
  try {
    const d = new Date(iso);
    return formatTime(d.getUTCHours(), d.getUTCMinutes());
  } catch {
    return iso;
  }
}

/** Format altitude for display (e.g. "FL085" or "5000ft"). */
export function formatAlt(ft: number): string {
  if (ft >= 10000) return `FL${Math.round(ft / 100)}`;
  return `${ft}ft`;
}

/** Check if a flight's end time (start + duration) is in the past.
 *  Accepts either a departure_time ISO string or legacy target_date + target_time_utc. */
export function isFlightPast(targetDate: string, targetTimeUtc: number, durationHours: number, departureTime?: string): boolean {
  const startMs = departureTime
    ? new Date(departureTime).getTime()
    : new Date(`${targetDate}T${targetTimeUtc.toString().padStart(2, '0')}:00:00Z`).getTime();
  if (isNaN(startMs)) return false;
  const endMs = startMs + durationHours * 3600_000;
  return Date.now() > endMs;
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
  return _catalog.filter(m => m.default && m.key !== 'best_match').map(m => m.key);
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

// --- Flight title helpers ---

/** Build a short title from waypoints: origin → destination. */
export function flightTitle(waypoints: string[]): string {
  if (waypoints.length === 0) return '';
  if (waypoints.length === 1) return waypoints[0];
  return `${waypoints[0]} \u2192 ${waypoints[waypoints.length - 1]}`;
}

/** Full route string from waypoints (all joined with →). */
export function flightRoute(waypoints: string[]): string {
  return waypoints.join(' \u2192 ');
}

/** Normalize an unknown caught value into a user-facing error string.
 *  Strips the `API 404:` / `API 422:` prefix added by `apiFetch` so the
 *  surfaced message is just the backend `detail` text. */
export function errorToMessage(err: unknown): string {
  const raw = err instanceof Error ? err.message : String(err);
  return raw.replace(/^API \d+:\s*/, '');
}

/** Build the shareable URL that recipients open to see a flight. Lands on
 *  the flight detail page, which has the Subscribe banner for non-owners. */
export function flightShareUrl(flightId: string): string {
  return `${window.location.origin}/flight.html?id=${encodeURIComponent(flightId)}`;
}

/** Copy the flight share URL to the clipboard. Falls back to a `prompt()`
 *  with the URL pre-filled on browsers where the clipboard API is unavailable
 *  (e.g. plain http origins). Returns true on clipboard success, false on
 *  fallback — callers can use this to decide whether to flash a "copied"
 *  indicator. Throws only on truly unexpected errors. */
export async function copyFlightShareLink(flightId: string): Promise<boolean> {
  const url = flightShareUrl(flightId);
  try {
    await navigator.clipboard.writeText(url);
    return true;
  } catch {
    prompt(t('flightDetail.copyShareLinkFallback'), url);
    return false;
  }
}

/** Auto-dismiss timeout for status messages (ms). */
export const STATUS_DISMISS_MS = 3000;

// --- Shared API fetch ---

export const API_BASE = '/api';

export async function apiFetch<T>(path: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(`${API_BASE}${path}`, {
    headers: {
      'Content-Type': 'application/json',
      'Accept-Language': getAcceptLanguage(),
      ...init?.headers,
    },
    ...init,
  });
  if (!resp.ok) {
    if (resp.status === 401) {
      window.location.href = '/login.html';
      throw new Error('Session expired');
    }
    if (resp.status === 403) {
      const body403 = await resp.text();
      let detail403 = '';
      try { detail403 = JSON.parse(body403).detail || ''; } catch { /* */ }
      // Only redirect for auth-level rejections (suspended account),
      // not feature-gating 403s (e.g. PIREP permissions).
      if (detail403 === 'Account suspended' || detail403 === 'Account is not approved') {
        window.location.href = '/login.html';
        throw new Error('Account suspended');
      }
      throw new Error(`API 403: ${detail403 || body403}`);
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
