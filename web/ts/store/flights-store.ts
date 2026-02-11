/** Zustand vanilla store for the Flights management page. */

import { createStore } from 'zustand/vanilla';
import type { FlightResponse, PackMeta, RouteInfo } from './types';
import * as api from '../adapters/api-adapter';

export interface FlightsState {
  // Data
  routes: RouteInfo[];
  flights: FlightResponse[];
  latestPacks: Record<string, PackMeta | null>; // flight_id → latest pack

  // UI state
  loading: boolean;
  error: string | null;

  // Actions
  loadRoutes: () => Promise<void>;
  loadFlights: () => Promise<void>;
  createFlight: (waypoints: string[], targetDate: string, opts?: {
    routeName?: string;
    targetTimeUtc?: number;
    cruiseAltitudeFt?: number;
    flightCeilingFt?: number;
    flightDurationHours?: number;
  }) => Promise<FlightResponse>;
  deleteFlight: (id: string) => Promise<void>;
}

export const flightsStore = createStore<FlightsState>((set, get) => ({
  routes: [],
  flights: [],
  latestPacks: {},
  loading: false,
  error: null,

  loadRoutes: async () => {
    try {
      const routes = await api.fetchRoutes();
      set({ routes });
    } catch (err) {
      set({ error: `Failed to load routes: ${err}` });
    }
  },

  loadFlights: async () => {
    set({ loading: true, error: null });
    try {
      const flights = await api.fetchFlights();
      set({ flights, loading: false });

      // Load latest pack for each flight (in parallel)
      const packs: Record<string, PackMeta | null> = {};
      await Promise.all(
        flights.map(async (f) => {
          try {
            packs[f.id] = await api.fetchLatestPack(f.id);
          } catch {
            packs[f.id] = null;
          }
        })
      );
      set({ latestPacks: packs });
    } catch (err) {
      set({ loading: false, error: `Failed to load flights: ${err}` });
    }
  },

  createFlight: async (waypoints, targetDate, opts) => {
    set({ loading: true, error: null });
    try {
      const flight = await api.createFlight({
        waypoints,
        route_name: opts?.routeName,
        target_date: targetDate,
        target_time_utc: opts?.targetTimeUtc,
        cruise_altitude_ft: opts?.cruiseAltitudeFt,
        flight_ceiling_ft: opts?.flightCeilingFt,
        flight_duration_hours: opts?.flightDurationHours,
      });
      // Refresh the list
      await get().loadFlights();
      set({ loading: false });
      return flight;
    } catch (err) {
      set({ loading: false, error: `Failed to create flight: ${err}` });
      throw err;
    }
  },

  deleteFlight: async (id) => {
    set({ loading: true, error: null });
    try {
      await api.deleteFlight(id);
      await get().loadFlights();
      set({ loading: false });
    } catch (err) {
      set({ loading: false, error: `Failed to delete flight: ${err}` });
    }
  },
}));
