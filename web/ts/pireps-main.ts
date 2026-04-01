/** PIREPs standalone page — list + map views with filters. */

import { fetchCurrentUser } from './adapters/auth-adapter';
import { fetchRecentPireps, type PirepResponse } from './adapters/pirep-adapter';
import {
  renderPirepList,
  renderPirepFilters,
  filtersToParams,
  type PirepFilterState,
} from './managers/pirep-ui';
import { PirepMap } from './visualization/pirep-map';
import { renderUserInfo, $, escapeHtml } from './utils';
import { initI18n } from './i18n/i18n';

let pirepMap: PirepMap | null = null;
let currentPireps: PirepResponse[] = [];
let currentView: 'list' | 'map' = 'list';
let filterState: PirepFilterState = {
  hours: 6,
  hazard: '',
  min_severity: '',
  altitude_min: '',
  altitude_max: '',
};

async function loadPireps(): Promise<void> {
  const listContainer = $('pirep-list-container');
  const mapContainer = $('pirep-map-container');

  try {
    const filters = filtersToParams(filterState);
    const resp = await fetchRecentPireps(filterState.hours, filters);
    currentPireps = resp.items;

    renderPirepList(listContainer, currentPireps);

    if (pirepMap) {
      pirepMap.setData(currentPireps);
    }
  } catch (err: unknown) {
    const msg = err instanceof Error ? err.message : String(err);
    if (msg.includes('403')) {
      listContainer.innerHTML = '<p class="muted">PIREP viewing is not enabled for your account.</p>';
      mapContainer.innerHTML = '';
    } else {
      listContainer.innerHTML = `<p class="error">Failed to load PIREPs: ${escapeHtml(msg)}</p>`;
    }
  }
}

function switchView(view: 'list' | 'map'): void {
  currentView = view;
  const listContainer = $('pirep-list-container');
  const mapContainer = $('pirep-map-container');

  $('view-list-btn').classList.toggle('active', view === 'list');
  $('view-map-btn').classList.toggle('active', view === 'map');

  if (view === 'list') {
    listContainer.style.display = '';
    mapContainer.style.display = 'none';
  } else {
    listContainer.style.display = 'none';
    mapContainer.style.display = '';

    if (!pirepMap) {
      pirepMap = new PirepMap(mapContainer);
      pirepMap.init();
      pirepMap.setData(currentPireps);
    }
    pirepMap.invalidateSize();
  }
}

async function init(): Promise<void> {
  await initI18n();
  const user = await fetchCurrentUser();
  if (!user) {
    window.location.href = '/login.html';
    return;
  }
  renderUserInfo(user, 'pireps');

  // Filters
  renderPirepFilters($('pirep-filters'), filterState, (newState) => {
    filterState = newState;
    loadPireps();
  });

  // View toggle
  $('view-list-btn').addEventListener('click', () => switchView('list'));
  $('view-map-btn').addEventListener('click', () => switchView('map'));

  // Initial load
  await loadPireps();
}

init();
