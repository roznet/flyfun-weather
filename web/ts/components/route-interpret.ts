/**
 * Route interpretation component — smart waypoint parsing with confirmation popup.
 *
 * Accepts raw route text, calls the interpret-route API to resolve known waypoints,
 * and shows a confirmation popup if any tokens were skipped.
 */

import { interpretRoute, type InterpretRouteResponse } from '../adapters/api-adapter';
import { escapeHtml } from '../utils';
import { t } from '../i18n/i18n';

export interface RouteInterpretResult {
  /** The final waypoint codes to use. */
  waypoints: string[];
  /** Whether the user confirmed (true) or cancelled (false). */
  confirmed: boolean;
}

/**
 * Interpret a raw route string and, if tokens were skipped, show a confirmation popup.
 * Returns the interpreted waypoints if confirmed, or null if cancelled/failed.
 *
 * If no tokens were skipped, returns immediately without showing a popup.
 */
export async function interpretAndConfirmRoute(
  rawRoute: string,
  onError: (msg: string) => void,
): Promise<RouteInterpretResult | null> {
  let resp: InterpretRouteResponse;
  try {
    resp = await interpretRoute(rawRoute);
  } catch (err) {
    onError(err instanceof Error ? err.message.replace(/^API \d+:\s*/, '') : String(err));
    return null;
  }

  if (resp.interpreted.length < 2) {
    onError(t('flights.form.errorWaypoints'));
    return null;
  }

  // No tokens skipped — proceed directly
  if (resp.skipped.length === 0) {
    return { waypoints: resp.interpreted, confirmed: true };
  }

  // Tokens were skipped — show confirmation popup
  return showRouteConfirmPopup(rawRoute, resp);
}

/**
 * Validate that new waypoints preserve the origin and destination of an existing flight.
 * Returns an error message if validation fails, or null if OK.
 */
export function validateOriginDestination(
  oldWaypoints: string[],
  newWaypoints: string[],
): string | null {
  if (oldWaypoints.length < 2 || newWaypoints.length < 2) return null;
  const oldOrigin = oldWaypoints[0].toUpperCase();
  const oldDest = oldWaypoints[oldWaypoints.length - 1].toUpperCase();
  const newOrigin = newWaypoints[0].toUpperCase();
  const newDest = newWaypoints[newWaypoints.length - 1].toUpperCase();

  if (newOrigin !== oldOrigin) {
    return `Origin cannot change (was ${oldOrigin}, got ${newOrigin}). Create a new flight instead.`;
  }
  if (newDest !== oldDest) {
    return `Destination cannot change (was ${oldDest}, got ${newDest}). Create a new flight instead.`;
  }
  return null;
}

function showRouteConfirmPopup(
  rawRoute: string,
  resp: InterpretRouteResponse,
): Promise<RouteInterpretResult> {
  return new Promise((resolve) => {
    const backdrop = document.createElement('div');
    backdrop.className = 'metric-popup-backdrop active';

    const modal = document.createElement('div');
    modal.className = 'metric-popup';

    const skippedHtml = resp.skipped.map(s => `<code>${escapeHtml(s)}</code>`).join(', ');
    const interpretedHtml = resp.interpreted.map(s => `<strong>${escapeHtml(s)}</strong>`).join(' \u2192 ');

    modal.innerHTML = `
      <button class="metric-popup-close" aria-label="${t('popup.close')}">\u00d7</button>
      <h3>Route Interpreted</h3>
      <div style="margin:0.75rem 0;">
        <div style="margin-bottom:0.5rem;">
          <span class="info-label">Route entered:</span>
          <span style="font-family:monospace;">${escapeHtml(rawRoute)}</span>
        </div>
        <div style="margin-bottom:0.5rem;">
          <span class="info-label">Route interpreted:</span>
          <span>${interpretedHtml}</span>
        </div>
        <div style="margin-bottom:0.5rem;color:var(--amber,#b45309);">
          <span class="info-label">Skipped (not recognized):</span>
          <span>${skippedHtml}</span>
        </div>
      </div>
      <div style="display:flex;gap:0.5rem;justify-content:flex-end;margin-top:1rem;">
        <button type="button" id="route-confirm-cancel" class="btn btn-outline btn-sm">Cancel</button>
        <button type="button" id="route-confirm-accept" class="btn btn-primary btn-sm">Accept</button>
      </div>
    `;

    backdrop.appendChild(modal);
    document.body.appendChild(backdrop);

    // Stop clicks inside modal from closing
    modal.addEventListener('click', (e) => e.stopPropagation());

    const close = (confirmed: boolean) => {
      document.removeEventListener('keydown', onEsc);
      backdrop.remove();
      resolve({
        waypoints: resp.interpreted,
        confirmed,
      });
    };

    const onEsc = (e: KeyboardEvent) => {
      if (e.key === 'Escape') close(false);
    };

    backdrop.addEventListener('click', () => close(false));
    modal.querySelector('.metric-popup-close')?.addEventListener('click', () => close(false));
    modal.querySelector('#route-confirm-cancel')?.addEventListener('click', () => close(false));
    modal.querySelector('#route-confirm-accept')?.addEventListener('click', () => close(true));

    document.addEventListener('keydown', onEsc);
  });
}
