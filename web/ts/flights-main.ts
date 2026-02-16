/** Flights page entry point — wires store, UI manager, and event handlers. */

import { fetchCurrentUser } from './adapters/auth-adapter';
import { flightsStore } from './store/flights-store';
import * as ui from './managers/flights-ui';
import { renderUserInfo } from './utils';

async function init(): Promise<void> {
  // Auth check — redirect to login if not authenticated
  const user = await fetchCurrentUser();
  if (!user) {
    window.location.href = '/login.html';
    return;
  }
  renderUserInfo(user);

  const store = flightsStore;

  // --- Subscribe to state changes ---
  store.subscribe((state, prev) => {
    if (state.flights !== prev.flights || state.latestPacks !== prev.latestPacks) {
      ui.renderFlightList(
        state.flights,
        state.latestPacks,
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
        });
        // Navigate to briefing page for the new flight
        navigateToBriefing(flight.id);
      } catch {
        // Error already set in store via API response
      }
    });
  }

  // --- Initial load ---
  store.getState().loadFlights();
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
