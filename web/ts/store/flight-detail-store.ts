/** Zustand vanilla store for the Flight Detail page. */

import { createStore } from 'zustand/vanilla';
import type { FlightResponse, PackMeta } from './types';
import type { WaypointInfo, UpdateFlightRequest, UpdateFlightResponse } from '../adapters/api-adapter';
import * as api from '../adapters/api-adapter';
import { errorToMessage } from '../utils';

export interface FlightDetailState {
  // Data
  flight: FlightResponse | null;
  packs: PackMeta[];
  waypoints: WaypointInfo[];

  // UI state
  loading: boolean;
  saving: boolean;
  editing: boolean;
  error: string | null;
  invalidation: string | null; // "none" | "advisories_only" | "refetch_needed"

  // Actions
  loadFlight: (id: string) => Promise<void>;
  loadPacks: () => Promise<void>;
  loadWaypoints: () => Promise<void>;
  startEditing: () => void;
  cancelEditing: () => void;
  saveFlight: (req: UpdateFlightRequest) => Promise<UpdateFlightResponse | null>;
  subscribe: () => Promise<void>;
  unsubscribe: () => Promise<void>;
  clearInvalidation: () => void;
}

export const flightDetailStore = createStore<FlightDetailState>((set, get) => ({
  flight: null,
  packs: [],
  waypoints: [],
  loading: false,
  saving: false,
  editing: false,
  error: null,
  invalidation: null,

  loadFlight: async (id: string) => {
    set({ loading: true, error: null });
    try {
      const flight = await api.fetchFlight(id);
      set({ flight, loading: false });
      // Load packs and waypoints in parallel
      get().loadPacks();
      get().loadWaypoints();
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      set({ loading: false, error: msg.replace(/^API \d+:\s*/, '') });
    }
  },

  loadPacks: async () => {
    const { flight } = get();
    if (!flight) return;
    try {
      const packs = await api.fetchPacks(flight.id);
      // Sort by timestamp descending (most recent first)
      packs.sort((a, b) => b.fetch_timestamp.localeCompare(a.fetch_timestamp));
      set({ packs });
    } catch {
      // Non-critical — packs may not exist yet
      set({ packs: [] });
    }
  },

  loadWaypoints: async () => {
    const { flight } = get();
    if (!flight || flight.waypoints.length < 2) return;
    try {
      const resp = await api.fetchRouteDistance(flight.waypoints);
      set({ waypoints: resp.waypoints });
    } catch {
      // Non-critical — map just won't show
    }
  },

  startEditing: () => set({ editing: true, invalidation: null }),
  cancelEditing: () => set({ editing: false }),

  saveFlight: async (req: UpdateFlightRequest) => {
    const { flight } = get();
    if (!flight) return null;

    set({ saving: true, error: null });
    try {
      const updated = await api.updateFlight(flight.id, req);
      set({
        flight: updated,
        saving: false,
        editing: false,
        invalidation: updated.invalidation === 'none' ? null : updated.invalidation,
      });
      return updated;
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      set({ saving: false, error: msg.replace(/^API \d+:\s*/, '') });
      return null;
    }
  },

  subscribe: async () => {
    const { flight } = get();
    if (!flight) return;
    set({ saving: true, error: null });
    try {
      const updated = await api.subscribeAndRefetch(flight.id);
      set({ flight: updated, saving: false });
    } catch (err) {
      set({ saving: false, error: errorToMessage(err) });
    }
  },

  unsubscribe: async () => {
    const { flight } = get();
    if (!flight) return;
    set({ saving: true, error: null });
    try {
      const updated = await api.unsubscribeAndRefetch(flight.id);
      set({ flight: updated, saving: false });
    } catch (err) {
      set({ saving: false, error: errorToMessage(err) });
    }
  },

  clearInvalidation: () => set({ invalidation: null }),
}));
