/** Maps page — forecast overview + verification bias (admin tab). */

import { fetchCurrentUser } from './adapters/auth-adapter';
import {
  fetchForecastMap, fetchVerificationMap, fetchAvailableHours,
  type ForecastMapResponse, type VerificationMapResponse,
} from './adapters/maps-adapter';
import { WeatherMap, type ForecastMetric, type VerifMetric } from './visualization/weather-map';
import { renderUserInfo, $ } from './utils';
import { initI18n } from './i18n/i18n';

let forecastMap: WeatherMap | null = null;
let verifMap: WeatherMap | null = null;
let currentTab: 'forecast' | 'verification' | 'stats' = 'forecast';
let statsLoaded = false;

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

// --- Tab switching ---

function switchTab(tab: 'forecast' | 'verification' | 'stats'): void {
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
    window.location.href = '/login.html';
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

  // Wire controls
  wireForecastControls();
  wireVerificationControls();

  // Tab clicks
  $('tabs')?.addEventListener('click', (e) => {
    const btn = (e.target as HTMLElement).closest('.tab-btn') as HTMLElement | null;
    if (!btn) return;
    const tab = btn.getAttribute('data-tab') as 'forecast' | 'verification' | 'stats';
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
