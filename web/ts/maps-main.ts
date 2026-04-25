/** Maps page — forecast overview + verification bias (admin tab). */

import { fetchCurrentUser } from './adapters/auth-adapter';
import {
  fetchForecastMap, fetchVerificationMap, fetchAvailableHours,
  type ForecastMapResponse, type VerificationMapResponse,
} from './adapters/maps-adapter';
import {
  fetchHewsonManifest, fetchHewsonSlice,
  type HewsonManifest, type HewsonManifestSnapshot,
} from './adapters/hewson-map-adapter';
import { WeatherMap, type ForecastMetric, type VerifMetric } from './visualization/weather-map';
import { SynopticMap } from './visualization/synoptic-map';
import { type HewsonMetric } from './visualization/hewson-colormaps';
import { initInfoPopup, showPopupContent } from './components/info-popup';
import { renderHewsonInfo } from './helpers/hewson-info';
import { redirectToLogin, renderUserInfo, $ } from './utils';
import { initI18n } from './i18n/i18n';

let forecastMap: WeatherMap | null = null;
let verifMap: WeatherMap | null = null;
let synopticMap: SynopticMap | null = null;
type Tab = 'forecast' | 'verification' | 'synoptic' | 'stats';
let currentTab: Tab = 'forecast';
let statsLoaded = false;

// Synoptic state
let synManifest: HewsonManifest | null = null;
let synModel = 'ecmwf';
let synInit: string | null = null;       // ISO 8601 with Z, picked from manifest
let synLevel = 850;
let synMetric: HewsonMetric = 'gradient';
let synHour = 0;
let synOpacity = 0.5;

// Forecast state
let forecastData: ForecastMapResponse | null = null;
let fcDay = 0;
let fcHour = 12;
let fcModel = 'worst';
let fcMetric: ForecastMetric = 'flight_category';

// Verification state
let verifData: VerificationMapResponse | null = null;
let verifPeriod = '7d';
let verifModel = 'all';
let verifDays = 0;
let verifMetric: VerifMetric = 'category_match_pct';

// --- Helpers ---

function setActive(groupId: string, value: string, attr: string): void {
  const group = $(groupId);
  if (!group) return;
  for (const btn of group.querySelectorAll('button')) {
    btn.classList.toggle('active', btn.dataset[attr] === value);
  }
}

function showInfo(text: string, targetId: string = 'map-info'): void {
  const el = $(targetId);
  if (el) el.textContent = text;
}

const _DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const _MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function updateForecastDatetime(): void {
  const el = $('forecast-datetime');
  if (!el) return;
  const now = new Date();
  const target = new Date(Date.UTC(
    now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + fcDay,
    fcHour, 0, 0,
  ));
  const day = _DAYS[target.getUTCDay()];
  const dd = String(target.getUTCDate()).padStart(2, '0');
  const mon = _MONTHS[target.getUTCMonth()];
  const yr = String(target.getUTCFullYear()).slice(2);
  el.textContent = `${day} ${dd}-${mon}-${yr} ${String(fcHour).padStart(2, '0')}Z`;
}

// --- Data loading ---

async function loadForecast(): Promise<void> {
  updateForecastDatetime();
  showInfo('Loading forecast...', 'map-info');
  try {
    forecastData = await fetchForecastMap(fcDay, fcHour);
    if (forecastMap && forecastData) {
      forecastMap.setForecastData(forecastData, fcMetric, fcModel);
      const initTimes = Object.entries(forecastData.model_init_times)
        .map(([m, t]) => `${m.toUpperCase()}: ${new Date(t).toISOString().slice(0, 13)}Z`)
        .join(', ');
      showInfo(`${forecastData.airports.length} airports | Model runs: ${initTimes}`, 'map-info');
    }
  } catch (err) {
    showInfo(`Failed to load forecast: ${err instanceof Error ? err.message : err}`, 'map-info');
  }
}

async function loadVerification(): Promise<void> {
  showInfo('Loading verification...', 'map-info-verif');
  try {
    verifData = await fetchVerificationMap(verifPeriod, verifModel, verifDays);
    if (verifMap && verifData) {
      verifMap.setVerificationData(verifData, verifMetric);
      showInfo(`${verifData.airports.length} airports | ${verifData.model.toUpperCase()} D-${verifData.days_out} (${verifPeriod})`, 'map-info-verif');
    }
  } catch (err) {
    showInfo(`Failed to load verification: ${err instanceof Error ? err.message : err}`, 'map-info-verif');
  }
}

function rerender(): void {
  if (currentTab === 'forecast' && forecastData && forecastMap) {
    forecastMap.setForecastData(forecastData, fcMetric, fcModel);
  } else if (currentTab === 'verification' && verifData && verifMap) {
    verifMap.setVerificationData(verifData, verifMetric);
  }
}

// --- Control wiring ---

function wireButtonGroup(groupId: string, attr: string, onChange: (value: string) => void): void {
  const group = $(groupId);
  if (!group) return;
  group.addEventListener('click', (e) => {
    const btn = (e.target as HTMLElement).closest('button');
    if (!btn) return;
    const val = btn.dataset[attr];
    if (val == null) return;
    for (const b of group.querySelectorAll('button')) b.classList.remove('active');
    btn.classList.add('active');
    onChange(val);
  });
}

function wireForecastControls(): void {
  wireButtonGroup('day-picker', 'day', async (v) => {
    fcDay = parseInt(v);
    // Update available hours
    try {
      const { hours } = await fetchAvailableHours(fcDay);
      const hourBtns = $('hour-picker')?.querySelectorAll('button');
      if (hourBtns) {
        for (const btn of hourBtns) {
          const h = parseInt(btn.dataset.hour || '0');
          btn.classList.toggle('disabled', !hours.includes(h));
          (btn as HTMLButtonElement).disabled = !hours.includes(h);
        }
        // If current hour is unavailable, switch to first available
        if (!hours.includes(fcHour) && hours.length > 0) {
          fcHour = hours[0];
          setActive('hour-picker', String(fcHour), 'hour');
        }
      }
    } catch { /* ignore */ }
    loadForecast();
  });

  wireButtonGroup('hour-picker', 'hour', (v) => {
    fcHour = parseInt(v);
    loadForecast();
  });

  wireButtonGroup('model-picker', 'model', (v) => {
    fcModel = v;
    // All mode switches are client-side — per-model data is already in the response
    rerender();
  });

  const metricSel = $('metric-picker') as HTMLSelectElement | null;
  metricSel?.addEventListener('change', () => {
    fcMetric = metricSel.value as ForecastMetric;
    rerender();
  });
}

function wireVerificationControls(): void {
  wireButtonGroup('verif-period-picker', 'period', (v) => { verifPeriod = v; loadVerification(); });
  wireButtonGroup('verif-days-picker', 'days', (v) => { verifDays = parseInt(v); loadVerification(); });
  wireButtonGroup('verif-model-picker', 'model', (v) => { verifModel = v; loadVerification(); });

  const metricSel = $('verif-metric-picker') as HTMLSelectElement | null;
  metricSel?.addEventListener('change', () => {
    verifMetric = metricSel.value as VerifMetric;
    rerender();
  });
}

// --- Synoptic tab ---

function currentSynapshot(): HewsonManifestSnapshot | null {
  if (!synManifest || !synInit) return null;
  const list = synManifest.models[synModel] ?? [];
  return list.find((s) => s.init_time === synInit) ?? null;
}

function repopulateInitPicker(): void {
  const sel = $('syn-init-picker') as HTMLSelectElement | null;
  if (!sel || !synManifest) return;
  const list = synManifest.models[synModel] ?? [];
  // Newest first
  const sorted = [...list].sort((a, b) => b.init_time_unix - a.init_time_unix);
  sel.innerHTML = '';
  for (const snap of sorted) {
    const opt = document.createElement('option');
    opt.value = snap.init_time;
    // Compact label: "24 Apr 12Z"
    const dt = new Date(snap.init_time);
    const dd = String(dt.getUTCDate()).padStart(2, '0');
    const mon = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][dt.getUTCMonth()];
    const hh = String(dt.getUTCHours()).padStart(2, '0');
    opt.textContent = `${dd} ${mon} ${hh}Z`;
    sel.appendChild(opt);
  }
  // Pick newest if current init isn't in this model's list
  if (!sorted.find((s) => s.init_time === synInit)) {
    synInit = sorted[0]?.init_time ?? null;
  }
  if (synInit) sel.value = synInit;
  reconfigureHourSlider();
}

function reconfigureHourSlider(): void {
  const slider = $('syn-hour-slider') as HTMLInputElement | null;
  const valEl = $('syn-hour-value');
  if (!slider) return;
  const snap = currentSynapshot();
  if (!snap) {
    slider.min = '0'; slider.max = '0'; slider.step = '3'; slider.value = '0';
    if (valEl) valEl.textContent = '+0 h';
    return;
  }
  const max = (snap.n_hours - 1) * snap.stride_hours;
  slider.min = '0';
  slider.max = String(max);
  slider.step = String(snap.stride_hours);
  // Clamp existing hour to the new range, snapped to stride
  let h = Math.min(synHour, max);
  h = Math.round(h / snap.stride_hours) * snap.stride_hours;
  synHour = h;
  slider.value = String(h);
  if (valEl) valEl.textContent = `+${h} h`;
}

async function loadSynoptic(): Promise<void> {
  if (!synopticMap) return;
  const info = $('map-info-synoptic');
  if (!synInit) {
    if (info) info.textContent = 'No precomputed snapshot available for this model.';
    synopticMap.clear();
    return;
  }
  if (info) info.textContent = 'Loading…';
  try {
    const slice = await fetchHewsonSlice({
      model: synModel,
      init: synInit,
      level: synLevel,
      metric: synMetric,
      hour: synHour,
    });
    synopticMap.setSlice(slice);
    synopticMap.setOpacity(synOpacity);
    if (info) {
      const validDt = new Date(slice.valid_time);
      const validLabel = validDt.toUTCString().slice(0, 22);
      info.textContent = `${synModel.toUpperCase()} ${slice.init_time.slice(0, 13)}Z · valid ${validLabel} (+${synHour} h) · ${slice.level} hPa`;
    }
  } catch (err) {
    if (info) info.textContent = `Failed: ${err instanceof Error ? err.message : err}`;
  }
}

function showSynopticInfo(): void {
  showPopupContent(renderHewsonInfo(synMetric));
}

function wireSynopticControls(): void {
  wireButtonGroup('syn-model-picker', 'model', (v) => {
    synModel = v;
    repopulateInitPicker();
    loadSynoptic();
  });

  const initSel = $('syn-init-picker') as HTMLSelectElement | null;
  initSel?.addEventListener('change', () => {
    synInit = initSel.value || null;
    reconfigureHourSlider();
    loadSynoptic();
  });

  wireButtonGroup('syn-level-picker', 'level', (v) => {
    synLevel = parseInt(v);
    loadSynoptic();
  });

  const metricSel = $('syn-metric-picker') as HTMLSelectElement | null;
  metricSel?.addEventListener('change', () => {
    synMetric = metricSel.value as HewsonMetric;
    loadSynoptic();
  });

  const hourSlider = $('syn-hour-slider') as HTMLInputElement | null;
  const hourVal = $('syn-hour-value');
  hourSlider?.addEventListener('input', () => {
    synHour = parseInt(hourSlider.value);
    if (hourVal) hourVal.textContent = `+${synHour} h`;
  });
  hourSlider?.addEventListener('change', () => {
    synHour = parseInt(hourSlider.value);
    loadSynoptic();
  });

  const opSlider = $('syn-opacity-slider') as HTMLInputElement | null;
  const opVal = $('syn-opacity-value');
  opSlider?.addEventListener('input', () => {
    const pct = parseInt(opSlider.value);
    synOpacity = pct / 100;
    if (opVal) opVal.textContent = `${pct}%`;
    synopticMap?.setOpacity(synOpacity);
  });

  $('syn-info-btn')?.addEventListener('click', (e) => {
    e.stopPropagation();
    showSynopticInfo();
  });
}

async function initSynopticTab(): Promise<void> {
  // Lazy map init — Leaflet needs the panel visible first.
  if (!synopticMap) {
    const container = $('map-container-synoptic');
    if (container) {
      synopticMap = new SynopticMap(container);
      synopticMap.init();
    }
  } else {
    synopticMap.invalidateSize();
  }

  if (!synManifest) {
    const info = $('map-info-synoptic');
    if (info) info.textContent = 'Looking up available snapshots…';
    try {
      synManifest = await fetchHewsonManifest();
    } catch (err) {
      if (info) info.textContent = `Failed to load manifest: ${err instanceof Error ? err.message : err}`;
      return;
    }
    // Pick the first model that has data — fallback if ECMWF isn't precomputed yet
    const modelsWithData = Object.keys(synManifest.models);
    if (modelsWithData.length === 0) {
      if (info) info.textContent = 'No Hewson snapshots on disk yet — run the precompute loop.';
      return;
    }
    if (!modelsWithData.includes(synModel)) {
      synModel = modelsWithData[0];
      setActive('syn-model-picker', synModel, 'model');
    }
    repopulateInitPicker();
  }
  loadSynoptic();
}

// --- Tab switching ---

function switchTab(tab: Tab): void {
  currentTab = tab;

  // Tab buttons
  for (const btn of document.querySelectorAll('#tabs .tab-btn')) {
    btn.classList.toggle('active', btn.getAttribute('data-tab') === tab);
  }

  // Panels — use the tab-panel active class
  for (const panel of document.querySelectorAll('.tab-panel')) {
    panel.classList.toggle('active', panel.id === `panel-${tab}`);
  }

  // Load data / init maps as needed
  if (tab === 'forecast') {
    if (!forecastData) loadForecast();
    else rerender();
    setTimeout(() => forecastMap?.invalidateSize(), 100);
  } else if (tab === 'verification') {
    // Delay map init until after panel is visible (Leaflet needs computed size)
    setTimeout(() => {
      if (!verifMap) {
        const container = $('map-container-verif');
        if (container) {
          verifMap = new WeatherMap(container);
          verifMap.init();
        }
      } else {
        verifMap.invalidateSize();
      }
      if (!verifData) loadVerification();
      else rerender();
    }, 50);
  } else if (tab === 'synoptic') {
    setTimeout(() => { initSynopticTab(); }, 50);
  } else if (tab === 'stats') {
    if (!statsLoaded) {
      const frame = $('stats-frame') as HTMLIFrameElement | null;
      if (frame) {
        frame.src = '/verification.html?embed';
        statsLoaded = true;
      }
    }
  }
}

// --- Init ---

async function main(): Promise<void> {
  await initI18n();

  const user = await fetchCurrentUser();
  if (!user) {
    redirectToLogin();
    return;
  }
  renderUserInfo(user, 'maps');

  // Experimental banner toggle
  $('experimental-toggle')?.addEventListener('click', () => {
    const detail = $('experimental-detail');
    if (detail) detail.classList.toggle('open');
  });

  // Init forecast map (verification map lazy-inits on tab switch)
  const container = $('map-container');
  if (!container) return;
  forecastMap = new WeatherMap(container);
  forecastMap.init();

  // Set up the briefing-style info modal (used by the synoptic tab's (i)).
  initInfoPopup();

  // Wire controls
  wireForecastControls();
  wireVerificationControls();
  wireSynopticControls();

  // Tab clicks
  $('tabs')?.addEventListener('click', (e) => {
    const btn = (e.target as HTMLElement).closest('.tab-btn') as HTMLElement | null;
    if (!btn) return;
    const tab = btn.getAttribute('data-tab') as Tab;
    if (tab) switchTab(tab);
  });

  // Default: pick nearest upcoming sample hour
  const nowHour = new Date().getUTCHours();
  const sampleHours = [6, 9, 12, 15, 18];
  const nearestIdx = sampleHours.reduce((best, h, i) =>
    Math.abs(h - nowHour) < Math.abs(sampleHours[best] - nowHour) ? i : best, 0);
  fcHour = sampleHours[nearestIdx];
  setActive('hour-picker', String(fcHour), 'hour');

  // Load initial data
  updateForecastDatetime();
  loadForecast();
}

main().catch(console.error);
