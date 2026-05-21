/** Zustand vanilla store for the Briefing report page. */

import { createStore } from 'zustand/vanilla';
import type { DataStatus, ElevationProfile, FlightResponse, ForecastSnapshot, PackMeta, RouteAnalysesManifest, WeatherDigest } from './types';
import type { AltitudeTableResult, RouteAdvisoriesManifest } from '../types/advisories';
import type { RouteWindOverlay } from '../adapters/api-adapter';
import type { DisplayMode, Tier } from '../types/metrics';
import type { VizLayout, VizSettings } from '../visualization/types';
import { getTierDefaults } from '../helpers/metrics-helper';
import { getDefaultEnabled, getPreset } from '../visualization/cross-section/layer-registry';
import { setActiveTheme, type ThemeId, THEMES } from '../visualization/cross-section/theme';
import { RefreshStreamError } from '../adapters/api-adapter';
import * as api from '../adapters/api-adapter';
import { errorToMessage } from '../utils';

// --- localStorage persistence helpers ---

function loadDisplayMode(): DisplayMode {
  try {
    const v = localStorage.getItem('wb_displayMode');
    if (v === 'compact') return v;
    if (v === 'full' || v === 'annotated') return 'full';
  } catch { /* ignore */ }
  return 'compact';
}

function loadSelectedModel(): string {
  try {
    const v = localStorage.getItem('wb_selectedModel');
    if (v) return v;
  } catch { /* ignore */ }
  return 'gfs';
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
    compareBandMode: 'consensus-outline',
    cloudStyle: 'square',
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
  /** Per-route-point wind components at the advisoryAltitudeOverride.
   * null when no override is active (manifest values are correct). */
  windOverlay: RouteWindOverlay | null;
  showingAlt: boolean;
  elevationProfile: ElevationProfile | null;
  freshness: DataStatus | null;
  freshnessLoading: boolean;

  // UI state
  selectedModel: string;
  selectedPointIndex: number | null;
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
  /** True between the SSE `briefing_ready` and `complete` events: the visible
   * briefing is rendered but the LLM digest is still being generated. The UI
   * uses this to show a "Generating summary…" placeholder in the digest panel
   * instead of the default "Summary not available" copy. */
  digestPending: boolean;
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
  setCloudStyle: (style: 'natural' | 'soft' | 'square') => void;
  setAdvisoryAltitudeOverride: (alt: number | null) => void;
  recalculateAdvisories: () => Promise<void>;
  changeFlightProfile: (profileId: number) => Promise<void>;
  fetchAltitudeTable: () => Promise<void>;
  refreshObservations: () => Promise<void>;
  setNotifyEmail: (notify: boolean) => void;
  sendEmail: () => Promise<void>;
  loadAltAdvisories: () => Promise<void>;
  computeAltAdvisories: () => Promise<void>;
  toggleAltView: () => void;
  updateFlightAutoRefresh: (autoRefresh: boolean, hour: number | null) => void;
  updateFlightPrivacy: (isPrivate: boolean) => void;
  subscribe: () => Promise<void>;
  unsubscribe: () => Promise<void>;
  setLayout: (layout: VizLayout) => void;
  setMapColorMetric: (metricId: string) => void;
  setMapWidthMetric: (metricId: string) => void;
  setMapAltitude: (altitudeFt: number | null) => void;
  setRouteGraphVisible: (visible: boolean) => void;
  setRouteGraphMetric: (axis: 'left' | 'right', metricId: string) => void;
  setCompareLayer: (layerId: string) => void;
  setCompareModel: (model: string, enabled: boolean) => void;
  setCompareBandMode: (mode: import('../visualization/types').CompareBandMode) => void;
  initCompareModels: (models: string[]) => void;
  setVizTheme: (themeId: string) => void;
  setVizPreset: (presetId: string | null) => void;
}

/** Build the SSE event handler shared by `refresh` and `forceRefresh`.
 *
 * Three event types:
 * - `progress`: update stage label / detail / fraction.
 * - `briefing_ready`: provisional pack with `has_digest=false` is in the DB and
 *   the visible artifacts are on disk. Reload the pack list and select the new
 *   pack so the UI renders the briefing immediately, while the digest stage
 *   continues in the background. `digestPending=true` tells the synopsis
 *   renderer to show "Generating summary…" instead of "not available".
 * - `complete`: capture elapsed time. The post-stream code paths reload+reselect
 *   to pick up the final assessment + digest. Backward-compatible: if the
 *   server doesn't emit `briefing_ready` (older deploy), the store sees only
 *   `progress` + `complete` and behaves exactly as before.
 */
function makeRefreshEventHandler(
  set: (partial: Partial<BriefingState>) => void,
  get: () => BriefingState,
  tracker: { elapsed: number | null },
): (event: import('../adapters/api-adapter').RefreshStreamEvent) => void {
  return (event) => {
    if (event.type === 'progress') {
      set({
        refreshStatus: 'refreshing',
        refreshStage: event.label || event.stage || null,
        refreshDetail: event.detail || null,
        refreshProgress: event.progress || 0,
      });
    } else if (event.type === 'briefing_ready' && event.pack) {
      const ts = event.pack.fetch_timestamp;
      set({ digestPending: true });
      // Render the visible briefing while the digest stage continues.
      // selectPack fetches the pack + snapshot + advisories directly via its
      // timestamp, so we don't need a fresh /packs list here — the
      // post-stream code path runs `loadPacks()` once `complete` arrives,
      // which is when the dropdown actually needs the new entry.
      get().selectPack(ts).catch(() => {
        /* non-critical — final reload after `complete` will recover */
      });
    } else if (event.type === 'complete' && event.elapsed_seconds) {
      tracker.elapsed = event.elapsed_seconds;
    }
  };
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
  windOverlay: null,
  showingAlt: false,
  elevationProfile: null,
  freshness: null,
  freshnessLoading: false,
  selectedModel: loadSelectedModel(),
  selectedPointIndex: null,
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
  digestPending: false,
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

      // Reconcile selectedModel against this pack's available models.
      // Why: packs fetched with a non-default model set (e.g. ECMWF only) would
      // otherwise leave selectedModel on its stale default ('gfs'), producing empty
      // cross-section layers and 404s on the on-demand Skew-T endpoint.
      const available = routeAnalyses?.models ?? [];
      let selectedModel = get().selectedModel;
      if (available.length > 0 && !available.includes(selectedModel)) {
        selectedModel = available.includes('gfs') ? 'gfs'
                      : available.includes('ecmwf') ? 'ecmwf'
                      : available[0];
      }
      set({ currentPack: pack, snapshot, digest, routeAnalyses, routeAdvisories, elevationProfile, selectedModel, altAdvisories: null, windOverlay: null, showingAlt: false, selectedPointIndex: null, loading: false });
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
    set({ refreshing: true, refreshStatus: 'refreshing', refreshStage: null, refreshDetail: null, refreshProgress: 0, refreshElapsed: null, digestPending: false, error: null });
    // Fetch average refresh time for the progress hint (best-effort, non-blocking)
    api.fetchRefreshStats().then(stats => {
      if (stats.avg_elapsed_seconds) set({ avgRefreshSeconds: stats.avg_elapsed_seconds });
    }).catch(() => { /* ignore */ });
    try {
      const tracker: { elapsed: number | null } = { elapsed: null };
      const handleEvent = makeRefreshEventHandler(set, get, tracker);
      const notifyEmail = get().notifyEmail;
      const newPack = await api.refreshBriefingStream(
        flight.id, handleEvent, false, asOfDate, notifyEmail,
      );
      // If the server returned a data_status (fresh skip), update freshness
      if (newPack.data_status) {
        set({ freshness: newPack.data_status });
      }
      await get().loadPacks();
      await get().selectPack(newPack.fetch_timestamp);
      set({ refreshing: false, refreshStatus: null, refreshStage: null, refreshDetail: null, refreshProgress: 0, refreshElapsed: tracker.elapsed, digestPending: false });
      // Re-check freshness after a real refresh
      if (!newPack.data_status) {
        get().checkFreshness();
      }
      // Clear elapsed message after 15 seconds
      if (tracker.elapsed) setTimeout(() => set({ refreshElapsed: null }), 15_000);
    } catch (err) {
      // If another refresh is already in progress, poll for its completion
      if (err instanceof RefreshStreamError && /already in progress/i.test(err.message)) {
        get().checkActiveRefresh();
        return;
      }
      const msg = err instanceof Error ? err.message : String(err);
      set({ refreshing: false, refreshStatus: null, refreshStage: null, refreshDetail: null, refreshProgress: 0, refreshElapsed: null, digestPending: false, error: msg });
    }
  },

  forceRefresh: async () => {
    const flight = get().flight;
    if (!flight) return;
    set({ refreshing: true, refreshStatus: 'refreshing', refreshStage: null, refreshDetail: null, refreshProgress: 0, refreshElapsed: null, digestPending: false, error: null });
    try {
      const tracker: { elapsed: number | null } = { elapsed: null };
      const handleEvent = makeRefreshEventHandler(set, get, tracker);
      const newPack = await api.refreshBriefingStream(flight.id, handleEvent, true);
      await get().loadPacks();
      await get().selectPack(newPack.fetch_timestamp);
      set({ refreshing: false, refreshStatus: null, refreshStage: null, refreshDetail: null, refreshProgress: 0, refreshElapsed: tracker.elapsed, digestPending: false });
      get().checkFreshness();
      if (tracker.elapsed) setTimeout(() => set({ refreshElapsed: null }), 15_000);
    } catch (err) {
      if (err instanceof RefreshStreamError && /already in progress/i.test(err.message)) {
        get().checkActiveRefresh();
        return;
      }
      const msg = err instanceof Error ? err.message : String(err);
      set({ refreshing: false, refreshStatus: null, refreshStage: null, refreshDetail: null, refreshProgress: 0, refreshElapsed: null, digestPending: false, error: msg });
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
          set({ refreshing: false, refreshStatus: null, refreshStage: null, refreshDetail: null, refreshProgress: 0, digestPending: false, error: 'Refresh timed out' });
          return;
        }
        await new Promise(r => setTimeout(r, 3000));
        const s = await api.fetchRefreshStatus(flight.id);
        if (!s.active) {
          // Done — reload packs and select latest
          set({ refreshing: false, refreshStatus: null, refreshStage: null, refreshDetail: null, refreshProgress: 0, digestPending: false });
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
    try { localStorage.setItem('wb_selectedModel', model); } catch { /* ignore */ }
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

  setCloudStyle: (style: 'natural' | 'soft' | 'square') => {
    const current = get().vizSettings;
    if (current.cloudStyle === style) return;
    const updated = { ...current, cloudStyle: style };
    set({ vizSettings: updated });
    saveVizSettings(updated);
  },

  setAdvisoryAltitudeOverride: (alt: number | null) => {
    // Clearing the override resets the wind overlay too — manifest values
    // now match the effective altitude again.
    if (alt === null) {
      set({ advisoryAltitudeOverride: null, windOverlay: null });
    } else {
      set({ advisoryAltitudeOverride: alt });
    }
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
      set({ routeAdvisories: result.manifest, windOverlay: result.wind_overlay });
    } catch (err) {
      set({ error: `Advisory recalculation failed: ${err}` });
    }
  },

  changeFlightProfile: async (profileId: number) => {
    const { flight, currentPack, advisoryAltitudeOverride } = get();
    if (!flight || !currentPack) return;
    try {
      // Persist profile change on the flight
      await api.updateFlight(flight.id, { profile_id: profileId });
      const updatedFlight = await api.fetchFlight(flight.id);
      set({ flight: updatedFlight });
      // Recalculate advisories with the new profile's settings
      const result = await api.recalculateAdvisories(
        flight.id,
        currentPack.fetch_timestamp,
        advisoryAltitudeOverride ?? undefined,
      );
      set({ routeAdvisories: result.manifest, windOverlay: result.wind_overlay });
    } catch (err) {
      set({ error: `Profile change failed: ${err}` });
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
      const result = await api.refreshObservations(flight.id, currentPack.fetch_timestamp);
      set({
        snapshot: {
          ...snapshot,
          route_observations: result.observations,
          route_sigmets: result.sigmets,
        },
      });
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

  subscribe: async () => {
    const flight = get().flight;
    if (!flight) return;
    set({ loading: true, error: null });
    try {
      const updated = await api.subscribeAndRefetch(flight.id);
      set({ flight: updated, loading: false });
    } catch (err) {
      set({ loading: false, error: errorToMessage(err) });
    }
  },

  unsubscribe: async () => {
    const flight = get().flight;
    if (!flight) return;
    set({ loading: true, error: null });
    try {
      const updated = await api.unsubscribeAndRefetch(flight.id);
      set({ flight: updated, loading: false });
    } catch (err) {
      set({ loading: false, error: errorToMessage(err) });
    }
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

  setCompareBandMode: (mode) => {
    const updated = { ...get().vizSettings, compareBandMode: mode };
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

  setVizPreset: (presetId: string | null) => {
    const current = get().vizSettings;
    if (!presetId) {
      // Revert to default layers and standard theme
      const updated = { ...current, enabledLayers: getDefaultEnabled(), vizTheme: 'standard' };
      setActiveTheme('standard');
      set({ vizSettings: updated });
      saveVizSettings(updated);
      window.dispatchEvent(new Event('theme-changed'));
      return;
    }
    const preset = getPreset(presetId);
    if (!preset) return;
    // Apply preset: override theme + layer enabled state
    const themeId = preset.themeId as ThemeId;
    if (themeId in THEMES) {
      setActiveTheme(themeId);
    }
    const enabled = { ...current.enabledLayers, ...preset.enabledLayers };
    const updated = { ...current, enabledLayers: enabled, vizTheme: preset.themeId };
    set({ vizSettings: updated });
    saveVizSettings(updated);
    window.dispatchEvent(new Event('theme-changed'));
  },
}));

// Initialize cross-section theme from saved settings
{
  const saved = briefingStore.getState().vizSettings.vizTheme;
  if (saved && saved in THEMES) {
    setActiveTheme(saved as ThemeId);
  }
}
