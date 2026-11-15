/** Briefing route-map chrome (#998): the #996 map style on the web.
 *
 *  Drawn over the map as one overlay (`#map-chrome`, a sibling of the Leaflet
 *  container inside `.rm-stage`), never inside Leaflet, so clicks never reach
 *  the map:
 *
 *  - top bar: the altitude stepper `◀ FL080 ▶` (only for a metric that uses
 *    altitude; tapping the label goes back to cruise) and Hide on the right;
 *  - bottom panel: SHOWING (metrics grouped At altitude / Along route), MODEL
 *    (the cross-section's), LAYERS (Airports / Observed / Forecast, each only
 *    when the briefing has it), the legend and the airport valid time;
 *  - floating: Fit route and Open full forecast map, above the panel.
 *
 *  Hidden leaves one pill, `FL080 · Show` or `Show`. The panel's height is
 *  published as `--rm-bottom-inset` on the stage so the observed badge and the
 *  Leaflet attribution sit above it.
 *
 *  SYNC: iOS `Views/Map/RouteMapView.swift`, Android `ui/map/RouteMapTabScreen.kt`. */

import type { MapMetric } from './metrics';
import { mapMetricGroups } from './metrics';
import { routeLegendHtml, airportLegendHtml } from './legend';
import type { MapLegend } from '../weather-map-format';
import { FORECAST_METRICS, METRIC_LABEL } from '../weather-map-format';
import { escapeHtml, modelLabel } from '../../utils';
import { t } from '../../i18n/i18n';

export const ALTITUDE_STEP_FT = 500;

export interface MapChromeAirports {
  visible: boolean;
  /** Active forecast-map metric id. */
  metric: string;
  /** The selected model has airport data for the slot. */
  modelSupported: boolean;
  model: string;
  /** Valid time of the snapshot ("Wed 12Z"); undefined while loading. */
  timeLabel?: string;
  fullMapUrl?: string;
  /** The airport metric's legend, drawn as the panel's second legend line. */
  legend: MapLegend | null;
}

export interface MapChromeObserved {
  /** Imagery choices present in this briefing ('' = none first). */
  options: Array<{ id: string; label: string }>;
  /** The resolved imagery source ('' for none). */
  selected: string;
  satellite: boolean;
  /** `null` when the briefing has no lightning. */
  lightning: boolean | null;
  cells: boolean;
  opacity: number;
}

export interface MapChromeState {
  hidden: boolean;
  metric: MapMetric;
  altitudeFt: number;
  ceilingFt: number;
  models: string[];
  selectedModel: string;
  airports: MapChromeAirports | null;
  observed: MapChromeObserved | null;
  /** `null` when the briefing has no fronts. */
  fronts: boolean | null;
}

export interface MapChromeCallbacks {
  onMetric(id: string): void;
  /** `null` = back to cruise. */
  onAltitude(ft: number | null): void;
  onModel(model: string): void;
  onHidden(hidden: boolean): void;
  onFitRoute(): void;
  onAirports(visible: boolean): void;
  onAirportMetric(metric: string): void;
  onObservedSource(source: string): void;
  onSatellite(show: boolean): void;
  onLightning(show: boolean): void;
  onCells(show: boolean): void;
  onOpacity(opacity: number): void;
  onFronts(show: boolean): void;
}

/** `FL080`, `SFC` below 100 ft. */
export function formatFlightLevel(ft: number): string {
  if (ft < 100) return 'SFC';
  return `FL${String(Math.round(ft / 100)).padStart(3, '0')}`;
}

/** One stepper press, clamped to 0 … ceiling. */
export function stepAltitude(ft: number, dir: -1 | 1, ceilingFt: number): number {
  const snapped = Math.round(ft / ALTITUDE_STEP_FT) * ALTITUDE_STEP_FT;
  return Math.max(0, Math.min(ceilingFt, snapped + dir * ALTITUDE_STEP_FT));
}

// The LAYERS popover stays open across re-renders (every toggle inside it
// re-renders the chrome), so its state lives here, not in the DOM.
let layersOpen = false;

const ICON_FIT = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
const ICON_MAP = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M1 6v16l7-4 8 4 7-4V2l-7 4-8-4-7 4zM8 2v16M16 6v16" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';
const ICON_HIDE = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M3 3l18 18M10.6 5.1A9.8 9.8 0 0 1 12 5c6 0 10 7 10 7a17 17 0 0 1-3.2 3.9M6.6 6.6C3.6 8.4 2 12 2 12s4 7 10 7a9.6 9.6 0 0 0 5.4-1.6M9.9 9.9a3 3 0 0 0 4.2 4.2" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';

function checkbox(id: string, checked: boolean, label: string, title = ''): string {
  const tip = title ? ` title="${escapeHtml(title)}"` : '';
  return `<label class="rm-check"${tip}><input type="checkbox" id="${id}"${checked ? ' checked' : ''}><span>${escapeHtml(label)}</span></label>`;
}

/** What LAYERS reads when closed: the layers that are on, or "None". */
export function layersSummary(s: MapChromeState): string {
  const on: string[] = [];
  if (s.airports?.visible) on.push(t('viz.airports'));
  if (s.observed) {
    if (s.observed.selected) on.push(t('map.ctl.radar'));
    if (s.observed.satellite) on.push(t('viz.observed.satellite'));
    if (s.observed.lightning) on.push(t('viz.observed.lightning'));
    if (s.observed.cells) on.push(t('viz.observed.cells'));
  }
  if (s.fronts) on.push(t('viz.fronts'));
  return on.length ? on.join(', ') : t('map.ctl.layersNone');
}

function layersBody(s: MapChromeState): string {
  let html = '';
  if (s.airports) {
    const a = s.airports;
    html += `<div class="rm-layer-section"><div class="rm-layer-title">${escapeHtml(t('viz.airports'))}</div>`;
    html += checkbox('rm-airports', a.visible, t('map.ctl.airportsOn'));
    if (a.modelSupported) {
      html += '<select id="rm-airport-metric" class="rm-select">';
      for (const m of FORECAST_METRICS) {
        html += `<option value="${m}"${m === a.metric ? ' selected' : ''}>${escapeHtml(METRIC_LABEL[m])}</option>`;
      }
      html += '</select>';
    } else {
      // Empty because of the model, not the weather (forecast-page.md).
      html += `<div class="rm-note">${escapeHtml(t('viz.airports.noModelData', { model: modelLabel(a.model) }))}</div>`;
    }
    html += '</div>';
  }
  if (s.observed) {
    const o = s.observed;
    html += `<div class="rm-layer-section"><div class="rm-layer-title">${escapeHtml(t('map.ctl.observedSection'))}</div>`;
    if (o.options.length > 1) {
      html += `<label class="rm-field"><span>${escapeHtml(t('map.ctl.radar'))}</span><select id="rm-observed-source" class="rm-select">`;
      for (const opt of o.options) {
        html += `<option value="${escapeHtml(opt.id)}"${opt.id === o.selected ? ' selected' : ''}>${escapeHtml(opt.label)}</option>`;
      }
      html += '</select></label>';
    }
    html += checkbox('rm-satellite', o.satellite, t('map.ctl.satellite'));
    if (o.lightning !== null) html += checkbox('rm-lightning', o.lightning, t('viz.observed.lightning'));
    html += checkbox('rm-cells', o.cells, t('map.ctl.cells'), t('viz.observed.cellsHint'));
    if (o.selected || o.satellite) {
      const pct = Math.round(o.opacity * 100);
      html += `<label class="rm-field"><span>${escapeHtml(t('viz.observed.opacity'))}</span>`
        + `<input type="range" id="rm-opacity" min="10" max="100" step="5" value="${pct}">`
        + `<span class="rm-opacity-value" id="rm-opacity-value">${pct}%</span></label>`;
    }
    html += '</div>';
  }
  if (s.fronts !== null) {
    html += `<div class="rm-layer-section"><div class="rm-layer-title">${escapeHtml(t('map.ctl.forecastSection'))}</div>`;
    html += checkbox('rm-fronts', s.fronts, t('viz.fronts'));
    html += '</div>';
  }
  return html;
}

function pick(caption: string, inner: string): string {
  return `<div class="rm-pick"><span class="rm-pick-caption">${escapeHtml(caption)}</span>${inner}</div>`;
}

/** Pure HTML for the whole chrome (tested in map-chrome.test.ts). */
export function mapChromeHtml(s: MapChromeState): string {
  const fl = formatFlightLevel(s.altitudeFt);
  const usesAlt = s.metric.altitudeDependent;

  if (s.hidden) {
    const text = usesAlt ? `${fl} · ${t('map.ctl.show')}` : t('map.ctl.show');
    return `<div class="rm-top"><span class="rm-spacer"></span>`
      + `<button type="button" class="rm-pill" id="rm-show">${escapeHtml(text)}</button></div>`;
  }

  let html = '<div class="rm-top">';
  if (usesAlt) {
    const down = s.altitudeFt > 0 ? '' : ' disabled';
    const up = s.altitudeFt < s.ceilingFt ? '' : ' disabled';
    html += '<div class="rm-stepper rm-surface">'
      + `<button type="button" id="rm-alt-down" aria-label="${escapeHtml(t('map.ctl.altDown'))}"${down}>◀</button>`
      + `<button type="button" id="rm-alt-reset" class="rm-alt-label" title="${escapeHtml(t('map.ctl.altReset'))}">${fl}</button>`
      + `<button type="button" id="rm-alt-up" aria-label="${escapeHtml(t('map.ctl.altUp'))}"${up}>▶</button>`
      + '</div>';
  }
  html += '<span class="rm-spacer"></span>';
  const hide = escapeHtml(t('map.ctl.hide'));
  html += `<button type="button" class="rm-circle rm-surface" id="rm-hide" title="${hide}" aria-label="${hide}">${ICON_HIDE}</button>`;
  html += '</div>';

  html += '<div class="rm-bottom">';
  html += '<div class="rm-floating">';
  const fit = escapeHtml(t('map.ctl.fitRoute'));
  html += `<button type="button" class="rm-circle rm-surface" id="rm-fit" title="${fit}" aria-label="${fit}">${ICON_FIT}</button>`;
  if (s.airports?.fullMapUrl) {
    const open = escapeHtml(t('viz.airports.openMap'));
    html += `<a class="rm-open-map rm-surface" id="rm-open-map" href="${escapeHtml(s.airports.fullMapUrl)}" target="_blank" rel="noopener" title="${open}">${ICON_MAP}<span>${open}</span></a>`;
  }
  html += '</div>';

  html += '<div class="rm-panel rm-surface">';
  html += '<div class="rm-picks">';
  let metricSelect = '<select id="rm-metric" class="rm-select">';
  for (const g of mapMetricGroups()) {
    metricSelect += `<optgroup label="${escapeHtml(g.label)}">`;
    for (const m of g.metrics) {
      metricSelect += `<option value="${m.id}"${m.id === s.metric.id ? ' selected' : ''}>${escapeHtml(m.label)}</option>`;
    }
    metricSelect += '</optgroup>';
  }
  metricSelect += '</select>';
  html += pick(t('map.ctl.showing'), metricSelect);

  if (s.models.length > 1) {
    let modelSelect = '<select id="rm-model" class="rm-select">';
    for (const m of s.models) {
      modelSelect += `<option value="${escapeHtml(m)}"${m === s.selectedModel ? ' selected' : ''}>${escapeHtml(modelLabel(m))}</option>`;
    }
    modelSelect += '</select>';
    html += pick(t('map.ctl.model'), modelSelect);
  }

  const body = layersBody(s);
  if (body) {
    html += pick(t('map.ctl.layers'),
      `<details class="rm-layers" id="rm-layers"${layersOpen ? ' open' : ''}>`
      + `<summary class="rm-select">${escapeHtml(layersSummary(s))}</summary>`
      + `<div class="rm-layers-pop rm-surface">${body}</div></details>`);
  }
  html += '</div>';

  html += '<div class="rm-legend">';
  html += routeLegendHtml(s.metric);
  const a = s.airports;
  if (a?.visible && a.modelSupported) {
    html += airportLegendHtml(a.legend);
    html += `<div class="rm-valid-time" title="${escapeHtml(t('viz.airports.timeHint'))}">`
      + `${escapeHtml(t('viz.airports'))}: <span id="map-forecast-time">${escapeHtml(a.timeLabel ?? '…')}</span></div>`;
  }
  html += '</div>';
  html += '</div>'; // panel
  html += '</div>'; // bottom
  return html;
}

/** Render the chrome into `container` and wire it. */
export function renderMapChrome(container: HTMLElement, s: MapChromeState, cb: MapChromeCallbacks): void {
  container.innerHTML = mapChromeHtml(s);
  container.classList.toggle('rm-chrome-hidden', s.hidden);

  const on = <K extends keyof HTMLElementEventMap>(id: string, ev: K, fn: (el: any) => void): void => {
    const el = container.querySelector(`#${id}`);
    if (el) el.addEventListener(ev, () => fn(el));
  };
  on('rm-show', 'click', () => cb.onHidden(false));
  on('rm-hide', 'click', () => cb.onHidden(true));
  on('rm-fit', 'click', () => cb.onFitRoute());
  on('rm-alt-down', 'click', () => cb.onAltitude(stepAltitude(s.altitudeFt, -1, s.ceilingFt)));
  on('rm-alt-up', 'click', () => cb.onAltitude(stepAltitude(s.altitudeFt, 1, s.ceilingFt)));
  on('rm-alt-reset', 'click', () => cb.onAltitude(null));
  on('rm-metric', 'change', (el: HTMLSelectElement) => cb.onMetric(el.value));
  on('rm-model', 'change', (el: HTMLSelectElement) => cb.onModel(el.value));
  on('rm-layers', 'toggle', (el: HTMLDetailsElement) => { layersOpen = el.open; });
  on('rm-airports', 'change', (el: HTMLInputElement) => cb.onAirports(el.checked));
  on('rm-airport-metric', 'change', (el: HTMLSelectElement) => cb.onAirportMetric(el.value));
  on('rm-observed-source', 'change', (el: HTMLSelectElement) => cb.onObservedSource(el.value));
  on('rm-satellite', 'change', (el: HTMLInputElement) => cb.onSatellite(el.checked));
  on('rm-lightning', 'change', (el: HTMLInputElement) => cb.onLightning(el.checked));
  on('rm-cells', 'change', (el: HTMLInputElement) => cb.onCells(el.checked));
  on('rm-fronts', 'change', (el: HTMLInputElement) => cb.onFronts(el.checked));
  // Readout follows the drag; the value is committed on release, because the
  // commit re-renders the chrome and would drop the slider mid-drag.
  on('rm-opacity', 'input', (el: HTMLInputElement) => {
    const out = container.querySelector('#rm-opacity-value');
    if (out) out.textContent = `${el.value}%`;
  });
  on('rm-opacity', 'change', (el: HTMLInputElement) => cb.onOpacity(Number(el.value) / 100));

  // Close the popover on a click elsewhere.
  const layers = container.querySelector('#rm-layers') as HTMLDetailsElement | null;
  if (layers && layers.open) {
    const close = (e: MouseEvent): void => {
      if (!container.contains(layers) ) { document.removeEventListener('click', close, true); return; }
      if (!layers.contains(e.target as Node)) {
        layers.open = false;
        layersOpen = false;
        document.removeEventListener('click', close, true);
      }
    };
    document.addEventListener('click', close, true);
  }

  const stage = container.parentElement;
  if (stage) {
    const panel = container.querySelector('.rm-panel') as HTMLElement | null;
    const inset = panel ? panel.offsetHeight + 8 : 0;
    stage.style.setProperty('--rm-bottom-inset', `${inset}px`);
  }
}
