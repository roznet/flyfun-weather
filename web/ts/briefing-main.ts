/** Briefing page entry point — wires store, UI manager, and event handlers. */

import { briefingStore } from './store/briefing-store';
import * as ui from './managers/briefing-ui';

function init(): void {
  const store = briefingStore;

  // Get flight ID from URL
  const params = new URLSearchParams(window.location.search);
  const flightId = params.get('flight');
  if (!flightId) {
    ui.renderError('No flight specified. Go back to flights list.');
    return;
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
      state.digest !== prev.digest
    ) {
      ui.renderAssessment(state.currentPack);
      ui.renderSynopsis(state.flight, state.currentPack, state.digest);
      ui.renderGramet(state.flight, state.currentPack);
      ui.renderModelComparison(state.snapshot);
      ui.renderSkewTs(state.flight, state.currentPack, state.snapshot, state.selectedModel);
    }
    if (state.selectedModel !== prev.selectedModel) {
      ui.renderSkewTs(state.flight, state.currentPack, state.snapshot, state.selectedModel);
    }
    if (state.loading !== prev.loading) {
      ui.renderLoading(state.loading);
    }
    if (state.refreshing !== prev.refreshing) {
      ui.renderRefreshing(state.refreshing);
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
    ui.renderModelComparison(s.snapshot);
    ui.renderSkewTs(s.flight, s.currentPack, s.snapshot, s.selectedModel);
    ui.renderLoading(s.loading);
  });
}

// Boot
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', init);
} else {
  init();
}
