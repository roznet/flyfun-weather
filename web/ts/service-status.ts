/**
 * Service-status banner: what the page shows when the server can't answer.
 *
 * When the app behind weather.flyfun.aero is down or switched into maintenance,
 * Caddy answers API calls with a 502/503/504 and a small JSON body written by
 * scripts/ops/maintenance.py:
 *
 *   {"service_status": "maintenance" | "unavailable",
 *    "detail": "FlyFun Weather is down for planned maintenance. Expected back around 14:30Z.",
 *    "until": "2026-11-07T14:30:00Z" | null, "updated_at": "..."}
 *
 * apiFetch recognises it with parseServiceStatus, throws the sentence as the
 * error message (instead of "API 502: <html>") and shows one banner at the top
 * of the page. The next successful API call clears it.
 */

export type ServiceStatusKind = 'maintenance' | 'unavailable';

export interface ServiceStatus {
  kind: ServiceStatusKind;
  message: string;
  until: Date | null;
}

/** The Caddy status body on a 5xx, or null for anything else (an app error). */
export function parseServiceStatus(status: number, body: string): ServiceStatus | null {
  if (status < 500) return null;
  let obj: unknown;
  try {
    obj = JSON.parse(body);
  } catch {
    return null;
  }
  if (typeof obj !== 'object' || obj === null) return null;
  const o = obj as { service_status?: unknown; detail?: unknown; until?: unknown };
  if (o.service_status !== 'maintenance' && o.service_status !== 'unavailable') return null;
  const message = typeof o.detail === 'string' && o.detail.trim()
    ? o.detail.trim()
    : 'FlyFun Weather is temporarily unavailable. Please try again in a few minutes.';
  let until: Date | null = null;
  if (typeof o.until === 'string') {
    const d = new Date(o.until);
    if (!Number.isNaN(d.getTime())) until = d;
  }
  return { kind: o.service_status, message, until };
}

const BANNER_ID = 'service-status-banner';

/** Show (or update) the banner. A no-op outside a browser. */
export function showServiceStatusBanner(s: ServiceStatus): void {
  if (typeof document === 'undefined' || !document.body) return;
  let el = document.getElementById(BANNER_ID);
  if (!el) {
    el = document.createElement('div');
    el.id = BANNER_ID;
    el.className = 'service-status-banner';
    el.setAttribute('role', 'status');
    document.body.prepend(el);
  }
  el.dataset.kind = s.kind;
  let text = s.message;
  if (s.until) {
    const local = s.until.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    text += ` (${local} your time)`;
  }
  el.textContent = text;
}

/** Remove the banner after the server answers again. */
export function clearServiceStatusBanner(): void {
  if (typeof document === 'undefined') return;
  document.getElementById(BANNER_ID)?.remove();
}
