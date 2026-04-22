/** Briefing page entry point — wires store, UI manager, and event handlers. */

import { fetchCurrentUser } from './adapters/auth-adapter';
import { briefingStore, type BriefingState } from './store/briefing-store';
import * as api from './adapters/api-adapter';
import * as ui from './managers/briefing-ui';
import { fetchPirepsByFlight } from './adapters/pirep-adapter';
import { renderPirepList } from './managers/pirep-ui';
import { renderAdvisories, renderAltitudeTablePopup, type AltitudeOverrideConfig, type AltTimeToggleConfig, type ProfileSelectorConfig } from './managers/advisories-ui';
import { fetchProfiles, type ProfileResponse } from './adapters/profiles-adapter';
import type { DisplayMode } from './types/metrics';
import { copyFlightShareLink, renderUserInfo, initModelCatalog, isFlightPast, formatDepartureTime } from './utils';
import { initInfoPopup, showMetricInfo, showPopupContent } from './components/info-popup';
import { CrossSectionRenderer } from './visualization/cross-section/renderer';
import { extractVizData, getUnavailableLayers } from './visualization/data-extract';
import { getAllLayers, getCompactLayerOverrides } from './visualization/cross-section/layer-registry';
import { renderVizControls, renderRouteGraphControls, renderMapControls, renderCompareControls } from './visualization/controls/panel';
import { attachInteraction, type InteractionHandle } from './visualization/cross-section/interaction';
import { CompareSectionRenderer, type CompareModelData } from './visualization/cross-section/compare-renderer';
import { attachCompareInteraction, type CompareInteractionHandle } from './visualization/cross-section/compare-interaction';
import { getComparableLayer } from './visualization/cross-section/compare-layers';
import { RouteGraphRenderer } from './visualization/route-graph/renderer';
import { getMetricById, METRIC_NONE } from './visualization/route-graph/metrics';
import { attachRouteGraphInteraction, type RouteGraphInteractionHandle } from './visualization/route-graph/interaction';
import { RouteMapRenderer } from './visualization/route-map/renderer';
import { getMapMetricById, MAP_METRIC_NONE } from './visualization/route-map/metrics';
import { attachMapInteraction, type MapInteractionHandle } from './visualization/route-map/interaction';
import { renderMapLegend } from './visualization/route-map/legend';
import { renderAltitudeSlider } from './visualization/route-map/altitude-slider';
import { initTheme } from './theme';
import { initI18n, t } from './i18n/i18n';
import { SkewTRenderer } from './visualization/skewt/renderer';
import { renderSkewtOverlayControls, renderSkewtCompareControls } from './visualization/skewt/overlay-controls';
import { attachSkewTInteraction, attachSkewTCompareInteraction, type SkewTInteractionHandle, type SkewTCompareInteractionHandle } from './visualization/skewt/interaction';
import { SkewTCompareRenderer, type CompareModelDataset as SkewtCompareModelDataset } from './visualization/skewt/compare-renderer';
import { getActiveTheme } from './visualization/cross-section/theme';


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
    window.location.href = '/login.html';
    return;
  }
  initTheme();
  renderUserInfo(user, 'briefing');

  // Load model catalog + preferred methods (non-blocking)
  let preferredMethods: Record<string, string> = {};
  import('./adapters/preferences-adapter').then(({ fetchModelCatalog, fetchPreferences }) => {
    fetchModelCatalog().then(initModelCatalog).catch(() => {});
    fetchPreferences().then((prefs) => {
      preferredMethods = { clouds: prefs.cloud_method, icing: prefs.icing_method, convection: prefs.convective_method };
      // Re-render controls so compact mode picks up the preferred methods
      renderVisualization(store.getState());
    }).catch(() => {});
  });

  // Initialize metric info popup
  initInfoPopup();
  initSkewtToggle();
  document.addEventListener('click', (e) => {
    const btn = (e.target as HTMLElement).closest('.metric-info-btn') as HTMLElement | null;
    if (btn && !btn.classList.contains('advisory-info-btn')) {
      e.preventDefault();
      showMetricInfo(btn.dataset.metric!, btn.dataset.value);
    }
  });

  const store = briefingStore;

  // Get flight ID and optional pack timestamp from URL
  const params = new URLSearchParams(window.location.search);
  const flightId = params.get('flight');
  const packTimestamp = params.get('pack');
  if (!flightId) {
    ui.renderError(t('briefing.noFlightSpecified'));
    return;
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
  function renderPointSections(state: BriefingState): void {
    // Show the Skew-T section wrapper once we have route data
    const skewtWrapper = document.querySelector('[data-section="skewt"]') as HTMLElement | null;
    if (skewtWrapper) skewtWrapper.style.display = state.routeAnalyses ? '' : 'none';
    ui.renderSoundingAnalysis(state.snapshot, state.routeAnalyses, state.selectedPointIndex, state.displayMode, state.tierVisibility, state.vizSettings.enabledLayers);
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
      onChange: (alt) => store.getState().setAdvisoryAltitudeOverride(alt === defaultAlt ? null : alt),
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
      renderAdvisories(getEffectiveAdvisories(s), () => store.getState().recalculateAdvisories(), s.displayMode, getAltitudeOverrideConfig(s), handleAltitudeTable, getAltTimeToggleConfig(s), getProfileSelectorConfig(s));
    }
  }).catch(err => console.error('Failed to fetch profiles:', err));

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
    return state.showingAlt && state.altAdvisories ? state.altAdvisories : state.routeAdvisories;
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

  function ensureSkewtRenderer(): SkewTRenderer {
    if (!skewtRenderer) {
      const container = document.getElementById('skewt-canvas-container');
      if (!container) throw new Error('skewt-canvas-container not found');
      skewtRenderer = new SkewTRenderer(container);
      // Render overlay toggle controls
      const controlsEl = document.getElementById('skewt-overlay-controls');
      if (controlsEl) renderSkewtOverlayControls(controlsEl, skewtRenderer);
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

    // Find the selected point — any route point works
    const idx = state.selectedPointIndex;
    if (idx == null) {
      ensureSkewtRenderer().clear();
      return;
    }
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

    const idx = state.selectedPointIndex;
    if (idx == null) {
      ensureSkewtCompareRenderer().clear();
      return;
    }
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

    const data = extractVizData(state.routeAnalyses, state.selectedModel, state.flight?.flight_ceiling_ft, state.elevationProfile);
    const unavailable = getUnavailableLayers(data);
    const allLayers = getAllLayers();
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
            data: extractVizData(state.routeAnalyses!, m, state.flight?.flight_ceiling_ft, state.elevationProfile),
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
        onPresetChange: (presetId) => store.getState().setVizPreset(presetId),
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
      // Merge unavailable layers as disabled so they don't render,
      // without modifying the stored user preference.
      const effectiveEnabled = { ...state.vizSettings.enabledLayers };
      for (const id of unavailable) effectiveEnabled[id] = false;
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
      mapRenderer.setSelectedPointIndex(state.selectedPointIndex ?? -1);
      mapRenderer.render();

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
        });
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
        onPresetChange: (presetId) => store.getState().setVizPreset(presetId),
      }, state.selectedModel, availableModels.length > 0 ? availableModels : undefined, state.displayMode, preferredMethods, unavailable);

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
      const flight = store.getState().flight;
      if (!flight) return;
      const copied = await copyFlightShareLink(flight.id);
      if (!copied) return;  // fell back to prompt(); skip the toolbar flash
      const btn = document.getElementById('share-btn') as HTMLButtonElement | null;
      if (btn) {
        const original = btn.title;
        btn.title = t('flightDetail.copyShareLinkCopied');
        btn.classList.add('btn-icon-flash');
        setTimeout(() => { btn.title = original; btn.classList.remove('btn-icon-flash'); }, 1500);
      }
    },
  };

  // --- Subscribe to state changes ---
  store.subscribe((state, prev) => {
    if (state.flight !== prev.flight || state.snapshot !== prev.snapshot) {
      ui.renderHeader(state.flight, state.snapshot);
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
    if (
      state.currentPack !== prev.currentPack ||
      state.snapshot !== prev.snapshot ||
      state.digest !== prev.digest ||
      state.routeAnalyses !== prev.routeAnalyses ||
      state.routeAdvisories !== prev.routeAdvisories ||
      state.altAdvisories !== prev.altAdvisories ||
      state.showingAlt !== prev.showingAlt ||
      state.elevationProfile !== prev.elevationProfile ||
      state.advisoryAltitudeOverride !== prev.advisoryAltitudeOverride
    ) {
      ui.renderAssessment(state.currentPack, state.flight);
      renderAdvisories(getEffectiveAdvisories(state), () => store.getState().recalculateAdvisories(), state.displayMode, getAltitudeOverrideConfig(state), handleAltitudeTable, getAltTimeToggleConfig(state), getProfileSelectorConfig(state));
      ui.renderRouteObservations(state.snapshot, () => store.getState().refreshObservations());
      ui.renderSynopsis(state.flight, state.currentPack, state.digest, state.displayMode);
      ui.renderDWDOverview(state.flight, state.currentPack);
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
        renderAdvisories(getEffectiveAdvisories(state), () => store.getState().recalculateAdvisories(), state.displayMode, getAltitudeOverrideConfig(state), handleAltitudeTable, getAltTimeToggleConfig(state), getProfileSelectorConfig(state));
        ui.renderSynopsis(state.flight, state.currentPack, state.digest, state.displayMode);
        // Entering compact: enforce preferred-only layers for clouds/icing
        // (triggers vizSettings change → renderVisualization runs via that subscriber)
        if (state.displayMode === 'compact' && Object.keys(preferredMethods).length > 0) {
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
      ui.updateWindyLink(state.routeAnalyses, state.selectedPointIndex, state.selectedModel);
      renderPointSections(state);
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
  });

  // --- Wire refresh button (owner-only) ---
  const refreshBtn = document.getElementById('refresh-btn') as HTMLButtonElement;
  if (refreshBtn) {
    refreshBtn.style.display = 'none'; // hidden until flight loads
    refreshBtn.addEventListener('click', () => {
      const { flight } = store.getState();
      if (!flight) return;
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

  // --- Wire PDF download button ---
  const pdfBtn = document.getElementById('pdf-btn') as HTMLButtonElement;
  if (pdfBtn) {
    pdfBtn.addEventListener('click', () => {
      const { flight, currentPack } = store.getState();
      if (flight && currentPack) {
        window.open(
          api.reportPdfUrl(flight.id, currentPack.fetch_timestamp),
          '_blank',
        );
      }
    });
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
        await api.submitFeedback({
          flight_id: flightId,
          pack_timestamp: packTimestamp,
          category: categoryEl.value,
          comment,
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
        store.getState().setDisplayMode(btn.dataset.mode as DisplayMode);
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
    ui.renderBriefingSharing(s.flight, sharingHandlers);
    ui.renderHistoryDropdown(s.packs, s.currentPack?.fetch_timestamp || null, (ts) => store.getState().selectPack(ts));
    ui.renderAssessment(s.currentPack, s.flight);
    renderAdvisories(getEffectiveAdvisories(s), () => store.getState().recalculateAdvisories(), s.displayMode, getAltitudeOverrideConfig(s), handleAltitudeTable, getAltTimeToggleConfig(s), getProfileSelectorConfig(s));
    ui.renderRouteObservations(s.snapshot, () => store.getState().refreshObservations());
    ui.renderSynopsis(s.flight, s.currentPack, s.digest, s.displayMode);
    ui.renderDWDOverview(s.flight, s.currentPack);
    ui.renderGramet(s.flight, s.currentPack);
    renderPointSections(s);
    renderVisualization(s);
    ui.renderLoading(s.loading);

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
      try {
        const updated = await api.updateAutoRefresh(s.flight.id, {
          auto_refresh: autoRefresh,
          auto_refresh_hour: hour,
        });
        // Update the flight in store with new auto-refresh fields
        store.getState().updateFlightAutoRefresh(updated.auto_refresh, updated.auto_refresh_hour);
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
  });
}

// Boot
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
