/** Flight detail page entry point — wires store, UI manager, map inset, and edit mode. */

import { fetchCurrentUser } from './adapters/auth-adapter';
import { fetchProfiles, type ProfileResponse } from './adapters/profiles-adapter';
import { flightDetailStore } from './store/flight-detail-store';
import * as ui from './managers/flight-detail-ui';
import { RouteMapInset } from './components/route-map-inset';
import { renderUserInfo } from './utils';
import { initTheme } from './theme';
import { localToUtc, utcToLocal } from './utils/timezone';

async function init(): Promise<void> {
  // Auth check
  const user = await fetchCurrentUser();
  if (!user) {
    window.location.href = '/login.html';
    return;
  }
  initTheme();
  renderUserInfo(user);

  const store = flightDetailStore;

  // Get flight ID from URL
  const params = new URLSearchParams(window.location.search);
  const flightId = params.get('id');
  if (!flightId) {
    ui.renderError('No flight specified. Go back to flights list.');
    return;
  }

  // Load profiles for the selector
  let profiles: ProfileResponse[] = [];
  try {
    profiles = await fetchProfiles();
  } catch {
    // Profile selector stays empty; editing still works without it
  }

  // --- Route map inset ---
  const mapContainer = document.getElementById('route-map-inset');
  let mapInset: RouteMapInset | null = null;
  if (mapContainer) {
    mapInset = new RouteMapInset(mapContainer);
  }

  // --- Wire edit mode ---
  function wireEditButtons(): void {
    const editBtn = document.getElementById('btn-edit-flight');
    editBtn?.addEventListener('click', () => {
      store.getState().startEditing();
    });
  }

  /** Internal UTC times — stored so TZ changes can re-display the same instant. */
  let editUtcHour = 9;
  let editUtcMinute = 0;
  let editAltUtcHour = 9;
  let editAltUtcMinute = 0;

  /** Helper to get the reference date from the current flight. */
  function getEditRefDate(): Date {
    const flight = store.getState().flight;
    const dateStr = flight?.target_date || new Date().toISOString().slice(0, 10);
    return new Date(`${dateStr}T12:00:00Z`);
  }

  /** Read the selected TZ and convert displayed local times to internal UTC. */
  function syncUtcFromLocal(): void {
    const hourEl = document.getElementById('edit-hour') as HTMLSelectElement;
    const minuteEl = document.getElementById('edit-minute') as HTMLSelectElement;
    const tzEl = document.getElementById('edit-timezone') as HTMLSelectElement;
    if (!hourEl || !minuteEl || !tzEl) return;
    const tz = tzEl.value;
    const refDate = getEditRefDate();
    const utc = localToUtc(parseInt(hourEl.value, 10), parseInt(minuteEl.value, 10), tz, refDate);
    editUtcHour = utc.hour;
    editUtcMinute = utc.minute;

    const altHourEl = document.getElementById('edit-alt-hour') as HTMLSelectElement;
    const altMinuteEl = document.getElementById('edit-alt-minute') as HTMLSelectElement;
    if (altHourEl && altMinuteEl) {
      const altUtc = localToUtc(parseInt(altHourEl.value, 10), parseInt(altMinuteEl.value, 10), tz, refDate);
      editAltUtcHour = altUtc.hour;
      editAltUtcMinute = altUtc.minute;
    }
  }

  /** Re-display internal UTC times in the currently selected timezone. */
  function redisplayTimesInTz(): void {
    const hourEl = document.getElementById('edit-hour') as HTMLSelectElement;
    const minuteEl = document.getElementById('edit-minute') as HTMLSelectElement;
    const tzEl = document.getElementById('edit-timezone') as HTMLSelectElement;
    if (!hourEl || !minuteEl || !tzEl) return;
    const tz = tzEl.value;
    const refDate = getEditRefDate();
    const local = utcToLocal(editUtcHour, editUtcMinute, tz, refDate);
    hourEl.value = String(local.hour);
    minuteEl.value = String(local.minute);

    const altHourEl = document.getElementById('edit-alt-hour') as HTMLSelectElement;
    const altMinuteEl = document.getElementById('edit-alt-minute') as HTMLSelectElement;
    if (altHourEl && altMinuteEl) {
      const altLocal = utcToLocal(editAltUtcHour, editAltUtcMinute, tz, refDate);
      altHourEl.value = String(altLocal.hour);
      altMinuteEl.value = String(altLocal.minute);
    }
  }

  function wireEditForm(): void {
    const saveBtn = document.getElementById('edit-save');
    const cancelBtn = document.getElementById('edit-cancel');

    // Initialize internal UTC times from the flight
    const flight = store.getState().flight;
    if (flight) {
      const dt = new Date(flight.departure_time);
      editUtcHour = dt.getUTCHours();
      editUtcMinute = dt.getUTCMinutes();
      if (flight.alt_departure_time) {
        const altDt = new Date(flight.alt_departure_time);
        editAltUtcHour = altDt.getUTCHours();
        editAltUtcMinute = altDt.getUTCMinutes();
      }
    }

    // Wire timezone change → re-display both times in new TZ
    const tzEl = document.getElementById('edit-timezone') as HTMLSelectElement;
    tzEl?.addEventListener('change', redisplayTimesInTz);

    // Wire hour/minute change → track internal UTC values
    const hourEl = document.getElementById('edit-hour') as HTMLSelectElement;
    const minuteEl = document.getElementById('edit-minute') as HTMLSelectElement;
    hourEl?.addEventListener('change', syncUtcFromLocal);
    minuteEl?.addEventListener('change', syncUtcFromLocal);

    // Wire alt hour/minute change
    const altHourEl = document.getElementById('edit-alt-hour') as HTMLSelectElement;
    const altMinuteEl = document.getElementById('edit-alt-minute') as HTMLSelectElement;
    altHourEl?.addEventListener('change', syncUtcFromLocal);
    altMinuteEl?.addEventListener('change', syncUtcFromLocal);

    saveBtn?.addEventListener('click', async () => {
      if (!flight) return;

      const profileEl = document.getElementById('edit-profile') as HTMLSelectElement;
      const altEl = document.getElementById('edit-altitude') as HTMLInputElement;
      const ceilEl = document.getElementById('edit-ceiling') as HTMLInputElement;
      const durEl = document.getElementById('edit-duration') as HTMLInputElement;
      const altEnabledEl = document.getElementById('edit-alt-enabled') as HTMLInputElement;

      // Sync final UTC values from displayed local time
      syncUtcFromLocal();

      const profileId = profileEl ? parseInt(profileEl.value, 10) : undefined;
      const altitude = parseInt(altEl.value, 10);
      const ceiling = parseInt(ceilEl.value, 10);
      const duration = parseFloat(durEl.value);

      // Build ISO datetime from internal UTC
      const departureTime = `${flight.target_date}T${editUtcHour.toString().padStart(2, '0')}:${editUtcMinute.toString().padStart(2, '0')}:00Z`;

      // Build alt departure time (or empty string to clear)
      const altEnabled = altEnabledEl?.checked ?? false;
      const altDepartureTime = altEnabled
        ? `${flight.target_date}T${editAltUtcHour.toString().padStart(2, '0')}:${editAltUtcMinute.toString().padStart(2, '0')}:00Z`
        : '';

      await store.getState().saveFlight({
        profile_id: profileId,
        departure_time: departureTime,
        alt_departure_time: altDepartureTime,
        cruise_altitude_ft: altitude,
        flight_ceiling_ft: ceiling,
        flight_duration_hours: duration,
      });
    });

    cancelBtn?.addEventListener('click', () => {
      store.getState().cancelEditing();
    });
  }

  // --- Subscribe to state changes ---
  store.subscribe((state, prev) => {
    if (state.flight !== prev.flight || state.editing !== prev.editing || state.waypoints !== prev.waypoints) {
      ui.renderHeader(state.flight, state.editing);
      ui.renderFlightInfo(state.flight, state.editing, profiles, state.waypoints);
      if (state.editing) {
        wireEditForm();
      } else {
        wireEditButtons();
      }
    }
    if (state.waypoints !== prev.waypoints) {
      if (mapInset && state.waypoints.length > 0) {
        mapInset.render(state.waypoints);
      }
    }
    if (state.packs !== prev.packs) {
      const latestPack = state.packs.length > 0 ? state.packs[0] : null;
      ui.renderLatestAssessment(latestPack);
      ui.renderBriefingHistory(state.packs, flightId);
    }
    if (state.invalidation !== prev.invalidation) {
      ui.renderInvalidationBanner(state.invalidation, flightId);
    }
    if (state.loading !== prev.loading) {
      ui.renderLoading(state.loading);
    }
    if (state.error !== prev.error) {
      ui.renderError(state.error);
    }
  });

  // --- Load flight data ---
  await store.getState().loadFlight(flightId);

  // Initial render (subscriptions don't fire on first load for existing data)
  const s = store.getState();
  ui.renderHeader(s.flight, s.editing);
  ui.renderFlightInfo(s.flight, s.editing, profiles, s.waypoints);
  wireEditButtons();

  if (s.waypoints.length > 0 && mapInset) {
    mapInset.render(s.waypoints);
  }

  const latestPack = s.packs.length > 0 ? s.packs[0] : null;
  ui.renderLatestAssessment(latestPack);
  ui.renderBriefingHistory(s.packs, flightId);
  ui.renderLoading(s.loading);

  // Invalidate map size after layout settles
  requestAnimationFrame(() => {
    if (mapInset) mapInset.invalidateSize();
  });
}

// Boot
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
