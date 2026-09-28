/** Historical tab (#629) — METAR, TAF and model runs at a past instant.
 *
 * Pickers: date (from `/maps/historical/range`), 30-min time, model lead
 * (Latest / D-1..D-6), source (METAR / TAF / Worst / Majority / GFS / ICON /
 * ECMWF) and metric (the forecast tab's served catalog).
 *
 * All selection logic is server-side (`tasks/historical_map.py`): which
 * report, which TAF reading, which run and valid time, and why a source is
 * empty. This module only fetches on date/time/lead changes and recolours
 * on source/metric changes, reusing `WeatherMap` — the payload's airport
 * entries have the forecast map's shape, with METAR/TAF under `observed`.
 */

import {
  fetchHistoricalMap, fetchHistoricalRange,
  type ForecastAirport, type ForecastMapResponse,
  type HistoricalAirport, type HistoricalMapResponse, type HistoricalRange,
  type HistoricalSourceInfo,
} from '../adapters/maps-adapter';
import { WeatherMap, FORECAST_METRICS, type ForecastMetric } from './weather-map';
import {
  CAT_COLORS, METRIC_LABEL, formatMetricValue,
} from './weather-map-format';
import { isConsensusMode } from './weather-map-consensus';
import { $, escapeHtml } from '../utils';

export const HISTORICAL_SOURCES = [
  'metar', 'taf', 'worst', 'majority', 'gfs', 'icon', 'ecmwf',
] as const;
export type HistoricalSource = typeof HISTORICAL_SOURCES[number];
const OBSERVED: readonly string[] = ['metar', 'taf'];
const MODELS: readonly string[] = ['gfs', 'icon', 'ecmwf'];
const SOURCE_LABEL: Record<string, string> = {
  metar: 'METAR', taf: 'TAF', worst: 'Worst', majority: 'Majority',
  gfs: 'GFS', icon: 'ICON', ecmwf: 'ECMWF',
};
export const HISTORICAL_LEADS: readonly number[] = [0, 1, 2, 3, 4, 5, 6];
/** Compact column headers for the airport panel (full label in the tooltip). */
const PANEL_LABEL: Partial<Record<ForecastMetric, string>> = {
  flight_category: 'Cat', ceiling_ft: 'Ceiling', visibility_m: 'Vis',
  wind_speed_kt: 'Wind', crosswind_kt: 'X-wind', alternate_needed: 'Alt req',
};

/** Deep-linkable state (see `hist.*` keys in maps-main.ts). Empty date/time
 *  mean "latest available slot". */
export interface HistoricalState {
  date: string;   // YYYY-MM-DD (UTC)
  time: string;   // HH:MM (UTC)
  lead: number;
  source: HistoricalSource;
  metric: ForecastMetric;
  apt: string;
}

const DAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];

function pad(n: number): string { return String(n).padStart(2, '0'); }
function isoDate(d: Date): string { return `${d.getUTCFullYear()}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}`; }
function hhmm(d: Date): string { return `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}`; }
function dateLabel(d: Date): string {
  return `${DAYS[d.getUTCDay()]} ${pad(d.getUTCDate())}-${MONTHS[d.getUTCMonth()]}-${String(d.getUTCFullYear()).slice(2)}`;
}
/** "05 00Z" — day-of-month + hour, the compact model-run notation. */
function runLabel(iso: string | undefined): string {
  if (!iso) return '';
  const d = new Date(iso);
  return `${pad(d.getUTCDate())} ${pad(d.getUTCHours())}Z`;
}
function timeLabel(iso: string | undefined): string {
  return iso ? `${hhmm(new Date(iso))}Z` : '';
}

/** Why a model has nothing at this instant/lead, in words. */
function reasonText(source: string, info: HistoricalSourceInfo | undefined, lead: number): string {
  const name = SOURCE_LABEL[source] ?? source.toUpperCase();
  switch (info?.reason) {
    case 'beyond_horizon':
      return `${name} isn't stored ${lead} day${lead === 1 ? '' : 's'} ahead — its map horizon is shorter.`;
    case 'no_valid_time':
      return `No model sample at this time — models are sampled every 3 h from 06Z to 18Z.`;
    case 'no_run':
      return `No ${name} run was stored for this time and lead.`;
    default:
      return `No ${name} data at this time.`;
  }
}

export class HistoricalTab {
  private map: WeatherMap | null = null;
  private range: HistoricalRange | null = null;
  private data: HistoricalMapResponse | null = null;
  private state: HistoricalState;
  private loadToken = 0;
  private initialised = false;
  private readonly onStateChange: () => void;

  constructor(initial: HistoricalState, onStateChange: () => void) {
    this.state = { ...initial };
    this.onStateChange = onStateChange;
  }

  getState(): HistoricalState {
    return { ...this.state };
  }

  async show(): Promise<void> {
    if (!this.initialised) {
      this.initialised = true;
      await this.init();
    } else {
      this.render();
    }
    setTimeout(() => this.map?.invalidateSize(), 100);
  }

  // --- Init -----------------------------------------------------------------

  private async init(): Promise<void> {
    const container = $('map-container-historical');
    if (!container) return;
    this.map = new WeatherMap(container);
    this.map.init();
    this.map.setAirportClickHandler((icao) => this.openPanel(icao));

    this.buildMetricPicker();
    this.buildLeadPicker();
    this.buildSourcePicker();
    this.wireControls();

    this.info('Loading available dates...');
    try {
      this.range = await fetchHistoricalRange();
    } catch (err) {
      this.info(`Failed to load the historical range: ${err instanceof Error ? err.message : err}`);
      return;
    }
    this.clampState();
    this.buildDatePicker();
    this.buildTimePicker();
    this.syncControls();
    await this.load();
  }

  /** Latest selectable instant (server's 30-min floor of "now"). */
  private latest(): Date {
    return new Date(this.range?.latest ?? Date.now());
  }

  /** Snap a hydrated/empty date+time into [earliest, latest]. */
  private clampState(): void {
    const latest = this.latest();
    const earliestIso = this.range?.earliest_observation ?? this.range?.earliest_model;
    const earliest = earliestIso ? new Date(earliestIso) : latest;
    let at = this.selectedInstant();
    if (!at || isNaN(at.getTime()) || at > latest) at = latest;
    if (at < earliest) at = earliest;
    const step = (this.range?.step_minutes ?? 30) * 60_000;
    at = new Date(Math.floor(at.getTime() / step) * step);
    this.state.date = isoDate(at);
    this.state.time = hhmm(at);
  }

  private selectedInstant(): Date | null {
    if (!this.state.date || !this.state.time) return null;
    return new Date(`${this.state.date}T${this.state.time}:00Z`);
  }

  // --- Pickers ----------------------------------------------------------------

  private buildDatePicker(): void {
    const sel = $('hist-date') as HTMLSelectElement | null;
    if (!sel || !this.range) return;
    const latest = this.latest();
    const earliestIso = this.range.earliest_observation ?? this.range.earliest_model ?? this.range.latest;
    const earliest = new Date(earliestIso);
    const modelFrom = this.range.earliest_model ? isoDate(new Date(this.range.earliest_model)) : null;
    const options: string[] = [];
    const d = new Date(Date.UTC(latest.getUTCFullYear(), latest.getUTCMonth(), latest.getUTCDate()));
    const stop = Date.UTC(earliest.getUTCFullYear(), earliest.getUTCMonth(), earliest.getUTCDate());
    while (d.getTime() >= stop) {
      const value = isoDate(d);
      // Before the model history starts only METAR/TAF exist — say so in the list.
      const obsOnly = modelFrom === null || value < modelFrom;
      options.push(`<option value="${value}">${dateLabel(d)}${obsOnly ? ' (METAR/TAF only)' : ''}</option>`);
      d.setUTCDate(d.getUTCDate() - 1);
    }
    sel.innerHTML = options.join('');
  }

  private buildTimePicker(): void {
    const sel = $('hist-time') as HTMLSelectElement | null;
    if (!sel) return;
    const step = this.range?.step_minutes ?? 30;
    const latest = this.latest();
    const isToday = this.state.date === isoDate(latest);
    const options: string[] = [];
    for (let m = 0; m < 24 * 60; m += step) {
      const value = `${pad(Math.floor(m / 60))}:${pad(m % 60)}`;
      const future = isToday && value > hhmm(latest);
      options.push(`<option value="${value}"${future ? ' disabled' : ''}>${value}Z</option>`);
    }
    sel.innerHTML = options.join('');
  }

  private buildLeadPicker(): void {
    const group = $('hist-lead');
    if (!group) return;
    group.innerHTML = HISTORICAL_LEADS.map((n) =>
      `<button class="btn-toggle" data-lead="${n}" title="${n === 0
        ? 'Latest run fetched before the selected time'
        : `Latest run fetched at least ${n} day${n === 1 ? '' : 's'} before the selected time`}">${n === 0 ? 'Latest' : `D-${n}`}</button>`,
    ).join('');
  }

  private buildSourcePicker(): void {
    const group = $('hist-source');
    if (!group) return;
    group.innerHTML = HISTORICAL_SOURCES.map((s) =>
      `<button class="btn-toggle" data-source="${s}">${SOURCE_LABEL[s]}</button>`,
    ).join('');
  }

  private buildMetricPicker(): void {
    const sel = $('hist-metric') as HTMLSelectElement | null;
    if (!sel) return;
    sel.innerHTML = FORECAST_METRICS.map((m) =>
      `<option value="${m}">${escapeHtml(METRIC_LABEL[m])}</option>`,
    ).join('');
  }

  /** Reflect state into the controls and mark unavailable sources/leads. */
  private syncControls(): void {
    const dateSel = $('hist-date') as HTMLSelectElement | null;
    if (dateSel) dateSel.value = this.state.date;
    const timeSel = $('hist-time') as HTMLSelectElement | null;
    if (timeSel) timeSel.value = this.state.time;
    const metricSel = $('hist-metric') as HTMLSelectElement | null;
    if (metricSel) metricSel.value = this.state.metric;

    for (const btn of $('hist-lead')?.querySelectorAll('button') ?? []) {
      btn.classList.toggle('active', btn.dataset.lead === String(this.state.lead));
    }
    for (const btn of $('hist-source')?.querySelectorAll('button') ?? []) {
      const s = btn.dataset.source!;
      btn.classList.toggle('active', s === this.state.source);
      const unavailable = !this.sourceAvailable(s);
      btn.classList.toggle('unavailable', unavailable);
      btn.setAttribute('aria-disabled', String(unavailable));
    }
    const label = $('hist-datetime');
    const at = this.selectedInstant();
    if (label && at) label.textContent = `${dateLabel(at)} ${hhmm(at)}Z`;
  }

  private sourceAvailable(source: string): boolean {
    if (!this.data) return true;  // unknown until loaded — don't grey out
    if (isConsensusMode(source)) return MODELS.some((m) => this.data!.sources[m]?.available);
    return this.data.sources[source]?.available ?? false;
  }

  private wireControls(): void {
    ($('hist-date') as HTMLSelectElement | null)?.addEventListener('change', (e) => {
      this.state.date = (e.target as HTMLSelectElement).value;
      this.buildTimePicker();
      this.clampState();
      this.changed(true);
    });
    ($('hist-time') as HTMLSelectElement | null)?.addEventListener('change', (e) => {
      this.state.time = (e.target as HTMLSelectElement).value;
      this.changed(true);
    });
    $('hist-prev')?.addEventListener('click', () => this.step(-1));
    $('hist-next')?.addEventListener('click', () => this.step(1));
    $('hist-lead')?.addEventListener('click', (e) => {
      const btn = (e.target as HTMLElement).closest('button');
      if (!btn || btn.dataset.lead == null) return;
      this.state.lead = parseInt(btn.dataset.lead, 10);
      this.changed(true);
    });
    $('hist-source')?.addEventListener('click', (e) => {
      const btn = (e.target as HTMLElement).closest('button');
      if (!btn || btn.dataset.source == null) return;
      const s = btn.dataset.source as HistoricalSource;
      if (!this.sourceAvailable(s)) {
        // Stays clickable so it can explain itself (see .unavailable CSS).
        const info = isConsensusMode(s) ? undefined : this.data?.sources[s];
        this.info(OBSERVED.includes(s)
          ? `No ${SOURCE_LABEL[s]} reports at this time.`
          : isConsensusMode(s) ? 'No model has data at this time and lead.' : reasonText(s, info, this.state.lead));
        return;
      }
      this.state.source = s;
      this.changed(false);
    });
    ($('hist-metric') as HTMLSelectElement | null)?.addEventListener('change', (e) => {
      this.state.metric = (e.target as HTMLSelectElement).value as ForecastMetric;
      this.changed(false);
    });
  }

  /** Step the time by ±1 slot, crossing midnight into the adjacent date. */
  private step(direction: number): void {
    const at = this.selectedInstant();
    if (!at) return;
    const stepMs = (this.range?.step_minutes ?? 30) * 60_000;
    const next = new Date(at.getTime() + direction * stepMs);
    if (next > this.latest()) return;
    const prevDate = this.state.date;
    this.state.date = isoDate(next);
    this.state.time = hhmm(next);
    this.clampState();
    if (this.state.date !== prevDate) this.buildTimePicker();
    this.changed(true);
  }

  private changed(refetch: boolean): void {
    this.syncControls();
    this.onStateChange();
    if (refetch) void this.load();
    else this.render();
  }

  // --- Data -------------------------------------------------------------------

  private async load(): Promise<void> {
    const at = this.selectedInstant();
    if (!at) return;
    const token = ++this.loadToken;
    this.info('Loading...');
    try {
      const data = await fetchHistoricalMap(at.toISOString(), this.state.lead);
      if (token !== this.loadToken) return;  // the user moved on
      this.data = data;
    } catch (err) {
      if (token !== this.loadToken) return;
      this.info(`Failed to load: ${err instanceof Error ? err.message : err}`);
      return;
    }
    this.syncControls();
    this.render();
    if (this.state.apt) this.openPanel(this.state.apt, false);
  }

  /** The payload re-shaped for `WeatherMap`: METAR/TAF are presented as the
   *  per-airport "model" being shown, and airports with nothing for the
   *  selected source are left off rather than drawn as a misleading default. */
  private viewFor(source: string): ForecastMapResponse {
    const airports: ForecastAirport[] = [];
    for (const apt of this.data?.airports ?? []) {
      if (OBSERVED.includes(source)) {
        const obs = apt.observed[source as 'metar' | 'taf'];
        if (!obs) continue;
        airports.push({ ...apt, models: { [source]: obs }, consensus: apt.consensus! } as unknown as ForecastAirport);
      } else if (isConsensusMode(source)) {
        if (!apt.consensus) continue;
        airports.push(apt as unknown as ForecastAirport);
      } else {
        if (!apt.models[source]) continue;
        airports.push(apt as unknown as ForecastAirport);
      }
    }
    return { forecast_time: this.data?.at ?? '', model_init_times: {}, airports };
  }

  private render(): void {
    if (!this.map || !this.data) return;
    const view = this.viewFor(this.state.source);
    this.map.setForecastData(view, this.state.metric, this.state.source);
    this.map.setHighlightedIcao(this.state.apt || null);
    const s = this.state.source;
    // An empty map must say why, not read as "all clear".
    const why = this.sourceAvailable(s) ? ''
      : OBSERVED.includes(s) ? `No ${SOURCE_LABEL[s]} reports at this time. `
      : isConsensusMode(s) ? 'No model has data at this time and lead. '
      : `${reasonText(s, this.data.sources[s], this.state.lead)} `;
    this.info(why + this.summaryLine(view.airports.length));
  }

  private summaryLine(shown: number): string {
    const d = this.data!;
    const parts: string[] = [`${shown} airports`];
    const metar = d.sources.metar;
    parts.push(`METAR: ${metar?.count ?? 0} (≤${metar?.max_age_min ?? 90} min old)`);
    parts.push(`TAF: ${d.sources.taf?.count ?? 0}`);
    for (const m of MODELS) {
      const info = d.sources[m];
      if (info?.available) {
        parts.push(`${SOURCE_LABEL[m]}: run ${runLabel(info.model_init_time)}, valid ${timeLabel(info.valid_time)} (+${info.lead_hours}h)`);
      } else {
        parts.push(`${SOURCE_LABEL[m]}: —`);
      }
    }
    return parts.join(' | ');
  }

  private info(text: string): void {
    const el = $('map-info-historical');
    if (el) el.textContent = text;
  }

  // --- Airport panel ------------------------------------------------------------

  private openPanel(icao: string, notify = true): void {
    const host = $('hist-panel-host') as HTMLElement | null;
    const apt = this.data?.airports.find((a) => a.icao === icao);
    if (!host || !apt) return;
    this.state.apt = icao;
    host.style.display = '';
    host.innerHTML = this.panelHtml(apt);
    host.querySelector('.hist-panel-close')?.addEventListener('click', () => this.closePanel());
    this.map?.setHighlightedIcao(icao);
    if (notify) this.onStateChange();
    setTimeout(() => this.map?.invalidateSize(), 50);
  }

  private closePanel(): void {
    const host = $('hist-panel-host') as HTMLElement | null;
    if (host) { host.style.display = 'none'; host.innerHTML = ''; }
    this.state.apt = '';
    this.map?.setHighlightedIcao(null);
    this.onStateChange();
    setTimeout(() => this.map?.invalidateSize(), 50);
  }

  private panelHtml(apt: HistoricalAirport): string {
    const d = this.data!;
    const metrics: ForecastMetric[] = [
      'flight_category', 'ceiling_ft', 'visibility_m', 'wind_speed_kt', 'crosswind_kt', 'alternate_needed',
    ];
    const rows: Array<{ key: string; label: string; data?: Record<string, unknown>; note: string }> = [];
    const metar = apt.observed.metar;
    rows.push({
      key: 'metar', label: 'METAR', data: metar as Record<string, unknown> | undefined,
      note: metar ? `${timeLabel(metar.observation_time)}${metar.report_type === 'SPECI' ? ' SPECI' : ''}` : 'none ≤90 min',
    });
    const taf = apt.observed.taf;
    rows.push({
      key: 'taf', label: 'TAF', data: taf as Record<string, unknown> | undefined,
      note: taf ? (taf.temporary_type ? `${taf.temporary_type} ${taf.temporary_category}` : 'prevailing') : 'none valid',
    });
    for (const m of MODELS) {
      const md = apt.models[m];
      const info = d.sources[m];
      rows.push({
        key: m, label: SOURCE_LABEL[m], data: md as unknown as Record<string, unknown> | undefined,
        note: md ? `${runLabel(md.model_init_time)} → ${timeLabel(md.valid_time)}`
          : info?.available ? 'no data here' : (info?.reason ?? '').replace(/_/g, ' '),
      });
    }

    const head = metrics.map((m) =>
      `<th title="${escapeHtml(METRIC_LABEL[m])}">${escapeHtml(PANEL_LABEL[m] ?? METRIC_LABEL[m])}</th>`,
    ).join('');
    const body = rows.map((r) => {
      const cells = metrics.map((m) => {
        if (!r.data) return '<td class="hist-muted">—</td>';
        if (m === 'flight_category') {
          const cat = r.data.flight_category as string | undefined;
          const color = cat ? CAT_COLORS[cat] ?? '#888' : '#888';
          return `<td><span class="hist-cat" style="background:${color}">${escapeHtml(cat ?? '—')}</span></td>`;
        }
        return `<td>${escapeHtml(formatMetricValue(r.data, m))}</td>`;
      }).join('');
      const active = r.key === this.state.source ? ' class="hist-row-active"' : '';
      return `<tr${active}><th scope="row">${r.label}<div class="hist-muted">${escapeHtml(r.note)}</div></th>${cells}</tr>`;
    }).join('');

    const raw: string[] = [];
    if (metar?.raw) raw.push(`<div class="hist-raw-label">METAR</div><pre class="hist-raw">${escapeHtml(metar.raw)}</pre>`);
    if (taf?.raw) raw.push(`<div class="hist-raw-label">TAF${taf.issue_time ? ` (issued ${runLabel(taf.issue_time)})` : ''}</div><pre class="hist-raw">${escapeHtml(taf.raw)}</pre>`);

    const at = new Date(d.at);
    const leadNote = d.lead_days === 0 ? 'latest runs' : `runs from D-${d.lead_days}`;
    return `
      <div class="hist-panel">
        <div class="ap-panel-header">
          <strong>${escapeHtml(apt.icao)}</strong>
          <span class="hist-muted">${dateLabel(at)} ${hhmm(at)}Z · ${leadNote}</span>
          <button class="hist-panel-close" aria-label="Close">✕</button>
        </div>
        <div class="hist-panel-body">
          <table class="hist-table"><thead><tr><th></th>${head}</tr></thead><tbody>${body}</tbody></table>
          ${raw.join('')}
        </div>
      </div>`;
  }
}
