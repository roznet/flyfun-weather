/** Briefing page entry point — wires store, UI manager, and event handlers. */

import { fetchCurrentUser } from './adapters/auth-adapter';
import { briefingStore, type BriefingState } from './store/briefing-store';
import * as api from './adapters/api-adapter';
import * as ui from './managers/briefing-ui';
import { renderUserInfo } from './utils';
import { initInfoPopup, showMetricInfo } from './components/info-popup';
import { CrossSectionRenderer } from './visualization/cross-section/renderer';
import { extractVizData } from './visualization/data-extract';
import { getAllLayers } from './visualization/cross-section/layer-registry';
import { renderVizControls } from './visualization/controls/panel';
import { attachInteraction } from './visualization/cross-section/interaction';

async function init(): Promise<void> {
  // Auth check — redirect to login if not authenticated
  const user = await fetchCurrentUser();
  if (!user) {
    window.location.href = '/login.html';
    return;
  }
  renderUserInfo(user);

  // Initialize metric info popup
  initInfoPopup();
  document.addEventListener('click', (e) => {
    const btn = (e.target as HTMLElement).closest('.metric-info-btn') as HTMLElement | null;
    if (btn) {
      e.preventDefault();
      showMetricInfo(btn.dataset.metric!, btn.dataset.value);
    }
  });

  const store = briefingStore;

  // Get flight ID from URL
  const params = new URLSearchParams(window.location.search);
  const flightId = params.get('flight');
  if (!flightId) {
    ui.renderError('No flight specified. Go back to flights list.');
    return;
  }

  // --- Apply display mode CSS class ---
  function applyDisplayModeClass(mode: string): void {
    const container = document.querySelector('.container');
    if (container) {
      container.classList.remove('display-compact', 'display-annotated');
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

  // --- Helper to render slider-dependent sections ---
  function renderSliderSections(state: BriefingState): void {
    ui.renderRouteSlider(
      state.routeAnalyses,
      state.selectedPointIndex,
      (idx) => store.getState().setSelectedPoint(idx),
    );
    ui.renderSoundingAnalysis(state.snapshot, state.routeAnalyses, state.selectedPointIndex, state.displayMode, state.tierVisibility);
    ui.renderSkewTs(state.flight, state.currentPack, state.snapshot, state.selectedModel, state.routeAnalyses, state.selectedPointIndex);
    ui.renderModelComparison(state.snapshot, state.routeAnalyses, state.selectedPointIndex, state.displayMode, state.tierVisibility);
  }

  // Apply initial display mode
  applyDisplayModeClass(store.getState().displayMode);
  updateToggleButtons(store.getState().displayMode);

  // --- Cross-section visualization ---
  let vizRenderer: CrossSectionRenderer | null = null;
  let vizCleanupInteraction: (() => void) | null = null;

  function renderVisualization(state: BriefingState): void {
    const vizSection = document.getElementById('viz-section');
    const canvasContainer = document.getElementById('viz-canvas-container');
    const controlsContainer = document.getElementById('viz-controls');
    if (!vizSection || !canvasContainer || !controlsContainer) return;

    if (!state.routeAnalyses) {
      vizSection.style.display = 'none';
      return;
    }
    vizSection.style.display = '';

    const data = extractVizData(state.routeAnalyses, state.selectedModel, state.flight?.flight_ceiling_ft, state.elevationProfile);
    const allLayers = getAllLayers();

    // Create or update renderer
    if (!vizRenderer) {
      vizRenderer = new CrossSectionRenderer(canvasContainer);
    }

    vizRenderer.setData(data);
    vizRenderer.setLayers(allLayers, state.vizSettings.enabledLayers);
    vizRenderer.setRenderMode(state.vizSettings.renderMode);
    vizRenderer.setSelectedPointIndex(state.selectedPointIndex);
    vizRenderer.render();

    // Re-attach interaction
    if (vizCleanupInteraction) vizCleanupInteraction();
    vizCleanupInteraction = attachInteraction(vizRenderer, data, {
      onSelectPoint: (idx) => store.getState().setSelectedPoint(idx),
    });

    // Render controls
    renderVizControls(controlsContainer, state.vizSettings, {
      onRenderModeChange: (mode) => store.getState().setRenderMode(mode),
      onLayerToggle: (layerId) => store.getState().toggleVizLayer(layerId),
    }, state.selectedModel);
  }

  function updateVizOverlay(state: BriefingState): void {
    if (vizRenderer && state.routeAnalyses) {
      vizRenderer.setSelectedPointIndex(state.selectedPointIndex);
    }
  }

  // --- Subscribe to state changes ---
  store.subscribe((state, prev) => {
    if (state.flight !== prev.flight || state.snapshot !== prev.snapshot) {
      ui.renderHeader(state.flight, state.snapshot);
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
      state.elevationProfile !== prev.elevationProfile
    ) {
      ui.renderAssessment(state.currentPack);
      ui.renderSynopsis(state.flight, state.currentPack, state.digest);
      ui.renderGramet(state.flight, state.currentPack);
      renderSliderSections(state);
      renderVisualization(state);
    }
    if (
      state.freshness !== prev.freshness ||
      state.freshnessLoading !== prev.freshnessLoading ||
      state.currentPack !== prev.currentPack ||
      state.refreshing !== prev.refreshing ||
      state.refreshStage !== prev.refreshStage ||
      state.refreshDetail !== prev.refreshDetail
    ) {
      ui.renderFreshnessBar(
        state.freshness,
        state.freshnessLoading,
        state.currentPack,
        user.is_admin,
        state.refreshing,
        state.refreshStage,
        state.refreshDetail,
        () => store.getState().forceRefresh(),
        () => store.getState().checkFreshness(),
      );
    }
    if (state.selectedPointIndex !== prev.selectedPointIndex) {
      renderSliderSections(state);
      updateVizOverlay(state);
    }
    if (state.displayMode !== prev.displayMode || state.tierVisibility !== prev.tierVisibility) {
      applyDisplayModeClass(state.displayMode);
      updateToggleButtons(state.displayMode);
      renderSliderSections(state);
    }
    if (state.selectedModel !== prev.selectedModel) {
      ui.renderSkewTs(state.flight, state.currentPack, state.snapshot, state.selectedModel, state.routeAnalyses, state.selectedPointIndex);
      renderVisualization(state);
    }
    if (state.vizSettings !== prev.vizSettings) {
      renderVisualization(state);
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
      store.getState().refresh();
    });
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

  // --- Wire model toggle ---
  const modelSelect = document.getElementById('model-select') as HTMLSelectElement;
  if (modelSelect) {
    modelSelect.addEventListener('change', () => {
      store.getState().setSelectedModel(modelSelect.value);
    });
  }

  // --- Wire display mode toggle ---
  const toggleContainer = document.getElementById('display-mode-toggle');
  if (toggleContainer) {
    toggleContainer.addEventListener('click', (e) => {
      const btn = (e.target as HTMLElement).closest('.btn-toggle') as HTMLElement | null;
      if (btn && btn.dataset.mode) {
        store.getState().setDisplayMode(btn.dataset.mode as 'compact' | 'annotated');
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
    // Re-render viz canvas if cross-section was just expanded (canvas needs size)
    if (key === 'cross-section' && !section.classList.contains('collapsed') && vizRenderer) {
      vizRenderer.render();
    }
  });

  // --- Wire back button ---
  const backBtn = document.getElementById('back-btn');
  if (backBtn) {
    backBtn.addEventListener('click', () => {
      window.location.href = '/';
    });
  }

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
  store.getState().loadFlight(flightId).then(() => {
    const s = store.getState();
    ui.renderHeader(s.flight, s.snapshot);
    ui.renderHistoryDropdown(s.packs, s.currentPack?.fetch_timestamp || null, (ts) => store.getState().selectPack(ts));
    ui.renderAssessment(s.currentPack);
    ui.renderSynopsis(s.flight, s.currentPack, s.digest);
    ui.renderGramet(s.flight, s.currentPack);
    renderSliderSections(s);
    renderVisualization(s);
    ui.renderLoading(s.loading);

    // Show refresh button only for the flight owner
    if (refreshBtn && s.flight?.user_id === user.id) {
      refreshBtn.style.display = '';
    }

    // Check freshness after loading
    if (s.packs.length > 0) {
      store.getState().checkFreshness();
    }
  });
}

// Boot
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
