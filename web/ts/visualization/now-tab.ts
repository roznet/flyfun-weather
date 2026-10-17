/** "Now" tab (#656) — Europe-wide current conditions on the forecast map page.
 *
 * Layers, each toggleable: the latest METAR as flight-category markers
 * (`/maps/now`), OPERA radar reflectivity and the satellite IR underlay as
 * stamp-keyed tiles (#652), lightning bolts faded by age (#665), the radar cell
 * overlay pushed by the home node (`cells-overlay.ts`, the same renderer as
 * the briefing route map). Cloud tops have no layer of their own: they appear
 * in a cell's details when the home node collects CTTH (a Europe-wide CTTH
 * read on the droplet is out of bounds, see #655).
 *
 * Refreshes every 5 min while the tab and the page are visible. The label is
 * the briefing route map's observed panel (`.map-observed-badge`): collapsed,
 * one chip per drawn layer with its own age; opened, one full line per layer
 * plus the cells legend — the radar and the cell overlay are different frames
 * and never share an "as of".
 */

import * as L from 'leaflet';
import {
  fetchNowMap,
  type ForecastAirport,
  type ForecastMapResponse,
  type HistoricalAirport,
  type NowMapResponse,
} from '../adapters/maps-adapter';
import { WeatherMap } from './weather-map';
import { CAT_COLORS } from './weather-map-format';
import { fetchObservedFrames, reconcileTiles, tileSpec, type TileSpec } from './route-map/observed-overlay';
import {
  FLASH_TRAIL_MINUTES,
  SATELLITE_SOURCE,
  boxParams,
  chipAge,
  chipText,
  currentFrame,
  fieldChip,
  flashBox,
  drawableFlashes,
  formatBadge,
  frameBadgeField,
  staleBadge,
  staleChip,
  type SummaryChip,
} from './route-map/observed-overlay-geometry';
import { CellsLayer } from './cells-overlay';
import { FlashGlyphLayer } from './flash-glyph-layer';
import { cellsLegendHtml, hhmmZ } from './cells-overlay-core';
import { $, escapeHtml } from '../utils';

export const NOW_REFRESH_MS = 5 * 60_000;
const TICK_MS = 30_000;
const SHOW_REFRESH_MS = 60_000;
const RADAR_SOURCE = 'opera_dbzh';
const RADAR_OPACITY = 0.75;
const SATELLITE_OPACITY = 0.8;

export type NowLayer = 'airports' | 'radar' | 'satellite' | 'lightning' | 'cells' | 'rain';
export const NOW_LAYERS: readonly NowLayer[] = ['airports', 'radar', 'satellite', 'lightning', 'cells', 'rain'];
const DEFAULT_LAYERS: Record<NowLayer, boolean> = {
  airports: true, radar: true, satellite: true, lightning: true, cells: true,
  // Rain areas are off by default, as in the prototype: dozens of frontal
  // outlines would bury the cores.
  rain: false,
};
const STORAGE_KEY = 'wb.now.layers';

interface FlashPoint { lat: number; lon: number; time: string }

function loadLayers(): Record<NowLayer, boolean> {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (raw) return { ...DEFAULT_LAYERS, ...(JSON.parse(raw) as Partial<Record<NowLayer, boolean>>) };
  } catch { /* private mode, blocked storage: defaults */ }
  return { ...DEFAULT_LAYERS };
}

function saveLayers(layers: Record<NowLayer, boolean>): void {
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify(layers)); } catch { /* per-viewer convenience only */ }
}

export class NowTab {
  private map: WeatherMap | null = null;
  private leaflet: L.Map | null = null;
  private tiles: L.LayerGroup | null = null;
  private flashLayer: FlashGlyphLayer | null = null;
  private cells: CellsLayer | null = null;
  private data: NowMapResponse | null = null;
  private flashes: FlashPoint[] = [];
  private flashNote = '';
  private layers = loadLayers();
  private visible = false;
  private lastRefresh = 0;
  private timer: ReturnType<typeof setInterval> | null = null;
  private moveTimer: ReturnType<typeof setTimeout> | null = null;
  private openIcao: string | null = null;
  private badges: Partial<Record<NowLayer, string>> = {};
  private chips: Partial<Record<NowLayer, SummaryChip | null>> = {};
  private inFlight: Promise<void> | null = null;
  // Lightning refreshes on every pan, so overlapping requests are normal: only
  // the newest one may draw.
  private flashSeq = 0;

  async show(): Promise<void> {
    this.visible = true;
    if (!this.map) this.init();
    setTimeout(() => this.map?.invalidateSize(), 100);
    // Coming back to the tab: refresh unless it just did, so every badge age
    // is current (listings are cached server- and client-side for a minute).
    if (Date.now() - this.lastRefresh >= SHOW_REFRESH_MS) await this.refresh();
  }

  hide(): void {
    this.visible = false;
  }

  private init(): void {
    const container = $('map-container-now');
    if (!container) return;
    this.map = new WeatherMap(container);
    this.map.init();
    this.map.setAirportClickHandler((icao) => this.openPanel(icao));
    this.leaflet = this.map.getLeafletMap();
    if (!this.leaflet) return;
    this.tiles = L.layerGroup().addTo(this.leaflet);
    this.flashLayer = new FlashGlyphLayer().addTo(this.leaflet);
    this.cells = new CellsLayer(this.leaflet, this.layers.cells);
    this.cells.setShowRain(this.layers.rain);

    const label = $('now-badge');
    if (label) {
      // Clicks on the label must not reach the map underneath.
      L.DomEvent.disableClickPropagation(label);
      L.DomEvent.disableScrollPropagation(label);
    }
    this.wireControls();

    // Lightning follows the viewport (the endpoint takes a box); the rest is
    // Europe-wide already.
    this.leaflet.on('moveend', () => {
      if (this.moveTimer) clearTimeout(this.moveTimer);
      this.moveTimer = setTimeout(() => { void this.refreshFlashes().then(() => this.renderBadge()); }, 400);
    });
    this.timer = setInterval(() => this.tick(), TICK_MS);
    document.addEventListener('visibilitychange', () => this.tick());
  }

  /** Visibility-aware refresh: only while this tab and the page are shown. */
  private tick(): void {
    if (!this.visible || document.visibilityState !== 'visible') return;
    if (Date.now() - this.lastRefresh >= NOW_REFRESH_MS) void this.refresh();
  }

  private wireControls(): void {
    for (const input of document.querySelectorAll<HTMLInputElement>('#controls-now input[data-layer]')) {
      const key = input.dataset.layer as NowLayer;
      input.checked = this.layers[key];
      input.addEventListener('change', () => {
        this.layers[key] = input.checked;
        saveLayers(this.layers);
        void this.applyLayer(key);
      });
    }
    $('now-refresh')?.addEventListener('click', () => { void this.refresh(); });
    this.syncRainToggle();
  }

  private syncRainToggle(): void {
    const rain = document.querySelector<HTMLInputElement>('#controls-now input[data-layer="rain"]');
    if (rain) rain.disabled = !this.layers.cells;
  }

  private async applyLayer(key: NowLayer): Promise<void> {
    if (key === 'airports') this.renderAirports();
    else if (key === 'radar' || key === 'satellite') {
      await this.refreshTiles();
      // Cells pair with the radar frame; a toggle changes (or clears) it.
      await this.refreshCells();
    }
    else if (key === 'lightning') await this.refreshFlashes();
    else if (key === 'cells' || key === 'rain') {
      this.cells?.setEnabled(this.layers.cells);
      this.cells?.setShowRain(this.layers.rain);
      this.syncRainToggle();
      await this.refreshCells();
    }
    this.renderBadge();
  }

  /** Everything, in the order a pilot looks: airports, radar, cells, lightning. */
  async refresh(): Promise<void> {
    if (!this.leaflet) return;
    // One refresh at a time: the button, the tick and show() can all ask, and
    // two overlapping runs could let a slower, older response win.
    if (this.inFlight) return this.inFlight;
    this.lastRefresh = Date.now();
    this.inFlight = (async () => {
      await Promise.all([this.refreshAirports(), this.refreshTiles()]);
      // Cells pair with the radar frame just drawn, so after the tiles.
      await Promise.all([this.refreshCells(), this.refreshFlashes()]);
      this.renderBadge();
    })().finally(() => { this.inFlight = null; });
    return this.inFlight;
  }

  // --- Airports -----------------------------------------------------------------

  private async refreshAirports(): Promise<void> {
    try {
      this.data = await fetchNowMap();
    } catch (err) {
      this.data = null;
      this.badges.airports = `METAR: failed to load (${err instanceof Error ? err.message : err})`;
      this.chips.airports = { name: 'METAR', status: 'failed', warning: true };
      this.renderAirports();
      return;
    }
    this.renderAirports();
    if (this.openIcao) this.openPanel(this.openIcao);
  }

  /** METAR presented as the per-airport "model", as the historical tab does. */
  private view(): ForecastMapResponse {
    const airports: ForecastAirport[] = [];
    if (this.layers.airports) {
      for (const apt of this.data?.airports ?? []) {
        const metar = apt.observed.metar;
        if (!metar) continue;
        airports.push({ ...apt, models: { metar } } as unknown as ForecastAirport);
      }
    }
    return { forecast_time: this.data?.at ?? '', model_init_times: {}, airports };
  }

  private renderAirports(): void {
    if (!this.map) return;
    const view = this.view();
    this.map.setForecastData(view, 'flight_category', 'metar');
    this.map.setHighlightedIcao(this.openIcao);
    if (!this.layers.airports) {
      this.badges.airports = '';
      this.chips.airports = null;
    } else if (this.data) {
      const m = this.data.sources.metar;
      const maxAge = m.max_age_min ?? 90;
      this.badges.airports = `METAR: latest report per airport (≤${maxAge} min old) · ${view.airports.length} airports · read ${hhmmZ(this.data.at)}`;
      // No chip when loaded: each marker carries its own report time, and the
      // summary line is about how old the imagery is.
      this.chips.airports = null;
    }
  }

  // --- Radar + satellite tiles ----------------------------------------------------

  private radarStamp: string | null = null;

  private async refreshTiles(): Promise<void> {
    if (!this.tiles) return;
    const wanted: TileSpec[] = [];
    const now = new Date();
    const [radar, sat] = await Promise.all([
      this.layers.radar ? fetchObservedFrames(RADAR_SOURCE) : Promise.resolve(null),
      this.layers.satellite ? fetchObservedFrames(SATELLITE_SOURCE) : Promise.resolve(null),
    ]);
    this.radarStamp = null;
    if (this.layers.satellite) {
      const frame = currentFrame(sat);
      if (sat && frame) {
        wanted.push(tileSpec(sat, frame, SATELLITE_OPACITY, 5));
        const field = frameBadgeField(sat, frame, now);
        this.badges.satellite = formatBadge(field);
        this.chips.satellite = fieldChip(SATELLITE_SOURCE, field);
      } else {
        this.badges.satellite = sat ? staleBadge(sat, now) : 'Satellite IR: not available';
        this.chips.satellite = sat ? staleChip(SATELLITE_SOURCE, sat) : { name: 'Sat', status: 'n/a', warning: true };
      }
    } else {
      this.badges.satellite = '';
      this.chips.satellite = null;
    }
    if (this.layers.radar) {
      const frame = currentFrame(radar);
      if (radar && frame) {
        wanted.push(tileSpec(radar, frame, RADAR_OPACITY, 6));
        this.radarStamp = frame.stamp;
        const field = frameBadgeField(radar, frame, now);
        this.badges.radar = formatBadge(field);
        this.chips.radar = fieldChip(RADAR_SOURCE, field);
      } else {
        // Stale or absent: draw nothing rather than an old sky.
        this.badges.radar = radar ? staleBadge(radar, now) : 'Radar: not available';
        this.chips.radar = radar ? staleChip(RADAR_SOURCE, radar) : { name: 'Radar', status: 'n/a', warning: true };
      }
    } else {
      this.badges.radar = '';
      this.chips.radar = null;
    }
    reconcileTiles(this.tiles, wanted);
  }

  // --- Cells --------------------------------------------------------------------------

  private async refreshCells(): Promise<void> {
    if (!this.cells) return;
    if (!this.layers.cells) {
      this.badges.cells = '';
      this.chips.cells = null;
      return;
    }
    // The whole of Europe (no box): the server hands over the stored gzip.
    this.badges.cells = await this.cells.refresh(this.radarStamp, null);
    this.chips.cells = this.cells.chip();
  }

  // --- Lightning ------------------------------------------------------------------------

  private async refreshFlashes(): Promise<void> {
    if (!this.flashLayer || !this.leaflet) return;
    const seq = ++this.flashSeq;
    if (!this.layers.lightning) {
      this.flashLayer.setFlashes([]);
      this.badges.lightning = '';
      this.chips.lightning = null;
      return;
    }
    const b = this.leaflet.getBounds();
    const box = flashBox({ south: b.getSouth(), west: b.getWest(), north: b.getNorth(), east: b.getEast() });
    let flashes: FlashPoint[] = [];
    let note: string;
    let chip: SummaryChip = { name: 'Lightning', status: 'failed', warning: true };
    try {
      const response = await fetch(`/api/observed/flashes?${boxParams(box)}&minutes=${FLASH_TRAIL_MINUTES}`);
      if (!response.ok) {
        note = response.status === 404 ? 'Lightning: not available on this server' : 'Lightning: failed to load';
        if (response.status === 404) chip = { name: 'Lightning', status: 'n/a', warning: true };
      } else {
        const payload = await response.json();
        flashes = (payload.flashes ?? []) as FlashPoint[];
        const newest = payload.newest_valid_time as string | null;
        note = newest
          ? `Lightning ${hhmmZ(newest)} · last ${FLASH_TRAIL_MINUTES} min, faded by age · ${flashes.length} flashes in view`
          : 'Lightning: no current frame';
        const newestMs = newest ? new Date(newest).getTime() : NaN;
        chip = Number.isNaN(newestMs)
          ? { name: 'Lightning', status: 'none', warning: true }
          : { name: 'Lightning', status: chipAge(Math.max(0, (Date.now() - newestMs) / 60000)), warning: false };
      }
    } catch {
      note = 'Lightning: failed to load';
    }
    if (seq !== this.flashSeq) return;  // a newer pan or refresh owns the layer
    this.flashes = flashes;
    this.flashNote = note;
    this.drawFlashes();
    this.badges.lightning = this.flashNote;
    this.chips.lightning = chip;
  }

  private drawFlashes(): void {
    this.flashLayer?.setFlashes(drawableFlashes(this.flashes, Date.now()));
  }

  // --- Badge + panel ------------------------------------------------------------------------

  /** One line of per-layer ages, ⓘ to open the full lines and the cells
   *  legend — the same panel as the briefing route map. The `<details>`
   *  persists across refreshes, so an open panel stays open. */
  private renderBadge(): void {
    const el = $('now-badge');
    if (!el) return;
    const order: NowLayer[] = ['radar', 'cells', 'satellite', 'lightning', 'airports'];
    const lines = order.map((k) => this.badges[k]).filter((s): s is string => !!s);
    const chips = order.map((k) => this.chips[k]).filter((c): c is SummaryChip => !!c);
    if (!lines.length && !chips.length) {
      el.style.display = 'none';
      return;
    }
    el.style.display = '';
    const summary = chips
      .map((c) => `<span class="${c.warning ? 'mob-chip mob-warn' : 'mob-chip'}">${escapeHtml(chipText(c))}</span>`)
      .join('<span class="mob-sep">·</span>');
    const text = lines.join('\n');
    el.title = text;
    el.innerHTML =
      `<summary aria-label="${escapeHtml(text.replace(/\n/g, '. '))}">${summary || 'Now'}<span class="mob-info" aria-hidden="true">ⓘ</span></summary>`
      + `<div class="mob-body">`
      + `<div class="mob-lines">${escapeHtml(text)}</div>`
      + (this.layers.cells ? `<div class="mob-section">${cellsLegendHtml()}</div>` : '')
      + `<div class="mob-note">Observed imagery shows the sky as it was, not as it is: at 120 kt, ten minutes is twenty nautical miles.</div>`
      + `</div>`;
  }

  private openPanel(icao: string): void {
    const host = $('now-panel-host') as HTMLElement | null;
    const apt = this.data?.airports.find((a) => a.icao === icao);
    if (!host || !apt) return;
    this.openIcao = icao;
    host.style.display = '';
    host.innerHTML = this.panelHtml(apt);
    host.querySelector('.hist-panel-close')?.addEventListener('click', () => this.closePanel());
    this.map?.setHighlightedIcao(icao);
    setTimeout(() => this.map?.invalidateSize(), 50);
  }

  private closePanel(): void {
    const host = $('now-panel-host') as HTMLElement | null;
    if (host) { host.style.display = 'none'; host.innerHTML = ''; }
    this.openIcao = null;
    this.map?.setHighlightedIcao(null);
    setTimeout(() => this.map?.invalidateSize(), 50);
  }

  private panelHtml(apt: HistoricalAirport): string {
    const metar = apt.observed.metar;
    const cat = metar?.flight_category;
    const color = cat ? CAT_COLORS[cat] ?? '#888' : '#888';
    const when = metar
      ? `${hhmmZ(metar.observation_time)} · ${Math.round(metar.age_min)} min old${metar.report_type === 'SPECI' ? ' · SPECI' : ''}`
      : 'no METAR in the last 90 min';
    return `
      <div class="hist-panel">
        <div class="ap-panel-header">
          <strong>${escapeHtml(apt.icao)}</strong>
          <span class="hist-cat" style="background:${color}">${escapeHtml(cat ?? '—')}</span>
          <button class="hist-panel-close" aria-label="Close">✕</button>
        </div>
        <div class="hist-panel-body">
          <div class="hist-muted">Observed ${escapeHtml(when)}</div>
          ${metar?.raw ? `<div class="hist-raw-label">METAR</div><pre class="hist-raw">${escapeHtml(metar.raw)}</pre>` : ''}
        </div>
      </div>`;
  }
}
