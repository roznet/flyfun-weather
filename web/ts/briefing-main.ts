/** Briefing page entry point — wires store, UI manager, and event handlers. */

import { fetchCurrentUser } from './adapters/auth-adapter';
import { briefingStore, type BriefingState } from './store/briefing-store';
import * as api from './adapters/api-adapter';
import * as ui from './managers/briefing-ui';
import { fetchPirepsByFlight } from './adapters/pirep-adapter';
import { renderPirepList } from './managers/pirep-ui';
import { renderAdvisories, renderAltitudeTablePopup, setLiveAdvisoryCatalog, advisoryName, type AltitudeOverrideConfig, type AltTimeToggleConfig, type ProfileSelectorConfig } from './managers/advisories-ui';
import { renderTimeOptions } from './managers/time-options-ui';
import { overlayAltitudeStatuses } from './helpers/altitude-diff';
import { fetchProfiles, type ProfileResponse } from './adapters/profiles-adapter';
import { fetchAdvisoryCatalog } from './adapters/preferences-adapter';
import type { DisplayMode } from './types/metrics';
import { copyFlightShareLink, redirectToLogin, renderUserInfo, initModelCatalog, isFlightPast, formatDepartureTime, escapeHtml } from './utils';
import { pressureToAltitudeFt } from './utils/atmo';
import { SKEWT_OVERLAYS } from './visualization/skewt/overlay-bands';
import { getVariableById } from './visualization/skewt/variable-panel';
import { getMetric, renderCompactThresholdStrip } from './helpers/metrics-helper';
import { initInfoPopup, showMetricInfo, showPopupContent } from './components/info-popup';
import { CrossSectionRenderer } from './visualization/cross-section/renderer';
import { extractVizData, getUnavailableLayers } from './visualization/data-extract';
import { getAllLayers, getCompactLayerOverrides } from './visualization/cross-section/layer-registry';
import {
  isAdvisoryPreset,
  getAdvisoryPreset,
  getPresetForAdvisory,
  resolveAdvisoryPreset,
  advisoryPresetInterpretation,
} from './visualization/cross-section/advisory-presets';
import { applyNwpFallback, getSubstitutedLayers } from './visualization/cross-section/nwp-fallback';
import { renderVizControls, renderRouteGraphControls, renderMapControls, renderCompareControls } from './visualization/controls/panel';
import { attachInteraction, type InteractionHandle } from './visualization/cross-section/interaction';
import { CompareSectionRenderer, type CompareModelData } from './visualization/cross-section/compare-renderer';
import { attachCompareInteraction, type CompareInteractionHandle } from './visualization/cross-section/compare-interaction';
import { getComparableLayer } from './visualization/cross-section/compare-layers';
import { RouteGraphRenderer } from './visualization/route-graph/renderer';
import { getMetricById, METRIC_NONE } from './visualization/route-graph/metrics';
import { attachRouteGraphInteraction, type RouteGraphInteractionHandle } from './visualization/route-graph/interaction';
import { RouteMapRenderer, type MapFrontLine } from './visualization/route-map/renderer';
import { fetchHewsonFronts } from './adapters/hewson-map-adapter';
import type { RouteFrontsManifest } from './types/fronts';
import { getMapMetricById, MAP_METRIC_NONE } from './visualization/route-map/metrics';
import { attachMapInteraction, type MapInteractionHandle } from './visualization/route-map/interaction';
import { renderMapLegend } from './visualization/route-map/legend';
import { renderAltitudeSlider } from './visualization/route-map/altitude-slider';
import { initTheme } from './theme';
import { setUnitsPreference, setFlightRegion, regionFromIcaos } from './units';
import { track, trackOncePerBriefing, setBriefingContext, EVENTS } from './analytics/track';
import { initI18n, t } from './i18n/i18n';
import { SkewTRenderer } from './visualization/skewt/renderer';
import { renderSkewtOverlayControls, renderSkewtCompareControls } from './visualization/skewt/overlay-controls';
import { attachSkewTInteraction, attachSkewTCompareInteraction, type SkewTInteractionHandle, type SkewTCompareInteractionHandle } from './visualization/skewt/interaction';
import { SkewTCompareRenderer, type CompareModelDataset as SkewtCompareModelDataset } from './visualization/skewt/compare-renderer';
import { getActiveTheme } from './visualization/cross-section/theme';
import { startBriefingTour, maybeAutoStartBriefingTour } from './tour/briefing-tour';
import { maybeOfferTour } from './tour/tour-offer';
import { initBriefingLayout } from './managers/sidebar-layout';


// --- Cross-section config snapshot (analytics, #232) -----------------------
// Build the ``xsection.viewed`` snapshot props from current store state. Read
// at the briefing.opened lifecycle point (after vizSettings hydrated from
// localStorage + preset resolved) so it reflects what's actually on screen,
// not defaults. All values are low-cardinality enums/ids/bools; ``layers`` is
// the bounded set of enabled layer ids. Keep keys in sync with
// ``XSECTION_SCALAR_DIMENSIONS`` / ``XSECTION_SET_DIMENSIONS`` in events.py.
function buildXsectionSnapshotProps(
  s: BriefingState,
): Record<string, string | number | boolean | string[]> {
  const v = s.vizSettings;
  // A layer is enabled unless explicitly set to ``false`` (mirrors the store's
  // own toggle semantics in toggleLayer).
  const layers = Object.keys(v.enabledLayers).filter(
    (id) => v.enabledLayers[id] !== false,
  );
  return {
    theme: v.vizTheme ?? 'standard',
    preset: v.activePreset ?? 'custom',
    layout: v.layout,
    cloud_style: v.cloudStyle ?? 'square',
    display_mode: s.displayMode,
    model: s.selectedModel,
    route_graph_visible: v.routeGraphVisible,
    map_fronts_visible: v.mapFrontsVisible ?? false,
    route_graph_left_metric: v.routeGraphLeftMetric,
    route_graph_right_metric: v.routeGraphRightMetric,
    map_color_metric: v.mapColorMetric,
    map_width_metric: v.mapWidthMetric,
    layers,
  };
}


// --- Route-map gated front lines (experimental #196) -----------------------
// The 2-D TFP=0 front axes for the *selected* model, drawn on the route map when
// the fronts layer is on. Reuses the same precomputed Hewson snapshot + the same
// FrontGateConfig the advisory used (recorded in route_fronts.json), so the line
// and the advisory grade share a gate. Drawn across ALL stored levels (so a low
// warm front at 850/925 gets a line, not just the mid cold front at the primary
// level) and clipped to the route corridor by the renderer. Switching the model
// re-fetches.
let lastFrontLinesKey = '';

interface FrontLevelSpec { level: number; hour: number; }

/** Resolve the per-level fetch plan for one model from the manifest, or null if
 *  it can't pin a snapshot. One entry per stored level (each carries its own
 *  forecast hour). */
function frontLineSpec(
  routeFronts: RouteFrontsManifest | null,
  model: string,
): { init: string; gate: string; levels: FrontLevelSpec[] } | null {
  if (!routeFronts) return null;
  const init = routeFronts.snapshot_inits?.[model];
  if (!init) return null;
  const analyses = routeFronts.per_model?.[model] ?? [];
  if (analyses.length === 0) return null;
  const gate = typeof routeFronts.gate_config?.name === 'string'
    ? routeFronts.gate_config.name : 'default';
  const levels = analyses.map(a => ({ level: a.level_hPa, hour: Math.round(a.hour) }));
  return { init, gate, levels };
}

/** Fetch + draw the selected model's gated front lines across all stored levels
 *  (cached by snapshot slice so unrelated re-renders don't refetch). Clears
 *  stale lines on model switch. */
function updateMapFrontLines(
  routeFronts: RouteFrontsManifest | null,
  model: string,
  visible: boolean,
  renderer: RouteMapRenderer,
): void {
  // Layer off: renderer.showFronts already suppresses drawing; keep any cached
  // lines so re-enabling is instant (no refetch).
  if (!visible) return;
  const spec = frontLineSpec(routeFronts, model);
  if (!spec) {
    if (lastFrontLinesKey !== '') {
      lastFrontLinesKey = '';
      renderer.setFrontLines(null);
      renderer.refreshFronts();
    }
    return;
  }
  const key = `${model}|${spec.init}|${spec.gate}|`
    + spec.levels.map(l => `${l.level}@${l.hour}`).join(',');
  if (key === lastFrontLinesKey) return;  // already fetched/drawn for this slice
  lastFrontLinesKey = key;
  renderer.setFrontLines(null);
  renderer.refreshFronts();  // drop the previous model's lines immediately
  // One request per level; tag each axis with its level for altitude styling.
  Promise.all(spec.levels.map(l =>
    fetchHewsonFronts({ model, init: spec.init, level: l.level, hour: l.hour, gate: spec.gate, minLengthKm: 150 })
      .then(resp => resp.fronts.map((f): MapFrontLine => ({ ...f, level_hPa: l.level })))
      .catch(() => [] as MapFrontLine[]),
  )).then((perLevel) => {
    if (lastFrontLinesKey !== key) return;  // superseded by a newer selection
    const all = perLevel.flat();
    renderer.setFrontLines(all.length ? all : null);
    renderer.refreshFronts();
  });
}


async function loadFlightPireps(flightId: string): Promise<void> {
  const wrapper = document.getElementById('pireps-wrapper');
  const section = document.getElementById('pireps-section');
  if (!wrapper || !section) return;

  try {
    const resp = await fetchPirepsByFlight(flightId);
    if (resp.items.length > 0) {
      wrapper.style.display = '';
      renderPirepList(section, resp.items);
    } else {
      wrapper.style.display = 'none';
    }
  } catch {
    // Permission denied or error — hide section silently
    wrapper.style.display = 'none';
  }
}

async function init(): Promise<void> {
  await initI18n();
  // Auth check — redirect to login if not authenticated
  const user = await fetchCurrentUser();
  if (!user) {
    redirectToLogin();
    return;
  }
  initTheme();
  setUnitsPreference(user.units_region);
  renderUserInfo(user, 'briefing');

  // Load model catalog + preferred methods (non-blocking)
  let preferredMethods: Record<string, string> = {};
  import('./adapters/preferences-adapter').then(({ fetchModelCatalog, fetchPreferences }) => {
    fetchModelCatalog().then(initModelCatalog).catch(() => {});
    fetchPreferences()
      .then((prefs) => {
        preferredMethods = { clouds: prefs.cloud_method, icing: prefs.icing_method, convection: prefs.convective_method };
      })
      .catch(() => {})
      .finally(() => {
        // Reconcile compact-mode layers once prefs have settled. Covers two paths:
        // (a) user toggled compact before the prefs fetch resolved, leaving
        //     non-preferred layers from full mode silently rendering;
        // (b) page booted directly into compact (default / persisted) with stale
        //     extras in localStorage that the panel can't expose to toggle off.
        if (store.getState().displayMode === 'compact') {
          store.getState().setLayersBatch(getCompactLayerOverrides(preferredMethods));
        } else {
          renderVisualization(store.getState());
        }
      });
  });

  // Initialize metric info popup
  initInfoPopup();
  initSkewtToggle();
  initSkewtViewTracking();
  document.addEventListener('click', (e) => {
    const btn = (e.target as HTMLElement).closest('.metric-info-btn') as HTMLElement | null;
    // advisory-info-btn and advisory-view-btn reuse .metric-info-btn styling but
    // have their own delegated handlers (in advisories-ui.ts) — skip them here.
    if (btn && !btn.classList.contains('advisory-info-btn') && !btn.classList.contains('advisory-view-btn')) {
      e.preventDefault();
      showMetricInfo(btn.dataset.metric!, btn.dataset.value);
    }
  });

  const store = briefingStore;

  // --- Preset wiring (#219) ---
  // Generalized preset dropdown handler: advisory presets are method-resolved
  // here (where `preferredMethods` is in scope) and applied via
  // applyAdvisoryPreset; GRAMET / Custom fall through to setVizPreset.
  function handlePresetChange(presetId: string | null): void {
    if (presetId && isAdvisoryPreset(presetId)) {
      const preset = getAdvisoryPreset(presetId);
      if (preset) {
        store.getState().applyAdvisoryPreset(presetId, resolveAdvisoryPreset(preset, preferredMethods));
        return;
      }
    }
    store.getState().setVizPreset(presetId);
  }

  // Advisory-card chip handler: resolve the advisory's preset (with any
  // per-advisory override, e.g. FIKI) and apply it. The companion route-map
  // metric is set in state (so it's "made" when the map is shown), but we keep
  // the cross-section as the view rather than forcing split — except when the
  // user is on the map-only layout, where we switch to split so the
  // cross-section the preset configures is actually visible. Then scroll it in.
  function handleAdvisoryChip(advisoryId: string): void {
    const preset = getPresetForAdvisory(advisoryId);
    if (!preset) return;
    store.getState().applyAdvisoryPreset(preset.id, resolveAdvisoryPreset(preset, preferredMethods));
    if (store.getState().vizSettings.layout === 'map') store.getState().setLayout('split');
    document.getElementById('viz-section')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
  }

  // URL params — declared before applyDeepLink() so the closure never reads it
  // in its temporal dead zone if a future call-site is added above init's body.
  const params = new URLSearchParams(window.location.search);

  // Deep-link (#308 Phase C): apply ?point=&model=&view=&preset=|advisory= once
  // the briefing is loaded, so an MCP/shared link opens a specific route point
  // with a given model + lens selected on a given surface. Best-effort: each
  // param is validated and silently skipped if it doesn't match this briefing.
  let deepLinkApplied = false;
  function applyDeepLink(): void {
    if (deepLinkApplied) return;
    const s = store.getState();
    // Guard BEFORE consuming the once-flag: if routeAnalyses failed to load
    // (old pack / network error) the link can still be honored on a later call.
    if (!s.routeAnalyses) return;
    deepLinkApplied = true;

    // Model — must be one this briefing actually has.
    const model = params.get('model');
    if (model && s.routeAnalyses.models.includes(model)) {
      store.getState().setSelectedModel(model);
    }

    // Route point — by point_index, clamped to a real analysis entry.
    const pointRaw = params.get('point');
    if (pointRaw != null) {
      const idx = Number.parseInt(pointRaw, 10);
      if (Number.isFinite(idx)) {
        // analyses are addressed by array position elsewhere; map point_index → position.
        const pos = s.routeAnalyses.analyses.findIndex(a => a.point_index === idx);
        if (pos >= 0) store.getState().setSelectedPoint(pos);
      }
    }

    // Lens — explicit ?preset= wins; otherwise resolve ?advisory= via the
    // shared advisory→preset mapping (single source of truth, no Python copy).
    const presetId = params.get('preset');
    const advisoryId = params.get('advisory');
    const preset = presetId && isAdvisoryPreset(presetId)
      ? getAdvisoryPreset(presetId)
      : (advisoryId ? getPresetForAdvisory(advisoryId) : undefined);
    if (preset) {
      store.getState().applyAdvisoryPreset(preset.id, resolveAdvisoryPreset(preset, preferredMethods));
    }

    // Surface — focus the Skew-T (or the compare/static variant) and scroll to it.
    // Known limitation: the lens overlay state (applyAdvisoryPreset above) is
    // pushed into the single-model SkewTRenderer via applySkewtPresetState();
    // the compare and static renderers have no applyPreset(), so a
    // ?view=skewt-compare&advisory=… link shades the cross-section but leaves the
    // compare/static Skew-T generic. The MCP only ever emits view=skewt, so this
    // affects only a hand-shared compare/static link — acceptable for now.
    const view = params.get('view');
    if (view === 'skewt' || view === 'skewt-compare' || view === 'skewt-static') {
      setSkewtViewMode(view === 'skewt-compare' ? 'compare' : view === 'skewt-static' ? 'static' : 'dynamic');
      document.querySelector('[data-section="skewt"]')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    } else if (view === 'cross-section' || presetId || advisoryId) {
      if (store.getState().vizSettings.layout === 'map') store.getState().setLayout('split');
      document.getElementById('viz-section')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }
  }

  // Get flight ID and optional pack timestamp from URL (params declared above)
  const flightId = params.get('flight');
  const packTimestamp = params.get('pack');
  if (!flightId) {
    ui.renderError(t('briefing.noFlightSpecified'));
    return;
  }

  // Dev-only eval labelling workbench (#254): when viewing a corpus pack
  // (flight id in the ``eval-`` namespace), dock the golden-label panel onto
  // the standard briefing view. Lazy-imported so it's tree-shaken out of the
  // normal briefing bundle path until an eval flight is opened.
  if (flightId.startsWith('eval-')) {
    import('./eval/label-panel').then((m) => m.initLabelPanel(flightId)).catch(() => {});
  }

  // --- Apply display mode CSS class ---
  function applyDisplayModeClass(mode: string): void {
    const container = document.querySelector('.container');
    if (container) {
      container.classList.remove('display-compact', 'display-full');
      container.classList.add(`display-${mode}`);
    }
  }

  // --- Update toggle button active state ---
  function updateToggleButtons(mode: string): void {
    const toggle = document.getElementById('display-mode-toggle');
    if (!toggle) return;
    toggle.querySelectorAll('.btn-toggle').forEach((btn) => {
      const el = btn as HTMLElement;
      el.classList.toggle('active', el.dataset.mode === mode);
    });
  }

  // --- Helper to render point-dependent sections ---
  /** Compute the effective cruise altitude relative to the manifest's
   *  baked value. Returns the altitude only when it actually differs
   *  from the manifest (i.e. either the user is probing an override or
   *  the flight was edited post-pack); returns null otherwise so
   *  downstream renderers respect server-computed values
   *  (cruise_in_icing) instead of re-deriving them. */
  function getEffectiveCruiseOverride(state: BriefingState): number | null {
    const manifestAlt = state.routeAnalyses?.cruise_altitude_ft ?? null;
    const raw = state.advisoryAltitudeOverride
      ?? state.flight?.cruise_altitude_ft
      ?? null;
    if (raw === null || raw === manifestAlt) return null;
    return raw;
  }

  function renderPointSections(state: BriefingState): void {
    // Show the Skew-T section wrapper once we have route data
    const skewtWrapper = document.querySelector('[data-section="skewt"]') as HTMLElement | null;
    if (skewtWrapper) skewtWrapper.style.display = state.routeAnalyses ? '' : 'none';
    // Hint nudging the user to click a point. We preview the first route point
    // when nothing is selected yet, so the hint clears once they pick a point.
    const skewtHint = document.getElementById('skewt-point-hint');
    if (skewtHint) {
      if (state.selectedPointIndex == null && state.routeAnalyses) {
        const first = state.routeAnalyses.analyses[0];
        const firstLabel = first?.waypoint_icao || (first ? `point ${first.point_index}` : '');
        skewtHint.textContent = `Showing the first point${firstLabel ? ` ${firstLabel}` : ''}. Click any point on the cross-section above to show the Skew-T there.`;
        skewtHint.style.display = '';
      } else {
        skewtHint.style.display = 'none';
      }
    }
    const effectiveCruiseAlt = getEffectiveCruiseOverride(state);
    ui.renderSoundingAnalysis(state.snapshot, state.routeAnalyses, state.selectedPointIndex, state.displayMode, state.tierVisibility, state.vizSettings.enabledLayers, effectiveCruiseAlt);
    // Dynamic Skew-T (canvas), compare, or static MetPy
    if (skewtViewMode === 'dynamic') {
      lastSkewtPointIndex = null; // force re-fetch when point/model changes
      lastSkewtModel = null;
      loadSkewtData(state);
    } else if (skewtViewMode === 'compare') {
      lastSkewtCompareKey = null; // force re-fetch
      loadSkewtCompareData(state);
    } else {
      ui.renderSkewTs(state.flight, state.currentPack, state.snapshot, state.selectedModel, state.routeAnalyses, state.selectedPointIndex);
    }
    ui.renderModelComparison(state.snapshot, state.routeAnalyses, state.selectedPointIndex, state.displayMode, state.tierVisibility);
    ui.updateWindyLink(state.routeAnalyses, state.selectedPointIndex, state.selectedModel);
  }

  /** Build altitude override config from current state, or undefined if no flight. */
  function getAltitudeOverrideConfig(state: BriefingState): AltitudeOverrideConfig | undefined {
    if (!state.flight) return undefined;
    const defaultAlt = state.flight.cruise_altitude_ft;
    const ceilingFt = state.flight.flight_ceiling_ft;
    return {
      currentAlt: state.advisoryAltitudeOverride ?? defaultAlt,
      defaultAlt,
      ceilingFt,
      // Drag (continuous): cheap local override → cross-section line follows.
      onChange: (alt) => store.getState().setAdvisoryAltitudeOverride(alt === defaultAlt ? null : alt),
      // Release: index the cached table for instant statuses + debounced,
      // stale-guarded wind overlay (#259) — no per-release full reload.
      onProbe: (alt) => store.getState().probeAltitude(alt),
      // The digest's analysis is anchored to the pack's planned altitude; the
      // delta note compares the probed altitude against it.
      plannedAlt: state.routeAnalyses?.cruise_altitude_ft ?? defaultAlt,
      table: state.altitudeTable,
    };
  }

  // Load user profiles for the profile selector on the advisory toolbar.
  // Fire-and-forget: re-render advisories when profiles arrive so the
  // selector appears even if the initial render happened first.
  let profiles: ProfileResponse[] = [];
  fetchProfiles().then(p => {
    profiles = p;
    // Re-render advisories now that profiles are available for the selector
    const s = store.getState();
    if (s.flight) {
      renderAdvisories(getEffectiveAdvisories(s), () => store.getState().recalculateAdvisories(), s.displayMode, getAltitudeOverrideConfig(s), handleAltitudeTable, getAltTimeToggleConfig(s), getProfileSelectorConfig(s), handleAdvisoryChip);
    }
  }).catch(err => console.error('Failed to fetch profiles:', err));

  // Load the live advisory catalog so the (i) popups show current copy
  // (descriptions / parameter defs) instead of whatever was baked into the pack
  // at generation time. Fire-and-forget: re-render advisories when it arrives.
  fetchAdvisoryCatalog().then(entries => {
    setLiveAdvisoryCatalog(entries);
    const s = store.getState();
    if (s.flight) {
      renderAdvisories(getEffectiveAdvisories(s), () => store.getState().recalculateAdvisories(), s.displayMode, getAltitudeOverrideConfig(s), handleAltitudeTable, getAltTimeToggleConfig(s), getProfileSelectorConfig(s), handleAdvisoryChip);
    }
  }).catch(err => console.error('Failed to fetch advisory catalog:', err));

  /** Build profile selector config for the advisory toolbar. */
  function getProfileSelectorConfig(state: BriefingState): ProfileSelectorConfig | undefined {
    if (!state.flight || profiles.length === 0) return undefined;
    return {
      profiles: profiles.map(p => ({ id: p.id, name: p.name })),
      currentProfileId: state.flight.profile_id,
      isOwner: state.flight.user_id === user!.id,
      onChange: async (profileId: number) => {
        await store.getState().changeFlightProfile(profileId);
        // Re-fetch profiles list isn't needed — IDs don't change
      },
    };
  }

  /** Fetch altitude table and show popup. */
  async function handleAltitudeTable(): Promise<void> {
    // Plain track (not once-per-briefing): each open is a deliberate click,
    // so total_uses reflects real intensity. briefings_with_feature still
    // dedupes by briefing_id server-side for the attachment rate.
    track(EVENTS.ALTITUDE_TABLE_OPENED);
    await store.getState().fetchAltitudeTable();
    const result = store.getState().altitudeTable;
    if (result) {
      showPopupContent(renderAltitudeTablePopup(result));
    }
  }

  /** Build alt time toggle config if alt advisories are available. */
  function getAltTimeToggleConfig(state: BriefingState): AltTimeToggleConfig | undefined {
    if (!state.flight?.alt_departure_time || !state.altAdvisories) return undefined;
    const primaryTime = formatDepartureTime(state.flight.departure_time);
    const altTime = formatDepartureTime(state.flight.alt_departure_time);
    return {
      primaryLabel: primaryTime,
      altLabel: altTime,
      showingAlt: state.showingAlt,
      onToggle: () => store.getState().toggleAltView(),
    };
  }

  /** Get the effective advisory manifest based on primary/alt toggle state. */
  function getEffectiveAdvisories(state: BriefingState): import('./types/advisories').RouteAdvisoriesManifest | null {
    const base = state.showingAlt && state.altAdvisories ? state.altAdvisories : state.routeAdvisories;
    // Lever moved (#259): overlay the probed altitude's advisory statuses from
    // the cached table so the cards update instantly, before the debounced
    // server recalc lands. Alt-time view keeps its own altitude — don't overlay.
    // Skip the overlay once the base manifest already reflects the override
    // altitude (the reanchor path recomputes routeAdvisories at `alt`):
    // re-overlaying a snapped table row there would clobber the exact live
    // statuses with the nearest 2000ft row's.
    if (
      base && !state.showingAlt && state.advisoryAltitudeOverride != null && state.altitudeTable
      && base.cruise_altitude_ft !== state.advisoryAltitudeOverride
    ) {
      return overlayAltitudeStatuses(base, state.altitudeTable, state.advisoryAltitudeOverride);
    }
    return base;
  }

  /** Render the timing-scenario panel (better departure windows). Suppresses
   *  itself when there's nothing better to show. Advisory ids are named via the
   *  shared advisory catalog lookup so both surfaces agree. */
  function renderTimeOptionsPanel(state: BriefingState): void {
    renderTimeOptions(
      document.getElementById('time-options-wrapper'),
      state.timeOptions,
      {
        onConfirm: (dt) => store.getState().confirmTimeCandidate(dt),
        resolveName: (id) => advisoryName(id, state.routeAdvisories),
      },
    );
  }

  // Apply initial display mode
  applyDisplayModeClass(store.getState().displayMode);
  updateToggleButtons(store.getState().displayMode);

  // --- Cross-section visualization + route graph + route map ---
  let vizRenderer: CrossSectionRenderer | null = null;
  let vizInteraction: InteractionHandle | null = null;
  let routeGraphRenderer: RouteGraphRenderer | null = null;
  let routeGraphInteraction: RouteGraphInteractionHandle | null = null;
  let mapRenderer: RouteMapRenderer | null = null;
  let mapInteraction: MapInteractionHandle | null = null;
  let compareRenderer: CompareSectionRenderer | null = null;
  let compareInteraction: CompareInteractionHandle | null = null;

  // --- Dynamic Skew-T renderer ---
  let skewtRenderer: SkewTRenderer | null = null;
  let skewtInteraction: SkewTInteractionHandle | null = null;
  let skewtCompareRenderer: SkewTCompareRenderer | null = null;
  let skewtCompareInteraction: SkewTCompareInteractionHandle | null = null;
  let skewtViewMode: 'dynamic' | 'compare' | 'static' = 'dynamic';
  let lastSkewtPointIndex: number | null = null;
  let lastSkewtModel: string | null = null;
  let lastSkewtCompareKey: string | null = null;

  function initSkewtToggle(): void {
    const dynBtn = document.getElementById('skewt-view-dynamic');
    const cmpBtn = document.getElementById('skewt-view-compare');
    const statBtn = document.getElementById('skewt-view-static');
    if (dynBtn) dynBtn.addEventListener('click', () => setSkewtViewMode('dynamic'));
    if (cmpBtn) cmpBtn.addEventListener('click', () => setSkewtViewMode('compare'));
    if (statBtn) statBtn.addEventListener('click', () => setSkewtViewMode('static'));
  }

  // The skew-T section is expanded by default in the (default) dynamic view,
  // so switching sub-mode is a poor proxy for engagement — most users just
  // scroll down and look. Count "scrolled the skew-T into view" as the open
  // signal. trackOncePerBriefing dedupes against the sub-mode-switch path so
  // a briefing is counted at most once regardless of which happens first.
  function initSkewtViewTracking(): void {
    if (typeof IntersectionObserver === 'undefined') return;
    const skewtWrapper = document.querySelector('[data-section="skewt"]');
    if (!skewtWrapper) return;
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            trackOncePerBriefing(EVENTS.SKEWT_OPENED, { view: skewtViewMode });
          }
        }
      },
      { threshold: 0.25 },
    );
    observer.observe(skewtWrapper);
  }

  function destroySkewtRenderer(): void {
    if (skewtInteraction) { skewtInteraction.destroy(); skewtInteraction = null; }
    if (skewtRenderer) { skewtRenderer.destroy(); skewtRenderer = null; }
    lastSkewtPointIndex = null;
    lastSkewtModel = null;
  }

  function destroySkewtCompareRenderer(): void {
    if (skewtCompareInteraction) { skewtCompareInteraction.destroy(); skewtCompareInteraction = null; }
    if (skewtCompareRenderer) { skewtCompareRenderer.destroy(); skewtCompareRenderer = null; }
    lastSkewtCompareKey = null;
  }

  function setSkewtViewMode(mode: 'dynamic' | 'compare' | 'static'): void {
    const prevMode = skewtViewMode;
    skewtViewMode = mode;
    if (prevMode !== mode) {
      // Once per briefing — toggling between view modes shouldn't inflate
      // the "user engaged with skew-T" signal.
      trackOncePerBriefing(EVENTS.SKEWT_OPENED, { view: mode });
    }

    const dynBtn = document.getElementById('skewt-view-dynamic');
    const cmpBtn = document.getElementById('skewt-view-compare');
    const statBtn = document.getElementById('skewt-view-static');
    const canvasContainer = document.getElementById('skewt-canvas-container');
    const staticSection = document.getElementById('skewt-section');
    const overlayControls = document.getElementById('skewt-overlay-controls');
    const compareControls = document.getElementById('skewt-compare-controls');

    if (dynBtn) dynBtn.classList.toggle('active', mode === 'dynamic');
    if (cmpBtn) cmpBtn.classList.toggle('active', mode === 'compare');
    if (statBtn) statBtn.classList.toggle('active', mode === 'static');

    // Destroy outgoing renderer (canvas container is shared)
    if (prevMode === 'dynamic' && mode !== 'dynamic') destroySkewtRenderer();
    if (prevMode === 'compare' && mode !== 'compare') destroySkewtCompareRenderer();

    // Show/hide containers
    if (canvasContainer) canvasContainer.style.display = (mode === 'dynamic' || mode === 'compare') ? 'block' : 'none';
    if (overlayControls) overlayControls.style.display = mode === 'dynamic' ? 'block' : 'none';
    if (compareControls) compareControls.style.display = mode === 'compare' ? 'block' : 'none';
    if (staticSection) staticSection.style.display = mode === 'static' ? 'block' : 'none';

    const state = store.getState();
    if (mode === 'dynamic') {
      loadSkewtData(state);
    } else if (mode === 'compare') {
      loadSkewtCompareData(state);
    } else {
      ui.renderSkewTs(state.flight, state.currentPack, state.snapshot, state.selectedModel, state.routeAnalyses, state.selectedPointIndex);
    }
  }

  /** (Re)render the single-model Skew-T overlay/side-panel controls, wired so a
   *  manual edit drops the preset to "Custom" and the help button explains the
   *  current view (#308). */
  function renderSkewtControls(): void {
    if (!skewtRenderer) return;
    const controlsEl = document.getElementById('skewt-overlay-controls');
    if (controlsEl) {
      renderSkewtOverlayControls(controlsEl, skewtRenderer, {
        onUserEdit: () => store.getState().markVizCustom(),
        onHelp: () => showSkewtHelp(),
      });
    }
  }

  /** Push the store's preset-driven Skew-T lens (overlay bands + primary
   *  side-panel variable) into the live renderer. Only acts when an advisory
   *  preset is active; re-renders the controls when the lens actually changed
   *  so the checkboxes/dropdowns reflect it (#308 Phase A). */
  function applySkewtPresetState(): void {
    if (!skewtRenderer) return;
    const vs = store.getState().vizSettings;
    if (!isAdvisoryPreset(vs.activePreset)) return;
    const changed = skewtRenderer.applyPreset({
      overlays: vs.skewtOverlays,
      primaryVar: vs.skewtPrimaryVar,
    });
    if (changed) renderSkewtControls();
  }

  /** "Help me read this graph" (#308 Phase B): explain the CURRENT Skew-T view
   *  in plain language — the active lens's interpretation text plus this
   *  sounding's key computed values and which overlay bands are shaded. */
  function showSkewtHelp(): void {
    const data = skewtRenderer?.getData() ?? null;
    const vs = store.getState().vizSettings;
    // Prefer the active advisory preset's interpretation; fall back to Basic's
    // (the neutral "how to read a Skew-T" text) when the view is Custom/null.
    const preset = isAdvisoryPreset(vs.activePreset)
      ? getAdvisoryPreset(vs.activePreset!)
      : getAdvisoryPreset('basic');
    const interpretation = preset ? advisoryPresetInterpretation(preset) : '';

    const fl = (p: number | null | undefined): string =>
      p == null ? '—' : `FL${Math.round(pressureToAltitudeFt(p) / 100)}`;
    const num = (v: unknown, unit: string): string =>
      typeof v === 'number' && isFinite(v) ? `${Math.round(v)}${unit}` : '—';
    const ftStr = (v: unknown): string =>
      typeof v === 'number' && isFinite(v) ? `${Math.round(v).toLocaleString()} ft` : '—';

    let factsHtml = '';
    const ind = data?.indices ?? null;
    if (ind) {
      // Optional 3rd element = metrics-catalog id → renders a drill-down (i) next
      // to the value (CAPE/CIN/LCL/LFC/EL have entries; 0 °C level is skipped).
      const facts: Array<[string, string, string?]> = [
        ['CAPE', num(ind.cape_surface_jkg, ' J/kg'), 'cape_surface_jkg'],
        ['CIN', num(ind.cin_surface_jkg, ' J/kg'), 'cin_surface_jkg'],
        ['0 °C level', ftStr(ind.freezing_level_ft)],
        ['LCL', fl(ind.lcl_pressure_hpa as number | null), 'lcl_altitude_ft'],
        ['LFC', fl(ind.lfc_pressure_hpa as number | null), 'lfc_altitude_ft'],
        ['EL', fl(ind.el_pressure_hpa as number | null), 'el_altitude_ft'],
      ];
      const factInfo = (metricId?: string): string =>
        metricId && getMetric(metricId)
          ? ` <button class="popup-drill-metric skewt-help-fact-info" data-metric="${metricId}"`
            + ` title="${escapeHtml(t('viz.skewtHelp.fullCard'))}" aria-label="${escapeHtml(t('viz.skewtHelp.fullCard'))}">ⓘ</button>`
          : '';
      factsHtml = '<dl class="skewt-help-facts">'
        + facts.map(([k, v, mid]) => `<div><dt>${escapeHtml(k)}${factInfo(mid)}</dt><dd>${escapeHtml(v)}</dd></div>`).join('')
        + '</dl>';
    }

    // Which overlay bands are currently shaded.
    const onIds = skewtRenderer ? skewtRenderer.getOverlayState() : {};
    const activeBands = SKEWT_OVERLAYS.filter(o => onIds[o.id]).map(o => o.label);
    const bandsHtml = activeBands.length
      ? `<p class="skewt-help-bands"><strong>${escapeHtml(t('viz.skewtHelp.shadedNow'))}</strong> ${escapeHtml(activeBands.join(', '))}.</p>`
      : `<p class="skewt-help-bands">${escapeHtml(t('viz.skewtHelp.noBands'))}</p>`;

    // Per-value "what does this mean" cards: for the active side-panel variable
    // and each shaded overlay band, pull the metrics-catalog vibe + interpretation
    // so the help explains the values actually on screen, not just names them.
    const renderVarCard = (kind: string, label: string, metricId?: string): string => {
      const entry = metricId ? getMetric(metricId) : undefined;
      // (i) drills into the full metric card (all fields) inside this same popup,
      // with a Back button — wired generically in info-popup (.popup-drill-metric).
      const info = entry
        ? `<button class="popup-drill-metric skewt-help-info-btn" data-metric="${metricId}"`
          + ` title="${escapeHtml(t('viz.skewtHelp.fullCard'))}" aria-label="${escapeHtml(t('viz.skewtHelp.fullCard'))}">ⓘ</button>`
        : '';
      const head = `<div class="skewt-help-var-head">`
        + `<span class="skewt-help-var-kind">${escapeHtml(kind)}</span> `
        + `<span class="skewt-help-var-name">${escapeHtml(label)}</span>${info}</div>`;
      if (!entry) return `<div class="skewt-help-var">${head}</div>`;
      const vibe = entry.vibe ? `<p class="skewt-help-var-vibe">${escapeHtml(entry.vibe)}</p>` : '';
      const interp = entry.best_used_for ? `<p class="skewt-help-var-interp">${escapeHtml(entry.best_used_for)}</p>` : '';
      const strip = metricId ? renderCompactThresholdStrip(metricId) : '';
      return `<div class="skewt-help-var">${head}${vibe}${interp}${strip}</div>`;
    };
    const cards: string[] = [];
    const primaryVar = skewtRenderer ? getVariableById(skewtRenderer.getPrimaryVar()) : undefined;
    if (primaryVar) cards.push(renderVarCard(t('viz.skewtHelp.sidePanelKind'), primaryVar.label, primaryVar.metricId));
    for (const o of SKEWT_OVERLAYS) {
      if (onIds[o.id]) cards.push(renderVarCard(t('viz.skewtHelp.bandKind'), o.label, o.metricId));
    }
    const onGraphHtml = cards.length
      ? `<div class="skewt-help-vars"><p class="skewt-help-vars-label"><strong>${escapeHtml(t('viz.skewtHelp.onGraphNow'))}</strong></p>${cards.join('')}</div>`
      : '';

    const title = preset
      ? t('viz.skewtHelp.title', { preset: preset.label })
      : t('viz.skewtHelp.title', { preset: t('viz.skewtHelp.titleFallback') });
    const html = `<div class="skewt-help-popup">`
      + `<h3>${escapeHtml(title)}</h3>`
      + `<p>${escapeHtml(interpretation)}</p>`
      + bandsHtml
      + onGraphHtml
      + (factsHtml ? `<p class="skewt-help-facts-label"><strong>${escapeHtml(t('viz.skewtHelp.thisSounding'))}</strong></p>${factsHtml}` : '')
      + `</div>`;
    showPopupContent(html);
  }

  function ensureSkewtRenderer(): SkewTRenderer {
    if (!skewtRenderer) {
      const container = document.getElementById('skewt-canvas-container');
      if (!container) throw new Error('skewt-canvas-container not found');
      skewtRenderer = new SkewTRenderer(container);
      // Render overlay toggle controls
      renderSkewtControls();
      // Seed from the active preset/deep-link lens, if any.
      applySkewtPresetState();
      // Attach hover interaction with linked cursor
      skewtInteraction = attachSkewTInteraction(
        skewtRenderer.getOverlayCanvas(),
        container,
        () => skewtRenderer!.getTransform(),
        () => skewtRenderer!.getData(),
        {
          onHoverAltitude: (altFt) => {
            // Linked cursor: draw horizontal line on cross-section at this altitude
            if (vizRenderer) {
              if (altFt !== undefined) {
                const transform = vizRenderer.createTransform();
                if (transform) {
                  const y = transform.altitudeToY(altFt);
                  vizRenderer.renderOverlay(undefined, y);
                }
              } else {
                vizRenderer.renderOverlay();
              }
            }
          },
        },
      );
    }
    return skewtRenderer;
  }

  async function loadSkewtData(state: BriefingState): Promise<void> {
    if (skewtViewMode !== 'dynamic') return;
    if (!state.flight || !state.currentPack || !state.routeAnalyses) {
      ensureSkewtRenderer().clear();
      return;
    }

    // Find the selected point — any route point works. When the user hasn't
    // clicked a point yet (null), preview the first route point (departure)
    // rather than showing nothing; a hint banner invites them to click.
    const idx = state.selectedPointIndex ?? 0;
    const point = state.routeAnalyses.analyses[idx];
    if (!point) {
      ensureSkewtRenderer().clear();
      return;
    }

    // Avoid re-fetching if same point and model
    if (idx === lastSkewtPointIndex && state.selectedModel === lastSkewtModel) return;
    lastSkewtPointIndex = idx;
    lastSkewtModel = state.selectedModel;

    try {
      const data = await api.fetchSoundingProfile(
        state.flight.id,
        state.currentPack.fetch_timestamp,
        point.point_index,
        state.selectedModel,
      );
      if (data) {
        ensureSkewtRenderer().setData(data);
        skewtInteraction?.update(data);
      } else {
        ensureSkewtRenderer().clear();
        skewtInteraction?.update(null);
      }
    } catch {
      ensureSkewtRenderer().clear();
    }
  }

  function ensureSkewtCompareRenderer(): SkewTCompareRenderer {
    if (!skewtCompareRenderer) {
      const container = document.getElementById('skewt-canvas-container');
      if (!container) throw new Error('skewt-canvas-container not found');
      skewtCompareRenderer = new SkewTCompareRenderer(container);
      // Attach compare interaction with linked cursor
      skewtCompareInteraction = attachSkewTCompareInteraction(
        skewtCompareRenderer.getOverlayCanvas(),
        container,
        () => skewtCompareRenderer!.getTransform(),
        () => skewtCompareRenderer!.getDatasets(),
        {
          onHoverAltitude: (altFt) => {
            if (vizRenderer) {
              if (altFt !== undefined) {
                const transform = vizRenderer.createTransform();
                if (transform) {
                  const y = transform.altitudeToY(altFt);
                  vizRenderer.renderOverlay(undefined, y);
                }
              } else {
                vizRenderer.renderOverlay();
              }
            }
          },
        },
      );
    }
    return skewtCompareRenderer;
  }

  async function loadSkewtCompareData(state: BriefingState): Promise<void> {
    if (skewtViewMode !== 'compare') return;
    if (!state.flight || !state.currentPack || !state.routeAnalyses) {
      ensureSkewtCompareRenderer().clear();
      return;
    }

    // Preview the first route point when nothing is selected yet (see loadSkewtData).
    const idx = state.selectedPointIndex ?? 0;
    const point = state.routeAnalyses.analyses[idx];
    if (!point) {
      ensureSkewtCompareRenderer().clear();
      return;
    }

    // Determine enabled models
    store.getState().initCompareModels(state.routeAnalyses.models);
    const compareModels = store.getState().vizSettings.compareModels;
    const enabledModels = state.routeAnalyses.models.filter(m => compareModels[m] !== false);
    if (enabledModels.length === 0) {
      ensureSkewtCompareRenderer().clear();
      return;
    }

    // Cache check: skip re-fetch if same point + same enabled models
    const cacheKey = JSON.stringify({ idx, models: [...enabledModels].sort() });
    if (cacheKey === lastSkewtCompareKey) return;
    lastSkewtCompareKey = cacheKey;

    // Fetch all models in parallel
    const theme = getActiveTheme();
    const allModels = state.routeAnalyses.models;

    try {
      const results = await Promise.all(
        enabledModels.map(m =>
          api.fetchSoundingProfile(state.flight!.id, state.currentPack!.fetch_timestamp, point.point_index, m)
            .catch(() => null),
        ),
      );

      const datasets: SkewtCompareModelDataset[] = [];
      for (let i = 0; i < enabledModels.length; i++) {
        if (results[i]) {
          // Color by position in full model list for stability
          const modelIndex = allModels.indexOf(enabledModels[i]);
          const colorIndex = modelIndex >= 0 ? modelIndex : i;
          datasets.push({
            model: enabledModels[i],
            data: results[i]!,
            color: theme.compareModelColors[colorIndex % theme.compareModelColors.length],
            isPrimary: enabledModels[i] === state.selectedModel,
          });
        }
      }

      // If primary model isn't in datasets (toggled off), mark first as primary
      if (datasets.length > 0 && !datasets.some(d => d.isPrimary)) {
        datasets[0].isPrimary = true;
      }

      const renderer = ensureSkewtCompareRenderer();
      renderer.setModelData(datasets);
      skewtCompareInteraction?.update(datasets);

      // Build model color map for controls
      const modelColors: Record<string, string> = {};
      for (const ds of datasets) modelColors[ds.model] = ds.color;
      // Include disabled models with their stable color
      for (const m of allModels) {
        if (!modelColors[m]) {
          const mi = allModels.indexOf(m);
          modelColors[m] = theme.compareModelColors[mi % theme.compareModelColors.length];
        }
      }

      // Render compare controls
      const controlsEl = document.getElementById('skewt-compare-controls');
      if (controlsEl) {
        renderSkewtCompareControls(controlsEl, renderer, allModels, compareModels, state.selectedModel, modelColors, {
          onModelToggle: (model, enabled) => {
            store.getState().setCompareModel(model, enabled);
          },
          onCapeCinToggle: () => renderer.toggleCapeCin(),
          onLevelMarkersToggle: () => renderer.toggleLevelMarkers(),
        });
      }
    } catch {
      ensureSkewtCompareRenderer().clear();
    }
  }

  /** Apply the CSS layout class to the layout wrapper. */
  function applyLayoutClass(layout: string): void {
    const wrapper = document.getElementById('viz-layout-wrapper');
    if (wrapper) {
      wrapper.classList.remove('layout-cross-section', 'layout-map', 'layout-split', 'layout-compare');
      wrapper.classList.add(`layout-${layout}`);
    }
  }

  function renderVisualization(state: BriefingState): void {
    const vizSection = document.getElementById('viz-section');
    const canvasContainer = document.getElementById('viz-canvas-container');
    const controlsContainer = document.getElementById('viz-controls');
    const routeGraphContainer = document.getElementById('route-graph-container');
    const routeGraphControlsContainer = document.getElementById('route-graph-controls');
    const mapContainer = document.getElementById('map-container');
    const mapControlsContainer = document.getElementById('map-controls');
    const mapLegendContainer = document.getElementById('map-legend');
    const mapSliderContainer = document.getElementById('map-altitude-slider');
    if (!vizSection || !canvasContainer || !controlsContainer) return;

    if (!state.routeAnalyses) {
      vizSection.style.display = 'none';
      return;
    }
    vizSection.style.display = '';

    const layout = state.vizSettings.layout;
    applyLayoutClass(layout);

    const extractOpts = {
      windOverlay: state.windOverlay,
      effectiveCruiseAltitudeFt: getEffectiveCruiseOverride(state),
      routeObservations: state.snapshot?.route_observations,
      routeSigmets: state.snapshot?.route_sigmets,
      routeFronts: state.routeFronts,
    };
    const data = extractVizData(state.routeAnalyses, state.selectedModel, state.flight?.flight_ceiling_ft, state.elevationProfile, extractOpts);
    const unavailable = getUnavailableLayers(data);
    const allLayers = getAllLayers();
    // Render-time map only — never mutates the stored enabledLayers pref.
    const effectiveEnabled = applyNwpFallback(state.vizSettings.enabledLayers, unavailable);
    const substitutedLayers = getSubstitutedLayers(state.vizSettings.enabledLayers, effectiveEnabled);
    // Terrain always renders (toggle removed); force-on overrides stale terrain:false in localStorage.
    effectiveEnabled['terrain'] = true;
    const showCrossSection = layout === 'cross-section' || layout === 'split';
    const showCompare = layout === 'compare';
    const showMap = layout === 'map' || layout === 'split';
    const availableModels = state.routeAnalyses?.models ?? [];

    // --- Compare mode ---
    if (showCompare) {
      // Destroy cross-section renderer if exists
      if (vizInteraction) { vizInteraction.destroy(); vizInteraction = null; }
      if (vizRenderer) { vizRenderer.destroy(); vizRenderer = null; }
      if (routeGraphInteraction) { routeGraphInteraction.destroy(); routeGraphInteraction = null; }
      if (routeGraphRenderer) { routeGraphRenderer.destroy(); routeGraphRenderer = null; }

      // Init compare models if empty
      store.getState().initCompareModels(availableModels);
      const compareModels = store.getState().vizSettings.compareModels;

      // Extract VizRouteData for each active model
      const datasets: CompareModelData[] = [];
      for (const m of availableModels) {
        if (compareModels[m] !== false) {
          datasets.push({
            model: m,
            data: extractVizData(state.routeAnalyses!, m, state.flight?.flight_ceiling_ft, state.elevationProfile, extractOpts),
          });
        }
      }

      if (datasets.length > 0) {
        // Create/reuse compare renderer
        if (!compareRenderer) {
          compareRenderer = new CompareSectionRenderer(canvasContainer);
        }

        const layer = getComparableLayer(state.vizSettings.compareLayer) ?? null;
        compareRenderer.setModelData(datasets);
        compareRenderer.setCompareLayer(layer);
        compareRenderer.setBandMode(state.vizSettings.compareBandMode ?? 'consensus-outline');
        compareRenderer.setSelectedPointIndex(state.selectedPointIndex ?? -1);
        compareRenderer.render();

        // Attach or update compare interaction
        if (compareInteraction) {
          compareInteraction.update(datasets, layer);
        } else {
          compareInteraction = attachCompareInteraction(
            compareRenderer, datasets, layer, {
              onSelectPoint: (idx) => store.getState().setSelectedPoint(idx),
            },
          );
        }
      }

      // Hide route graph in compare mode
      if (routeGraphContainer) routeGraphContainer.style.display = 'none';

      // Render compare controls
      renderCompareControls(controlsContainer, state.vizSettings, {
        onLayoutChange: (l) => store.getState().setLayout(l),
        onCompareLayerChange: (layerId) => store.getState().setCompareLayer(layerId),
        onCompareModelToggle: (model, enabled) => store.getState().setCompareModel(model, enabled),
        onCompareBandModeChange: (mode) => store.getState().setCompareBandMode(mode),
        onThemeChange: (themeId) => store.getState().setVizTheme(themeId),
        onPresetChange: (presetId) => handlePresetChange(presetId),
      }, availableModels);

      // Hide route graph controls in compare mode
      if (routeGraphControlsContainer) routeGraphControlsContainer.innerHTML = '';

    } else {
      // Not compare — destroy compare renderer if exists
      if (compareInteraction) { compareInteraction.destroy(); compareInteraction = null; }
      if (compareRenderer) { compareRenderer.destroy(); compareRenderer = null; }
    }

    // --- Cross-section ---
    if (showCrossSection) {
      if (!vizRenderer) {
        vizRenderer = new CrossSectionRenderer(canvasContainer);
      }

      vizRenderer.setData(data);
      vizRenderer.setLayers(allLayers, effectiveEnabled);
      vizRenderer.setSelectedPointIndex(state.selectedPointIndex ?? -1);
      vizRenderer.render();

      // --- Route graph ---
      const graphVisible = state.vizSettings.routeGraphVisible;
      if (routeGraphContainer) {
        routeGraphContainer.style.display = graphVisible ? '' : 'none';
      }

      if (graphVisible && routeGraphContainer) {
        if (!routeGraphRenderer) {
          routeGraphRenderer = new RouteGraphRenderer(routeGraphContainer);
        }

        const leftMetric = getMetricById(state.vizSettings.routeGraphLeftMetric) ?? null;
        const rightId = state.vizSettings.routeGraphRightMetric;
        const rightMetric = rightId === METRIC_NONE ? null : (getMetricById(rightId) ?? null);

        routeGraphRenderer.setData(data);
        routeGraphRenderer.setMetrics(leftMetric, rightMetric);
        routeGraphRenderer.setSelectedPointIndex(state.selectedPointIndex ?? -1);
        routeGraphRenderer.render();

        // Attach or update route graph interaction
        if (routeGraphInteraction) {
          routeGraphInteraction.update(data, leftMetric, rightMetric);
        } else {
          routeGraphInteraction = attachRouteGraphInteraction(
            routeGraphRenderer, data, leftMetric, rightMetric, {
              onSelectPoint: (idx) => store.getState().setSelectedPoint(idx),
              onHover: (x) => {
                if (vizRenderer) vizRenderer.renderOverlay(x);
                // Sync hover to map: find nearest point from x
                if (mapRenderer && store.getState().vizSettings.layout !== 'cross-section' && x !== undefined) {
                  const distToX = routeGraphRenderer!.createDistanceToX();
                  if (distToX && data.points.length > 0) {
                    const plotArea = routeGraphRenderer!.getPlotArea();
                    if (plotArea) {
                      const dist = ((x - plotArea.left) / plotArea.width) * data.totalDistanceNm;
                      let bestIdx = 0;
                      let bestDelta = Infinity;
                      for (let i = 0; i < data.points.length; i++) {
                        const d = Math.abs(data.points[i].distanceNm - dist);
                        if (d < bestDelta) { bestDelta = d; bestIdx = i; }
                      }
                      mapRenderer.highlightSegment(bestIdx);
                    }
                  }
                } else if (mapRenderer && x === undefined) {
                  mapRenderer.highlightSegment(-1);
                }
              },
            },
          );
        }
      } else {
        if (routeGraphInteraction) { routeGraphInteraction.destroy(); routeGraphInteraction = null; }
        if (routeGraphRenderer) { routeGraphRenderer.destroy(); routeGraphRenderer = null; }
      }

      // Attach or update cross-section interaction
      if (vizInteraction) {
        vizInteraction.update(data);
      } else {
        vizInteraction = attachInteraction(vizRenderer, data, {
          onSelectPoint: (idx) => store.getState().setSelectedPoint(idx),
          onHover: (x) => {
            const liveState = store.getState();
            if (routeGraphRenderer && liveState.vizSettings.routeGraphVisible) routeGraphRenderer.renderOverlay(x);
            // Sync hover to map
            if (mapRenderer && liveState.vizSettings.layout !== 'cross-section' && x !== undefined) {
              const transform = vizRenderer!.createTransform();
              if (transform) {
                const dist = transform.xToDistance(x);
                let bestIdx = 0;
                let bestDelta = Infinity;
                for (let i = 0; i < data.points.length; i++) {
                  const d = Math.abs(data.points[i].distanceNm - dist);
                  if (d < bestDelta) { bestDelta = d; bestIdx = i; }
                }
                mapRenderer.highlightSegment(bestIdx);
              }
            } else if (mapRenderer && x === undefined) {
              mapRenderer.highlightSegment(-1);
            }
          },
          onHoverAltitude: (altFt) => {
            // Linked cursor: show altitude on Skew-T
            if (skewtInteraction) {
              skewtInteraction.setExternalHoverAlt(altFt ?? null);
            }
          },
        });
      }
    } else {
      // Cross-section not visible — destroy renderers
      if (vizInteraction) { vizInteraction.destroy(); vizInteraction = null; }
      if (vizRenderer) { vizRenderer.destroy(); vizRenderer = null; }
      if (routeGraphInteraction) { routeGraphInteraction.destroy(); routeGraphInteraction = null; }
      if (routeGraphRenderer) { routeGraphRenderer.destroy(); routeGraphRenderer = null; }
    }

    // --- Route map ---
    if (showMap && mapContainer) {
      const colorMetric = getMapMetricById(state.vizSettings.mapColorMetric) ?? null;
      const widthId = state.vizSettings.mapWidthMetric;
      const widthMetric = widthId === MAP_METRIC_NONE ? null : (getMapMetricById(widthId) ?? null);
      const altFt = state.vizSettings.mapAltitudeFt ?? data.cruiseAltitudeFt;
      const isAltDependent = (colorMetric?.altitudeDependent || widthMetric?.altitudeDependent) ?? false;

      if (!mapRenderer) {
        mapRenderer = new RouteMapRenderer(mapContainer);
      }

      mapRenderer.setData(data);
      mapRenderer.setColorMetric(colorMetric);
      mapRenderer.setWidthMetric(widthMetric);
      mapRenderer.setAltitude(altFt);
      mapRenderer.setShowFronts(state.vizSettings.mapFrontsVisible ?? false);
      mapRenderer.setSelectedPointIndex(state.selectedPointIndex ?? -1);
      mapRenderer.render();
      // Gated front axes for the selected model (async; redraws when ready).
      updateMapFrontLines(
        state.routeFronts,
        state.selectedModel,
        state.vizSettings.mapFrontsVisible ?? false,
        mapRenderer,
      );

      // Attach or update map interaction
      if (mapInteraction) {
        mapInteraction.update(data, colorMetric, widthMetric, altFt);
      } else {
        mapInteraction = attachMapInteraction(
          mapRenderer, data, colorMetric, widthMetric, altFt, {
            onSelectPoint: (idx) => store.getState().setSelectedPoint(idx),
            onHover: (idx) => {
              const liveState = store.getState();
              const liveShowCrossSection = liveState.vizSettings.layout !== 'map';
              if (idx !== undefined && vizRenderer && liveShowCrossSection) {
                const transform = vizRenderer.createTransform();
                if (transform && data.points[idx]) {
                  const x = transform.distanceToX(data.points[idx].distanceNm);
                  vizRenderer.renderOverlay(x);
                  if (routeGraphRenderer && liveState.vizSettings.routeGraphVisible) {
                    routeGraphRenderer.renderOverlay(x);
                  }
                }
              } else if (idx === undefined) {
                if (vizRenderer) vizRenderer.renderOverlay();
                if (routeGraphRenderer && liveState.vizSettings.routeGraphVisible) routeGraphRenderer.renderOverlay();
              }
            },
          },
        );
      }

      // Map controls
      if (mapControlsContainer) {
        renderMapControls(mapControlsContainer, state.vizSettings, {
          onColorMetricChange: (id) => store.getState().setMapColorMetric(id),
          onWidthMetricChange: (id) => store.getState().setMapWidthMetric(id),
          onFrontsToggle: (visible) => store.getState().setMapFrontsVisible(visible),
        }, data.fronts != null);
      }

      // Legend
      if (mapLegendContainer) {
        renderMapLegend(mapLegendContainer, colorMetric, widthMetric);
      }

      // Altitude slider
      if (mapSliderContainer) {
        if (isAltDependent) {
          mapSliderContainer.style.display = '';
          renderAltitudeSlider(mapSliderContainer, altFt, data.flightCeilingFt, {
            onChange: (ft) => store.getState().setMapAltitude(ft),
          });
        } else {
          mapSliderContainer.style.display = 'none';
        }
      }

      // Ensure map size is correct after layout change
      requestAnimationFrame(() => {
        if (mapRenderer) mapRenderer.invalidateSize();
      });
    } else {
      // Map not visible — destroy
      if (mapInteraction) { mapInteraction.destroy(); mapInteraction = null; }
      if (mapRenderer) { mapRenderer.destroy(); mapRenderer = null; }
    }

    // Render cross-section controls (above canvas) — skip in compare mode (rendered above)
    if (!showCompare) {
      renderVizControls(controlsContainer, state.vizSettings, {
        onLayerToggle: (layerId) => store.getState().toggleVizLayer(layerId),
        onLayoutChange: (l) => store.getState().setLayout(l),
        onModelChange: (model) => store.getState().setSelectedModel(model),
        onThemeChange: (themeId) => store.getState().setVizTheme(themeId),
        onPresetChange: (presetId) => handlePresetChange(presetId),
        onCloudStyleChange: (style) => store.getState().setCloudStyle(style),
      }, state.selectedModel, availableModels.length > 0 ? availableModels : undefined, state.displayMode, preferredMethods, unavailable, substitutedLayers);

      // Render route graph controls (below graph)
      if (routeGraphControlsContainer && showCrossSection) {
        renderRouteGraphControls(routeGraphControlsContainer, state.vizSettings, {
          onRouteGraphToggle: (visible) => store.getState().setRouteGraphVisible(visible),
          onRouteGraphMetricChange: (axis, metricId) => store.getState().setRouteGraphMetric(axis, metricId),
        });
      }
    }
  }

  function updateVizOverlay(state: BriefingState): void {
    if (vizRenderer && state.routeAnalyses) {
      vizRenderer.setSelectedPointIndex(state.selectedPointIndex ?? -1);
    }
    if (compareRenderer && state.routeAnalyses) {
      compareRenderer.setSelectedPointIndex(state.selectedPointIndex ?? -1);
    }
    if (routeGraphRenderer && state.routeAnalyses && state.vizSettings.routeGraphVisible) {
      routeGraphRenderer.setSelectedPointIndex(state.selectedPointIndex ?? -1);
    }
    if (mapRenderer && state.routeAnalyses) {
      mapRenderer.setSelectedPointIndex(state.selectedPointIndex ?? -1);
    }
  }

  // --- Shared handlers for sharing/subscription controls (toolbar + shared-by line). ---
  const sharingHandlers = {
    onSubscribe: () => void store.getState().subscribe(),
    onUnsubscribe: () => void store.getState().unsubscribe(),
    onCopyShareLink: async () => {
      const { flight, currentPack, packs } = store.getState();
      if (!flight) return;
      // Pin the share link to ``currentPack`` only when the user is
      // looking at a non-latest (historical) pack — otherwise the
      // recipient should just see the freshest briefing on open. packs
      // is sorted newest-first by the store, so packs[0] is the latest.
      const latestTs = packs[0]?.fetch_timestamp ?? null;
      const pinTs =
        currentPack && currentPack.fetch_timestamp !== latestTs
          ? currentPack.fetch_timestamp
          : null;
      const copied = await copyFlightShareLink(flight.id, pinTs, flight.share_code);
      if (!copied) return;  // fell back to prompt(); skip the toolbar flash
      const btn = document.getElementById('share-btn') as HTMLButtonElement | null;
      if (btn) {
        const original = btn.title;
        btn.title = t('flightDetail.copyShareLinkCopied');
        btn.classList.add('btn-icon-flash');
        // Re-query in the timeout: renderBriefingSharing may have replaced
        // the button via cloneNode between the click and now, in which case
        // `btn` points to a detached element and the flash would stick on
        // the new clone. Re-querying gets the live element.
        setTimeout(() => {
          const live = document.getElementById('share-btn') as HTMLButtonElement | null;
          if (!live) return;
          live.title = original;
          live.classList.remove('btn-icon-flash');
        }, 1500);
      }
    },
  };

  // --- Stale-pack banner refresh handler (shared by the subscriber and
  // the post-init render so future changes to refresh routing apply
  // in one place). Reads live store state on click so it remains
  // correct even if the flight is reloaded after the banner renders.
  const stalePackOnRefresh = () => {
    const f = store.getState().flight;
    if (!f) return;
    if (isFlightPast(f.target_date, f.target_time_utc, f.flight_duration_hours)) {
      showHistoricalRefreshModal(f);
    } else {
      store.getState().refresh();
    }
  };

  // Altitude-only stale-pack action (#259): re-evaluate advisories at the new
  // planned altitude via the cheap recalc path — no full pipeline refresh, and
  // the flight's saved cruise altitude is never written from here.
  const stalePackOnReanchor = (alt: number) => {
    void store.getState().reanchorAdvisories(alt);
  };

  // --- Subscribe to state changes ---
  store.subscribe((state, prev) => {
    // Resolve 'auto' units against this flight's region (US flights → US units)
    // before any sub-render reads getUnitsRegion(). No-op for a forced pref.
    if (state.snapshot !== prev.snapshot && state.snapshot) {
      setFlightRegion(regionFromIcaos(state.snapshot.route.waypoints.map(w => w.icao)));
    }
    if (state.flight !== prev.flight || state.snapshot !== prev.snapshot) {
      ui.renderHeader(state.flight, state.snapshot);
    }
    if (
      state.flight !== prev.flight
      || state.routeAnalyses !== prev.routeAnalyses
      || state.routeAdvisories !== prev.routeAdvisories
    ) {
      const isOwner = !!user && state.flight?.user_id === user.id;
      ui.renderStalePackBanner(state.flight, state.routeAnalyses, isOwner, stalePackOnRefresh, stalePackOnReanchor, state.routeAdvisories?.cruise_altitude_ft ?? null);
    }
    if (state.flight !== prev.flight) {
      ui.renderBriefingSharing(state.flight, sharingHandlers);
    }
    if (state.packs !== prev.packs || state.currentPack !== prev.currentPack) {
      ui.renderHistoryDropdown(
        state.packs,
        state.currentPack?.fetch_timestamp || null,
        (ts) => store.getState().selectPack(ts),
      );
    }
    // Re-attach analytics context whenever the selected pack changes —
    // history-dropdown switches and refresh-completed both land here.
    if (state.currentPack !== prev.currentPack && state.flight && state.currentPack) {
      setBriefingContext(state.flight.id, state.currentPack.fetch_timestamp);
      // A new pack timestamp (i.e. one not previously in state.packs) means
      // a refresh just completed. Emit briefing.refreshed once for it.
      // Guard on prev.packs being non-empty: on initial page load it is
      // always empty so ``some()`` returns false and we'd fire refreshed
      // for every first visit, inflating the count 1:1 with briefing.opened.
      const wasNew =
        prev.packs.length > 0 &&
        !prev.packs.some(
          (p: { fetch_timestamp: string }) => p.fetch_timestamp === state.currentPack!.fetch_timestamp,
        );
      if (wasNew) {
        track(EVENTS.BRIEFING_REFRESHED);
      }
    }
    if (
      state.currentPack !== prev.currentPack ||
      state.snapshot !== prev.snapshot ||
      state.digest !== prev.digest ||
      state.digestPending !== prev.digestPending ||
      state.routeAnalyses !== prev.routeAnalyses ||
      state.routeAdvisories !== prev.routeAdvisories ||
      state.altAdvisories !== prev.altAdvisories ||
      state.timeOptions !== prev.timeOptions ||
      state.showingAlt !== prev.showingAlt ||
      state.elevationProfile !== prev.elevationProfile ||
      state.advisoryAltitudeOverride !== prev.advisoryAltitudeOverride ||
      state.windOverlay !== prev.windOverlay
    ) {
      ui.renderAssessment(state.currentPack, state.flight, state.routeAdvisories, state.altAdvisories, state.digestPending, () => store.getState().generateDigest());
      renderAdvisories(getEffectiveAdvisories(state), () => store.getState().recalculateAdvisories(), state.displayMode, getAltitudeOverrideConfig(state), handleAltitudeTable, getAltTimeToggleConfig(state), getProfileSelectorConfig(state), handleAdvisoryChip);
      renderTimeOptionsPanel(state);
      ui.renderRefreshDelta(state.snapshot);
      ui.renderRouteSigmets(state.snapshot);
      ui.renderRouteObservations(state.snapshot, () => store.getState().refreshObservations());
      ui.renderRouteAlternates(state.snapshot);
      ui.renderSynopsis(state.flight, state.currentPack, state.digest, state.displayMode, state.digestPending);
      ui.renderDwdCharts(state.flight, state.currentPack, user.is_admin || !!state.currentPack?.metoffice_charts_public);
      ui.renderDWDOverview(state.flight, state.currentPack, user.is_admin);
      ui.renderGramet(state.flight, state.currentPack);
      renderPointSections(state);
      renderVisualization(state);
      ui.updateWindyLink(state.routeAnalyses, state.selectedPointIndex, state.selectedModel);
    }
    if (
      state.freshness !== prev.freshness ||
      state.freshnessLoading !== prev.freshnessLoading ||
      state.currentPack !== prev.currentPack ||
      state.refreshing !== prev.refreshing ||
      state.refreshStatus !== prev.refreshStatus ||
      state.refreshStage !== prev.refreshStage ||
      state.refreshDetail !== prev.refreshDetail ||
      state.refreshElapsed !== prev.refreshElapsed ||
      state.avgRefreshSeconds !== prev.avgRefreshSeconds ||
      state.notifyEmail !== prev.notifyEmail
    ) {
      ui.renderFreshnessBar(
        state.freshness,
        state.freshnessLoading,
        state.currentPack,
        user.is_admin,
        state.refreshing,
        state.refreshStatus,
        state.refreshStage,
        state.refreshDetail,
        () => store.getState().forceRefresh(),
        () => store.getState().checkFreshness(),
        state.refreshElapsed,
        state.avgRefreshSeconds,
        state.notifyEmail,
        (checked: boolean) => store.getState().setNotifyEmail(checked),
      );
    }
    if (state.selectedPointIndex !== prev.selectedPointIndex) {
      renderPointSections(state);
      updateVizOverlay(state);
    }
    if (state.displayMode !== prev.displayMode || state.tierVisibility !== prev.tierVisibility) {
      applyDisplayModeClass(state.displayMode);
      updateToggleButtons(state.displayMode);
      renderPointSections(state);
      if (state.displayMode !== prev.displayMode) {
        renderAdvisories(getEffectiveAdvisories(state), () => store.getState().recalculateAdvisories(), state.displayMode, getAltitudeOverrideConfig(state), handleAltitudeTable, getAltTimeToggleConfig(state), getProfileSelectorConfig(state), handleAdvisoryChip);
        ui.renderSynopsis(state.flight, state.currentPack, state.digest, state.displayMode, state.digestPending);
        // Entering compact: enforce preferred-only layers for clouds/icing
        // (triggers vizSettings change → renderVisualization runs via that subscriber).
        // Runs even with empty preferredMethods — getCompactLayerOverrides falls
        // back to each group's defaultEnabled layer so we never strand non-preferred
        // layers enabled while their checkbox is hidden.
        if (state.displayMode === 'compact') {
          store.getState().setLayersBatch(getCompactLayerOverrides(preferredMethods));
        } else {
          renderVisualization(state);
        }
      }
    }
    if (state.selectedModel !== prev.selectedModel) {
      renderPointSections(state);
      renderVisualization(state);
    }
    if (state.vizSettings !== prev.vizSettings) {
      renderVisualization(state);
      // Apply a newly-selected lens (or deep-link) to the Skew-T too, so the
      // preset is coherent across cross-section + Skew-T (#308).
      if (
        state.vizSettings.activePreset !== prev.vizSettings.activePreset ||
        state.vizSettings.skewtOverlays !== prev.vizSettings.skewtOverlays ||
        state.vizSettings.skewtPrimaryVar !== prev.vizSettings.skewtPrimaryVar
      ) {
        applySkewtPresetState();
      }
      ui.updateWindyLink(state.routeAnalyses, state.selectedPointIndex, state.selectedModel);
      renderPointSections(state);
      // Analytics: emit at most once per briefing on transition into
      // map / compare / split. The user can toggle between layouts
      // freely; counting every toggle would inflate engagement.
      if (state.vizSettings.layout !== prev.vizSettings.layout) {
        const layout = state.vizSettings.layout;
        if (layout === 'map' || layout === 'split') {
          trackOncePerBriefing(EVENTS.FORECAST_MAP_OPENED, { layout });
        } else if (layout === 'compare') {
          trackOncePerBriefing(EVENTS.COMPARE_OPENED);
        }
      }
    }
    if (state.loading !== prev.loading) {
      ui.renderLoading(state.loading);
    }
    if (state.refreshing !== prev.refreshing) {
      ui.renderRefreshing(state.refreshing);
    }
    if (state.emailing !== prev.emailing) {
      ui.renderEmailing(state.emailing);
    }
    if (state.error !== prev.error) {
      ui.renderError(state.error);
    }
    // Offer the tour only once the briefing stream is fully done so all
    // tour targets are rendered. maybeOfferTour short-circuits if the user
    // already accepted or dismissed it.
    if (
      state.currentPack !== prev.currentPack ||
      state.refreshing !== prev.refreshing ||
      state.digestPending !== prev.digestPending
    ) {
      maybeOfferTour({
        currentPack: state.currentPack,
        refreshing: state.refreshing,
        digestPending: state.digestPending,
      });
    }
  });

  // --- Wire refresh button (owner-only) ---
  const refreshBtn = document.getElementById('refresh-btn') as HTMLButtonElement;
  if (refreshBtn) {
    refreshBtn.style.display = 'none'; // hidden until flight loads
    refreshBtn.addEventListener('click', () => {
      const { flight } = store.getState();
      if (!flight) return;
      track(EVENTS.BRIEFING_REFRESH_REQUESTED);
      if (isFlightPast(flight.target_date, flight.target_time_utc, flight.flight_duration_hours)) {
        showHistoricalRefreshModal(flight);
      } else {
        store.getState().refresh();
      }
    });
  }

  function showHistoricalRefreshModal(flight: { target_date: string; id: string }): void {
    // Remove existing modal if any
    document.getElementById('historical-modal')?.remove();

    const overlay = document.createElement('div');
    overlay.id = 'historical-modal';
    overlay.className = 'feedback-modal-overlay';
    overlay.innerHTML = `
      <div class="feedback-modal">
        <h3>${t('historical.title')}</h3>
        <p class="muted" style="margin:0 0 0.75rem;">${t('historical.subtitle')}</p>
        <label for="historical-as-of-date" style="font-weight:500;font-size:0.85rem;">${t('historical.asOfDate')}</label>
        <input type="date" id="historical-as-of-date"
          value="${flight.target_date}"
          max="${flight.target_date}"
          style="width:100%;padding:0.4rem;margin:0.25rem 0 0.75rem;border:1px solid var(--border);border-radius:4px;font-family:inherit;background:var(--surface);color:var(--text);" />
        <div style="display:flex;gap:0.5rem;justify-content:flex-end;margin-top:0.75rem;">
          <button class="btn" id="historical-cancel">${t('historical.cancel')}</button>
          <button class="btn btn-primary" id="historical-refresh">${t('historical.refresh')}</button>
        </div>
      </div>`;

    document.body.appendChild(overlay);

    const dateInput = document.getElementById('historical-as-of-date') as HTMLInputElement;

    function dismiss(): void {
      overlay.remove();
    }

    document.getElementById('historical-cancel')!.addEventListener('click', dismiss);
    overlay.addEventListener('click', (e) => { if (e.target === overlay) dismiss(); });

    function onEsc(e: KeyboardEvent): void {
      if (e.key === 'Escape') { dismiss(); document.removeEventListener('keydown', onEsc); }
    }
    document.addEventListener('keydown', onEsc);

    document.getElementById('historical-refresh')!.addEventListener('click', () => {
      const asOfDate = dateInput.value;
      dismiss();
      store.getState().refresh(asOfDate);
    });

    dateInput.focus();
  }

  // --- Wire download menu (PDF report + FlightExchange export) ---
  const pdfBtn = document.getElementById('pdf-btn') as HTMLButtonElement;
  const downloadMenu = document.getElementById('download-menu');
  const downloadMenuList = document.getElementById('download-menu-list');
  if (pdfBtn && downloadMenu && downloadMenuList) {
    // Localize menu labels (HTML carries English defaults).
    const pdfItem = document.getElementById('download-pdf-item');
    const exchangeItem = document.getElementById('download-exchange-item');
    if (pdfItem) pdfItem.textContent = t('download.pdf');
    if (exchangeItem) exchangeItem.textContent = t('download.flightExchange');
    pdfBtn.setAttribute('aria-label', t('download.menu'));
    pdfBtn.setAttribute('title', t('download.menu'));

    function closeDownloadMenu(): void {
      downloadMenuList!.hidden = true;
      pdfBtn!.setAttribute('aria-expanded', 'false');
      document.removeEventListener('click', onOutsideClick);
    }
    function onOutsideClick(e: MouseEvent): void {
      if (!downloadMenu!.contains(e.target as Node)) closeDownloadMenu();
    }

    pdfBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      const open = downloadMenuList.hidden;
      if (open) {
        downloadMenuList.hidden = false;
        pdfBtn.setAttribute('aria-expanded', 'true');
        // Defer so this same click doesn't immediately close the menu.
        setTimeout(() => document.addEventListener('click', onOutsideClick), 0);
      } else {
        closeDownloadMenu();
      }
    });

    pdfItem?.addEventListener('click', () => {
      closeDownloadMenu();
      const { flight, currentPack } = store.getState();
      if (flight && currentPack) {
        window.open(
          api.reportPdfUrl(flight.id, currentPack.fetch_timestamp),
          '_blank',
        );
      }
    });

    exchangeItem?.addEventListener('click', async () => {
      closeDownloadMenu();
      const { flight } = store.getState();
      if (!flight) return;
      try {
        const exchange = await api.fetchFlightExchange(flight.id);
        const slug = [flight.waypoints[0], flight.waypoints[flight.waypoints.length - 1]]
          .filter(Boolean)
          .join('-')
          .toLowerCase() || 'flight';
        downloadJsonFile(
          exchange,
          `flightexchange_${slug}_${flight.target_date}.json`,
        );
      } catch (err) {
        ui.renderError(
          err instanceof Error ? err.message : t('download.exchangeFailed'),
        );
      }
    });
  }

  /** Trigger a client-side download of a JSON object as a file. */
  function downloadJsonFile(data: unknown, filename: string): void {
    const blob = new Blob([JSON.stringify(data, null, 2)], {
      type: 'application/json',
    });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    URL.revokeObjectURL(url);
  }

  // --- Wire email button ---
  const emailBtn = document.getElementById('email-btn') as HTMLButtonElement;
  if (emailBtn) {
    emailBtn.addEventListener('click', () => {
      store.getState().sendEmail();
    });
  }

  // --- Wire feedback button + modal ---
  const feedbackBtn = document.getElementById('feedback-btn') as HTMLButtonElement;
  if (feedbackBtn) {
    feedbackBtn.addEventListener('click', () => {
      const { flight, currentPack } = store.getState();
      if (!flight) return;
      showFeedbackModal(flight.id, currentPack?.fetch_timestamp ?? '');
    });
  }

  function showFeedbackModal(flightId: string, packTimestamp: string): void {
    // Remove existing modal if any
    document.getElementById('feedback-modal')?.remove();

    const categories = [
      ['data_issue', t('feedback.cat.dataIssue')],
      ['too_conservative', t('feedback.cat.tooConservative')],
      ['too_optimistic', t('feedback.cat.tooOptimistic')],
      ['incorrect_interpretation', t('feedback.cat.incorrectInterpretation')],
      ['other', t('feedback.cat.other')],
    ];
    const optionsHtml = categories
      .map(([val, label]) => `<option value="${val}">${label}</option>`)
      .join('');

    const overlay = document.createElement('div');
    overlay.id = 'feedback-modal';
    overlay.className = 'feedback-modal-overlay';
    overlay.innerHTML = `
      <div class="feedback-modal">
        <h3>${t('feedback.title')}</h3>
        <p class="muted" style="margin:0 0 0.75rem;">${t('feedback.subtitle')}</p>
        <label for="feedback-category" style="font-weight:500;font-size:0.85rem;">${t('feedback.categoryLabel')}</label>
        <select id="feedback-category" style="width:100%;padding:0.4rem;margin:0.25rem 0 0.75rem;border:1px solid var(--border);border-radius:4px;">
          ${optionsHtml}
        </select>
        <label for="feedback-comment" style="font-weight:500;font-size:0.85rem;">${t('feedback.commentLabel')}</label>
        <textarea id="feedback-comment" rows="4" style="width:100%;padding:0.4rem;margin:0.25rem 0 0;border:1px solid var(--border);border-radius:4px;resize:vertical;font-family:inherit;" placeholder="${t('feedback.commentPlaceholder')}"></textarea>
        <label style="display:flex;align-items:center;gap:0.4rem;font-size:0.85rem;margin-top:0.5rem;cursor:pointer;">
          <input type="checkbox" id="feedback-contact-ok" checked> ${t('feedback.contactOk')}
        </label>
        <div id="feedback-error" style="color:#dc3545;font-size:0.8rem;min-height:1.2em;margin-top:0.25rem;"></div>
        <div style="display:flex;gap:0.5rem;justify-content:flex-end;margin-top:0.75rem;">
          <button class="btn" id="feedback-cancel">${t('feedback.cancel')}</button>
          <button class="btn btn-primary" id="feedback-submit">${t('feedback.submit')}</button>
        </div>
      </div>`;

    document.body.appendChild(overlay);

    const commentEl = document.getElementById('feedback-comment') as HTMLTextAreaElement;
    const categoryEl = document.getElementById('feedback-category') as HTMLSelectElement;
    const errorEl = document.getElementById('feedback-error')!;
    const submitBtn = document.getElementById('feedback-submit') as HTMLButtonElement;

    function dismiss(): void {
      overlay.remove();
    }

    document.getElementById('feedback-cancel')!.addEventListener('click', dismiss);
    overlay.addEventListener('click', (e) => { if (e.target === overlay) dismiss(); });

    function onEsc(e: KeyboardEvent): void {
      if (e.key === 'Escape') { dismiss(); document.removeEventListener('keydown', onEsc); }
    }
    document.addEventListener('keydown', onEsc);

    submitBtn.addEventListener('click', async () => {
      const comment = commentEl.value.trim();
      if (!comment) {
        errorEl.textContent = t('feedback.errorEmpty');
        return;
      }
      errorEl.textContent = '';
      submitBtn.disabled = true;
      submitBtn.textContent = t('feedback.submitting');

      try {
        const contactOk = (document.getElementById('feedback-contact-ok') as HTMLInputElement | null)?.checked ?? false;
        await api.submitFeedback({
          flight_id: flightId,
          pack_timestamp: packTimestamp,
          category: categoryEl.value,
          comment,
          target: 'general',
          contact_ok: contactOk,
        });
        // Show success state
        const modal = overlay.querySelector('.feedback-modal')!;
        modal.innerHTML = `
          <h3>${t('feedback.thanks')}</h3>
          <p class="muted">${t('feedback.submitted')}</p>`;
        setTimeout(dismiss, 1500);
      } catch (err) {
        errorEl.textContent = t('feedback.failedSubmit', { error: String(err) });
        submitBtn.disabled = false;
        submitBtn.textContent = t('feedback.submit');
      }
    });

    // Focus textarea
    commentEl.focus();
  }

  // Model selector is now in the cross-section controls panel (viz-model-select)

  // --- Wire display mode toggle ---
  const toggleContainer = document.getElementById('display-mode-toggle');
  if (toggleContainer) {
    toggleContainer.addEventListener('click', (e) => {
      const btn = (e.target as HTMLElement).closest('.btn-toggle') as HTMLElement | null;
      if (btn && btn.dataset.mode) {
        const from = store.getState().displayMode;
        const to = btn.dataset.mode as DisplayMode;
        if (from !== to) {
          track(EVENTS.DISPLAY_MODE_CHANGED, { from, to });
        }
        store.getState().setDisplayMode(to);
      }
    });
  }

  // --- Wire tier toggle buttons (delegated) ---
  document.addEventListener('click', (e) => {
    const btn = (e.target as HTMLElement).closest('.tier-toggle-btn') as HTMLElement | null;
    if (btn && btn.dataset.tier) {
      store.getState().toggleTier(btn.dataset.tier as 'key' | 'useful' | 'advanced');
    }
  });

  // --- Wire collapsible sections ---
  function loadCollapsedSections(): Set<string> {
    try {
      const v = localStorage.getItem('wb_collapsedSections');
      if (v) return new Set(JSON.parse(v));
    } catch { /* ignore */ }
    return new Set();
  }

  function saveCollapsedSections(collapsed: Set<string>): void {
    try { localStorage.setItem('wb_collapsedSections', JSON.stringify([...collapsed])); } catch { /* ignore */ }
  }

  const collapsedSections = loadCollapsedSections();
  // Apply persisted collapsed state
  document.querySelectorAll('.section.collapsible[data-section]').forEach((el) => {
    const key = (el as HTMLElement).dataset.section!;
    if (collapsedSections.has(key)) {
      el.classList.add('collapsed');
    }
  });

  document.addEventListener('click', (e) => {
    const h3 = (e.target as HTMLElement).closest('.section.collapsible > h3');
    if (!h3) return;
    const section = h3.parentElement as HTMLElement;
    const key = section.dataset.section;
    section.classList.toggle('collapsed');
    if (key) {
      if (section.classList.contains('collapsed')) {
        collapsedSections.add(key);
      } else {
        collapsedSections.delete(key);
      }
      saveCollapsedSections(collapsedSections);
    }
    // Re-render viz canvases if cross-section was just expanded (canvas needs size)
    if (key === 'cross-section' && !section.classList.contains('collapsed')) {
      if (vizRenderer) vizRenderer.render();
      if (compareRenderer) compareRenderer.render();
      if (routeGraphRenderer && store.getState().vizSettings.routeGraphVisible) routeGraphRenderer.render();
      if (mapRenderer) {
        mapRenderer.invalidateSize();
        mapRenderer.render();
      }
    }
  });

  // --- Optional sidebar layout (opt-in, reversible) ---
  // Reparents header/assessment/advisories/freshness into a rail and adds a
  // scroll-spy section nav + focus mode. No-op in the default classic layout.
  initBriefingLayout();

  // --- Wire image lightbox ---
  const lightbox = document.getElementById('lightbox');
  const lightboxImg = document.getElementById('lightbox-img') as HTMLImageElement;
  if (lightbox && lightboxImg) {
    document.addEventListener('click', (e) => {
      const target = e.target as HTMLElement;
      if (target.classList.contains('skewt-img')) {
        lightboxImg.src = (target as HTMLImageElement).src;
        lightbox.classList.add('active');
      }
    });
    lightbox.addEventListener('click', () => {
      lightbox.classList.remove('active');
      lightboxImg.src = '';
    });
  }

  // --- Load flight data, then render even if no packs exist ---
  store.getState().loadFlight(flightId).then(async () => {
    // If a specific pack timestamp was requested via URL, select it
    if (packTimestamp) {
      const s = store.getState();
      const matchingPack = s.packs.find(p => p.fetch_timestamp === packTimestamp);
      if (matchingPack && s.currentPack?.fetch_timestamp !== packTimestamp) {
        await store.getState().selectPack(packTimestamp);
      }
    }
  }).then(() => {
    const s = store.getState();
    ui.renderHeader(s.flight, s.snapshot);
    {
      const isOwner = !!user && s.flight?.user_id === user.id;
      ui.renderStalePackBanner(s.flight, s.routeAnalyses, isOwner, stalePackOnRefresh, stalePackOnReanchor, s.routeAdvisories?.cruise_altitude_ft ?? null);
    }
    // renderBriefingSharing already ran via the store subscriber above when
    // flight was set; don't re-invoke (it would waste a clone+replace cycle).
    ui.renderHistoryDropdown(s.packs, s.currentPack?.fetch_timestamp || null, (ts) => store.getState().selectPack(ts));
    // Wire analytics context + emit briefing.opened once the pack is loaded.
    if (s.flight && s.currentPack) {
      setBriefingContext(s.flight.id, s.currentPack.fetch_timestamp);
      track(EVENTS.BRIEFING_OPENED, { days_out: s.currentPack.days_out });
      // Snapshot the cross-section display config as it is right now — the
      // store is hydrated from localStorage and any preset already resolved,
      // so this captures the persisted steady state even for users who never
      // change a setting. One event per view; navigating to another briefing
      // is a fresh page load and re-emits.
      track(EVENTS.XSECTION_VIEWED, buildXsectionSnapshotProps(s));
    }
    ui.renderAssessment(s.currentPack, s.flight, s.routeAdvisories, s.altAdvisories, s.digestPending, () => store.getState().generateDigest());
    renderAdvisories(getEffectiveAdvisories(s), () => store.getState().recalculateAdvisories(), s.displayMode, getAltitudeOverrideConfig(s), handleAltitudeTable, getAltTimeToggleConfig(s), getProfileSelectorConfig(s), handleAdvisoryChip);
    renderTimeOptionsPanel(s);
    ui.renderRefreshDelta(s.snapshot);
    ui.renderRouteSigmets(s.snapshot);
    ui.renderRouteObservations(s.snapshot, () => store.getState().refreshObservations());
    ui.renderRouteAlternates(s.snapshot);
    ui.renderSynopsis(s.flight, s.currentPack, s.digest, s.displayMode, s.digestPending);
    ui.renderDwdCharts(s.flight, s.currentPack, user.is_admin || !!s.currentPack?.metoffice_charts_public);
    ui.renderDWDOverview(s.flight, s.currentPack, user.is_admin);
    ui.renderGramet(s.flight, s.currentPack);
    renderPointSections(s);
    renderVisualization(s);
    ui.renderLoading(s.loading);

    // Deep-link (#308): now that routeAnalyses is loaded, honor any
    // ?point/model/view/preset/advisory params from an MCP or shared link.
    applyDeepLink();

    // Load PIREPs for this flight (fire-and-forget, non-blocking)
    if (s.flight) {
      loadFlightPireps(s.flight.id);
    }

    // Refresh button visibility is handled by renderBriefingSharing above
    // (role-based). Here we only set the disabled/title state for past flights
    // (admins can still refresh past flights for historical briefings).
    const past = s.flight
      ? isFlightPast(s.flight.target_date, s.flight.target_time_utc, s.flight.flight_duration_hours)
      : false;
    if (refreshBtn && s.flight?.user_id === user.id && past) {
      refreshBtn.disabled = !user.is_admin;
      refreshBtn.title = user.is_admin
        ? t('briefing.historicalRefreshTitle')
        : t('briefing.flightPastTitle');
    }

    // Render privacy toggle
    ui.renderPrivacyToggle(s.flight, user.id, async (isPrivate) => {
      if (!s.flight) return;
      try {
        const updated = await api.updatePrivacy(s.flight.id, isPrivate);
        store.getState().updateFlightPrivacy(updated.private);
      } catch (err) {
        ui.renderError(t('privacy.failedUpdate', { error: String(err) }));
      }
    });

    // Render auto-refresh toggle
    ui.renderAutoRefreshBar(s.flight, user.id, past, async (autoRefresh, hour) => {
      if (!s.flight) return;
      const wasEnabled = s.flight.auto_refresh;
      try {
        const updated = await api.updateAutoRefresh(s.flight.id, {
          auto_refresh: autoRefresh,
          auto_refresh_hour: hour,
        });
        // Update the flight in store with new auto-refresh fields
        store.getState().updateFlightAutoRefresh(updated.auto_refresh, updated.auto_refresh_hour);
        if (autoRefresh !== wasEnabled) {
          track(autoRefresh ? EVENTS.AUTO_REFRESH_ENABLED : EVENTS.AUTO_REFRESH_DISABLED);
        }
      } catch (err) {
        ui.renderError(t('autoRefresh.failedUpdate', { error: String(err) }));
      }
    });

    // Auto-refresh on first visit (no packs yet), otherwise check freshness.
    // Subscribers can't trigger refreshes — the owner_id check already gates this.
    if (s.packs.length === 0 && s.flight?.user_id === user.id && !past) {
      store.getState().refresh();
    } else if (s.packs.length > 0) {
      store.getState().checkFreshness();
      // Pick up scheduler or other-tab refreshes
      if (!s.refreshing) {
        store.getState().checkActiveRefresh();
      }
    }

    document.getElementById('tour-btn')?.addEventListener('click', () => startBriefingTour());
    maybeAutoStartBriefingTour();
    // The tour offer is wired into the store subscription so it only fires
    // once the briefing stream is fully complete (currentPack rendered,
    // refresh finished, digest done) — see the subscribe block above.
  });
}

// Boot
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
