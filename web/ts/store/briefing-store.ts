/** Zustand vanilla store for the Briefing report page. */

import { createStore } from 'zustand/vanilla';
import type { FlightResponse, ForecastSnapshot, PackMeta } from './types';
import * as api from '../adapters/api-adapter';

export interface BriefingState {
  // Data
  flight: FlightResponse | null;
  packs: PackMeta[];
  currentPack: PackMeta | null;
  snapshot: ForecastSnapshot | null;

  // UI state
  selectedModel: string;
  loading: boolean;
  refreshing: boolean;
  error: string | null;

  // Actions
  loadFlight: (id: string) => Promise<void>;
  loadPacks: () => Promise<void>;
  selectPack: (timestamp: string) => Promise<void>;
  selectLatest: () => Promise<void>;
  refresh: () => Promise<void>;
  setSelectedModel: (model: string) => void;
}

export const briefingStore = createStore<BriefingState>((set, get) => ({
  flight: null,
  packs: [],
  currentPack: null,
  snapshot: null,
  selectedModel: 'gfs',
  loading: false,
  refreshing: false,
  error: null,

  loadFlight: async (id: string) => {
    set({ loading: true, error: null });
    try {
      const flight = await api.fetchFlight(id);
      set({ flight, loading: false });
      await get().loadPacks();
      await get().selectLatest();
    } catch (err) {
      set({ loading: false, error: `Failed to load flight: ${err}` });
    }
  },

  loadPacks: async () => {
    const flight = get().flight;
    if (!flight) return;
    try {
      const packs = await api.fetchPacks(flight.id);
      set({ packs });
    } catch (err) {
      set({ error: `Failed to load packs: ${err}` });
    }
  },

  selectPack: async (timestamp: string) => {
    const flight = get().flight;
    if (!flight) return;
    set({ loading: true, error: null });
    try {
      const pack = await api.fetchPack(flight.id, timestamp);
      let snapshot: ForecastSnapshot | null = null;
      try {
        snapshot = await api.fetchSnapshot(flight.id, timestamp);
      } catch {
        // Snapshot may not be available
      }
      set({ currentPack: pack, snapshot, loading: false });
    } catch (err) {
      set({ loading: false, error: `Failed to load pack: ${err}` });
    }
  },

  selectLatest: async () => {
    const { packs } = get();
    if (packs.length > 0) {
      await get().selectPack(packs[0].fetch_timestamp);
    }
  },

  refresh: async () => {
    const flight = get().flight;
    if (!flight) return;
    set({ refreshing: true, error: null });
    try {
      const newPack = await api.refreshBriefing(flight.id);
      await get().loadPacks();
      await get().selectPack(newPack.fetch_timestamp);
      set({ refreshing: false });
    } catch (err) {
      set({ refreshing: false, error: `Refresh failed: ${err}` });
    }
  },

  setSelectedModel: (model: string) => {
    set({ selectedModel: model });
  },
}));
