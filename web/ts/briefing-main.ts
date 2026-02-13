/** Briefing page entry point — wires store, UI manager, and event handlers. */

import { fetchCurrentUser, logout } from './adapters/auth-adapter';
import { briefingStore, type BriefingState } from './store/briefing-store';
import * as api from './adapters/api-adapter';
import * as ui from './managers/briefing-ui';

async function init(): Promise<void> {
  // Auth check — redirect to login if not authenticated
  const user = await fetchCurrentUser();
  if (!user) {
    window.location.href = '/login.html';
    return;
  }
  renderUserInfo(user.name);

  const store = briefingStore;

  // Get flight ID from URL
  const params = new URLSearchParams(window.location.search);
  const flightId = params.get('flight');
  if (!flightId) {
    ui.renderError('No flight specified. Go back to flights list.');
    return;
  }

  // --- Helper to render slider-dependent sections ---
  function renderSliderSections(state: BriefingState): void {
    ui.renderRouteSlider(
      state.routeAnalyses,
      state.selectedPointIndex,
      (idx) => store.getState().setSelectedPoint(idx),
    );
    ui.renderSoundingAnalysis(state.snapshot, state.routeAnalyses, state.selectedPointIndex);
    ui.renderSkewTs(state.flight, state.currentPack, state.snapshot, state.selectedModel, state.routeAnalyses, state.selectedPointIndex);
    ui.renderModelComparison(state.snapshot, state.routeAnalyses, state.selectedPointIndex);
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
      state.routeAnalyses !== prev.routeAnalyses
    ) {
      ui.renderAssessment(state.currentPack);
      ui.renderSynopsis(state.flight, state.currentPack, state.digest);
      ui.renderGramet(state.flight, state.currentPack);
      renderSliderSections(state);
    }
    if (state.selectedPointIndex !== prev.selectedPointIndex) {
      renderSliderSections(state);
    }
    if (state.selectedModel !== prev.selectedModel) {
      ui.renderSkewTs(state.flight, state.currentPack, state.snapshot, state.selectedModel, state.routeAnalyses, state.selectedPointIndex);
    }
    if (state.loading !== prev.loading) {
      ui.renderLoading(state.loading);
    }
    if (
      state.refreshing !== prev.refreshing ||
      state.refreshStage !== prev.refreshStage ||
      state.refreshDetail !== prev.refreshDetail
    ) {
      ui.renderRefreshing(state.refreshing, state.refreshStage, state.refreshDetail);
    }
    if (state.emailing !== prev.emailing) {
      ui.renderEmailing(state.emailing);
    }
    if (state.error !== prev.error) {
      ui.renderError(state.error);
    }
  });

  // --- Wire refresh button ---
  const refreshBtn = document.getElementById('refresh-btn');
  if (refreshBtn) {
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
      if (target.classList.contains('gramet-img') || target.classList.contains('skewt-img')) {
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
    ui.renderLoading(s.loading);
  });
}

function renderUserInfo(name: string): void {
  const container = document.getElementById('user-info');
  if (!container) return;
  container.innerHTML = `
    <span class="user-name">${name}</span>
    <button class="btn-logout" id="logout-btn">Sign out</button>
  `;
  document.getElementById('logout-btn')?.addEventListener('click', () => logout());
}

// Boot
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
