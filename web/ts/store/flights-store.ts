/** Zustand vanilla store for the Flights management page. */

import { createStore } from 'zustand/vanilla';
import type { FlightResponse, PackMeta } from './types';
import type { RefreshEntry } from '../adapters/api-adapter';
import * as api from '../adapters/api-adapter';

export interface FlightsState {
  // Data
  flights: FlightResponse[];
  latestPacks: Record<string, PackMeta | null>; // flight_id → latest pack
  activeRefreshes: Record<string, RefreshEntry>; // flight_id → active refresh entry

  // UI state
  loading: boolean;
  error: string | null;

  // Actions
  loadFlights: () => Promise<void>;
  pollActiveRefreshes: () => Promise<void>;
  createFlight: (waypoints: string[], targetDate: string, opts?: {
    routeName?: string;
    targetTimeUtc?: number;
    cruiseAltitudeFt?: number;
    flightCeilingFt?: number;
    flightDurationHours?: number;
    profileId?: number;
  }) => Promise<FlightResponse>;
  deleteFlight: (id: string) => Promise<void>;
}

export const flightsStore = createStore<FlightsState>((set, get) => ({
  flights: [],
  latestPacks: {},
  activeRefreshes: {},
  loading: false,
  error: null,

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

  pollActiveRefreshes: async () => {
    try {
      const entries = await api.fetchActiveRefreshes();
      const map: Record<string, RefreshEntry> = {};
      for (const e of entries) {
        map[e.flight_id] = e;
      }
      set({ activeRefreshes: map });
    } catch {
      // Non-critical — silently ignore polling errors
    }
  },

  createFlight: async (waypoints, targetDate, opts) => {
    set({ loading: true, error: null });
    try {
      // Build ISO datetime from date + hour
      const hour = (opts?.targetTimeUtc ?? 9).toString().padStart(2, '0');
      const departureTime = `${targetDate}T${hour}:00:00Z`;

      const flight = await api.createFlight({
        waypoints,
        route_name: opts?.routeName,
        departure_time: departureTime,
        cruise_altitude_ft: opts?.cruiseAltitudeFt,
        flight_ceiling_ft: opts?.flightCeilingFt,
        flight_duration_hours: opts?.flightDurationHours,
        profile_id: opts?.profileId,
      });
      // Refresh the list
      await get().loadFlights();
      set({ loading: false });
      return flight;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      // Strip "API 422: " prefix to show the backend detail directly
      const detail = msg.replace(/^API \d+:\s*/, '');
      set({ loading: false, error: detail });
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
