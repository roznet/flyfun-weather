/** Zustand vanilla store for the Flights management page. */

import { createStore } from 'zustand/vanilla';
import type { DebriefStats, FlightResponse, PackMeta } from './types';
import type { RefreshEntry } from '../adapters/api-adapter';
import * as api from '../adapters/api-adapter';
import { fetchDebriefStats } from '../adapters/debrief-adapter';
import { errorToMessage } from '../utils';

export interface FlightsState {
  // Data
  flights: FlightResponse[];
  latestPacks: Record<string, PackMeta | null>; // flight_id → latest pack
  activeRefreshes: Record<string, RefreshEntry>; // flight_id → active refresh entry
  debriefStats: DebriefStats | null;

  // UI state
  loading: boolean;
  error: string | null;
  selectedIds: Set<string>;  // flights ticked for bulk actions

  // Actions
  loadFlights: () => Promise<void>;
  loadDebriefStats: () => Promise<void>;
  pollActiveRefreshes: () => Promise<void>;
  createFlight: (waypoints: string[], targetDate: string, opts?: {
    routeName?: string;
    targetTimeUtc?: number;
    targetMinuteUtc?: number;
    cruiseAltitudeFt?: number;
    flightCeilingFt?: number;
    flightDurationHours?: number;
    profileId?: number;
    aircraftId?: number;
    rawRoute?: string;  // original Field-15 input from the popup flow
  }) => Promise<FlightResponse>;
  deleteFlight: (id: string) => Promise<void>;
  unsubscribeFlight: (id: string) => Promise<void>;
  toggleSelected: (id: string) => void;
  setSelected: (ids: string[]) => void;
  clearSelection: () => void;
  bulkDeleteSelected: () => Promise<{ deleted: number; notFound: number }>;
}

export const flightsStore = createStore<FlightsState>((set, get) => ({
  flights: [],
  latestPacks: {},
  activeRefreshes: {},
  debriefStats: null,
  loading: false,
  error: null,
  selectedIds: new Set(),

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

  loadDebriefStats: async () => {
    try {
      const stats = await fetchDebriefStats();
      set({ debriefStats: stats });
    } catch {
      // Non-critical — leave stats null on failure (panel just doesn't render).
    }
  },

  pollActiveRefreshes: async () => {
    try {
      const entries = await api.fetchActiveRefreshes();
      const map: Record<string, RefreshEntry> = {};
      for (const e of entries) {
        map[e.flight_id] = e;
      }
      // Skip set() if contents are identical — otherwise a fresh empty {}
      // every 5s churns object identity and triggers a full list re-render,
      // which wipes inline state (e.g. an open debrief form).
      const current = get().activeRefreshes;
      const currentKeys = Object.keys(current);
      const newKeys = Object.keys(map);
      const sameShape =
        currentKeys.length === newKeys.length &&
        newKeys.every((k) => current[k]?.status === map[k]?.status);
      if (!sameShape) {
        set({ activeRefreshes: map });
      }
    } catch {
      // Non-critical — silently ignore polling errors
    }
  },

  createFlight: async (waypoints, targetDate, opts) => {
    set({ loading: true, error: null });
    try {
      // Build ISO datetime from date + hour + minute
      const hour = (opts?.targetTimeUtc ?? 9).toString().padStart(2, '0');
      const minute = (opts?.targetMinuteUtc ?? 0).toString().padStart(2, '0');
      const departureTime = `${targetDate}T${hour}:${minute}:00Z`;

      const flight = await api.createFlight({
        waypoints,
        route_name: opts?.routeName,
        departure_time: departureTime,
        cruise_altitude_ft: opts?.cruiseAltitudeFt,
        flight_ceiling_ft: opts?.flightCeilingFt,
        flight_duration_hours: opts?.flightDurationHours,
        profile_id: opts?.profileId,
        aircraft_id: opts?.aircraftId,
        raw_route: opts?.rawRoute,
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

  unsubscribeFlight: async (id) => {
    set({ loading: true, error: null });
    try {
      await api.unsubscribeFlight(id);
      await get().loadFlights();
      set({ loading: false });
    } catch (err) {
      set({ loading: false, error: errorToMessage(err) });
    }
  },

  toggleSelected: (id) => {
    const next = new Set(get().selectedIds);
    if (next.has(id)) next.delete(id);
    else next.add(id);
    set({ selectedIds: next });
  },

  setSelected: (ids) => {
    set({ selectedIds: new Set(ids) });
  },

  clearSelection: () => {
    set({ selectedIds: new Set() });
  },

  bulkDeleteSelected: async () => {
    const ids = Array.from(get().selectedIds);
    if (ids.length === 0) return { deleted: 0, notFound: 0 };
    set({ loading: true, error: null });
    try {
      const resp = await api.bulkDeleteFlights(ids);
      set({ selectedIds: new Set() });
      await get().loadFlights();
      set({ loading: false });
      return { deleted: resp.deleted.length, notFound: resp.not_found.length };
    } catch (err) {
      set({ loading: false, error: `Failed to delete flights: ${err}` });
      throw err;
    }
  },
}));
