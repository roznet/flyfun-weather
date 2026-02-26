/** Flights page entry point — wires store, UI manager, and event handlers. */

import { fetchCurrentUser } from './adapters/auth-adapter';
import { fetchRouteDistance } from './adapters/api-adapter';
import { fetchModelCatalog } from './adapters/preferences-adapter';
import { fetchProfiles, type ProfileResponse } from './adapters/profiles-adapter';
import { flightsStore } from './store/flights-store';
import * as ui from './managers/flights-ui';
import { renderUserInfo, initModelCatalog } from './utils';
import { showWelcomeWizard } from './components/welcome-wizard';

let loadedProfiles: ProfileResponse[] = [];

/** Whether the user has manually edited the duration field since the last
 *  waypoint or profile change. When true, auto-calculation is suppressed. */
let durationManuallyEdited = false;

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

/** Compute and populate the duration field from route distance and profile speed. */
async function updateDurationFromSpeed(): Promise<void> {
  if (durationManuallyEdited) return;

  const profile = getSelectedProfile();
  const speedKt = profile?.settings?.speed_kt;
  if (!speedKt || speedKt <= 0) return;

  const waypoints = parseWaypoints();
  if (waypoints.length < 2) return;

  try {
    const { total_distance_nm } = await fetchRouteDistance(waypoints);
    const durationHours = Math.ceil(total_distance_nm / speedKt);
    const durationInput = document.getElementById('input-duration') as HTMLInputElement;
    if (durationInput) {
      durationInput.value = String(durationHours);
    }
  } catch (err) {
    console.error('Failed to auto-calculate flight duration:', err);
  }
}

async function init(): Promise<void> {
  // Auth check — redirect to login if not authenticated
  const user = await fetchCurrentUser();
  if (!user) {
    window.location.href = '/login.html';
    return;
  }
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
        await showWelcomeWizard(user.name, defaultProfile, modelCatalog);
        // Reload profiles after wizard may have updated the default profile
        loadedProfiles = await fetchProfiles();
        populateProfileSelector(loadedProfiles);
      }
    } catch (err) {
      console.error('Welcome wizard error:', err);
    }
  }

  // --- Wire create flight form ---
  const form = document.getElementById('create-flight-form') as HTMLFormElement;
  if (form) {
    form.addEventListener('submit', async (e) => {
      e.preventDefault();

      const wpRaw = (document.getElementById('input-waypoints') as HTMLInputElement).value.trim();
      const targetDate = (document.getElementById('input-date') as HTMLInputElement).value;
      const targetTime = parseInt((document.getElementById('input-time') as HTMLInputElement).value || '9', 10);
      const altitude = parseInt((document.getElementById('input-altitude') as HTMLInputElement).value || '8000', 10);
      const ceiling = parseInt((document.getElementById('input-ceiling') as HTMLInputElement).value || '18000', 10);
      const duration = parseFloat((document.getElementById('input-duration') as HTMLInputElement).value || '0');
      const profileSelect = document.getElementById('input-profile') as HTMLSelectElement;
      const profileId = profileSelect?.value ? parseInt(profileSelect.value, 10) : undefined;

      const waypoints = wpRaw.split(/[\s,]+/).filter(Boolean).map((w) => w.toUpperCase());
      if (!targetDate) {
        ui.renderError('Please enter a date.');
        return;
      }
      if (waypoints.length < 2) {
        ui.renderError('Route must be a list of at least 2 ICAO airport codes separated by spaces (e.g. EGTK LFQA LSGS).');
        return;
      }
      const invalidCodes = waypoints.filter((w) => !/^[A-Z]{4}$/.test(w));
      if (invalidCodes.length > 0) {
        ui.renderError(
          `Each waypoint must be a 4-letter ICAO airport code separated by spaces. Invalid: ${invalidCodes.join(', ')}`,
        );
        return;
      }

      try {
        const flight = await store.getState().createFlight(waypoints, targetDate, {
          targetTimeUtc: targetTime,
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

  // --- Auto-calculate duration when waypoints change ---
  const waypointsInput = document.getElementById('input-waypoints') as HTMLInputElement;
  waypointsInput?.addEventListener('blur', () => {
    durationManuallyEdited = false;
    updateDurationFromSpeed();
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
    updateDurationFromSpeed();
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
    const defaultTag = p.is_default ? ' (default)' : '';
    const selected = p.is_default ? ' selected' : '';
    return `<option value="${p.id}"${selected}>${p.name}${defaultTag}</option>`;
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

function navigateToBriefing(flightId: string): void {
  window.location.href = `/briefing.html?flight=${encodeURIComponent(flightId)}`;
}

// Boot
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
