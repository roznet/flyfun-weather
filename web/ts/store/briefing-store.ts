/** Zustand vanilla store for the Briefing report page. */

import { createStore } from 'zustand/vanilla';
import type { DataStatus, ElevationProfile, FlightResponse, ForecastSnapshot, PackMeta, RouteAnalysesManifest, WeatherDigest } from './types';
import type { AltitudeTableResult, RouteAdvisoriesManifest } from '../types/advisories';
import type { RouteFrontsManifest } from '../types/fronts';
import type { RouteWindOverlay, TimeOptionsResponse } from '../adapters/api-adapter';
import type { DisplayMode, Tier } from '../types/metrics';
import type { VizLayout, VizSettings } from '../visualization/types';
import { getTierDefaults } from '../helpers/metrics-helper';
import { getDefaultEnabled, getPreset } from '../visualization/cross-section/layer-registry';
import type { ResolvedView } from '../visualization/cross-section/advisory-presets';
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
    mapFrontsVisible: false,
    activePreset: null,
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
  /** Experimental Hewson front-detection artifact (#196). null unless the
   *  "Auto Front Detection" pref was on at generation time. Feeds the route-map
   *  + cross-section front overlays. */
  routeFronts: RouteFrontsManifest | null;
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
  /** Timing-scenario scan (Flexibility): status + result from the background
   * job, polled after pack load until the status is terminal. null when the
   * flight has Flexibility "none" (section hidden). */
  timeOptions: TimeOptionsResponse | null;

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
  /** Lever move (#259): set the override and index the cached altitude table
   *  for instant advisory statuses; the route-graph wind overlay still needs
   *  the server, so it's fetched debounced + stale-guarded (no out-of-order). */
  probeAltitude: (alt: number) => void;
  /** Re-anchor advisories to a new planned altitude via the cheap recalc path
   *  (used by the altitude-only stale-pack banner). Never saves the altitude. */
  reanchorAdvisories: (alt: number) => Promise<void>;
  changeFlightProfile: (profileId: number) => Promise<void>;
  fetchAltitudeTable: () => Promise<void>;
  refreshObservations: () => Promise<void>;
  /** Generate the AI summary on demand for a pack whose profile had AI off. */
  generateDigest: () => Promise<void>;
  setNotifyEmail: (notify: boolean) => void;
  sendEmail: () => Promise<void>;
  loadAltAdvisories: () => Promise<void>;
  computeAltAdvisories: () => Promise<void>;
  toggleAltView: () => void;
  /** Poll the timing-scenario scan for the current pack (backoff, stops on
   * terminal status or pack change). Safe to call unconditionally. */
  loadTimeOptions: () => Promise<void>;
  /** Queue the multi-model check of one provisional candidate (slice 3);
   * the result arrives via the loadTimeOptions poll. */
  confirmTimeOption: (departureTime: string) => Promise<void>;
  /** "Set as alternate time": pin a discovered scenario as the flight's
   * alternate — full advisory detail then flows through the existing
   * planned↔alt view once the re-queued scan persists the alt artifacts. */
  setScenarioAsAlternate: (departureTime: string) => Promise<void>;
  updateFlightAutoRefresh: (autoRefresh: boolean, hour: number | null) => void;
  updateFlightPrivacy: (isPrivate: boolean) => void;
  subscribe: () => Promise<void>;
  unsubscribe: () => Promise<void>;
  setLayout: (layout: VizLayout) => void;
  setMapColorMetric: (metricId: string) => void;
  setMapWidthMetric: (metricId: string) => void;
  setMapAltitude: (altitudeFt: number | null) => void;
  setMapFrontsVisible: (visible: boolean) => void;
  setRouteGraphVisible: (visible: boolean) => void;
  setRouteGraphMetric: (axis: 'left' | 'right', metricId: string) => void;
  setCompareLayer: (layerId: string) => void;
  setCompareModel: (model: string, enabled: boolean) => void;
  setCompareBandMode: (mode: import('../visualization/types').CompareBandMode) => void;
  initCompareModels: (models: string[]) => void;
  setVizTheme: (themeId: string) => void;
  setVizPreset: (presetId: string | null) => void;
  /** Apply a pre-resolved advisory preset view (issue #219). The caller
   *  resolves the preset → concrete layer IDs where preferredMethods is in
   *  scope, and hands this a {@link ResolvedView}. Does not touch the theme. */
  applyAdvisoryPreset: (presetId: string, view: ResolvedView) => void;
  /** Drop the active-preset label to "Custom" after a user-initiated Skew-T
   *  edit (overlay toggle / side-panel change). No-op when already Custom. */
  markVizCustom: () => void;
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

/**
 * True when a refresh error is a dropped/truncated SSE stream rather than a real
 * failure. The fetch-based refresh stream is held open for the whole ~2min
 * pipeline; Safari and reverse proxies reap idle POST streams, which throws a
 * generic `TypeError` ("Load failed" / "Failed to fetch"), or the stream ends
 * cleanly without a `complete` event. In both cases the refresh keeps running
 * server-side, so we recover by polling instead of showing the error.
 */
function isStreamDrop(err: unknown): boolean {
  if (err instanceof RefreshStreamError) return false;
  if (err instanceof TypeError) return true;
  return err instanceof Error && err.message === 'Refresh stream ended without completion';
}

// Altitude-lever wind-overlay debounce + stale-response guard (#259). Module
// scope (not store state) — purely transient request bookkeeping.
let _windOverlayTimer: number | null = null;
let _windOverlaySeq = 0;
// Timing-scenario poll backoff (3s → ×1.5 → cap 15s); reset on pack change.
let timeOptionsPollDelay = 3_000;
// Consecutive transient poll failures; only give up after a few (404 is the
// sole immediate-terminal answer). Reset on any successful poll / pack change.
let timeOptionsErrorStreak = 0;

export const briefingStore = createStore<BriefingState>((set, get) => ({
  flight: null,
  packs: [],
  currentPack: null,
  snapshot: null,
  digest: null,
  routeAnalyses: null,
  routeAdvisories: null,
  routeFronts: null,
  altAdvisories: null,
  timeOptions: null,
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
    // Cancel any in-flight altitude-probe wind-overlay request and bump the
    // sequence so a slow response from the previous pack can't apply to this
    // one (the per-probe guard alone misses the release-at-default case where
    // override is null on both packs). #259 review follow-up.
    _windOverlaySeq++;
    if (_windOverlayTimer !== null) {
      clearTimeout(_windOverlayTimer);
      _windOverlayTimer = null;
    }
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
      // Fetch route analyses, advisories, elevation, and fronts in parallel.
      // route-fronts is experimental + frequently absent (pref off) — fetch it
      // non-blocking and treat any rejection as "no fronts".
      let routeAdvisories: RouteAdvisoriesManifest | null = null;
      let routeFronts: RouteFrontsManifest | null = null;
      // Precomputed altitude table (#259) — cheap, best-effort. Absent on old
      // packs; the lever then falls back to the on-demand sweep endpoint.
      let altitudeTable: AltitudeTableResult | null = null;
      const [raResult, epResult, advResult, frResult, atResult] = await Promise.allSettled([
        api.fetchRouteAnalyses(flight.id, timestamp),
        api.fetchElevationProfile(flight.id, timestamp),
        pack.has_advisories ? api.fetchRouteAdvisories(flight.id, timestamp) : Promise.reject('no advisories'),
        api.fetchRouteFronts(flight.id, timestamp),
        pack.has_advisories ? api.fetchAltitudeTableCached(flight.id, timestamp) : Promise.reject('no advisories'),
      ]);
      if (raResult.status === 'fulfilled') routeAnalyses = raResult.value;
      if (epResult.status === 'fulfilled') elevationProfile = epResult.value;
      if (advResult.status === 'fulfilled') routeAdvisories = advResult.value;
      if (frResult.status === 'fulfilled') routeFronts = frResult.value;
      if (atResult.status === 'fulfilled') altitudeTable = atResult.value;

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
      set({ currentPack: pack, snapshot, digest, routeAnalyses, routeAdvisories, routeFronts, elevationProfile, altitudeTable, selectedModel, advisoryAltitudeOverride: null, altAdvisories: null, windOverlay: null, showingAlt: false, selectedPointIndex: null, timeOptions: null, loading: false });
      // Auto-load alt advisories if available
      if (pack.has_alt_advisories) {
        get().loadAltAdvisories();
      }
      // Timing scenarios (Flexibility): kick off the status poll. The
      // endpoint lazy-schedules the scan if Flexibility was enabled after
      // this pack was generated.
      timeOptionsPollDelay = 3_000;
      timeOptionsErrorStreak = 0;
      if (get().flight?.flexibility && get().flight!.flexibility !== 'none') {
        void get().loadTimeOptions();
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
      // A dropped/truncated stream isn't a real failure — the refresh keeps
      // running server-side, so poll for it instead of surfacing "Load failed".
      if (isStreamDrop(err)) {
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
      if (isStreamDrop(err)) {
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
      if (!status.active) {
        // Nothing running. If we still believed a refresh was in flight (e.g.
        // the SSE stream dropped near the end), it finished while we were
        // disconnected — reconcile to the latest pack and clear the spinner.
        if (get().refreshing) {
          set({ refreshing: false, refreshStatus: null, refreshStage: null, refreshDetail: null, refreshProgress: 0, digestPending: false });
          await get().loadPacks();
          await get().selectLatest();
          get().checkFreshness();
        }
        return;
      }

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
    // User-initiated toggle → the view no longer matches any preset, so the
    // dropdown reflects "Custom". (Programmatic batch updates use
    // setLayersBatch, which deliberately preserves activePreset.)
    const current = get().vizSettings;
    const enabled = { ...current.enabledLayers, [layerId]: !(current.enabledLayers[layerId] !== false) };
    const updated = { ...current, enabledLayers: enabled, activePreset: null };
    set({ vizSettings: updated });
    saveVizSettings(updated);
  },

  setLayersBatch: (overrides: Record<string, boolean>) => {
    // Programmatic batch (e.g. compact-mode enforcement). Intentionally leaves
    // activePreset untouched — this is not a user toggle.
    const current = get().vizSettings;
    const enabled = { ...current.enabledLayers, ...overrides };
    const updated = { ...current, enabledLayers: enabled };
    set({ vizSettings: updated });
    saveVizSettings(updated);
  },

  markVizCustom: () => {
    // A user-initiated Skew-T edit (overlay toggle / side-panel change) means the
    // view no longer matches the applied preset → dropdown reflects "Custom".
    // The Skew-T renderer owns its own overlay/var localStorage, so we only need
    // to drop the preset label here (no enabledLayers change).
    const current = get().vizSettings;
    if (current.activePreset == null) return;
    const updated = { ...current, activePreset: null };
    set({ vizSettings: updated });
    saveVizSettings(updated);
  },

  setCloudStyle: (style: 'natural' | 'soft' | 'square') => {
    const current = get().vizSettings;
    if (current.cloudStyle === style) return;
    // Changing the cloud style is a user-initiated view change → Custom.
    const updated = { ...current, cloudStyle: style, activePreset: null };
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

  probeAltitude: (alt: number) => {
    const { flight, currentPack } = get();
    if (!flight || !currentPack) return;
    // Anchor = the lever's home (the flight's cruise altitude); matches
    // getAltitudeOverrideConfig.defaultAlt so the null/non-null override
    // decision is consistent across drag and release.
    const anchor = flight.cruise_altitude_ft;
    set({ advisoryAltitudeOverride: alt === anchor ? null : alt });

    // The advisory statuses come from the cached table (overlaid client-side in
    // getEffectiveAdvisories — instant, no race). Only the route-graph wind
    // overlay needs the server (it reparses cross_section.json), so debounce it
    // and tag each request so a slow earlier response can't clobber a newer one.
    // We deliberately apply ONLY windOverlay — routeAdvisories stays the pack
    // baseline so resetting the lever to anchor restores the planned statuses
    // instantly (no stale-probe flash during the debounce window).
    if (_windOverlayTimer !== null) clearTimeout(_windOverlayTimer);
    const seq = ++_windOverlaySeq;
    _windOverlayTimer = window.setTimeout(() => {
      void api.recalculateAdvisories(flight.id, currentPack.fetch_timestamp, alt)
        .then(result => {
          // Drop stale responses: only apply if this is still the latest probe
          // AND the lever hasn't moved away from the altitude we requested.
          if (seq !== _windOverlaySeq) return;
          if ((get().advisoryAltitudeOverride ?? anchor) !== alt) return;
          set({ windOverlay: result.wind_overlay });
        })
        .catch(() => { /* overlay-only failure — cards already updated from table */ });
    }, 300);
  },

  reanchorAdvisories: async (alt: number) => {
    // Altitude-only stale-pack path: re-evaluate at the new planned altitude via
    // the cheap recalc endpoint (no full pipeline re-fetch). The override is set
    // so the displayed cards + cross-section line follow the new altitude; the
    // flight's saved cruise_altitude_ft is never written from here.
    const { flight, currentPack } = get();
    if (!flight || !currentPack) return;
    const packTimestamp = currentPack.fetch_timestamp;
    // Cancel any in-flight probe wind-overlay request + bump the sequence so a
    // slow probe response at a different altitude can't land on top of the
    // reanchored wind overlay (mirrors selectPack).
    if (_windOverlayTimer !== null) {
      clearTimeout(_windOverlayTimer);
      _windOverlayTimer = null;
    }
    _windOverlaySeq++;
    set({ advisoryAltitudeOverride: alt });
    try {
      const result = await api.recalculateAdvisories(flight.id, packTimestamp, alt);
      // Drop the response if the user switched packs while it was in flight,
      // otherwise we'd write a stale manifest into the new pack's state.
      if (get().currentPack?.fetch_timestamp !== packTimestamp) return;
      // routeAdvisories now reflects `alt`; the override stays set so the
      // cross-section cruise line follows, and getEffectiveAdvisories skips the
      // table overlay once base.cruise_altitude_ft === the override (no double
      // overlay — see briefing-main).
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
      // Only overwrite SIGMETs when the server actually returned them; a null
      // means the SIGMET fetch failed server-side — keep the existing ones
      // rather than silently blanking the section.
      set({
        snapshot: {
          ...snapshot,
          route_observations: result.observations,
          ...(result.sigmets != null ? { route_sigmets: result.sigmets } : {}),
          last_refresh_delta: result.delta ?? null,
        },
      });
    } catch (err) {
      set({ error: `Observation refresh failed: ${err}` });
    }
  },

  generateDigest: async () => {
    const { flight, currentPack } = get();
    if (!flight || !currentPack) return;
    // Reuse digestPending so the synopsis renderer shows "Generating summary…"
    // while the (paid) LLM call runs.
    set({ digestPending: true, error: null });
    try {
      await api.generateDigest(flight.id, currentPack.fetch_timestamp);
      // Reload the pack + digest JSON via the normal path so has_digest, the
      // assessment, and the digest body all refresh together.
      await get().selectPack(currentPack.fetch_timestamp);
    } catch (err) {
      set({ error: `Failed to generate summary: ${err}` });
    } finally {
      set({ digestPending: false });
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

  loadTimeOptions: async () => {
    const { flight, currentPack } = get();
    if (!flight || !currentPack || flight.flexibility === 'none') return;
    const packTs = currentPack.fetch_timestamp;
    try {
      const resp = await api.fetchTimeOptions(flight.id, packTs);
      // Stale-guard: the user may have switched packs while we were fetching.
      if (get().currentPack?.fetch_timestamp !== packTs) return;
      set({ timeOptions: resp });
      timeOptionsErrorStreak = 0;

      const status = resp.status?.status;
      const confirmPending = (resp.scan?.candidates ?? []).some((c) => c.confirm_pending);
      if (status === 'pending' || status === 'running' || confirmPending || (!status && !resp.scan)) {
        // Background job still working — poll with a gentle backoff. The
        // refresh SSE stream is already closed by the time the scan runs, so
        // polling is the delivery channel (see timing-scenario-plan.md).
        timeOptionsPollDelay = Math.min(timeOptionsPollDelay * 1.5, 15_000);
        window.setTimeout(() => {
          if (get().currentPack?.fetch_timestamp === packTs) {
            void get().loadTimeOptions();
          }
        }, timeOptionsPollDelay);
        return;
      }
      timeOptionsPollDelay = 3_000;
      // The scan's pinned alternate row also (re)writes the legacy alt
      // artifact + pack fields — pick them up when we don't have them yet.
      if (status === 'done' && !get().altAdvisories) {
        void get().loadAltAdvisories();
      }
    } catch (err) {
      if (get().currentPack?.fetch_timestamp !== packTs) return;
      // 404 is a real answer (flexibility none / legacy pack) — hide the
      // section. Anything else (network blip, 5xx) is transient: keep the
      // poll cadence alive for a few retries so a single failed request
      // can't make a mid-scan "Scenarios running…" silently vanish.
      const msg = err instanceof Error ? err.message : String(err);
      if (msg.includes('API 404')) {
        set({ timeOptions: null });
        return;
      }
      timeOptionsErrorStreak += 1;
      if (timeOptionsErrorStreak <= 3) {
        timeOptionsPollDelay = Math.min(timeOptionsPollDelay * 1.5, 15_000);
        window.setTimeout(() => {
          if (get().currentPack?.fetch_timestamp === packTs) {
            void get().loadTimeOptions();
          }
        }, timeOptionsPollDelay);
      } else {
        set({ timeOptions: null });
      }
    }
  },

  setScenarioAsAlternate: async (departureTime: string) => {
    const { flight, currentPack } = get();
    if (!flight || !currentPack) return;
    try {
      const updated = await api.updateFlight(flight.id, {
        alt_departure_time: departureTime,
      });
      set({ flight: updated });
      // Re-queue the scan: the changed alternate invalidates the reuse check,
      // so the pinned row re-grades and route_advisories_alt.json persists —
      // full per-advisory detail then appears in the planned↔alt view.
      await api.rescanTimeOptions(flight.id, currentPack.fetch_timestamp);
      timeOptionsPollDelay = 3_000;
      timeOptionsErrorStreak = 0;
      window.setTimeout(() => void get().loadTimeOptions(), 2_000);
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      set({ error: msg.replace(/^API \d+:\s*/, '') });
    }
  },

  confirmTimeOption: async (departureTime: string) => {
    const { flight, currentPack, timeOptions } = get();
    if (!flight || !currentPack) return;
    // One confirm at a time per pack (mirrors the server's 429 guard).
    if (timeOptions?.scan?.candidates.some((c) => c.confirm_pending)) return;
    try {
      await api.confirmTimeOption(flight.id, currentPack.fetch_timestamp, departureTime);
      // Optimistically flag the candidate so the UI shows "checking…"
      // immediately; the poll takes over from the server's copy.
      const to = get().timeOptions;
      if (to?.scan) {
        const scan = {
          ...to.scan,
          candidates: to.scan.candidates.map((c) =>
            c.departure_time === departureTime ? { ...c, confirm_pending: true } : c,
          ),
        };
        set({ timeOptions: { ...to, scan } });
      }
      timeOptionsPollDelay = 3_000;
      window.setTimeout(() => void get().loadTimeOptions(), 3_000);
    } catch (err) {
      set({ error: `Confirm failed: ${err}` });
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

  setMapFrontsVisible: (visible: boolean) => {
    const updated = { ...get().vizSettings, mapFrontsVisible: visible };
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
    // Reflection-label model: applying a preset sets activePreset so the
    // dropdown sticks on it; selecting "Custom" (null) is a no-op label for the
    // dirty state — it only clears activePreset and leaves layers/theme as-is
    // (no factory reset).
    const current = get().vizSettings;
    if (!presetId) {
      const updated = { ...current, activePreset: null };
      set({ vizSettings: updated });
      saveVizSettings(updated);
      return;
    }
    const preset = getPreset(presetId);
    if (!preset) return;
    // Apply preset: override theme + layer enabled state, and record it.
    const themeId = preset.themeId as ThemeId;
    if (themeId in THEMES) {
      setActiveTheme(themeId);
    }
    const enabled = { ...current.enabledLayers, ...preset.enabledLayers };
    const updated = { ...current, enabledLayers: enabled, vizTheme: preset.themeId, activePreset: presetId };
    set({ vizSettings: updated });
    saveVizSettings(updated);
    window.dispatchEvent(new Event('theme-changed'));
  },

  applyAdvisoryPreset: (presetId: string, view: ResolvedView) => {
    // Apply a PRE-RESOLVED advisory view (method resolution already done by the
    // caller, where preferredMethods is in scope). Dispatches over present
    // directives only; does NOT touch the cross-section theme. New effect types
    // slot in as additional `if (view.X)` branches — existing presets and call
    // sites untouched.
    const cur = get().vizSettings;
    const next: VizSettings = { ...cur, activePreset: presetId };
    if (view.enabledLayers) next.enabledLayers = { ...cur.enabledLayers, ...view.enabledLayers };
    // Route-graph metrics are set, but routeGraphVisible is intentionally left
    // as the user had it — applying a preset shouldn't pop open a graph the user
    // collapsed (mirrors the chip's "don't disrupt the user's layout" stance).
    if (view.routeGraph?.left) next.routeGraphLeftMetric = view.routeGraph.left;
    if (view.routeGraph?.right) next.routeGraphRightMetric = view.routeGraph.right;
    if (view.map?.metric) next.mapColorMetric = view.map.metric;
    if (view.map && 'altitudeFt' in view.map) next.mapAltitudeFt = view.map.altitudeFt ?? null;
    // Skew-T directives (#308): the resolver hands a full clean-slate overlay
    // map + the primary side-panel variable. briefing-main pushes these into the
    // live SkewTRenderer when activePreset changes; storing them here is what
    // makes the lens survive reload and lets a deep-link drive the Skew-T.
    if (view.skewtOverlays !== undefined) next.skewtOverlays = view.skewtOverlays;
    if (view.skewtSidePanel !== undefined) next.skewtPrimaryVar = view.skewtSidePanel;
    // future directives: add a branch here, nothing else changes
    set({ vizSettings: next });
    saveVizSettings(next);
    window.dispatchEvent(new Event('theme-changed')); // existing re-render trigger
  },
}));

// Initialize cross-section theme from saved settings
{
  const saved = briefingStore.getState().vizSettings.vizTheme;
  if (saved && saved in THEMES) {
    setActiveTheme(saved as ThemeId);
  }
}
