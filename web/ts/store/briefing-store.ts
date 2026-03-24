/** Zustand vanilla store for the Briefing report page. */

import { createStore } from 'zustand/vanilla';
import type { DataStatus, ElevationProfile, FlightResponse, ForecastSnapshot, PackMeta, RouteAnalysesManifest, WeatherDigest } from './types';
import type { AltitudeTableResult, RouteAdvisoriesManifest } from '../types/advisories';
import type { DisplayMode, Tier } from '../types/metrics';
import type { VizLayout, VizSettings } from '../visualization/types';
import { getTierDefaults } from '../helpers/metrics-helper';
import { getDefaultEnabled } from '../visualization/cross-section/layer-registry';
import { setActiveTheme, type ThemeId, THEMES } from '../visualization/cross-section/theme';
import { RefreshStreamError } from '../adapters/api-adapter';
import * as api from '../adapters/api-adapter';

// --- localStorage persistence helpers ---

function loadDisplayMode(): DisplayMode {
  try {
    const v = localStorage.getItem('wb_displayMode');
    if (v === 'compact') return v;
    if (v === 'full' || v === 'annotated') return 'full';
  } catch { /* ignore */ }
  return 'compact';
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
    enabledLayers: getDefaultEnabled(),
    mapColorMetric: 'icing-risk-at-level',
    mapWidthMetric: 'cloud-cover-total',
    mapAltitudeFt: null,
    routeGraphVisible: true,
    routeGraphLeftMetric: 'headwind',
    routeGraphRightMetric: 'temperature',
    compareLayer: 'icing-bands',
    compareModels: {},
  };
  try {
    const v = localStorage.getItem('wb_vizSettings');
    if (v) {
      const saved = JSON.parse(v);
      return {
        ...defaults,
        ...saved,
        enabledLayers: { ...defaults.enabledLayers, ...saved.enabledLayers },
        compareModels: { ...defaults.compareModels, ...saved.compareModels },
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
  routeAdvisories: RouteAdvisoriesManifest | null;
  altAdvisories: RouteAdvisoriesManifest | null;
  showingAlt: boolean;
  elevationProfile: ElevationProfile | null;
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
  refreshStatus: 'queued' | 'refreshing' | null;
  refreshStage: string | null;
  refreshDetail: string | null;
  refreshProgress: number;
  refreshElapsed: number | null;
  avgRefreshSeconds: number | null;
  notifyEmail: boolean;
  advisoryAltitudeOverride: number | null;
  altitudeTable: AltitudeTableResult | null;
  altitudeTableLoading: boolean;
  emailing: boolean;
  error: string | null;

  // Actions
  loadFlight: (id: string) => Promise<void>;
  loadPacks: () => Promise<void>;
  selectPack: (timestamp: string) => Promise<void>;
  selectLatest: () => Promise<void>;
  refresh: (asOfDate?: string) => Promise<void>;
  forceRefresh: () => Promise<void>;
  checkActiveRefresh: () => Promise<void>;
  checkFreshness: () => Promise<void>;
  setSelectedModel: (model: string) => void;
  setSelectedPoint: (index: number) => void;
  setDisplayMode: (mode: DisplayMode) => void;
  toggleTier: (tier: Tier) => void;
  toggleVizLayer: (layerId: string) => void;
  setLayersBatch: (overrides: Record<string, boolean>) => void;
  setAdvisoryAltitudeOverride: (alt: number | null) => void;
  recalculateAdvisories: () => Promise<void>;
  fetchAltitudeTable: () => Promise<void>;
  refreshObservations: () => Promise<void>;
  setNotifyEmail: (notify: boolean) => void;
  sendEmail: () => Promise<void>;
  loadAltAdvisories: () => Promise<void>;
  computeAltAdvisories: () => Promise<void>;
  toggleAltView: () => void;
  updateFlightAutoRefresh: (autoRefresh: boolean, hour: number | null) => void;
  updateFlightPrivacy: (isPrivate: boolean) => void;
  setLayout: (layout: VizLayout) => void;
  setMapColorMetric: (metricId: string) => void;
  setMapWidthMetric: (metricId: string) => void;
  setMapAltitude: (altitudeFt: number | null) => void;
  setRouteGraphVisible: (visible: boolean) => void;
  setRouteGraphMetric: (axis: 'left' | 'right', metricId: string) => void;
  setCompareLayer: (layerId: string) => void;
  setCompareModel: (model: string, enabled: boolean) => void;
  initCompareModels: (models: string[]) => void;
  setVizTheme: (themeId: string) => void;
}

export const briefingStore = createStore<BriefingState>((set, get) => ({
  flight: null,
  packs: [],
  currentPack: null,
  snapshot: null,
  digest: null,
  routeAnalyses: null,
  routeAdvisories: null,
  altAdvisories: null,
  showingAlt: false,
  elevationProfile: null,
  freshness: null,
  freshnessLoading: false,
  selectedModel: 'gfs',
  selectedPointIndex: 0,
  displayMode: loadDisplayMode(),
  tierVisibility: loadTierVisibility(),
  vizSettings: loadVizSettings(),
  loading: false,
  refreshing: false,
  refreshStatus: null,
  refreshStage: null,
  refreshDetail: null,
  refreshProgress: 0,
  refreshElapsed: null,
  avgRefreshSeconds: null,
  notifyEmail: false,
  advisoryAltitudeOverride: null,
  altitudeTable: null,
  altitudeTableLoading: false,
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
      let elevationProfile: ElevationProfile | null = null;
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
      // Fetch route analyses, advisories, and elevation profile in parallel
      let routeAdvisories: RouteAdvisoriesManifest | null = null;
      const [raResult, epResult, advResult] = await Promise.allSettled([
        api.fetchRouteAnalyses(flight.id, timestamp),
        api.fetchElevationProfile(flight.id, timestamp),
        pack.has_advisories ? api.fetchRouteAdvisories(flight.id, timestamp) : Promise.reject('no advisories'),
      ]);
      if (raResult.status === 'fulfilled') routeAnalyses = raResult.value;
      if (epResult.status === 'fulfilled') elevationProfile = epResult.value;
      if (advResult.status === 'fulfilled') routeAdvisories = advResult.value;
      set({ currentPack: pack, snapshot, digest, routeAnalyses, routeAdvisories, elevationProfile, altAdvisories: null, showingAlt: false, selectedPointIndex: 0, loading: false });
      // Auto-load alt advisories if available
      if (pack.has_alt_advisories) {
        get().loadAltAdvisories();
      }
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

  refresh: async (asOfDate?: string) => {
    const flight = get().flight;
    if (!flight) return;
    set({ refreshing: true, refreshStatus: 'refreshing', refreshStage: null, refreshDetail: null, refreshProgress: 0, refreshElapsed: null, error: null });
    // Fetch average refresh time for the progress hint (best-effort, non-blocking)
    api.fetchRefreshStats().then(stats => {
      if (stats.avg_elapsed_seconds) set({ avgRefreshSeconds: stats.avg_elapsed_seconds });
    }).catch(() => { /* ignore */ });
    try {
      let elapsed: number | null = null;
      const notifyEmail = get().notifyEmail;
      const newPack = await api.refreshBriefingStream(flight.id, (event) => {
        if (event.type === 'progress') {
          set({
            refreshStatus: 'refreshing',
            refreshStage: event.label || event.stage || null,
            refreshDetail: event.detail || null,
            refreshProgress: event.progress || 0,
          });
        } else if (event.type === 'complete' && event.elapsed_seconds) {
          elapsed = event.elapsed_seconds;
        }
      }, false, asOfDate, notifyEmail);
      // If the server returned a data_status (fresh skip), update freshness
      if (newPack.data_status) {
        set({ freshness: newPack.data_status });
      }
      await get().loadPacks();
      await get().selectPack(newPack.fetch_timestamp);
      set({ refreshing: false, refreshStatus: null, refreshStage: null, refreshDetail: null, refreshProgress: 0, refreshElapsed: elapsed });
      // Re-check freshness after a real refresh
      if (!newPack.data_status) {
        get().checkFreshness();
      }
      // Clear elapsed message after 15 seconds
      if (elapsed) setTimeout(() => set({ refreshElapsed: null }), 15_000);
    } catch (err) {
      // If another refresh is already in progress, poll for its completion
      if (err instanceof RefreshStreamError && /already in progress/i.test(err.message)) {
        get().checkActiveRefresh();
        return;
      }
      set({ refreshing: false, refreshStatus: null, refreshStage: null, refreshDetail: null, refreshProgress: 0, refreshElapsed: null, error: `Refresh failed: ${err}` });
    }
  },

  forceRefresh: async () => {
    const flight = get().flight;
    if (!flight) return;
    set({ refreshing: true, refreshStatus: 'refreshing', refreshStage: null, refreshDetail: null, refreshProgress: 0, refreshElapsed: null, error: null });
    try {
      let elapsed: number | null = null;
      const newPack = await api.refreshBriefingStream(flight.id, (event) => {
        if (event.type === 'progress') {
          set({
            refreshStatus: 'refreshing',
            refreshStage: event.label || event.stage || null,
            refreshDetail: event.detail || null,
            refreshProgress: event.progress || 0,
          });
        } else if (event.type === 'complete' && event.elapsed_seconds) {
          elapsed = event.elapsed_seconds;
        }
      }, true);
      await get().loadPacks();
      await get().selectPack(newPack.fetch_timestamp);
      set({ refreshing: false, refreshStatus: null, refreshStage: null, refreshDetail: null, refreshProgress: 0, refreshElapsed: elapsed });
      get().checkFreshness();
      if (elapsed) setTimeout(() => set({ refreshElapsed: null }), 15_000);
    } catch (err) {
      if (err instanceof RefreshStreamError && /already in progress/i.test(err.message)) {
        get().checkActiveRefresh();
        return;
      }
      set({ refreshing: false, refreshStatus: null, refreshStage: null, refreshDetail: null, refreshProgress: 0, refreshElapsed: null, error: `Refresh failed: ${err}` });
    }
  },

  checkActiveRefresh: async () => {
    const flight = get().flight;
    if (!flight) return;
    try {
      const status = await api.fetchRefreshStatus(flight.id);
      if (!status.active) return;

      // A refresh is active — show its progress and poll until done
      set({
        refreshing: true,
        refreshStatus: (status.status as 'queued' | 'refreshing') ?? 'refreshing',
        refreshStage: status.label ?? status.stage ?? null,
        refreshDetail: status.detail ?? null,
        refreshProgress: 0,
        error: null,
      });

      const MAX_POLL_ATTEMPTS = 100; // ~5 minutes at 3s intervals
      const poll = async (attempt = 0): Promise<void> => {
        if (attempt >= MAX_POLL_ATTEMPTS) {
          set({ refreshing: false, refreshStatus: null, refreshStage: null, refreshDetail: null, refreshProgress: 0, error: 'Refresh timed out' });
          return;
        }
        await new Promise(r => setTimeout(r, 3000));
        const s = await api.fetchRefreshStatus(flight.id);
        if (!s.active) {
          // Done — reload packs and select latest
          set({ refreshing: false, refreshStatus: null, refreshStage: null, refreshDetail: null, refreshProgress: 0 });
          await get().loadPacks();
          await get().selectLatest();
          get().checkFreshness();
          return;
        }
        set({
          refreshStatus: (s.status as 'queued' | 'refreshing') ?? 'refreshing',
          refreshStage: s.label ?? s.stage ?? null,
          refreshDetail: s.detail ?? null,
        });
        return poll(attempt + 1);
      };
      await poll();
    } catch {
      // Non-critical — silently ignore
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

  setNotifyEmail: (notify: boolean) => {
    set({ notifyEmail: notify });
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

  toggleVizLayer: (layerId: string) => {
    const current = get().vizSettings;
    const enabled = { ...current.enabledLayers, [layerId]: !(current.enabledLayers[layerId] !== false) };
    const updated = { ...current, enabledLayers: enabled };
    set({ vizSettings: updated });
    saveVizSettings(updated);
  },

  setLayersBatch: (overrides: Record<string, boolean>) => {
    const current = get().vizSettings;
    const enabled = { ...current.enabledLayers, ...overrides };
    const updated = { ...current, enabledLayers: enabled };
    set({ vizSettings: updated });
    saveVizSettings(updated);
  },

  setAdvisoryAltitudeOverride: (alt: number | null) => {
    set({ advisoryAltitudeOverride: alt });
  },

  recalculateAdvisories: async () => {
    const { flight, currentPack, advisoryAltitudeOverride } = get();
    if (!flight || !currentPack) return;
    try {
      const result = await api.recalculateAdvisories(
        flight.id,
        currentPack.fetch_timestamp,
        advisoryAltitudeOverride ?? undefined,
      );
      set({ routeAdvisories: result });
    } catch (err) {
      set({ error: `Advisory recalculation failed: ${err}` });
    }
  },

  fetchAltitudeTable: async () => {
    const { flight, currentPack } = get();
    if (!flight || !currentPack) return;
    set({ altitudeTableLoading: true, altitudeTable: null });
    try {
      const result = await api.fetchAltitudeTable(
        flight.id,
        currentPack.fetch_timestamp,
      );
      set({ altitudeTable: result, altitudeTableLoading: false });
    } catch (err) {
      set({ altitudeTableLoading: false, error: `Altitude table failed: ${err}` });
    }
  },

  refreshObservations: async () => {
    const { flight, currentPack, snapshot } = get();
    if (!flight || !currentPack || !snapshot) return;
    try {
      const newObs = await api.refreshObservations(flight.id, currentPack.fetch_timestamp);
      set({ snapshot: { ...snapshot, route_observations: newObs } });
    } catch (err) {
      set({ error: `Observation refresh failed: ${err}` });
    }
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

  loadAltAdvisories: async () => {
    const { flight, currentPack } = get();
    if (!flight || !currentPack) return;
    try {
      const altAdv = await api.fetchAltAdvisories(flight.id, currentPack.fetch_timestamp);
      set({ altAdvisories: altAdv });
    } catch {
      // Non-critical — alt advisories may not exist
    }
  },

  computeAltAdvisories: async () => {
    const { flight, currentPack } = get();
    if (!flight || !currentPack) return;
    try {
      const altAdv = await api.computeAltAdvisories(flight.id, currentPack.fetch_timestamp);
      // Re-fetch pack meta to get updated alt_assessment fields
      const updatedPack = await api.fetchPack(flight.id, currentPack.fetch_timestamp);
      set({ altAdvisories: altAdv, currentPack: updatedPack });
    } catch (err) {
      set({ error: `Alt advisories computation failed: ${err}` });
    }
  },

  toggleAltView: () => {
    set({ showingAlt: !get().showingAlt });
  },

  updateFlightAutoRefresh: (autoRefresh: boolean, hour: number | null) => {
    const flight = get().flight;
    if (!flight) return;
    set({ flight: { ...flight, auto_refresh: autoRefresh, auto_refresh_hour: hour } });
  },

  updateFlightPrivacy: (isPrivate: boolean) => {
    const flight = get().flight;
    if (!flight) return;
    set({ flight: { ...flight, private: isPrivate } });
  },

  setLayout: (layout: VizLayout) => {
    const updated = { ...get().vizSettings, layout };
    set({ vizSettings: updated });
    saveVizSettings(updated);
  },

  setMapColorMetric: (metricId: string) => {
    const updated = { ...get().vizSettings, mapColorMetric: metricId };
    set({ vizSettings: updated });
    saveVizSettings(updated);
  },

  setMapWidthMetric: (metricId: string) => {
    const updated = { ...get().vizSettings, mapWidthMetric: metricId };
    set({ vizSettings: updated });
    saveVizSettings(updated);
  },

  setMapAltitude: (altitudeFt: number | null) => {
    const updated = { ...get().vizSettings, mapAltitudeFt: altitudeFt };
    set({ vizSettings: updated });
    saveVizSettings(updated);
  },

  setRouteGraphVisible: (visible: boolean) => {
    const updated = { ...get().vizSettings, routeGraphVisible: visible };
    set({ vizSettings: updated });
    saveVizSettings(updated);
  },

  setRouteGraphMetric: (axis: 'left' | 'right', metricId: string) => {
    const key = axis === 'left' ? 'routeGraphLeftMetric' : 'routeGraphRightMetric';
    const updated = { ...get().vizSettings, [key]: metricId };
    set({ vizSettings: updated });
    saveVizSettings(updated);
  },

  setCompareLayer: (layerId: string) => {
    const updated = { ...get().vizSettings, compareLayer: layerId };
    set({ vizSettings: updated });
    saveVizSettings(updated);
  },

  setCompareModel: (model: string, enabled: boolean) => {
    const current = get().vizSettings;
    const compareModels = { ...current.compareModels, [model]: enabled };
    const updated = { ...current, compareModels };
    set({ vizSettings: updated });
    saveVizSettings(updated);
  },

  initCompareModels: (models: string[]) => {
    const current = get().vizSettings;
    if (Object.keys(current.compareModels).length > 0) return;
    const compareModels: Record<string, boolean> = {};
    for (const m of models) compareModels[m] = true;
    const updated = { ...current, compareModels };
    set({ vizSettings: updated });
    saveVizSettings(updated);
  },

  setVizTheme: (themeId: string) => {
    if (themeId in THEMES) {
      setActiveTheme(themeId as ThemeId);
      const updated = { ...get().vizSettings, vizTheme: themeId };
      set({ vizSettings: updated });
      saveVizSettings(updated);
      window.dispatchEvent(new Event('theme-changed'));
    }
  },
}));

// Initialize cross-section theme from saved settings
{
  const saved = briefingStore.getState().vizSettings.vizTheme;
  if (saved && saved in THEMES) {
    setActiveTheme(saved as ThemeId);
  }
}
