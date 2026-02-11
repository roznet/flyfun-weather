/** Flights page entry point — wires store, UI manager, and event handlers. */

import { flightsStore } from './store/flights-store';
import * as ui from './managers/flights-ui';

function init(): void {
  const store = flightsStore;

  // --- Subscribe to state changes ---
  store.subscribe((state, prev) => {
    if (state.flights !== prev.flights || state.latestPacks !== prev.latestPacks || state.routes !== prev.routes) {
      ui.renderFlightList(
        state.flights,
        state.latestPacks,
        state.routes,
        (id) => navigateToBriefing(id),
        (id) => store.getState().deleteFlight(id),
      );
    }
    if (state.routes !== prev.routes) {
      ui.renderRouteOptions(state.routes);
      ui.onRouteSelected(state.routes);
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
      const routeName = (document.getElementById('route-select') as HTMLSelectElement).value || undefined;
      const targetDate = (document.getElementById('input-date') as HTMLInputElement).value;
      const targetTime = parseInt((document.getElementById('input-time') as HTMLInputElement).value || '9', 10);
      const altitude = parseInt((document.getElementById('input-altitude') as HTMLInputElement).value || '8000', 10);
      const ceiling = parseInt((document.getElementById('input-ceiling') as HTMLInputElement).value || '18000', 10);
      const duration = parseFloat((document.getElementById('input-duration') as HTMLInputElement).value || '0');

      const waypoints = wpRaw.split(/[\s,]+/).filter(Boolean).map((w) => w.toUpperCase());
      if (waypoints.length < 2 || !targetDate) {
        ui.renderError('Enter at least 2 waypoints and a date.');
        return;
      }

      try {
        const flight = await store.getState().createFlight(waypoints, targetDate, {
          routeName,
          targetTimeUtc: targetTime,
          cruiseAltitudeFt: altitude,
          flightCeilingFt: ceiling,
          flightDurationHours: duration,
        });
        // Navigate to briefing page for the new flight
        navigateToBriefing(flight.id);
      } catch {
        // Error already set in store
      }
    });
  }

  // --- Initial load ---
  store.getState().loadRoutes();
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
