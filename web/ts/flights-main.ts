/** Flights page entry point — wires store, UI manager, and event handlers. */

import { fetchCurrentUser } from './adapters/auth-adapter';
import {
  AutorouterNotLinkedError,
  fetchAutorouterRoutes,
  fetchRouteDistance,
  parseFpl,
  type AutorouterRouteSummary,
  type WaypointInfo,
} from './adapters/api-adapter';
import type { FlightResponse } from './store/types';
import { fetchAircraft, type AircraftResponse } from './adapters/aircraft-adapter';
import { interpretAndConfirmRoute, previewRoute } from './components/route-interpret';
import { confirmZeroDuration } from './components/duration-confirm';
import { fetchModelCatalog, fetchBookingConfig, fetchPreferences } from './adapters/preferences-adapter';
import { fetchProfiles, type ProfileResponse } from './adapters/profiles-adapter';
import { flightsStore } from './store/flights-store';
import * as ui from './managers/flights-ui';
import {
  escapeHtml, redirectToLogin, renderUserInfo,
  initModelCatalog, getModelCatalog, initBookingConfig, maxBookingLeadDays,
} from './utils';
import { showWelcomeWizard } from './components/welcome-wizard';
import { initTheme } from './theme';
import { initI18n, t } from './i18n/i18n';
import { initInfoPopup, showPopupContent } from './components/info-popup';
import { maybeShowFlexibilityExplainer } from './components/flexibility-explainer';
import { iasToTasISA, resolveCruiseSpeedIAS } from './utils/atmo';
import {
  splitDurationCeil, combineDuration,
  buildDurationHourOptions, buildDurationMinuteOptions,
} from './utils/duration';
import {
  buildTimezoneOptions, localToUtc, utcToLocal, nearestMinuteOption,
} from './utils/timezone';
import { track, EVENTS } from './analytics/track';
import { startFlightsTour, maybeAutoStartFlightsTour } from './tour/flights-tour';

let loadedProfiles: ProfileResponse[] = [];
let loadedAircraft: AircraftResponse[] = [];


/** Whether the user has linked Autorouter — drives the import-button tooltip
 *  and which dialog handleImportFromAutorouter shows. Optimistic snapshot from
 *  /user/preferences; the picker still calls the API and reacts to a 409. */
let hasAutorouterCreds = false;

/** Whether the user has manually edited the duration field since the last
 *  waypoint or profile change. When true, auto-calculation is suppressed. */
let durationManuallyEdited = false;

/** Cached waypoint info from the last route-distance response. */
let lastWaypoints: WaypointInfo[] = [];

/** Total route distance (nm) from the last route-distance response. Cached so the
 *  duration estimate can be recomputed on altitude change without re-hitting the API
 *  (distance is altitude-independent; only the IAS→TAS conversion is). */
let lastTotalDistanceNm = 0;

/** Build a reference Date from the currently selected date and UTC time selects. */
function getRefDate(): Date {
  const dateInput = document.getElementById('input-date') as HTMLInputElement;
  const dateStr = dateInput?.value || new Date().toISOString().slice(0, 10);
  return new Date(`${dateStr}T12:00:00Z`);
}

/** Populate the timezone dropdown with unique timezones from waypoints. */
function populateTimezones(waypoints: WaypointInfo[]): void {
  const select = document.getElementById('input-timezone') as HTMLSelectElement;
  if (!select) return;

  const currentValue = select.value;
  const tzEntries = buildTimezoneOptions(waypoints, getRefDate());

  let html = '<option value="UTC">UTC</option>';
  for (const entry of tzEntries) {
    if (entry.tz === 'UTC') continue;
    html += `<option value="${escapeHtml(entry.tz)}">${escapeHtml(entry.label)}</option>`;
  }

  select.innerHTML = html;

  if (currentValue && [...select.options].some(o => o.value === currentValue)) {
    select.value = currentValue;
  }
}

/** Convert the currently displayed local time to UTC hour + minute. */
function localTimeToUtc(): { hour: number; minute: number } {
  const hourSel = document.getElementById('input-hour') as HTMLSelectElement;
  const minSel = document.getElementById('input-minute') as HTMLSelectElement;
  const tzSel = document.getElementById('input-timezone') as HTMLSelectElement;
  const localHour = parseInt(hourSel?.value ?? '9', 10);
  const localMinute = parseInt(minSel?.value ?? '0', 10);
  const tz = tzSel?.value ?? 'UTC';
  return localToUtc(localHour, localMinute, tz, getRefDate());
}

/** Convert UTC hour + minute to the currently selected timezone and update the selects. */
function utcToLocalDisplay(utcHour: number, utcMinute: number): void {
  const hourSel = document.getElementById('input-hour') as HTMLSelectElement;
  const minSel = document.getElementById('input-minute') as HTMLSelectElement;
  const tzSel = document.getElementById('input-timezone') as HTMLSelectElement;
  const tz = tzSel?.value ?? 'UTC';
  const { hour, minute } = utcToLocal(utcHour, utcMinute, tz, getRefDate());
  hourSel.value = String(hour);
  minSel.value = String(minute);
}

/** Internal UTC time — stored so TZ changes can re-display the same instant. */
let internalUtcHour = 9;
let internalUtcMinute = 0;

/** Get the currently selected profile, if any. */
function getSelectedProfile(): ProfileResponse | undefined {
  const profileSelect = document.getElementById('input-profile') as HTMLSelectElement;
  if (!profileSelect?.value) return undefined;
  const id = parseInt(profileSelect.value, 10);
  return loadedProfiles.find(p => p.id === id);
}

function getSelectedAircraft(): AircraftResponse | undefined {
  const aircraftSelect = document.getElementById('input-aircraft') as HTMLSelectElement;
  if (!aircraftSelect?.value) return undefined;
  const id = parseInt(aircraftSelect.value, 10);
  return loadedAircraft.find(a => a.id === id);
}

/** Cruise IAS for the current form selection — aircraft first, profile fallback.
 *  The precedence rule itself lives in resolveCruiseSpeedIAS (unit-tested); this
 *  is just the DOM wiring. */
function getCruiseSpeedIAS(): number | undefined {
  return resolveCruiseSpeedIAS(
    getSelectedAircraft()?.cruise_speed_kt,
    getSelectedProfile()?.settings?.speed_kt,
  );
}

/** Parse waypoints from the input field. Returns valid codes or empty array.
 *  Accepts ICAO airports (4 letters), navaids (2-3 chars), and fixes (5 chars). */
function parseWaypoints(): string[] {
  const wpRaw = (document.getElementById('input-waypoints') as HTMLInputElement)?.value.trim();
  if (!wpRaw) return [];
  const waypoints = wpRaw.split(/[\s,]+/).filter(Boolean).map(w => w.toUpperCase());
  if (waypoints.length < 2) return [];
  if (waypoints.some(w => !/^[A-Z0-9]{2,5}$/.test(w))) return [];
  return waypoints;
}

/** Fetch route distance, populate timezones, and optionally auto-calculate duration. */
async function fetchRouteAndUpdateUI(): Promise<void> {
  const waypoints = parseWaypoints();
  if (waypoints.length < 2) return;

  try {
    const resp = await fetchRouteDistance(waypoints);
    lastWaypoints = resp.waypoints;

    // Populate timezone dropdown from route waypoints
    populateTimezones(resp.waypoints);
    lastTotalDistanceNm = resp.total_distance_nm;

    recalcDurationEstimate();
  } catch (err) {
    console.error('Failed to fetch route distance:', err);
  }
}

/** Read the duration hour/minute dropdowns as decimal hours. */
function getDurationHours(): number {
  const hoursSel = document.getElementById('input-duration-hours') as HTMLSelectElement | null;
  const minsSel = document.getElementById('input-duration-minutes') as HTMLSelectElement | null;
  const hours = parseInt(hoursSel?.value || '0', 10) || 0;
  const minutes = parseInt(minsSel?.value || '0', 10) || 0;
  return combineDuration(hours, minutes);
}

/** Populate the duration hour/minute dropdowns from a decimal-hours value,
 *  rounding UP to the nearest 15-minute unit (never shorter — see
 *  splitDurationCeil). Setting ``.value`` does not fire a change event, so the
 *  durationManuallyEdited guard stays untouched on programmatic updates. */
function setDurationControls(decimalHours: number): void {
  const { hours, minutes } = splitDurationCeil(decimalHours);
  const hoursSel = document.getElementById('input-duration-hours') as HTMLSelectElement | null;
  const minsSel = document.getElementById('input-duration-minutes') as HTMLSelectElement | null;
  if (hoursSel) hoursSel.value = String(hours);
  if (minsSel) minsSel.value = String(minutes);
}

/** Auto-calculate the duration field from the cached route distance and the
 *  resolved cruise speed (aircraft IAS first, then flight-default profile —
 *  see getCruiseSpeedIAS), unless the user has edited it manually. The speed is
 *  cruise IAS; we convert it to TAS at the selected cruise altitude (ISA
 *  standard atmosphere) so distance ÷ TAS = still-air duration. The estimate is
 *  rounded UP to the next 15-minute unit when populating the dropdowns. */
function recalcDurationEstimate(): void {
  if (durationManuallyEdited || lastTotalDistanceNm <= 0) return;
  const iasKt = getCruiseSpeedIAS();
  if (!iasKt || iasKt <= 0) return;
  const altFt = parseInt(
    (document.getElementById('input-altitude') as HTMLInputElement)?.value || '8000', 10);
  const tasKt = iasToTasISA(iasKt, altFt);
  setDurationControls(lastTotalDistanceNm / tasKt);
}

/** Populate the Recent Routes dropdown from the user's flight history.
 *  Shows the most-recent distinct waypoint sets (up to 8). Hidden if fewer
 *  than 2 distinct routes are available. */
function populateRouteHistory(flights: FlightResponse[]): void {
  const select = document.getElementById('input-route-history') as HTMLSelectElement | null;
  if (!select) return;

  const seen = new Set<string>();
  const distinct: string[][] = [];
  // Sort by created_at desc so the most-recent flight wins per unique route.
  const sorted = [...flights].sort((a, b) =>
    (b.created_at || '').localeCompare(a.created_at || ''),
  );
  for (const f of sorted) {
    if (f.waypoints.length < 2) continue;
    const key = f.waypoints.join(' ');
    if (seen.has(key)) continue;
    seen.add(key);
    distinct.push(f.waypoints);
    if (distinct.length >= 8) break;
  }

  if (distinct.length < 2) {
    select.style.display = 'none';
    return;
  }

  const placeholder = t('flights.recentRoutesPlaceholder');
  select.innerHTML = `<option value="">${escapeHtml(placeholder)}</option>` +
    distinct.map(wps => {
      const val = wps.join(' ');
      return `<option value="${escapeHtml(val)}">${escapeHtml(val)}</option>`;
    }).join('');
  select.style.display = '';
}

/** Translate static HTML elements on the flights page. */
function translateStaticElements(): void {
  const set = (sel: string, key: string) => {
    const el = document.querySelector(sel);
    if (el) el.textContent = t(key);
  };
  set('h1', 'page.flights.title');
  set('.create-panel h3', 'page.flights.newFlight');
  set('label[for="input-waypoints"]', 'page.flights.waypoints');
  // Aircraft + profile labels each carry an (i) info button child; re-setting
  // textContent would wipe it, so relabel the text node and re-append the button.
  translateLabelWithInfoBtn('input-aircraft', 'page.flights.aircraft', '.aircraft-info-btn',
    t('flights.form.aircraftInfoTitle'), showAircraftInfo);
  translateLabelWithInfoBtn('input-profile', 'page.flights.profile', '.profile-info-btn',
    t('flights.form.profileInfoTitle'), showProfileInfo);
  set('label[for="input-date"]', 'page.flights.date');
  set('label[for="input-hour"]', 'page.flights.time');
  set('label[for="input-altitude"]', 'page.flights.altitude');
  set('label[for="input-ceiling"]', 'page.flights.ceiling');
  set('label[for="input-duration-hours"]', 'page.flights.duration');
  set('#loading-spinner', 'page.flights.loading');
  const submitBtn = document.querySelector('#create-flight-form button[type="submit"]');
  if (submitBtn) submitBtn.textContent = t('page.flights.createFlight');
  const pasteFplBtn = document.querySelector('#btn-paste-fpl');
  if (pasteFplBtn) pasteFplBtn.textContent = t('flights.fpl.pasteFpl');
  const tourBtn = document.getElementById('tour-btn');
  if (tourBtn) {
    tourBtn.setAttribute('title', t('tour.flights.helpTitle'));
    tourBtn.setAttribute('aria-label', t('tour.flights.helpTitle'));
  }
}

/** Relabel a form label that carries an (i) info button child. Setting
 *  textContent on the label would delete the button, so we rewrite only the
 *  label text and re-append the (freshly wired) button. Idempotent: clones the
 *  button to drop any previously-attached click listener before re-binding. */
function translateLabelWithInfoBtn(
  inputId: string, labelKey: string, btnSelector: string,
  btnTitle: string, onClick: () => void,
): void {
  const label = document.querySelector(`label[for="${inputId}"]`);
  if (!label) return;
  const existing = label.querySelector(btnSelector);
  label.textContent = t(labelKey) + ' ';
  if (!existing) return;
  // Clone to strip stale listeners (translateStaticElements can run more than
  // once if the locale changes), then re-wire.
  const btn = existing.cloneNode(true) as HTMLButtonElement;
  btn.setAttribute('title', btnTitle);
  btn.setAttribute('aria-label', btnTitle);
  btn.addEventListener('click', (e) => {
    e.preventDefault();
    onClick();
  });
  label.appendChild(btn);
}

/** Info popup: what the Aircraft selector is for, plus a deep-link to edit the
 *  selected aircraft (or the aircraft list) in Settings. */
function showAircraftInfo(): void {
  const ac = getSelectedAircraft();
  const editHref = ac
    ? `/settings.html?tab=aircraft&aircraft=${encodeURIComponent(ac.id)}`
    : '/settings.html?tab=aircraft';
  const linkLabel = ac
    ? t('flights.form.aircraftInfoEditThis')
    : t('flights.form.aircraftInfoEditAny');
  showPopupContent(`
    <h3 style="margin-top:0">${escapeHtml(t('flights.form.aircraftInfoTitle'))}</h3>
    <p>${escapeHtml(t('flights.form.aircraftInfoBody'))}</p>
    <p style="margin-top:1rem"><a class="btn btn-primary btn-sm" href="${editHref}">${escapeHtml(linkLabel)}</a></p>
  `);
}

/** Info popup: what a Profile is, that you can keep several for different
 *  missions, and a deep-link to edit the selected profile in Settings. */
function showProfileInfo(): void {
  const profile = getSelectedProfile();
  const editHref = profile
    ? `/settings.html?tab=flight&profile=${encodeURIComponent(profile.id)}`
    : '/settings.html?tab=flight';
  const linkLabel = profile
    ? t('flights.form.profileInfoEditThis')
    : t('flights.form.profileInfoEditAny');
  showPopupContent(`
    <h3 style="margin-top:0">${escapeHtml(t('flights.form.profileInfoTitle'))}</h3>
    <p>${escapeHtml(t('flights.form.profileInfoBody'))}</p>
    <p style="margin-top:1rem"><a class="btn btn-primary btn-sm" href="${editHref}">${escapeHtml(linkLabel)}</a></p>
  `);
}

/** Apply parsed FPL data to the flight creation form. */
async function applyParsedFpl(text: string): Promise<void> {
  try {
    const parsed = await parseFpl(text);
    if (parsed.error) {
      ui.renderError(parsed.error);
      return;
    }

    // Waypoints
    if (parsed.waypoints.length >= 2) {
      const wpInput = document.getElementById('input-waypoints') as HTMLInputElement;
      if (wpInput) wpInput.value = parsed.waypoints.join(' ');
    }

    // Date
    if (parsed.date) {
      const dateInput = document.getElementById('input-date') as HTMLInputElement;
      if (dateInput) dateInput.value = parsed.date;
    }

    // Time (UTC) — set timezone to UTC first, then set hour/minute
    if (parsed.time_utc) {
      const [h, m] = parsed.time_utc.split(':').map(Number);
      const tzSel = document.getElementById('input-timezone') as HTMLSelectElement;
      if (tzSel) tzSel.value = 'UTC';
      const hourSel = document.getElementById('input-hour') as HTMLSelectElement;
      const minSel = document.getElementById('input-minute') as HTMLSelectElement;
      if (hourSel) hourSel.value = String(h);
      if (minSel) {
        // Snap to nearest 15-min option
        const snapped = nearestMinuteOption(m);
        minSel.value = String(snapped);
      }
      internalUtcHour = h;
      internalUtcMinute = nearestMinuteOption(m);
    }

    // Altitude
    if (parsed.altitude_ft != null) {
      const altInput = document.getElementById('input-altitude') as HTMLInputElement;
      if (altInput) altInput.value = String(parsed.altitude_ft);
    }

    // Duration
    if (parsed.duration_hours != null) {
      setDurationControls(parsed.duration_hours);
      durationManuallyEdited = true; // prevent auto-calc from overwriting
    }

    // Trigger route distance fetch to populate timezones and validate
    await fetchRouteAndUpdateUI();

    // If time was set and we now have timezone options, re-display in UTC
    if (parsed.time_utc) {
      utcToLocalDisplay(internalUtcHour, internalUtcMinute);
    }
  } catch (err) {
    ui.renderError(t('flights.fpl.parseError'));
    console.error('FPL parse failed:', err);
  }
}

/** Show a modal for the user to paste their FPL text. */
function showFplModal(prefill?: string): void {
  const backdrop = document.createElement('div');
  backdrop.className = 'metric-popup-backdrop active';

  const modal = document.createElement('div');
  modal.className = 'metric-popup';
  modal.innerHTML = `
    <button class="metric-popup-close" aria-label="${t('popup.close')}">\u00d7</button>
    <h3>${t('flights.fpl.modalTitle')}</h3>
    <textarea id="fpl-paste-area" rows="8" style="width:100%;font-family:monospace;font-size:0.85rem;resize:vertical;" placeholder="(FPL-N122DR-VG&#10;-S22T/L-SBDGORVY/LB2&#10;-LFAT0930&#10;-N0166F085 DCT LYD DCT VESAN DCT&#10;-EGTF0033 EGLL&#10;-DOF/260318)"></textarea>
    <div style="margin-top:0.75rem;display:flex;gap:0.5rem;justify-content:flex-end;">
      <button type="button" id="fpl-cancel-btn" class="btn btn-outline btn-sm">${t('flights.fpl.cancel')}</button>
      <button type="button" id="fpl-import-btn" class="btn btn-primary btn-sm">${t('flights.fpl.import')}</button>
    </div>
  `;

  backdrop.appendChild(modal);
  document.body.appendChild(backdrop);

  // Stop clicks inside modal from closing
  modal.addEventListener('click', (e) => e.stopPropagation());

  // ESC to close (defined early so close() can remove it)
  const onEsc = (e: KeyboardEvent) => {
    if (e.key === 'Escape') close();
  };

  const close = () => {
    document.removeEventListener('keydown', onEsc);
    backdrop.remove();
  };

  backdrop.addEventListener('click', close);
  modal.querySelector('.metric-popup-close')?.addEventListener('click', close);
  modal.querySelector('#fpl-cancel-btn')?.addEventListener('click', close);

  modal.querySelector('#fpl-import-btn')?.addEventListener('click', async () => {
    const area = document.getElementById('fpl-paste-area') as HTMLTextAreaElement;
    const text = area?.value?.trim();
    if (!text) return;
    close();
    await applyParsedFpl(text);
  });

  // Also handle Enter in textarea with Ctrl/Cmd
  const area = modal.querySelector('#fpl-paste-area') as HTMLTextAreaElement;
  if (prefill) area.value = prefill;
  area?.addEventListener('keydown', async (e) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault();
      const text = area.value?.trim();
      if (!text) return;
      close();
      await applyParsedFpl(text);
    }
  });

  document.addEventListener('keydown', onEsc);

  // Focus textarea
  requestAnimationFrame(() => area?.focus());
}

/** Format an Autorouter route row's display title (ICAO + names if present). */
function formatAutorouterRouteTitle(r: AutorouterRouteSummary): string {
  const dep = r.departure_name ? `${r.departure} (${r.departure_name})` : r.departure;
  const dest = r.destination_name ? `${r.destination} (${r.destination_name})` : r.destination;
  return `${dep} → ${dest}`;
}

/** Format an Autorouter route row's metadata line (date, distance, aircraft). */
function formatAutorouterRouteSubtitle(r: AutorouterRouteSummary): string {
  const parts: string[] = [];
  if (r.departure_time) {
    try {
      const d = new Date(r.departure_time);
      parts.push(d.toISOString().slice(0, 16).replace('T', ' ') + 'Z');
    } catch {
      parts.push(r.departure_time);
    }
  }
  if (r.route_distance_nm != null) parts.push(`${r.route_distance_nm} NM`);
  if (r.aircraft_description) parts.push(r.aircraft_description);
  if (r.callsign) parts.push(r.callsign);
  return parts.join(' · ');
}

/** Show the "Link Autorouter in Settings" prompt for users who haven't linked. */
function showAutorouterNotLinkedModal(): void {
  const backdrop = document.createElement('div');
  backdrop.className = 'metric-popup-backdrop active';
  const modal = document.createElement('div');
  modal.className = 'metric-popup';
  modal.innerHTML = `
    <button class="metric-popup-close" aria-label="${t('popup.close')}">×</button>
    <h3>${escapeHtml(t('flights.autorouter.notLinkedTitle'))}</h3>
    <p>${escapeHtml(t('flights.autorouter.notLinkedBody'))}</p>
    <div style="margin-top:0.75rem;display:flex;gap:0.5rem;justify-content:flex-end;">
      <button type="button" id="ar-cancel-btn" class="btn btn-outline btn-sm">${t('flights.fpl.cancel')}</button>
      <a href="/settings.html#autorouter-section" class="btn btn-primary btn-sm">${escapeHtml(t('flights.autorouter.openSettings'))}</a>
    </div>
  `;
  backdrop.appendChild(modal);
  document.body.appendChild(backdrop);
  modal.addEventListener('click', (e) => e.stopPropagation());
  const close = () => backdrop.remove();
  backdrop.addEventListener('click', close);
  modal.querySelector('.metric-popup-close')?.addEventListener('click', close);
  modal.querySelector('#ar-cancel-btn')?.addEventListener('click', close);
}

/** Show the route picker modal with the supplied Autorouter routes. */
function showAutorouterPickerModal(routes: AutorouterRouteSummary[]): void {
  const backdrop = document.createElement('div');
  backdrop.className = 'metric-popup-backdrop active';
  const modal = document.createElement('div');
  modal.className = 'metric-popup';

  const rowsHtml = routes.length === 0
    ? `<p class="muted">${escapeHtml(t('flights.autorouter.noRoutes'))}</p>`
    : `<ul class="autorouter-route-list" style="list-style:none;padding:0;margin:0;max-height:60vh;overflow-y:auto;">
        ${routes.map((r, i) => `
          <li>
            <button type="button" class="autorouter-route-item" data-idx="${i}"
              style="display:block;width:100%;text-align:left;padding:0.5rem 0.75rem;border:1px solid var(--border-color,#ddd);border-radius:4px;margin-bottom:0.5rem;background:transparent;cursor:pointer;">
              <div style="font-weight:600;">${escapeHtml(formatAutorouterRouteTitle(r))}</div>
              <div class="muted" style="font-size:0.85em;">${escapeHtml(formatAutorouterRouteSubtitle(r))}</div>
            </button>
          </li>
        `).join('')}
      </ul>`;

  modal.innerHTML = `
    <button class="metric-popup-close" aria-label="${t('popup.close')}">×</button>
    <h3>${escapeHtml(t('flights.autorouter.pickerTitle'))}</h3>
    ${rowsHtml}
    <div style="margin-top:0.75rem;display:flex;gap:0.5rem;justify-content:flex-end;">
      <button type="button" id="ar-cancel-btn" class="btn btn-outline btn-sm">${t('flights.fpl.cancel')}</button>
    </div>
  `;

  backdrop.appendChild(modal);
  document.body.appendChild(backdrop);
  modal.addEventListener('click', (e) => e.stopPropagation());
  const onEsc = (e: KeyboardEvent) => { if (e.key === 'Escape') close(); };
  const close = () => {
    document.removeEventListener('keydown', onEsc);
    backdrop.remove();
  };
  backdrop.addEventListener('click', close);
  modal.querySelector('.metric-popup-close')?.addEventListener('click', close);
  modal.querySelector('#ar-cancel-btn')?.addEventListener('click', close);
  document.addEventListener('keydown', onEsc);

  modal.querySelectorAll<HTMLButtonElement>('.autorouter-route-item').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const idx = parseInt(btn.dataset.idx ?? '-1', 10);
      const route = routes[idx];
      if (!route) return;
      close();
      await applyParsedFpl(route.fplan);
    });
  });
}

/** Handle "Import from Autorouter" button click — mirrors handlePasteFpl. */
async function handleImportFromAutorouter(): Promise<void> {
  if (!hasAutorouterCreds) {
    showAutorouterNotLinkedModal();
    return;
  }
  try {
    const resp = await fetchAutorouterRoutes(25);
    showAutorouterPickerModal(resp.routes);
  } catch (err) {
    if (err instanceof AutorouterNotLinkedError) {
      // Token was just revoked server-side — update cached flag and prompt re-link.
      hasAutorouterCreds = false;
      updateAutorouterButtonState();
      showAutorouterNotLinkedModal();
      return;
    }
    ui.renderError(t('flights.autorouter.fetchError'));
    console.error('Autorouter routes fetch failed:', err);
  }
}

/** Refresh the Import-from-Autorouter button's tooltip from hasAutorouterCreds. */
function updateAutorouterButtonState(): void {
  const btn = document.getElementById('btn-import-autorouter');
  if (!btn) return;
  btn.setAttribute(
    'title',
    hasAutorouterCreds
      ? t('flights.autorouter.tooltipLinked')
      : t('flights.autorouter.tooltipNotLinked'),
  );
  btn.textContent = t('flights.autorouter.import');
}

/** Handle "Paste FPL" button click — try clipboard first, fall back to modal. */
async function handlePasteFpl(): Promise<void> {
  // Try clipboard API (requires secure context and user gesture)
  try {
    if (navigator.clipboard && navigator.clipboard.readText) {
      const text = await navigator.clipboard.readText();
      if (text && text.includes('FPL')) {
        const parsed = await parseFpl(text);
        if (!parsed.error && parsed.waypoints.length >= 2) {
          await applyParsedFpl(text);
          return;
        }
        // Parse failed — fall through to show modal with text pre-filled
        showFplModal(text);
        return;
      }
    }
  } catch {
    // Clipboard access denied or unavailable — fall through to modal
  }
  showFplModal();
}

async function init(): Promise<void> {
  await initI18n();
  translateStaticElements();
  // Auth check — redirect to login if not authenticated
  const user = await fetchCurrentUser();
  if (!user) {
    redirectToLogin();
    return;
  }
  initTheme();
  renderUserInfo(user, 'flights');
  initInfoPopup();

  // Load the model catalog + booking config up front so the welcome wizard and
  // the date picker can read them. Cheap static endpoints; best-effort.
  try {
    const [catalog, bookingCfg] = await Promise.all([
      fetchModelCatalog(),
      fetchBookingConfig(),
    ]);
    initModelCatalog(catalog);
    initBookingConfig(bookingCfg);
  } catch {
    // Non-fatal — catalog/config-derived helpers fall back to their defaults.
  }

  const store = flightsStore;

  // --- Subscribe to state changes ---
  const bulkDeleteHandler = async () => {
    try {
      const result = await store.getState().bulkDeleteSelected();
      if (result.notFound > 0) {
        ui.renderError(t('flights.bulkDeletePartial', {
          deleted: result.deleted,
          notFound: result.notFound,
        }));
      }
    } catch {
      // Error already surfaced via store.error
    }
  };
  const selectionHandlers = {
    onToggle: (id: string) => store.getState().toggleSelected(id),
    onSelectAll: (ids: string[]) => store.getState().setSelected(
      [...store.getState().selectedIds, ...ids],
    ),
    onSelectAllPast: (ids: string[]) => store.getState().setSelected(
      [...store.getState().selectedIds, ...ids],
    ),
    onBulkDelete: bulkDeleteHandler,
    onClearSelection: () => store.getState().clearSelection(),
  };

  const refreshAfterDebrief = () => {
    // Re-load flights so the debriefed row moves out of "recent" and the
    // stats panel updates.
    void store.getState().loadFlights();
    void store.getState().loadDebriefStats();
  };

  // Flights-list filters (#542). Two section-scoped queries: upcoming filters
  // future + recent in the browser, past round-trips to the server.
  const filterHandlers = (state: { upcomingQuery: string; pastQuery: string }) => ({
    upcomingQuery: state.upcomingQuery,
    pastQuery: state.pastQuery,
    onUpcomingQuery: (q: string) => store.getState().setUpcomingQuery(q),
    onPastQuery: (q: string) => void store.getState().setPastQuery(q),
  });
  ui.wireUpcomingFilter((q) => store.getState().setUpcomingQuery(q));

  store.subscribe((state, prev) => {
    if (
      state.flights !== prev.flights ||
      state.pastTotal !== prev.pastTotal ||
      state.activeRefreshes !== prev.activeRefreshes ||
      state.selectedIds !== prev.selectedIds ||
      state.debriefStats !== prev.debriefStats ||
      state.loaded !== prev.loaded ||
      state.upcomingQuery !== prev.upcomingQuery ||
      state.pastQuery !== prev.pastQuery
    ) {
      ui.renderFlightList(
        state.flights,
        state.activeRefreshes,
        state.selectedIds,
        state.pastTotal,
        state.loaded,
        (id) => navigateToBriefing(id),
        (id) => navigateToFlight(id),
        (id) => store.getState().deleteFlight(id),
        selectionHandlers,
        () => store.getState().loadMorePast(),
        (id) => store.getState().unsubscribeFlight(id),
        state.debriefStats,
        refreshAfterDebrief,
        filterHandlers(state),
      );
    }
    if (state.flights !== prev.flights) {
      populateRouteHistory(state.flights);
    }
    if (state.loading !== prev.loading) {
      ui.renderLoading(state.loading);
    }
    if (state.error !== prev.error) {
      ui.renderError(state.error);
    }
  });

  // --- Load profiles, aircraft, and preferences for the selectors ---
  try {
    const [profs, acList, prefs] = await Promise.all([
      fetchProfiles().catch(() => [] as ProfileResponse[]),
      fetchAircraft().catch(() => [] as AircraftResponse[]),
      fetchPreferences().catch(() => null),
    ]);
    loadedProfiles = profs;
    loadedAircraft = acList;
    populateProfileSelector(loadedProfiles);
    populateAircraftSelector(loadedAircraft);
    hasAutorouterCreds = !!prefs?.has_autorouter_creds;
    updateAutorouterButtonState();
  } catch {
    // Selectors stay empty; flights still work without them
  }

  // --- Populate hour dropdown ---
  const hourSelect = document.getElementById('input-hour') as HTMLSelectElement;
  if (hourSelect) {
    for (let h = 0; h < 24; h++) {
      const opt = document.createElement('option');
      opt.value = String(h);
      opt.textContent = h.toString().padStart(2, '0');
      if (h === 9) opt.selected = true;
      hourSelect.appendChild(opt);
    }
  }

  // --- Populate duration dropdowns from the shared helpers (default 0h00) ---
  const durHoursSelect = document.getElementById('input-duration-hours') as HTMLSelectElement | null;
  if (durHoursSelect) durHoursSelect.innerHTML = buildDurationHourOptions(0);
  const durMinutesSelect = document.getElementById('input-duration-minutes') as HTMLSelectElement | null;
  if (durMinutesSelect) durMinutesSelect.innerHTML = buildDurationMinuteOptions(0);

  // --- First-login welcome wizard ---
  if (!user.setup_completed) {
    try {
      const modelCatalog = getModelCatalog();
      const defaultProfile = loadedProfiles.find(p => p.is_default) || loadedProfiles[0];
      if (defaultProfile) {
        await showWelcomeWizard(defaultProfile, modelCatalog, loadedProfiles);
        // Reload profiles after wizard may have updated the default profile
        loadedProfiles = await fetchProfiles();
        populateProfileSelector(loadedProfiles);
      }
    } catch (err) {
      console.error('Welcome wizard error:', err);
    }
  }

  // First-time flexibility explainer (#352): show the modal the first time a
  // user picks a scan mode, gated by durable usage + a session ack flag.
  const createFlex = document.getElementById('input-flexibility') as HTMLSelectElement | null;
  createFlex?.addEventListener('change', () => {
    if (createFlex.value !== 'none') void maybeShowFlexibilityExplainer();
  });

  // --- Wire create flight form ---
  const form = document.getElementById('create-flight-form') as HTMLFormElement;
  if (form) {
    form.addEventListener('submit', async (e) => {
      e.preventDefault();

      const wpRaw = (document.getElementById('input-waypoints') as HTMLInputElement).value.trim();
      const targetDate = (document.getElementById('input-date') as HTMLInputElement).value;
      const { hour: utcHour, minute: utcMinute } = localTimeToUtc();
      const altitude = parseInt((document.getElementById('input-altitude') as HTMLInputElement).value || '8000', 10);
      const ceiling = parseInt((document.getElementById('input-ceiling') as HTMLInputElement).value || '18000', 10);
      let duration = getDurationHours();
      const profileSelect = document.getElementById('input-profile') as HTMLSelectElement;
      const profileId = profileSelect?.value ? parseInt(profileSelect.value, 10) : undefined;
      const aircraftSelect = document.getElementById('input-aircraft') as HTMLSelectElement;
      const aircraftId = aircraftSelect?.value ? parseInt(aircraftSelect.value, 10) : undefined;
      const flexSelect = document.getElementById('input-flexibility') as HTMLSelectElement;
      const flexibility = (flexSelect?.value || 'none') as 'none' | 'same_day' | 'prev_day' | 'next_day';

      if (!targetDate) {
        ui.renderError(t('flights.form.errorDate'));
        return;
      }
      if (!wpRaw) {
        ui.renderError(t('flights.form.errorWaypoints'));
        return;
      }

      // Smart route interpretation — resolve known waypoints, skip unknown tokens
      const result = await interpretAndConfirmRoute(wpRaw, (msg) => ui.renderError(msg));
      if (!result || !result.confirmed) return;
      const waypoints = result.waypoints;

      // Update the waypoints input to show the interpreted route
      const wpInput = document.getElementById('input-waypoints') as HTMLInputElement;
      if (wpInput) wpInput.value = waypoints.join(' ');

      // Zero duration is almost always an accidental empty field. Ask the
      // pilot to confirm — they can either type a value (with 120kt/150kt
      // hints from the route distance) or proceed with 0 for a snapshot-
      // in-time briefing.
      if (!duration || duration <= 0) {
        const confirm = await confirmZeroDuration(waypoints);
        if (!confirm.confirmed) return;
        // Reflect the confirmed value in the dropdowns (rounded up to the next
        // 15-min unit) and re-read so the submitted value matches what's shown.
        setDurationControls(confirm.duration);
        duration = getDurationHours();
      }

      try {
        const flight = await store.getState().createFlight(waypoints, targetDate, {
          targetTimeUtc: utcHour,
          targetMinuteUtc: utcMinute,
          cruiseAltitudeFt: altitude,
          flightCeilingFt: ceiling,
          flightDurationHours: duration,
          profileId: !isNaN(profileId!) ? profileId : undefined,
          aircraftId: !isNaN(aircraftId!) ? aircraftId : undefined,
          rawRoute: wpRaw,
          flexibility,
        });
        // Pass flight_id via context (top-level field) so server-side
        // enrichment (upsert_flight_dim) picks it up.
        track(EVENTS.FLIGHT_CREATED, undefined, { flight_id: flight.id });
        // Navigate to briefing page for the new flight
        navigateToBriefing(flight.id);
      } catch {
        // Error already set in store via API response
      }
    });
  }

  // --- Auto-calculate duration and populate timezones when waypoints change ---
  const waypointsInput = document.getElementById('input-waypoints') as HTMLInputElement;
  waypointsInput?.addEventListener('blur', () => {
    durationManuallyEdited = false;
    fetchRouteAndUpdateUI();
  });

  // --- Interpret button: preview the resolved route + map without saving ---
  const previewBtn = document.getElementById('btn-preview-route') as HTMLButtonElement | null;
  previewBtn?.addEventListener('click', () => {
    const wpRaw = waypointsInput?.value.trim() ?? '';
    if (!wpRaw) {
      ui.renderError(t('flights.form.errorWaypoints'));
      return;
    }
    void previewRoute(wpRaw, (msg) => ui.renderError(msg));
  });

  // --- Recent routes dropdown: fill waypoints input on selection ---
  const routeHistorySelect = document.getElementById('input-route-history') as HTMLSelectElement;
  routeHistorySelect?.addEventListener('change', () => {
    if (!routeHistorySelect.value) return;
    if (waypointsInput) {
      waypointsInput.value = routeHistorySelect.value;
    }
    routeHistorySelect.value = '';
    durationManuallyEdited = false;
    fetchRouteAndUpdateUI();
  });

  // --- Timezone change: re-display the same UTC instant in the new timezone ---
  const tzSelect = document.getElementById('input-timezone') as HTMLSelectElement;
  tzSelect?.addEventListener('change', () => {
    utcToLocalDisplay(internalUtcHour, internalUtcMinute);
  });

  // --- Hour/minute change: update internal UTC time ---
  const hourInput = document.getElementById('input-hour') as HTMLSelectElement;
  const minuteInput = document.getElementById('input-minute') as HTMLSelectElement;
  const onTimeChange = () => {
    const { hour, minute } = localTimeToUtc();
    internalUtcHour = hour;
    internalUtcMinute = minute;
  };
  hourInput?.addEventListener('change', onTimeChange);
  minuteInput?.addEventListener('change', onTimeChange);

  // --- Re-compute TZ offset labels when date changes (DST may differ) ---
  const dateInput = document.getElementById('input-date') as HTMLInputElement;
  if (dateInput) {
    // Bound the picker to the booking cap, not the forecast horizon: flights
    // beyond the horizon save in a pending-coverage state and brief once a
    // model reaches the date. Only truly far-out dates are refused.
    const maxDate = new Date();
    maxDate.setUTCDate(maxDate.getUTCDate() + maxBookingLeadDays());
    dateInput.max = maxDate.toISOString().slice(0, 10);
  }
  dateInput?.addEventListener('change', () => {
    if (lastWaypoints.length > 0) {
      populateTimezones(lastWaypoints);
    }
  });

  // --- Track manual duration edits ---
  const onDurationEdit = () => { durationManuallyEdited = true; };
  document.getElementById('input-duration-hours')?.addEventListener('change', onDurationEdit);
  document.getElementById('input-duration-minutes')?.addEventListener('change', onDurationEdit);

  // --- Recompute the duration estimate when cruise altitude changes ---
  // TAS (and therefore still-air duration) depends on altitude. Recompute from
  // the cached route distance — no need to re-fetch (distance is altitude-independent).
  // The recalc itself respects the durationManuallyEdited guard.
  const altitudeInput = document.getElementById('input-altitude') as HTMLInputElement;
  altitudeInput?.addEventListener('input', recalcDurationEstimate);

  // Update altitude/ceiling defaults and recalculate duration when profile changes
  const profileSelect = document.getElementById('input-profile') as HTMLSelectElement;
  profileSelect?.addEventListener('change', () => {
    const id = parseInt(profileSelect.value, 10);
    const profile = loadedProfiles.find(p => p.id === id);
    if (profile?.settings) {
      const altInput = document.getElementById('input-altitude') as HTMLInputElement;
      const ceilInput = document.getElementById('input-ceiling') as HTMLInputElement;
      if (altInput && profile.settings.cruise_altitude_ft != null) {
        altInput.value = String(profile.settings.cruise_altitude_ft);
      }
      if (ceilInput && profile.settings.flight_ceiling_ft != null) {
        ceilInput.value = String(profile.settings.flight_ceiling_ft);
      }
    }
    // Recalculate duration with new profile's speed
    durationManuallyEdited = false;
    fetchRouteAndUpdateUI();
  });

  // --- Wire "Paste FPL" button ---
  const pasteFplBtn = document.getElementById('btn-paste-fpl');
  pasteFplBtn?.addEventListener('click', handlePasteFpl);

  // --- Wire "Import from Autorouter" button ---
  const importArBtn = document.getElementById('btn-import-autorouter');
  importArBtn?.addEventListener('click', handleImportFromAutorouter);
  // Fallback paint for the failure path: if fetchPreferences() above threw,
  // the earlier updateAutorouterButtonState() call inside the try block was
  // skipped, leaving the button with its hardcoded HTML title. This call
  // paints it with the localised "not linked" tooltip (hasAutorouterCreds
  // stayed at its `false` default), matching what the user would see on a
  // first visit before linking Autorouter.
  updateAutorouterButtonState();

  // --- Guided tour (#400): help icon + ?tour=1 auto-start ---
  document.getElementById('tour-btn')?.addEventListener('click', () => startFlightsTour());
  maybeAutoStartFlightsTour();

  // --- Initial load ---
  store.getState().loadFlights().then(() => {
    // Start polling active refreshes after flights load
    store.getState().pollActiveRefreshes();
  });
  // Stats are independent of the flights load — fire and forget.
  store.getState().loadDebriefStats();

  // Poll active refreshes every 5 seconds
  const refreshPollInterval = setInterval(() => {
    store.getState().pollActiveRefreshes();
  }, 5000);

  window.addEventListener('beforeunload', () => {
    clearInterval(refreshPollInterval);
  });
}

function populateProfileSelector(profiles: ProfileResponse[]): void {
  const select = document.getElementById('input-profile') as HTMLSelectElement;
  if (!select) return;

  select.innerHTML = profiles.map(p => {
    const defaultTag = p.is_default ? t('flights.form.defaultTag') : '';
    const selected = p.is_default ? ' selected' : '';
    return `<option value="${p.id}"${selected}>${escapeHtml(p.name)}${defaultTag}</option>`;
  }).join('');

  // Apply default profile's altitude/ceiling values
  const defaultProfile = profiles.find(p => p.is_default) || profiles[0];
  if (defaultProfile?.settings) {
    const altInput = document.getElementById('input-altitude') as HTMLInputElement;
    const ceilInput = document.getElementById('input-ceiling') as HTMLInputElement;
    if (altInput && defaultProfile.settings.cruise_altitude_ft != null) {
      altInput.value = String(defaultProfile.settings.cruise_altitude_ft);
    }
    if (ceilInput && defaultProfile.settings.flight_ceiling_ft != null) {
      ceilInput.value = String(defaultProfile.settings.flight_ceiling_ft);
    }
  }
}

function populateAircraftSelector(aircraft: AircraftResponse[]): void {
  const select = document.getElementById('input-aircraft') as HTMLSelectElement;
  if (!select) return;

  const formGroup = select.closest('.form-group') as HTMLElement | null;
  // Remove any previous footnote
  formGroup?.parentElement?.querySelector('.aircraft-hint')?.remove();

  if (aircraft.length === 0) {
    // Hide dropdown, show hint instead
    if (formGroup) formGroup.style.display = 'none';
    const hint = document.createElement('p');
    hint.className = 'aircraft-hint muted';
    hint.style.fontSize = '0.8rem';
    hint.style.margin = '0.25rem 0 0';
    hint.innerHTML = `You can add aircraft presets in <a href="/settings.html">Settings</a>.`;
    // Insert hint after the form-row containing the aircraft dropdown
    const formRow = formGroup?.parentElement;
    formRow?.parentElement?.insertBefore(hint, formRow.nextSibling);
    return;
  }

  if (formGroup) formGroup.style.display = '';

  select.innerHTML = '<option value="">None</option>' + aircraft.map(ac => {
    const label = ac.tail_number
      ? `${ac.tail_number} — ${ac.type_name}`
      : ac.type_name;
    const nickname = ac.nickname ? ` (${ac.nickname})` : '';
    const defaultTag = ac.is_default ? ' *' : '';
    const selected = ac.is_default ? ' selected' : '';
    return `<option value="${ac.id}"${selected}>${escapeHtml(label + nickname + defaultTag)}</option>`;
  }).join('');

  // When aircraft changes, re-resolve the cruise speed (getCruiseSpeedIAS now
  // prefers the aircraft) and re-estimate duration. Done on every change —
  // including switching to "None" or a speedless aircraft — so the estimate
  // falls back to the flight-default profile speed. Pull the aircraft ceiling
  // when one is set.
  select.addEventListener('change', () => {
    const acId = parseInt(select.value, 10);
    const ac = aircraft.find(a => a.id === acId);
    durationManuallyEdited = false;
    fetchRouteAndUpdateUI();
    if (ac?.ceiling_ft) {
      const ceilInput = document.getElementById('input-ceiling') as HTMLInputElement;
      if (ceilInput) ceilInput.value = String(ac.ceiling_ft);
    }
  });
}

function navigateToFlight(flightId: string): void {
  window.location.href = `/flight.html?id=${encodeURIComponent(flightId)}`;
}

function navigateToBriefing(flightId: string): void {
  window.location.href = `/briefing.html?flight=${encodeURIComponent(flightId)}`;
}


// Boot
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
