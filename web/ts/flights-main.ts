/** Flights page entry point — wires store, UI manager, and event handlers. */

import { fetchCurrentUser } from './adapters/auth-adapter';
import { fetchRouteDistance, type WaypointInfo } from './adapters/api-adapter';
import { fetchModelCatalog } from './adapters/preferences-adapter';
import { fetchProfiles, type ProfileResponse } from './adapters/profiles-adapter';
import { flightsStore } from './store/flights-store';
import * as ui from './managers/flights-ui';
import { escapeHtml, renderUserInfo, initModelCatalog } from './utils';
import { showWelcomeWizard } from './components/welcome-wizard';
import { initTheme } from './theme';
import { initI18n, t } from './i18n/i18n';
import {
  buildTimezoneOptions, localToUtc, utcToLocal, nearestMinuteOption,
} from './utils/timezone';

let loadedProfiles: ProfileResponse[] = [];

/** Whether the user has manually edited the duration field since the last
 *  waypoint or profile change. When true, auto-calculation is suppressed. */
let durationManuallyEdited = false;

/** Cached waypoint info from the last route-distance response. */
let lastWaypoints: WaypointInfo[] = [];

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

/** Parse waypoints from the input field. Returns valid ICAO codes or empty array. */
function parseWaypoints(): string[] {
  const wpRaw = (document.getElementById('input-waypoints') as HTMLInputElement)?.value.trim();
  if (!wpRaw) return [];
  const waypoints = wpRaw.split(/[\s,]+/).filter(Boolean).map(w => w.toUpperCase());
  if (waypoints.length < 2) return [];
  if (waypoints.some(w => !/^[A-Z]{4}$/.test(w))) return [];
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

    // Auto-calculate duration from speed if not manually edited
    if (!durationManuallyEdited) {
      const profile = getSelectedProfile();
      const speedKt = profile?.settings?.speed_kt;
      if (speedKt && speedKt > 0) {
        const durationHours = Math.ceil(resp.total_distance_nm / speedKt);
        const durationInput = document.getElementById('input-duration') as HTMLInputElement;
        if (durationInput) {
          durationInput.value = String(durationHours);
        }
      }
    }
  } catch (err) {
    console.error('Failed to fetch route distance:', err);
  }
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
  set('label[for="input-profile"]', 'page.flights.profile');
  set('label[for="input-date"]', 'page.flights.date');
  set('label[for="input-hour"]', 'page.flights.time');
  set('label[for="input-altitude"]', 'page.flights.altitude');
  set('label[for="input-ceiling"]', 'page.flights.ceiling');
  set('label[for="input-duration"]', 'page.flights.duration');
  set('#loading-spinner', 'page.flights.loading');
  const submitBtn = document.querySelector('#create-flight-form button[type="submit"]');
  if (submitBtn) submitBtn.textContent = t('page.flights.createFlight');
}

async function init(): Promise<void> {
  await initI18n();
  translateStaticElements();
  // Auth check — redirect to login if not authenticated
  const user = await fetchCurrentUser();
  if (!user) {
    window.location.href = '/login.html';
    return;
  }
  initTheme();
  renderUserInfo(user, 'flights');

  const store = flightsStore;

  // --- Subscribe to state changes ---
  store.subscribe((state, prev) => {
    if (
      state.flights !== prev.flights ||
      state.latestPacks !== prev.latestPacks ||
      state.activeRefreshes !== prev.activeRefreshes
    ) {
      ui.renderFlightList(
        state.flights,
        state.latestPacks,
        state.activeRefreshes,
        (id) => navigateToBriefing(id),
        (id) => navigateToFlight(id),
        (id) => store.getState().deleteFlight(id),
      );
    }
    if (state.loading !== prev.loading) {
      ui.renderLoading(state.loading);
    }
    if (state.error !== prev.error) {
      ui.renderError(state.error);
    }
  });

  // --- Load profiles for the selector ---
  try {
    loadedProfiles = await fetchProfiles();
    populateProfileSelector(loadedProfiles);
  } catch {
    // Profile selector stays empty; flights still work without it
  }

  // --- First-login welcome wizard ---
  if (!user.setup_completed) {
    try {
      const modelCatalog = await fetchModelCatalog();
      initModelCatalog(modelCatalog);
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
      const duration = parseFloat((document.getElementById('input-duration') as HTMLInputElement).value || '0');
      const profileSelect = document.getElementById('input-profile') as HTMLSelectElement;
      const profileId = profileSelect?.value ? parseInt(profileSelect.value, 10) : undefined;

      const waypoints = wpRaw.split(/[\s,]+/).filter(Boolean).map((w) => w.toUpperCase());
      if (!targetDate) {
        ui.renderError(t('flights.form.errorDate'));
        return;
      }
      if (waypoints.length < 2) {
        ui.renderError(t('flights.form.errorWaypoints'));
        return;
      }
      const invalidCodes = waypoints.filter((w) => !/^[A-Z]{4}$/.test(w));
      if (invalidCodes.length > 0) {
        ui.renderError(
          t('flights.form.errorInvalidCodes', { codes: invalidCodes.join(', ') }),
        );
        return;
      }

      try {
        const flight = await store.getState().createFlight(waypoints, targetDate, {
          targetTimeUtc: utcHour,
          targetMinuteUtc: utcMinute,
          cruiseAltitudeFt: altitude,
          flightCeilingFt: ceiling,
          flightDurationHours: duration,
          profileId: !isNaN(profileId!) ? profileId : undefined,
        });
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
  dateInput?.addEventListener('change', () => {
    if (lastWaypoints.length > 0) {
      populateTimezones(lastWaypoints);
    }
  });

  // --- Track manual duration edits ---
  const durationInput = document.getElementById('input-duration') as HTMLInputElement;
  durationInput?.addEventListener('input', () => {
    durationManuallyEdited = true;
  });

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

  // --- Initial load ---
  store.getState().loadFlights().then(() => {
    // Start polling active refreshes after flights load
    store.getState().pollActiveRefreshes();
  });

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
