/** Zustand vanilla store for the Briefing report page. */

import { createStore } from 'zustand/vanilla';
import type { DataStatus, FlightResponse, ForecastSnapshot, PackMeta, RouteAnalysesManifest, WeatherDigest } from './types';
import type { DisplayMode, Tier } from '../types/metrics';
import type { RenderMode, VizSettings } from '../visualization/types';
import { getTierDefaults } from '../helpers/metrics-helper';
import { getDefaultEnabled } from '../visualization/cross-section/layer-registry';
import * as api from '../adapters/api-adapter';

// --- localStorage persistence helpers ---

function loadDisplayMode(): DisplayMode {
  try {
    const v = localStorage.getItem('wb_displayMode');
    if (v === 'compact' || v === 'annotated') return v;
  } catch { /* ignore */ }
  return 'annotated';
}

function loadTierVisibility(): Record<Tier, boolean> {
  try {
    const v = localStorage.getItem('wb_tierVisibility');
    if (v) return { ...getTierDefaults(), ...JSON.parse(v) };
  } catch { /* ignore */ }
  return getTierDefaults();
}

function loadVizSettings(): VizSettings {
  const defaults: VizSettings = {
    layout: 'cross-section',
    renderMode: 'smooth',
    enabledLayers: getDefaultEnabled(),
    mapColorMetric: 'icing-risk',
    mapWidthMetric: 'cloud-cover',
  };
  try {
    const v = localStorage.getItem('wb_vizSettings');
    if (v) {
      const saved = JSON.parse(v);
      return {
        ...defaults,
        ...saved,
        enabledLayers: { ...defaults.enabledLayers, ...saved.enabledLayers },
      };
    }
  } catch { /* ignore */ }
  return defaults;
}

function saveVizSettings(settings: VizSettings): void {
  try { localStorage.setItem('wb_vizSettings', JSON.stringify(settings)); } catch { /* ignore */ }
}

export interface BriefingState {
  // Data
  flight: FlightResponse | null;
  packs: PackMeta[];
  currentPack: PackMeta | null;
  snapshot: ForecastSnapshot | null;
  digest: WeatherDigest | null;
  routeAnalyses: RouteAnalysesManifest | null;
  freshness: DataStatus | null;
  freshnessLoading: boolean;

  // UI state
  selectedModel: string;
  selectedPointIndex: number;
  displayMode: DisplayMode;
  tierVisibility: Record<Tier, boolean>;
  vizSettings: VizSettings;
  loading: boolean;
  refreshing: boolean;
  refreshStage: string | null;
  refreshDetail: string | null;
  refreshProgress: number;
  emailing: boolean;
  error: string | null;

  // Actions
  loadFlight: (id: string) => Promise<void>;
  loadPacks: () => Promise<void>;
  selectPack: (timestamp: string) => Promise<void>;
  selectLatest: () => Promise<void>;
  refresh: () => Promise<void>;
  forceRefresh: () => Promise<void>;
  checkFreshness: () => Promise<void>;
  setSelectedModel: (model: string) => void;
  setSelectedPoint: (index: number) => void;
  setDisplayMode: (mode: DisplayMode) => void;
  toggleTier: (tier: Tier) => void;
  setRenderMode: (mode: RenderMode) => void;
  toggleVizLayer: (layerId: string) => void;
  sendEmail: () => Promise<void>;
}

export const briefingStore = createStore<BriefingState>((set, get) => ({
  flight: null,
  packs: [],
  currentPack: null,
  snapshot: null,
  digest: null,
  routeAnalyses: null,
  freshness: null,
  freshnessLoading: false,
  selectedModel: 'ecmwf',
  selectedPointIndex: 0,
  displayMode: loadDisplayMode(),
  tierVisibility: loadTierVisibility(),
  vizSettings: loadVizSettings(),
  loading: false,
  refreshing: false,
  refreshStage: null,
  refreshDetail: null,
  refreshProgress: 0,
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
    set({ refreshing: true, refreshStage: null, refreshDetail: null, refreshProgress: 0, error: null });
    try {
      const newPack = await api.refreshBriefingStream(flight.id, (event) => {
        if (event.type === 'progress') {
          set({
            refreshStage: event.label || event.stage || null,
            refreshDetail: event.detail || null,
            refreshProgress: event.progress || 0,
          });
        }
      });
      // If the server returned a data_status (fresh skip), update freshness
      if (newPack.data_status) {
        set({ freshness: newPack.data_status });
      }
      await get().loadPacks();
      await get().selectPack(newPack.fetch_timestamp);
      set({ refreshing: false, refreshStage: null, refreshDetail: null, refreshProgress: 0 });
      // Re-check freshness after a real refresh
      if (!newPack.data_status) {
        get().checkFreshness();
      }
    } catch (err) {
      set({ refreshing: false, refreshStage: null, refreshDetail: null, refreshProgress: 0, error: `Refresh failed: ${err}` });
    }
  },

  forceRefresh: async () => {
    const flight = get().flight;
    if (!flight) return;
    set({ refreshing: true, refreshStage: null, refreshDetail: null, refreshProgress: 0, error: null });
    try {
      const newPack = await api.refreshBriefingStream(flight.id, (event) => {
        if (event.type === 'progress') {
          set({
            refreshStage: event.label || event.stage || null,
            refreshDetail: event.detail || null,
            refreshProgress: event.progress || 0,
          });
        }
      }, true);
      await get().loadPacks();
      await get().selectPack(newPack.fetch_timestamp);
      set({ refreshing: false, refreshStage: null, refreshDetail: null, refreshProgress: 0 });
      get().checkFreshness();
    } catch (err) {
      set({ refreshing: false, refreshStage: null, refreshDetail: null, refreshProgress: 0, error: `Refresh failed: ${err}` });
    }
  },

  checkFreshness: async () => {
    const flight = get().flight;
    if (!flight) return;
    set({ freshnessLoading: true });
    try {
      const status = await api.fetchFreshness(flight.id);
      set({ freshness: status, freshnessLoading: false });
    } catch {
      set({ freshnessLoading: false });
    }
  },

  setSelectedModel: (model: string) => {
    set({ selectedModel: model });
  },

  setSelectedPoint: (index: number) => {
    set({ selectedPointIndex: index });
  },

  setDisplayMode: (mode: DisplayMode) => {
    set({ displayMode: mode });
    try { localStorage.setItem('wb_displayMode', mode); } catch { /* ignore */ }
  },

  toggleTier: (tier: Tier) => {
    const current = get().tierVisibility;
    const updated = { ...current, [tier]: !current[tier] };
    set({ tierVisibility: updated });
    try { localStorage.setItem('wb_tierVisibility', JSON.stringify(updated)); } catch { /* ignore */ }
  },

  setRenderMode: (mode: RenderMode) => {
    const updated = { ...get().vizSettings, renderMode: mode };
    set({ vizSettings: updated });
    saveVizSettings(updated);
  },

  toggleVizLayer: (layerId: string) => {
    const current = get().vizSettings;
    const enabled = { ...current.enabledLayers, [layerId]: !(current.enabledLayers[layerId] !== false) };
    const updated = { ...current, enabledLayers: enabled };
    set({ vizSettings: updated });
    saveVizSettings(updated);
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
