/** Maps page — forecast overview + synoptic forecast + accuracy stats. */

import { fetchCurrentUser } from './adapters/auth-adapter';
import {
  fetchForecastMap, fetchAvailableHours,
  type ForecastMapResponse,
} from './adapters/maps-adapter';
import {
  fetchHewsonManifest, fetchHewsonSlice, fetchHewsonAllMetrics,
  type HewsonManifest, type HewsonManifestSnapshot,
  type HewsonAllMetricsSlice,
} from './adapters/hewson-map-adapter';
import { WeatherMap, type ForecastMetric } from './visualization/weather-map';
import { AirportProfilePanel } from './visualization/airport-profile-panel';
import { SynopticMap } from './visualization/synoptic-map';
import { ClimatologyTab } from './visualization/climatology-tab';
import { type HewsonMetric, type ColorScale, vRangeFor } from './visualization/hewson-colormaps';
import { initInfoPopup, showPopupContent } from './components/info-popup';
import { renderHewsonInfo } from './helpers/hewson-info';
import { redirectToLogin, renderUserInfo, $ } from './utils';
import { initI18n, t } from './i18n/i18n';
import { setUnitsPreference } from './units';
import { createUrlState } from './utils/url-state';
import { shareCurrentUrl } from './utils/share-link';

let forecastMap: WeatherMap | null = null;
let synopticMap: SynopticMap | null = null;
let climatologyTab: ClimatologyTab | null = null;
type Tab = 'forecast' | 'synoptic' | 'climatology' | 'stats';
let currentTab: Tab = 'forecast';
let statsLoaded = false;

// Synoptic state
let synManifest: HewsonManifest | null = null;
let synModel = 'ecmwf';
let synInit: string | null = null;       // ISO 8601 with Z, picked from manifest
let synLevel = 850;
let synMetric: HewsonMetric = 'tfp';
let synHour = 0;
let synOpacity = 0.5;
let synScale: ColorScale = 'default';
// Cached multi-metric grid for the active (model, init, level, hour) — used
// by the cursor tooltip and (when present) lets metric-change skip a
// network call. Fetched in the background after the fast initial render.
let synActiveGrid: HewsonAllMetricsSlice | null = null;
// "Token" identifying the current (model, init, level, hour) request. Async
// fetches check this on completion and discard their results if the user
// has moved on — prevents a stale all-metrics response from poisoning the
// hover cache after the user changed hour mid-fetch.
let synLoadToken = 0;

function currentLoadKey(): string {
  return `${synModel}|${synInit}|${synLevel}|${synHour}`;
}

// Forecast state
let forecastData: ForecastMapResponse | null = null;
let fcDay = 0;
let fcHour = 12;
let fcModel = 'worst';
let fcMetric: ForecastMetric = 'flight_category';

// Number of forward hours the airport-profile panel requests beyond the
// selected start hour. Mirror of `_DEFAULT_WINDOW_H` in the Python
// endpoint — both must agree, otherwise the cross-section X-axis would
// show fewer/more time ticks than the streamed payload contains.
const AIRPORT_PROFILE_WINDOW_H = 3;

// Airport profile panel state (right-click on a forecast marker)
let airportPanel: AirportProfilePanel | null = null;
let airportPanelIcao: string | null = null;

// --- URL state ---
//
// Schema covers the forecast tab. The synoptic tab's init time is
// data-driven (depends on the manifest) and opt-in per user, so it's
// deliberately not deep-linked yet — switching to the synoptic tab is
// preserved via the `tab` key, but its inner controls aren't.
//
// Defaults match the module-level state above and the HTML's pre-active
// buttons, so an untouched view yields a bare `/maps.html` URL.
const mapsUrlState = createUrlState({
  tab:         { default: 'forecast' as Tab, values: ['forecast', 'synoptic', 'climatology', 'stats'] as readonly Tab[] },
  'fc.day':    { default: 0,  values: [0, 1, 2, 3] as readonly number[] },
  'fc.hour':   { default: 12, values: [6, 9, 12, 15, 18] as readonly number[] },
  'fc.model':  { default: 'worst', values: ['worst', 'majority', 'gfs', 'icon', 'ecmwf'] as readonly string[] },
  'fc.metric': {
    default: 'flight_category' as ForecastMetric,
    values: ['flight_category', 'wind_speed_kt', 'crosswind_kt', 'headwind_kt', 'ceiling_ft', 'visibility_m', 'cape_jkg', 'convective_risk', 'cloud_cover_pct'] as readonly ForecastMetric[],
  },
  // Open airport-profile panel (ICAO) and the panel's selected model.
  // Empty ICAO = no panel open. Panel model defaults differ from the
  // map's per-airport model (panel always needs a real model, not a
  // consensus mode), so they're separate keys.
  'fc.apt':    { default: '' },
  'fc.apModel':{ default: 'ecmwf', values: ['gfs', 'icon', 'ecmwf'] as readonly string[] },
});

function syncUrl(): void {
  mapsUrlState.write({
    tab:         currentTab,
    'fc.day':    fcDay,
    'fc.hour':   fcHour,
    'fc.model':  fcModel,
    'fc.metric': fcMetric,
    'fc.apt':    airportPanelIcao ?? '',
    'fc.apModel': airportPanel?.getModel() ?? 'ecmwf',
  });
}

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

function forecastStartHour(): string {
  // Build an ISO 8601 UTC string for the currently-selected (day, hour).
  const now = new Date();
  const dt = new Date(Date.UTC(
    now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + fcDay,
    fcHour, 0, 0,
  ));
  return dt.toISOString().replace(/\.\d{3}Z$/, 'Z');
}

function panelDefaultModel(): string {
  // Map: forecast tab might be in consensus mode, panel needs a real
  // model. Pick the same model when one is selected, otherwise ECMWF.
  return ['gfs', 'icon', 'ecmwf'].includes(fcModel) ? fcModel : 'ecmwf';
}

function openAirportPanel(icao: string, opts: { initialModel?: string } = {}): void {
  const host = $('ap-panel-host') as HTMLElement | null;
  if (!host) return;
  host.style.display = 'flex';
  if (!airportPanel) {
    airportPanel = new AirportProfilePanel({
      container: host,
      initialModel: opts.initialModel ?? panelDefaultModel(),
      onClose: () => closeAirportPanel(),
      onModelChange: () => syncUrl(),
    });
  } else if (opts.initialModel) {
    airportPanel.setModel(opts.initialModel);
  }
  airportPanelIcao = icao;
  forecastMap?.setHighlightedIcao(icao);
  airportPanel.load({
    icao, startHour: forecastStartHour(), windowH: AIRPORT_PROFILE_WINDOW_H,
  });
  syncUrl();
  // The map width changed — let Leaflet re-layout.
  setTimeout(() => forecastMap?.invalidateSize(), 50);
}

function closeAirportPanel(): void {
  const host = $('ap-panel-host') as HTMLElement | null;
  if (host) host.style.display = 'none';
  airportPanel?.destroy();
  airportPanel = null;
  airportPanelIcao = null;
  forecastMap?.setHighlightedIcao(null);
  syncUrl();
  setTimeout(() => forecastMap?.invalidateSize(), 50);
}

function refreshAirportPanelOnHourChange(): void {
  // Map's day/hour changed: reload the open panel; metric changes are ignored.
  if (!airportPanel || !airportPanelIcao) return;
  airportPanel.load({
    icao: airportPanelIcao, startHour: forecastStartHour(), windowH: AIRPORT_PROFILE_WINDOW_H,
  });
}

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

function rerender(): void {
  if (currentTab === 'forecast' && forecastData && forecastMap) {
    forecastMap.setForecastData(forecastData, fcMetric, fcModel);
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
    syncUrl();
    loadForecast();
    refreshAirportPanelOnHourChange();
  });

  wireButtonGroup('hour-picker', 'hour', (v) => {
    fcHour = parseInt(v);
    syncUrl();
    loadForecast();
    refreshAirportPanelOnHourChange();
  });

  wireButtonGroup('model-picker', 'model', (v) => {
    fcModel = v;
    // All mode switches are client-side — per-model data is already in the response
    syncUrl();
    rerender();
  });

  const metricSel = $('metric-picker') as HTMLSelectElement | null;
  metricSel?.addEventListener('change', () => {
    fcMetric = metricSel.value as ForecastMetric;
    syncUrl();
    rerender();
  });
}

// --- Synoptic tab ---

function currentSnapshot(): HewsonManifestSnapshot | null {
  if (!synManifest || !synInit) return null;
  const list = synManifest.models[synModel] ?? [];
  return list.find((s) => s.init_time === synInit) ?? null;
}

function repopulateModelPicker(): void {
  const group = $('syn-model-picker');
  if (!group || !synManifest) return;
  const models = Object.keys(synManifest.models).sort();
  group.innerHTML = '';
  for (const m of models) {
    const btn = document.createElement('button');
    btn.className = 'btn-toggle';
    btn.dataset.model = m;
    if (m === synModel) btn.classList.add('active');
    btn.textContent = m.toUpperCase();
    group.appendChild(btn);
  }
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
    // Compact label: "24 Apr 12Z" — for ERA5 the date can be any year.
    const dt = new Date(snap.init_time);
    const dd = String(dt.getUTCDate()).padStart(2, '0');
    const mon = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][dt.getUTCMonth()];
    const hh = String(dt.getUTCHours()).padStart(2, '0');
    const yr = dt.getUTCFullYear();
    const thisYr = new Date().getUTCFullYear();
    opt.textContent = yr === thisYr ? `${dd} ${mon} ${hh}Z` : `${dd} ${mon} ${yr} ${hh}Z`;
    sel.appendChild(opt);
  }
  // Pick newest if current init isn't in this model's list
  if (!sorted.find((s) => s.init_time === synInit)) {
    synInit = sorted[0]?.init_time ?? null;
  }
  if (synInit) sel.value = synInit;
  reconfigureLevelPicker();
  reconfigureHourSlider();
}

function reconfigureLevelPicker(): void {
  const group = $('syn-level-picker');
  if (!group) return;
  const snap = currentSnapshot();
  const available = new Set(snap?.levels ?? [925, 850, 700]);
  let activeStillValid = false;
  for (const btn of group.querySelectorAll<HTMLButtonElement>('button')) {
    const lvl = parseInt(btn.dataset.level || '0');
    const ok = available.has(lvl);
    btn.disabled = !ok;
    btn.classList.toggle('disabled', !ok);
    if (lvl === synLevel && ok) activeStillValid = true;
  }
  // If the current level isn't in the snapshot, pick the first available.
  if (!activeStillValid && snap) {
    const fallback = snap.levels.includes(850) ? 850 : snap.levels[0];
    if (fallback !== undefined) {
      synLevel = fallback;
      setActive('syn-level-picker', String(synLevel), 'level');
    }
  }
}

/** Update the read-only "Sat 16-May-26 12Z" chip next to the hour slider,
 * computed from synInit + synHour. Mirrors updateForecastDatetime() so both
 * tabs format the implied valid time the same way. Empty when no init is
 * selected (e.g. the manifest is empty). */
function updateSynopticDatetime(): void {
  const el = $('syn-valid-datetime');
  if (!el) return;
  if (!synInit) { el.textContent = ''; return; }
  const target = new Date(new Date(synInit).getTime() + synHour * 3_600_000);
  const day = _DAYS[target.getUTCDay()];
  const dd = String(target.getUTCDate()).padStart(2, '0');
  const mon = _MONTHS[target.getUTCMonth()];
  const yr = String(target.getUTCFullYear()).slice(2);
  const hh = String(target.getUTCHours()).padStart(2, '0');
  el.textContent = `${day} ${dd}-${mon}-${yr} ${hh}Z`;
}

function reconfigureHourSlider(): void {
  const slider = $('syn-hour-slider') as HTMLInputElement | null;
  const valEl = $('syn-hour-value');
  if (!slider) return;
  const snap = currentSnapshot();
  if (!snap) {
    slider.min = '0'; slider.max = '0'; slider.step = '3'; slider.value = '0';
    if (valEl) valEl.textContent = '+0 h';
    updateSynopticDatetime();
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
  updateSynopticDatetime();
}

async function loadSynoptic(): Promise<void> {
  if (!synopticMap) return;
  const info = $('map-info-synoptic');
  if (!synInit) {
    if (info) info.textContent = 'No precomputed snapshot available for this model.';
    synopticMap.clear();
    synActiveGrid = null;
    return;
  }

  // Bump the token, invalidating any in-flight fetches.
  const myToken = ++synLoadToken;
  // Tear down stale hover state — no all-metrics for the new (model, init,
  // level, hour) yet, so the tooltip should disable until the background
  // fetch completes.
  synActiveGrid = null;
  synopticMap.setHoverGrid(null);

  if (info) info.textContent = 'Loading…';

  // --- 1. Fast path: single-metric slice (~80 KB) for the canvas overlay.
  let firstSlice;
  try {
    firstSlice = await fetchHewsonSlice({
      model: synModel,
      init: synInit,
      level: synLevel,
      metric: synMetric,
      hour: synHour,
    });
  } catch (err) {
    if (myToken !== synLoadToken) return;
    if (info) info.textContent = `Failed: ${err instanceof Error ? err.message : err}`;
    return;
  }
  if (myToken !== synLoadToken) return;  // user moved on

  const { vmin, vmax } = vRangeFor(synMetric, synScale);
  synopticMap.setSlice(firstSlice, vmin, vmax);
  synopticMap.setOpacity(synOpacity);
  if (info) {
    const validDt = new Date(firstSlice.valid_time);
    const validLabel = validDt.toUTCString().slice(0, 22);
    info.textContent = `${synModel.toUpperCase()} ${firstSlice.init_time.slice(0, 13)}Z · valid ${validLabel} (+${synHour} h) · ${firstSlice.level} hPa · loading hover…`;
  }

  // --- 2. Background: all-metrics (~2.3 MB) so the cursor tooltip can read
  //     all six values without round-tripping per mousemove. We don't await —
  //     the canvas is already rendered above; this just enables hover when
  //     it lands. Errors are non-fatal; map remains interactive without hover.
  fetchAllMetricsInBackground(myToken);
}

/** Fire an /all-metrics fetch in the background and wire its response to
 * synActiveGrid + the hover layer. On success drops the "loading hover…"
 * suffix from the info bar. Stale responses (token mismatch) are silently
 * discarded; failures leave the map interactive without hover. Used by
 * both loadSynoptic() and changeActiveMetric() (which discards the
 * original load's all-metrics by bumping the token). */
function fetchAllMetricsInBackground(myToken: number): void {
  if (!synInit) return;
  fetchHewsonAllMetrics({
    model: synModel,
    init: synInit,
    level: synLevel,
    hour: synHour,
  }).then((grid) => {
    if (myToken !== synLoadToken) return;  // stale response — discard
    synActiveGrid = grid;
    synopticMap?.setHoverGrid(grid);
    const info = $('map-info-synoptic');
    if (info) {
      const validDt = new Date(grid.valid_time);
      const validLabel = validDt.toUTCString().slice(0, 22);
      info.textContent = `${synModel.toUpperCase()} ${grid.init_time.slice(0, 13)}Z · valid ${validLabel} (+${synHour} h) · ${grid.level} hPa`;
    }
  }).catch((err) => {
    if (myToken !== synLoadToken) return;
    console.warn('Hewson all-metrics background fetch failed:', err);
    // Leave the canvas + status as-is; hover just stays disabled.
  });
}

/** Render a different metric using the cached all-metrics grid (no
 * network), or fall back to a single-metric refetch if the cache isn't
 * ready yet (background load still in flight). */
async function changeActiveMetric(): Promise<void> {
  if (!synopticMap) return;
  const { vmin, vmax } = vRangeFor(synMetric, synScale);

  if (synActiveGrid) {
    const values = synActiveGrid.metrics[synMetric];
    if (!values) {
      // Snapshot is missing this metric (legacy build). Clear the canvas
      // and surface an explicit message — silently returning would leave
      // the picker showing one metric and the map showing another.
      synopticMap.clear();
      const info = $('map-info-synoptic');
      if (info) info.textContent = `Metric "${synMetric}" not available in this snapshot.`;
      return;
    }
    const single = {
      model: synActiveGrid.model,
      init_time: synActiveGrid.init_time,
      valid_time: synActiveGrid.valid_time,
      level: synActiveGrid.level,
      metric: synMetric,
      hour: synActiveGrid.hour,
      stride_hours: synActiveGrid.stride_hours,
      lat: synActiveGrid.lat,
      lon: synActiveGrid.lon,
      values,
    };
    synopticMap.setSlice(single, vmin, vmax);
    synopticMap.setOpacity(synOpacity);
    return;
  }

  // All-metrics not ready — fast-fetch the single new metric. Bump the
  // token so any in-flight loadSynoptic() fetch (carrying the previous
  // metric for this hour) is discarded when it lands; otherwise it could
  // overwrite the canvas with the wrong metric for the active picker.
  if (!synInit) return;
  const myToken = ++synLoadToken;
  try {
    const slice = await fetchHewsonSlice({
      model: synModel,
      init: synInit,
      level: synLevel,
      metric: synMetric,
      hour: synHour,
    });
    if (myToken !== synLoadToken) return;
    synopticMap.setSlice(slice, vmin, vmax);
    synopticMap.setOpacity(synOpacity);
  } catch (err) {
    console.warn('Hewson metric refetch failed:', err);
    return;
  }

  // We discarded the in-flight all-metrics from the previous loadSynoptic()
  // by bumping the token; refetch so hover recovers (and the info-bar's
  // "loading hover…" suffix is cleared) without making the user wiggle a
  // control.
  fetchAllMetricsInBackground(myToken);
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
    changeActiveMetric();
  });

  const hourSlider = $('syn-hour-slider') as HTMLInputElement | null;
  const hourVal = $('syn-hour-value');
  hourSlider?.addEventListener('input', () => {
    synHour = parseInt(hourSlider.value);
    if (hourVal) hourVal.textContent = `+${synHour} h`;
    updateSynopticDatetime();
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

  wireButtonGroup('syn-scale-picker', 'scale', (v) => {
    synScale = v as ColorScale;
    // No refetch needed — just rescale the existing canvas + legend.
    if (synopticMap) {
      const { vmin, vmax } = vRangeFor(synMetric, synScale);
      synopticMap.setVRange(synMetric, vmin, vmax);
    }
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
    if (!modelsWithData.includes(synModel)) synModel = modelsWithData[0];
    repopulateModelPicker();
    repopulateInitPicker();
  }
  loadSynoptic();
}

// --- Tab switching ---

function switchTab(tab: Tab): void {
  currentTab = tab;
  // Note: syncUrl() is intentionally NOT called here. The init path in
  // main() calls switchTab() to hydrate from a non-default URL, and we
  // don't want that to rewrite the URL with smart-default fcHour before
  // the user has touched anything. The tab-click handler in main()
  // calls syncUrl() explicitly after switchTab() so user-driven tab
  // switches still keep the URL in sync.

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
  } else if (tab === 'synoptic') {
    setTimeout(() => { initSynopticTab(); }, 50);
  } else if (tab === 'climatology') {
    if (!climatologyTab) climatologyTab = new ClimatologyTab();
    climatologyTab.show();
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
  // Pan-European overview — no single flight, so 'auto' falls back to europe.
  setUnitsPreference(user.units_region);

  // Synoptic Forecast is opt-in per user. Hide the tab unless the user
  // enabled it in Settings → Account → Optional Services. The toggle is
  // off by default; the underlying endpoint only requires authentication.
  const synopticEnabled = user.synoptic_forecast_map_enabled === true;
  if (!synopticEnabled) {
    document.querySelector('#tabs .tab-btn[data-tab="synoptic"]')?.remove();
    $('panel-synoptic')?.remove();
  }

  // Experimental banner toggle — banner is now scoped to the Synoptic
  // tab only (it's still calibrating). The Forecast Overview map is
  // stable, so we don't want a page-wide "Experimental" badge anymore.
  $('experimental-toggle')?.addEventListener('click', () => {
    const detail = $('experimental-detail');
    if (detail) detail.classList.toggle('open');
  });

  // Init forecast map
  const container = $('map-container');
  if (!container) return;
  forecastMap = new WeatherMap(container);
  forecastMap.init();
  forecastMap.setAirportContextHandler((icao) => openAirportPanel(icao));

  // Set up the briefing-style info modal (used by the synoptic tab's (i)).
  initInfoPopup();

  // Wire controls
  wireForecastControls();
  wireSynopticControls();

  // Tab clicks
  $('tabs')?.addEventListener('click', (e) => {
    const btn = (e.target as HTMLElement).closest('.tab-btn') as HTMLElement | null;
    if (!btn) return;
    const tab = btn.getAttribute('data-tab') as Tab;
    if (!tab) return;
    switchTab(tab);
    syncUrl();
  });

  // Hydrate from the URL. Anything not in the URL falls back to the
  // module-level default. `fc.hour` gets a smart fallback (nearest sample
  // hour to "now") so the default landing isn't always 12Z.
  const urlParams = new URLSearchParams(window.location.search);
  const init = mapsUrlState.read();
  currentTab  = init.tab;
  // Clamp: the gate above removed the synoptic panel for users who
  // haven't opted in, so a shared `?tab=synoptic` link from an admin
  // would land them on a page where switchTab() strips `active` from
  // every other panel and none gets it back — blank content area. Drop
  // them on the forecast tab instead.
  if (currentTab === 'synoptic' && !synopticEnabled) currentTab = 'forecast';
  fcDay       = init['fc.day'];
  fcHour      = init['fc.hour'];
  fcModel     = init['fc.model'];
  fcMetric    = init['fc.metric'];

  if (!urlParams.has('fc.hour')) {
    const nowHour = new Date().getUTCHours();
    const sampleHours = [6, 9, 12, 15, 18];
    const nearestIdx = sampleHours.reduce((best, h, i) =>
      Math.abs(h - nowHour) < Math.abs(sampleHours[best] - nowHour) ? i : best, 0);
    fcHour = sampleHours[nearestIdx];
  }

  // Reflect hydrated state into the controls (HTML defaults match
  // module defaults; setActive/value-set is a no-op when they match).
  setActive('day-picker', String(fcDay), 'day');
  setActive('hour-picker', String(fcHour), 'hour');
  setActive('model-picker', fcModel, 'model');
  const fcMetricSel = $('metric-picker') as HTMLSelectElement | null;
  if (fcMetricSel) fcMetricSel.value = fcMetric;

  // Wire the share button (in the page header). Flashes "Link copied"
  // on success; on mobile the OS share sheet handles it instead.
  const shareBtn = $('share-link-btn') as HTMLButtonElement | null;
  if (shareBtn) {
    shareBtn.title = t('maps.shareLinkTitle');
    shareBtn.querySelector('.share-link-label')!.textContent = t('maps.shareLink');
    shareBtn.addEventListener('click', async () => {
      // Flush current state to the URL before sharing — covers the
      // first-load case where the smart-default fcHour was assigned
      // but no control has been touched yet, so the URL is still bare.
      syncUrl();
      const result = await shareCurrentUrl(document.title);
      // 'shared' = OS share sheet handled it (no flash, nothing was copied).
      // 'copied' = clipboard write succeeded — flash the button.
      // false    = fell back to prompt() — user already saw the URL inline.
      if (result !== 'copied') return;
      const label = shareBtn.querySelector('.share-link-label') as HTMLElement;
      const original = label.textContent;
      label.textContent = t('maps.shareLinkCopied');
      shareBtn.disabled = true;
      setTimeout(() => { label.textContent = original; shareBtn.disabled = false; }, 2000);
    });
  }

  // Load initial data for the active tab. If we hydrated to a non-forecast
  // tab from the URL, switchTab() handles its lazy init; the forecast tab
  // is already the default-visible panel so we just kick off the fetch.
  updateForecastDatetime();
  if (currentTab === 'forecast') {
    loadForecast();
  } else {
    switchTab(currentTab);
  }

  // Re-open the airport-profile panel if the URL carried one. Defer until
  // after the forecast load triggers a marker render so setHighlightedIcao
  // has a marker to highlight.
  const urlIcao = init['fc.apt'];
  if (urlIcao && currentTab === 'forecast') {
    openAirportPanel(urlIcao, { initialModel: init['fc.apModel'] });
  }
}

main().catch(console.error);
