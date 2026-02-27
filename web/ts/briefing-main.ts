/** Briefing page entry point — wires store, UI manager, and event handlers. */

import { fetchCurrentUser } from './adapters/auth-adapter';
import { briefingStore, type BriefingState } from './store/briefing-store';
import * as api from './adapters/api-adapter';
import * as ui from './managers/briefing-ui';
import { renderAdvisories } from './managers/advisories-ui';
import type { DisplayMode } from './types/metrics';
import { renderUserInfo, initModelCatalog, isFlightPast } from './utils';
import { initInfoPopup, showMetricInfo } from './components/info-popup';
import { CrossSectionRenderer } from './visualization/cross-section/renderer';
import { extractVizData } from './visualization/data-extract';
import { getAllLayers } from './visualization/cross-section/layer-registry';
import { renderVizControls, renderRouteGraphControls, renderMapControls } from './visualization/controls/panel';
import { attachInteraction, type InteractionHandle } from './visualization/cross-section/interaction';
import { RouteGraphRenderer } from './visualization/route-graph/renderer';
import { getMetricById, METRIC_NONE } from './visualization/route-graph/metrics';
import { attachRouteGraphInteraction, type RouteGraphInteractionHandle } from './visualization/route-graph/interaction';
import { RouteMapRenderer } from './visualization/route-map/renderer';
import { getMapMetricById, MAP_METRIC_NONE } from './visualization/route-map/metrics';
import { attachMapInteraction, type MapInteractionHandle } from './visualization/route-map/interaction';
import { renderMapLegend } from './visualization/route-map/legend';
import { renderAltitudeSlider } from './visualization/route-map/altitude-slider';
import { initTheme } from './theme';

async function init(): Promise<void> {
  // Auth check — redirect to login if not authenticated
  const user = await fetchCurrentUser();
  if (!user) {
    window.location.href = '/login.html';
    return;
  }
  initTheme();
  renderUserInfo(user, 'briefing');

  // Load model catalog (non-blocking — modelLabel() has uppercase fallback)
  import('./adapters/preferences-adapter').then(({ fetchModelCatalog }) =>
    fetchModelCatalog().then(initModelCatalog).catch(() => {}),
  );

  // Initialize metric info popup
  initInfoPopup();
  document.addEventListener('click', (e) => {
    const btn = (e.target as HTMLElement).closest('.metric-info-btn') as HTMLElement | null;
    if (btn && !btn.classList.contains('advisory-info-btn')) {
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

  // --- Helper to render slider-dependent sections ---
  function renderSliderSections(state: BriefingState): void {
    ui.renderRouteSlider(
      state.routeAnalyses,
      state.selectedPointIndex,
      (idx) => store.getState().setSelectedPoint(idx),
    );
    ui.renderSoundingAnalysis(state.snapshot, state.routeAnalyses, state.selectedPointIndex, state.displayMode, state.tierVisibility, state.vizSettings.enabledLayers);
    ui.renderSkewTs(state.flight, state.currentPack, state.snapshot, state.selectedModel, state.routeAnalyses, state.selectedPointIndex);
    ui.renderModelComparison(state.snapshot, state.routeAnalyses, state.selectedPointIndex, state.displayMode, state.tierVisibility);
    ui.updateWindyLink(state.routeAnalyses, state.selectedPointIndex, state.selectedModel);
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

  /** Apply the CSS layout class to the layout wrapper. */
  function applyLayoutClass(layout: string): void {
    const wrapper = document.getElementById('viz-layout-wrapper');
    if (wrapper) {
      wrapper.classList.remove('layout-cross-section', 'layout-map', 'layout-split');
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
    const allLayers = getAllLayers();
    const showCrossSection = layout !== 'map';
    const showMap = layout !== 'cross-section';

    // --- Cross-section ---
    if (showCrossSection) {
      if (!vizRenderer) {
        vizRenderer = new CrossSectionRenderer(canvasContainer);
      }

      vizRenderer.setData(data);
      vizRenderer.setLayers(allLayers, state.vizSettings.enabledLayers);
      vizRenderer.setRenderMode(state.vizSettings.renderMode);
      vizRenderer.setSelectedPointIndex(state.selectedPointIndex);
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
        routeGraphRenderer.setSelectedPointIndex(state.selectedPointIndex);
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
      mapRenderer.setSelectedPointIndex(state.selectedPointIndex);
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

    // Render cross-section controls (above canvas)
    const availableModels = state.routeAnalyses?.models ?? undefined;
    renderVizControls(controlsContainer, state.vizSettings, {
      onRenderModeChange: (mode) => store.getState().setRenderMode(mode),
      onLayerToggle: (layerId) => store.getState().toggleVizLayer(layerId),
      onLayoutChange: (layout) => store.getState().setLayout(layout),
      onModelChange: (model) => store.getState().setSelectedModel(model),
    }, state.selectedModel, availableModels);

    // Render route graph controls (below graph)
    if (routeGraphControlsContainer && showCrossSection) {
      renderRouteGraphControls(routeGraphControlsContainer, state.vizSettings, {
        onRouteGraphToggle: (visible) => store.getState().setRouteGraphVisible(visible),
        onRouteGraphMetricChange: (axis, metricId) => store.getState().setRouteGraphMetric(axis, metricId),
      });
    }
  }

  function updateVizOverlay(state: BriefingState): void {
    if (vizRenderer && state.routeAnalyses) {
      vizRenderer.setSelectedPointIndex(state.selectedPointIndex);
    }
    if (routeGraphRenderer && state.routeAnalyses && state.vizSettings.routeGraphVisible) {
      routeGraphRenderer.setSelectedPointIndex(state.selectedPointIndex);
    }
    if (mapRenderer && state.routeAnalyses) {
      mapRenderer.setSelectedPointIndex(state.selectedPointIndex);
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
      state.routeAdvisories !== prev.routeAdvisories ||
      state.elevationProfile !== prev.elevationProfile
    ) {
      ui.renderAssessment(state.currentPack);
      renderAdvisories(state.routeAdvisories, () => store.getState().recalculateAdvisories(), state.displayMode);
      ui.renderRouteObservations(state.snapshot, () => store.getState().refreshObservations());
      ui.renderSynopsis(state.flight, state.currentPack, state.digest, state.displayMode);
      ui.renderGramet(state.flight, state.currentPack);
      renderSliderSections(state);
      renderVisualization(state);
    }
    if (
      state.freshness !== prev.freshness ||
      state.freshnessLoading !== prev.freshnessLoading ||
      state.currentPack !== prev.currentPack ||
      state.refreshing !== prev.refreshing ||
      state.refreshStatus !== prev.refreshStatus ||
      state.refreshStage !== prev.refreshStage ||
      state.refreshDetail !== prev.refreshDetail
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
      if (state.displayMode !== prev.displayMode) {
        renderAdvisories(state.routeAdvisories, () => store.getState().recalculateAdvisories(), state.displayMode);
        ui.renderSynopsis(state.flight, state.currentPack, state.digest, state.displayMode);
      }
    }
    if (state.selectedModel !== prev.selectedModel) {
      ui.renderSkewTs(state.flight, state.currentPack, state.snapshot, state.selectedModel, state.routeAnalyses, state.selectedPointIndex);
      ui.updateWindyLink(state.routeAnalyses, state.selectedPointIndex, state.selectedModel);
      renderVisualization(state);
    }
    if (state.vizSettings !== prev.vizSettings) {
      renderVisualization(state);
      renderSliderSections(state);
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
      ['data_issue', 'Briefing Data Issue'],
      ['too_conservative', 'Briefing Too Conservative'],
      ['too_optimistic', 'Briefing Too Optimistic'],
      ['incorrect_interpretation', 'Briefing Incorrect Interpretation'],
      ['other', 'Other Bug/Issue'],
    ];
    const optionsHtml = categories
      .map(([val, label]) => `<option value="${val}">${label}</option>`)
      .join('');

    const overlay = document.createElement('div');
    overlay.id = 'feedback-modal';
    overlay.className = 'feedback-modal-overlay';
    overlay.innerHTML = `
      <div class="feedback-modal">
        <h3>Send Feedback</h3>
        <p class="muted" style="margin:0 0 0.75rem;">Report an issue or suggest an improvement for this briefing.</p>
        <label for="feedback-category" style="font-weight:500;font-size:0.85rem;">Category</label>
        <select id="feedback-category" style="width:100%;padding:0.4rem;margin:0.25rem 0 0.75rem;border:1px solid var(--border);border-radius:4px;">
          ${optionsHtml}
        </select>
        <label for="feedback-comment" style="font-weight:500;font-size:0.85rem;">Comment</label>
        <textarea id="feedback-comment" rows="4" style="width:100%;padding:0.4rem;margin:0.25rem 0 0;border:1px solid var(--border);border-radius:4px;resize:vertical;font-family:inherit;" placeholder="Describe the issue or suggestion..."></textarea>
        <div id="feedback-error" style="color:#dc3545;font-size:0.8rem;min-height:1.2em;margin-top:0.25rem;"></div>
        <div style="display:flex;gap:0.5rem;justify-content:flex-end;margin-top:0.75rem;">
          <button class="btn" id="feedback-cancel">Cancel</button>
          <button class="btn btn-primary" id="feedback-submit">Submit</button>
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
        errorEl.textContent = 'Please enter a comment.';
        return;
      }
      errorEl.textContent = '';
      submitBtn.disabled = true;
      submitBtn.textContent = 'Submitting...';

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
          <h3>Thanks for your feedback!</h3>
          <p class="muted">Your report has been submitted.</p>`;
        setTimeout(dismiss, 1500);
      } catch (err) {
        errorEl.textContent = `Failed to submit: ${err}`;
        submitBtn.disabled = false;
        submitBtn.textContent = 'Submit';
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
  store.getState().loadFlight(flightId).then(() => {
    const s = store.getState();
    ui.renderHeader(s.flight, s.snapshot);
    ui.renderHistoryDropdown(s.packs, s.currentPack?.fetch_timestamp || null, (ts) => store.getState().selectPack(ts));
    ui.renderAssessment(s.currentPack);
    renderAdvisories(s.routeAdvisories, () => store.getState().recalculateAdvisories(), s.displayMode);
    ui.renderRouteObservations(s.snapshot, () => store.getState().refreshObservations());
    ui.renderSynopsis(s.flight, s.currentPack, s.digest, s.displayMode);
    ui.renderGramet(s.flight, s.currentPack);
    renderSliderSections(s);
    renderVisualization(s);
    ui.renderLoading(s.loading);

    // Show refresh button only for the flight owner; disable for past flights
    const past = s.flight
      ? isFlightPast(s.flight.target_date, s.flight.target_time_utc, s.flight.flight_duration_hours)
      : false;
    if (refreshBtn && s.flight?.user_id === user.id) {
      refreshBtn.style.display = '';
      if (past) {
        refreshBtn.disabled = true;
        refreshBtn.title = 'Flight is in the past';
      }
    }

    // Render privacy toggle
    ui.renderPrivacyToggle(s.flight, user.id, async (isPrivate) => {
      if (!s.flight) return;
      try {
        const updated = await api.updatePrivacy(s.flight.id, isPrivate);
        store.getState().updateFlightPrivacy(updated.private);
      } catch (err) {
        ui.renderError(`Failed to update privacy: ${err}`);
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
        ui.renderError(`Failed to update auto-refresh: ${err}`);
      }
    });

    // Auto-refresh on first visit (no packs yet), otherwise check freshness
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
