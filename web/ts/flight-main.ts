/** Flight detail page entry point — wires store, UI manager, map inset, and edit mode. */

import { fetchCurrentUser } from './adapters/auth-adapter';
import { fetchProfiles, type ProfileResponse } from './adapters/profiles-adapter';
import { flightDetailStore } from './store/flight-detail-store';
import * as ui from './managers/flight-detail-ui';
import { RouteMapInset } from './components/route-map-inset';
import { renderUserInfo } from './utils';
import { initTheme } from './theme';

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

  function wireEditForm(): void {
    const saveBtn = document.getElementById('edit-save');
    const cancelBtn = document.getElementById('edit-cancel');

    saveBtn?.addEventListener('click', async () => {
      const flight = store.getState().flight;
      if (!flight) return;

      const profileEl = document.getElementById('edit-profile') as HTMLSelectElement;
      const hourEl = document.getElementById('edit-hour') as HTMLSelectElement;
      const minuteEl = document.getElementById('edit-minute') as HTMLSelectElement;
      const altEl = document.getElementById('edit-altitude') as HTMLInputElement;
      const ceilEl = document.getElementById('edit-ceiling') as HTMLInputElement;
      const durEl = document.getElementById('edit-duration') as HTMLInputElement;

      const profileId = profileEl ? parseInt(profileEl.value, 10) : undefined;
      const hour = parseInt(hourEl.value, 10);
      const minute = parseInt(minuteEl.value, 10);
      const altitude = parseInt(altEl.value, 10);
      const ceiling = parseInt(ceilEl.value, 10);
      const duration = parseFloat(durEl.value);

      // Build ISO datetime using the same date
      const departureTime = `${flight.target_date}T${hour.toString().padStart(2, '0')}:${minute.toString().padStart(2, '0')}:00Z`;

      await store.getState().saveFlight({
        profile_id: profileId,
        departure_time: departureTime,
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
    if (state.flight !== prev.flight || state.editing !== prev.editing) {
      ui.renderHeader(state.flight, state.editing);
      ui.renderFlightInfo(state.flight, state.editing, profiles);
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
  ui.renderFlightInfo(s.flight, s.editing, profiles);
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
