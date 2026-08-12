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

    // The hamburger toggle is desktop-hidden via CSS; under ~700px it reveals
    // the .nav-menu as a dropdown. On desktop .nav-menu is a normal flex row,
    // so the wide layout is unchanged (issue #205).
    container.innerHTML = `
      <button type="button" class="nav-toggle" id="nav-toggle"
              aria-label="${t('nav.menu')}" aria-expanded="false" aria-controls="nav-menu">☰</button>
      <div class="nav-menu" id="nav-menu">
        ${links}
        <span class="user-name">${escapeHtml(user.name)}</span>
        <button class="btn-logout" id="logout-btn">${t('nav.signOut')}</button>
      </div>
    `;
    document.getElementById('logout-btn')?.addEventListener('click', () => logout());

    const toggle = document.getElementById('nav-toggle');
    const menu = document.getElementById('nav-menu');
    toggle?.addEventListener('click', (e) => {
      e.stopPropagation();
      const open = menu?.classList.toggle('open') ?? false;
      toggle.setAttribute('aria-expanded', String(open));
    });
  };

  // Initial render (no PIREP tab)
  renderNav(currentPage === 'pireps');

  // Close the mobile menu on outside-click / Escape. Attached once per page;
  // the handlers re-query the DOM so they survive nav re-renders. A no-op on
  // desktop where the menu is never in the `.open` state.
  setupNavDismiss();

  // Fire-and-forget: check pirep permission and re-render if enabled
  checkPirepNav(currentPage).then(enabled => {
    if (enabled) renderNav(true);
  });

  // Fire-and-forget: check for unseen messages and show badge
  checkMessagesBadge();

  // The header is sticky; publish its height so sticky side rails can offset.
  trackHeaderHeight();
}

let _headerObserver: ResizeObserver | null = null;

/**
 * Publish the pinned page-header's rendered height as `--header-h` on :root.
 *
 * The header is `position: sticky` on every page, but its height varies (an
 * `<h1>` on most pages, a slim nav-only bar on the briefing page, and it
 * reflows when the nav wraps or the hamburger menu takes over). Anything that
 * needs to sit below it — the briefing rail, the help TOC, `scroll-padding-top`
 * — reads the var rather than hard-coding a guess. Idempotent: safe to call on
 * every nav re-render; the observer is attached once.
 */
export function trackHeaderHeight(): void {
  const header = document.querySelector('.page-header') as HTMLElement | null;
  if (!header) return;
  const apply = (): void => {
    document.documentElement.style.setProperty('--header-h', `${header.offsetHeight}px`);
  };
  apply();
  if (_headerObserver) return;
  _headerObserver = new ResizeObserver(apply);
  _headerObserver.observe(header);
}

let _navDismissBound = false;

/** Wire outside-click and Escape to close the mobile nav dropdown. Idempotent —
 *  safe to call on every render; the listeners attach to `document` only once. */
function setupNavDismiss(): void {
  if (_navDismissBound) return;
  _navDismissBound = true;

  const close = () => {
    const ui = document.getElementById('user-info');
    const menu = ui?.querySelector('.nav-menu');
    if (!menu || !menu.classList.contains('open')) return;
    menu.classList.remove('open');
    ui?.querySelector('.nav-toggle')?.setAttribute('aria-expanded', 'false');
  };

  document.addEventListener('click', (e) => {
    const ui = document.getElementById('user-info');
    // Clicks on the toggle (stopPropagation) and on links inside the menu
    // (which navigate) are handled elsewhere; close only for clicks outside.
    if (ui && !ui.contains(e.target as Node)) close();
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') close();
  });
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

/** Format a USD amount at 4dp — the resolution the cost surfaces need, where
 *  a per-briefing figure rounds to $0.00 at 2dp. */
export function usd4(n: number): string {
  return `$${n.toFixed(4)}`;
}

/** Format a USD amount at 4dp, keeping a minus sign in front of the currency.
 *
 *  For quantities that are legitimately negative — a cache "saving" is a real
 *  surcharge in a write-heavy window — where `$-0.0300` reads as a typo. Only
 *  the sign placement differs from `usd4`. */
export function signedUsd4(n: number): string {
  return n < 0 ? `-${usd4(Math.abs(n))}` : usd4(n);
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

export interface ModelCatalogEntry { key: string; name: string; default: boolean; max_days?: number; }

let _catalog: ModelCatalogEntry[] = [];

export function initModelCatalog(catalog: ModelCatalogEntry[]): void {
  _catalog = catalog;
}

/** The full model catalog as last loaded (empty until initModelCatalog runs). */
export function getModelCatalog(): ModelCatalogEntry[] {
  return _catalog;
}

/** Booking limits served by `/api/models/config` (see api/models.py) so the
 *  date picker shares one source with the backend gate instead of hardcoding. */
export interface BookingConfig {
  max_booking_lead_days: number;
  forecast_horizon_days: number;
}

let _bookingConfig: BookingConfig | null = null;

export function initBookingConfig(cfg: BookingConfig): void {
  _bookingConfig = cfg;
}

/** How far ahead a flight may be saved (backend-served via `/api/models/config`).
 *  Beyond the forecast horizon but within this cap, a flight saves in a "pending
 *  coverage" state and briefs once a model run reaches the date; past the cap the
 *  backend rejects the save. Bounds the flight-date picker. Falls back to
 *  `fallback` only if the config endpoint hasn't loaded (network failure). */
export function maxBookingLeadDays(fallback = 180): number {
  return _bookingConfig?.max_booking_lead_days ?? fallback;
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

/** In-place-upgrade badges, keyed by the freshness source key the pack records
 *  in `model_sources`. A model SLOT can be served by a higher-resolution
 *  variant of the same family — the icon slot by ICON-D2 (2.2 km) on short
 *  central-European routes (#456), the gfs slot by HRRR (3 km) on CONUS routes
 *  inside the HRRR horizon (#457). `modelLabel()` keys off the model name
 *  alone and cannot tell the variants apart, so the source key supplies the
 *  tag. A table rather than a conditional chain: the next variant is one row.
 */
const MODEL_SLOT_BADGES: Record<string, string> = {
  'icon_d2:dwd': 'D2',
  'hrrr:noaa': 'HRRR',
};

/** `modelLabel()` plus a variant badge when the slot was served by a
 *  higher-resolution source — e.g. `ICON (D2)`, `GFS (HRRR)`. An unknown or
 *  missing source key is not an error: it just means the slot ran on its
 *  default model, so the plain label is correct. */
export function modelSlotLabel(model: string, source?: string | null): string {
  const badge = source ? MODEL_SLOT_BADGES[source] : undefined;
  return badge ? `${modelLabel(model)} (${badge})` : modelLabel(model);
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

export interface CompactRoute {
  /** Rendered HTML-safe route, possibly truncated with an ellipsis. */
  html: string;
  /** Full untruncated plain-text route, suitable for `title=""`. */
  fullText: string;
  /** Whether the route was abbreviated. */
  isTruncated: boolean;
}

/** Compact route renderer: shows first 2 + ellipsis + last 2 when the route
 *  has more than `maxVisible` (default 4) waypoints; otherwise the full
 *  chain. Returns HTML-escaped output ready for innerHTML. */
export function flightRouteCompact(waypoints: string[], maxVisible: number = 4): CompactRoute {
  if (waypoints.length === 0) {
    return { html: '', fullText: '', isTruncated: false };
  }
  const fullText = waypoints.join(' \u2192 ');
  const escaped = waypoints.map(escapeHtml);
  if (waypoints.length <= maxVisible) {
    return { html: escaped.join(' \u2192 '), fullText, isTruncated: false };
  }
  const head = escaped.slice(0, 2).join(' \u2192 ');
  const tail = escaped.slice(-2).join(' \u2192 ');
  return {
    html: `${head} \u2192 \u2026 \u2192 ${tail}`,
    fullText,
    isTruncated: true,
  };
}

/** Normalize an unknown caught value into a user-facing error string.
 *  Strips the `API 404:` / `API 422:` prefix added by `apiFetch` so the
 *  surfaced message is just the backend `detail` text. */
export function errorToMessage(err: unknown): string {
  const raw = err instanceof Error ? err.message : String(err);
  return raw.replace(/^API \d+:\s*/, '');
}

/** Build the shareable URL that recipients open to see a flight.
 *
 *  Prefers the short ``/s/{code}`` form when a ``shareCode`` is available
 *  (every flight created after migration 049 has one). For long routes
 *  this trims the URL from ~80+ chars of slug back down to ~10 chars.
 *
 *  When `packTimestamp` is given, the link points at that specific
 *  briefing pack on the briefing page so the recipient sees the exact
 *  briefing the sender was looking at — using the short form keeps the
 *  URL compact even with the pack query string.
 *
 *  Falls back to the legacy ``?id=`` URL when no shareCode is provided
 *  (very old rows, or callers that don't have it on hand). */
export function flightShareUrl(
  flightId: string,
  packTimestamp?: string | null,
  shareCode?: string | null,
): string {
  if (shareCode) {
    const base = `${window.location.origin}/s/${encodeURIComponent(shareCode)}`;
    if (packTimestamp) {
      return `${base}?pack=${encodeURIComponent(packTimestamp)}`;
    }
    return base;
  }
  if (packTimestamp) {
    return `${window.location.origin}/briefing.html?flight=${encodeURIComponent(flightId)}&pack=${encodeURIComponent(packTimestamp)}`;
  }
  return `${window.location.origin}/flight.html?id=${encodeURIComponent(flightId)}`;
}

/** Share the flight (or specific briefing pack) URL.
 *
 *  Tier 1: ``navigator.share()`` — opens the OS share sheet on iOS,
 *  macOS Safari, Android Chrome, and recent Edge/Chrome on Windows.
 *  Lets the user pick iMessage, Mail, AirDrop, etc. directly. The
 *  Promise resolves on send and rejects with ``AbortError`` if the
 *  user dismisses the sheet — we treat that as a no-op (no toast).
 *
 *  Tier 2: ``navigator.clipboard.writeText()`` — desktop browsers
 *  without share-sheet support. Returns true so the caller can flash
 *  the "copied" indicator on the trigger button.
 *
 *  Tier 3: ``prompt()`` — last-resort fallback for non-HTTPS origins
 *  where the clipboard API is unavailable. Returns false so callers
 *  skip the flash (the user already saw the URL inline).
 *
 *  Returns ``true`` when something happened that doesn't need a
 *  toolbar flash (share sheet shown / share sheet aborted), or when
 *  the clipboard copy succeeded; ``false`` only when we fell all the
 *  way back to ``prompt()``. */
export async function copyFlightShareLink(
  flightId: string,
  packTimestamp?: string | null,
  shareCode?: string | null,
): Promise<boolean> {
  const url = flightShareUrl(flightId, packTimestamp, shareCode);

  if (typeof navigator.share === 'function') {
    try {
      await navigator.share({
        title: t('flightDetail.copyShareLinkTitle'),
        url,
      });
      return true;
    } catch (err) {
      // User cancelled the sheet — don't fall back to clipboard, they
      // explicitly bailed. Other errors (NotAllowedError on http://,
      // share unsupported for this payload) drop through to clipboard.
      if (err instanceof DOMException && err.name === 'AbortError') {
        return true;
      }
      // fall through
    }
  }

  try {
    await navigator.clipboard.writeText(url);
    alert(t('flightDetail.copyShareLinkAlert'));
    return true;
  } catch {
    prompt(t('flightDetail.copyShareLinkFallback'), url);
    return false;
  }
}

/** Auto-dismiss timeout for status messages (ms). */
export const STATUS_DISMISS_MS = 3000;

// --- Login redirect with next-URL preservation ---

/** Redirect to /login.html, preserving the current path+query in `?next=` so
 *  the server can bounce the user back after OAuth. Skips `next` when already
 *  on root or the login page itself. */
export function redirectToLogin(): void {
  const path = location.pathname;
  if (path === '/' || path === '/login.html') {
    location.href = '/login.html';
    return;
  }
  const next = path + location.search;
  location.href = '/login.html?next=' + encodeURIComponent(next);
}

// --- Shared API fetch ---

export const API_BASE = '/api';

/**
 * @param onResponse Inspect response metadata (e.g. read a header) before the
 *   body is parsed. Read headers only — do NOT consume the body (e.g. by
 *   calling `resp.json()`/`resp.text()`), or the `resp.json()` below will reject.
 */
export async function apiFetch<T>(
  path: string,
  init?: RequestInit,
  onResponse?: (resp: Response) => void,
): Promise<T> {
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
      redirectToLogin();
      throw new Error('Session expired');
    }
    if (resp.status === 403) {
      const body403 = await resp.text();
      let detail403 = '';
      try { detail403 = JSON.parse(body403).detail || ''; } catch { /* */ }
      // Only redirect for auth-level rejections (suspended account),
      // not feature-gating 403s (e.g. PIREP permissions). No ?next=: the
      // user is already authenticated, re-logging won't lift the block,
      // and bouncing them back to the 403ing page would tight-loop.
      if (detail403 === 'Account suspended' || detail403 === 'Account is not approved') {
        location.href = '/login.html';
        throw new Error('Account suspended');
      }
      throw new Error(`API 403: ${detail403 || body403}`);
    }
    const body = await resp.text();
    let parsed: unknown;
    try {
      parsed = JSON.parse(body).detail ?? body;
    } catch {
      parsed = body;
    }
    // A structured detail ({error, codes, message}) used to stringify as
    // "[object Object]" here, hiding the only useful part. Prefer its `message`
    // for display, and hand the whole object to the caller on `.detail` so a
    // form can act on the specific field it names (e.g. the unknown ICAOs in
    // the declared-approach list) rather than re-parsing prose.
    const isObj = typeof parsed === 'object' && parsed !== null;
    const message = isObj
      ? String((parsed as { message?: unknown }).message ?? body)
      : String(parsed || body);
    const err = new Error(`API ${resp.status}: ${message}`) as Error & {
      status?: number;
      detail?: unknown;
    };
    err.status = resp.status;
    err.detail = parsed;
    throw err;
  }
  onResponse?.(resp);
  if (resp.status === 204) return undefined as T;
  return resp.json();
}
