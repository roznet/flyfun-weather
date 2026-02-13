/** Zustand vanilla store for the Briefing report page. */

import { createStore } from 'zustand/vanilla';
import type { FlightResponse, ForecastSnapshot, PackMeta, RouteAnalysesManifest, WeatherDigest } from './types';
import * as api from '../adapters/api-adapter';

export interface BriefingState {
  // Data
  flight: FlightResponse | null;
  packs: PackMeta[];
  currentPack: PackMeta | null;
  snapshot: ForecastSnapshot | null;
  digest: WeatherDigest | null;
  routeAnalyses: RouteAnalysesManifest | null;

  // UI state
  selectedModel: string;
  selectedPointIndex: number;
  loading: boolean;
  refreshing: boolean;
  emailing: boolean;
  error: string | null;

  // Actions
  loadFlight: (id: string) => Promise<void>;
  loadPacks: () => Promise<void>;
  selectPack: (timestamp: string) => Promise<void>;
  selectLatest: () => Promise<void>;
  refresh: () => Promise<void>;
  setSelectedModel: (model: string) => void;
  setSelectedPoint: (index: number) => void;
  sendEmail: () => Promise<void>;
}

export const briefingStore = createStore<BriefingState>((set, get) => ({
  flight: null,
  packs: [],
  currentPack: null,
  snapshot: null,
  digest: null,
  routeAnalyses: null,
  selectedModel: 'ecmwf',
  selectedPointIndex: 0,
  loading: false,
  refreshing: false,
  emailing: false,
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
      let digest: WeatherDigest | null = null;
      let routeAnalyses: RouteAnalysesManifest | null = null;
      try {
        snapshot = await api.fetchSnapshot(flight.id, timestamp);
      } catch {
        // Snapshot may not be available
      }
      if (pack.has_digest) {
        try {
          const url = api.digestJsonUrl(flight.id, timestamp);
          const resp = await fetch(url);
          if (resp.ok) digest = await resp.json();
        } catch {
          // Digest fetch is non-critical
        }
      }
      try {
        routeAnalyses = await api.fetchRouteAnalyses(flight.id, timestamp);
      } catch {
        // Old packs may not have route analyses
      }
      set({ currentPack: pack, snapshot, digest, routeAnalyses, selectedPointIndex: 0, loading: false });
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

  setSelectedPoint: (index: number) => {
    set({ selectedPointIndex: index });
  },

  sendEmail: async () => {
    const { flight, currentPack } = get();
    if (!flight || !currentPack) return;
    set({ emailing: true, error: null });
    try {
      await api.sendEmail(flight.id, currentPack.fetch_timestamp);
      set({ emailing: false });
    } catch (err) {
      set({ emailing: false, error: `Email failed: ${err}` });
    }
  },
}));
