/**
 * Route interpretation component — smart waypoint parsing with confirmation popup.
 *
 * Two entry points:
 *   - interpretAndConfirmRoute: save-flow gate. Auto-accepts when nothing
 *     was skipped; otherwise shows a popup with Accept/Cancel.
 *   - previewRoute: preview-only path. Always shows the popup with a single
 *     Close button so the pilot can inspect the resolved chain + map without
 *     committing to a save.
 *
 * Both flows render the resolved route on a small Leaflet inset.
 */

import { interpretRoute, type InterpretRouteResponse } from '../adapters/api-adapter';
import { RouteMapInset } from './route-map-inset';
import { escapeHtml } from '../utils';
import { t } from '../i18n/i18n';

export interface RouteInterpretResult {
  /** The final waypoint codes to use. */
  waypoints: string[];
  /** Whether the user confirmed (true) or cancelled (false). */
  confirmed: boolean;
}

type PopupMode = 'save' | 'preview';

/**
 * Interpret a raw route string and, when the resolver had to skip
 * something, show a preview popup with the resolved route on a small
 * map so the pilot can verify before committing. Clean routes (zero
 * skipped tokens) proceed silently.
 *
 * Returns the interpreted waypoints if confirmed (or auto-accepted),
 * or null if cancelled/failed.
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

  // Clean route — accept silently
  if (resp.skipped.length === 0) {
    return { waypoints: resp.interpreted, confirmed: true };
  }

  return showRouteConfirmPopup(rawRoute, resp, 'save');
}

/**
 * Preview-only entry point — always shows the popup so the pilot can
 * see what was interpreted (and visualise it on a map) without
 * triggering a save. The "Close" button just dismisses; nothing the
 * pilot does in here changes form state.
 */
export async function previewRoute(
  rawRoute: string,
  onError: (msg: string) => void,
): Promise<void> {
  let resp: InterpretRouteResponse;
  try {
    resp = await interpretRoute(rawRoute);
  } catch (err) {
    onError(err instanceof Error ? err.message.replace(/^API \d+:\s*/, '') : String(err));
    return;
  }

  if (resp.interpreted.length === 0) {
    onError(t('flights.form.errorWaypoints'));
    return;
  }

  await showRouteConfirmPopup(rawRoute, resp, 'preview');
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
  mode: PopupMode,
): Promise<RouteInterpretResult> {
  return new Promise((resolve) => {
    const backdrop = document.createElement('div');
    backdrop.className = 'metric-popup-backdrop active';

    const modal = document.createElement('div');
    modal.className = 'metric-popup';
    // Slightly wider than default so the map has room to show the route
    modal.style.maxWidth = '560px';
    modal.style.width = '95vw';

    const interpretedHtml = resp.interpreted.map(s => `<strong>${escapeHtml(s)}</strong>`).join(' → ');
    const skippedRow = resp.skipped.length > 0
      ? `
        <div style="margin-bottom:0.5rem;color:var(--amber,#b45309);">
          <span class="info-label">Skipped (not recognized):</span>
          <span>${resp.skipped.map(s => `<code>${escapeHtml(s)}</code>`).join(', ')}</span>
        </div>`
      : '';
    // Map only renders with >=2 points (need a polyline). Suppress the
    // 240px container entirely below that — otherwise the popup would
    // show a blank box (e.g. preview path with one waypoint).
    const showMap = resp.waypoints.length >= 2;
    const mapBlock = showMap
      ? `<div id="route-confirm-map" style="height:240px;width:100%;margin-top:0.75rem;border-radius:6px;overflow:hidden;border:1px solid var(--border,#e5e7eb);"></div>`
      : '';

    // Save mode: Accept/Cancel — the click outcome carries meaning back to
    // the caller (Accept = use these waypoints).
    // Preview mode: single Close — the popup is just visualisation; the
    // returned promise resolves with confirmed=false so callers don't
    // accidentally treat a preview dismiss as a save trigger.
    const buttonsHtml = mode === 'save'
      ? `
        <button type="button" id="route-confirm-cancel" class="btn btn-outline btn-sm">Cancel</button>
        <button type="button" id="route-confirm-accept" class="btn btn-primary btn-sm">Accept</button>`
      : `
        <button type="button" id="route-confirm-cancel" class="btn btn-primary btn-sm">Close</button>`;

    modal.innerHTML = `
      <button class="metric-popup-close" aria-label="${t('popup.close')}">×</button>
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
        ${skippedRow}
        ${mapBlock}
      </div>
      <div style="display:flex;gap:0.5rem;justify-content:flex-end;margin-top:1rem;">
        ${buttonsHtml}
      </div>
    `;

    backdrop.appendChild(modal);
    document.body.appendChild(backdrop);

    // Stop clicks inside modal from closing
    modal.addEventListener('click', (e) => e.stopPropagation());

    // Mount the map after the container is in the DOM (Leaflet measures
    // clientWidth/Height during init). The interpret-route response
    // includes lat/lon for every resolved waypoint, so we don't need
    // a second lookup.
    const mapContainer = modal.querySelector('#route-confirm-map') as HTMLElement | null;
    let mapInset: RouteMapInset | null = null;
    if (mapContainer && resp.waypoints.length >= 2) {
      mapInset = new RouteMapInset(mapContainer);
      // Defer to next frame so layout settles and the container has a real size.
      requestAnimationFrame(() => {
        mapInset?.render(resp.waypoints);
        mapInset?.invalidateSize();
      });
    }

    const close = (confirmed: boolean) => {
      document.removeEventListener('keydown', onEsc);
      mapInset?.destroy();
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
