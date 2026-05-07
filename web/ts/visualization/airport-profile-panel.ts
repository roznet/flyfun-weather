/** Airport profile panel — right-click an airport on /maps.html to inspect
 *  its vertical conditions (cross-section + Skew-T) for a 4-hour window.
 *
 *  Renders into a host element that the maps page sizes via CSS. The panel
 *  owns its own SSE stream lifecycle; the only inputs from the host are
 *  airport coords + selected (day, hour, model).
 *
 *  View modes (segmented toggle, persisted to localStorage):
 *    'both'  — cross-section on top, Skew-T below (default)
 *    'cross' — cross-section only
 *    'skewt' — Skew-T only
 *
 *  The issue calls out "stacked-views crowding (likely)" as something to
 *  revisit visually; the toggle is in place from day one so the follow-up
 *  is "change the default", not a refactor.
 */

import { CrossSectionRenderer } from './cross-section/renderer';
import { SkewTRenderer } from './skewt/renderer';
import { getAllLayers, getDefaultEnabled } from './cross-section/layer-registry';
import {
  streamAirportProfile, snapshotToVizData, snapshotToSkewtData,
  type AirportProfileSnapshot, type AirportProfileStreamHandle,
  type AirportProfileEnriched,
} from '../adapters/airport-profile-adapter';

/** Render a one-line summary of which GRIB sources contributed
 *  (or empty when none did, so the status row collapses). */
function formatEnrichmentBadge(e: AirportProfileEnriched | null): string {
  if (!e) return '';
  const parts: string[] = [];
  for (const [m, info] of Object.entries(e.sources)) {
    const dt = new Date(info.init_time_unix * 1000);
    const hh = String(dt.getUTCHours()).padStart(2, '0');
    parts.push(`${m.toUpperCase()} ${hh}Z`);
  }
  if (parts.length === 0) return '';
  return `GRIB: ${parts.join(', ')}`;
}

type ViewMode = 'both' | 'cross' | 'skewt';
const VIEW_MODE_KEY = 'wb_apProfileView';

function loadViewMode(): ViewMode {
  const v = localStorage.getItem(VIEW_MODE_KEY);
  if (v === 'cross' || v === 'skewt' || v === 'both') return v;
  return 'both';
}
function saveViewMode(m: ViewMode): void {
  localStorage.setItem(VIEW_MODE_KEY, m);
}

export interface AirportProfilePanelOptions {
  container: HTMLElement;
  initialModel?: string;
  onClose?: () => void;
  /** Fired when the user picks a different model from the panel's
   *  dropdown — lets the host round-trip the selection through URL
   *  state without polling. */
  onModelChange?: (model: string) => void;
}

export interface AirportProfileRequest {
  icao: string;
  startHour: string;  // ISO 8601 UTC, e.g. "2026-05-07T12:00:00Z"
  windowH?: number;
}

const MODELS = ['gfs', 'icon', 'ecmwf'] as const;

export class AirportProfilePanel {
  private container: HTMLElement;
  private model: string;
  private viewMode: ViewMode;
  private onClose?: () => void;
  private onModelChange?: (model: string) => void;

  private crossEl: HTMLDivElement;
  private skewtEl: HTMLDivElement;
  private statusEl: HTMLDivElement;
  private titleEl: HTMLDivElement;
  private modelSel: HTMLSelectElement;
  private viewBtns: Record<ViewMode, HTMLButtonElement>;

  private crossRenderer: CrossSectionRenderer | null = null;
  private skewtRenderer: SkewTRenderer | null = null;

  private snapshot: AirportProfileSnapshot = {
    meta: null, surface: [], levels: [], enriched: null, derived: [],
  };
  private stream: AirportProfileStreamHandle | null = null;
  private currentRequest: AirportProfileRequest | null = null;

  constructor(opts: AirportProfilePanelOptions) {
    this.container = opts.container;
    this.model = opts.initialModel ?? 'ecmwf';
    this.viewMode = loadViewMode();
    this.onClose = opts.onClose;
    this.onModelChange = opts.onModelChange;

    this.container.classList.add('ap-panel');
    this.container.innerHTML = `
      <div class="ap-panel-header">
        <div class="ap-panel-title" id="ap-title">—</div>
        <button class="ap-panel-close" type="button" title="Close">&times;</button>
      </div>
      <div class="ap-panel-controls">
        <label>Model</label>
        <select class="ap-model-sel">
          <option value="gfs">GFS</option>
          <option value="icon">ICON</option>
          <option value="ecmwf">ECMWF</option>
        </select>
        <label>View</label>
        <div class="ap-view-group btn-group" role="group">
          <button class="btn-toggle" data-view="both">Both</button>
          <button class="btn-toggle" data-view="cross">Cross-section</button>
          <button class="btn-toggle" data-view="skewt">Skew-T</button>
        </div>
      </div>
      <div class="ap-panel-status" id="ap-status"></div>
      <div class="ap-panel-body">
        <div class="ap-cross" id="ap-cross"></div>
        <div class="ap-skewt" id="ap-skewt"></div>
      </div>
    `;

    this.titleEl = this.container.querySelector('.ap-panel-title') as HTMLDivElement;
    this.statusEl = this.container.querySelector('.ap-panel-status') as HTMLDivElement;
    this.crossEl = this.container.querySelector('.ap-cross') as HTMLDivElement;
    this.skewtEl = this.container.querySelector('.ap-skewt') as HTMLDivElement;
    this.modelSel = this.container.querySelector('.ap-model-sel') as HTMLSelectElement;
    this.modelSel.value = this.model;

    const viewGroup = this.container.querySelector('.ap-view-group') as HTMLElement;
    this.viewBtns = {
      both: viewGroup.querySelector('[data-view="both"]') as HTMLButtonElement,
      cross: viewGroup.querySelector('[data-view="cross"]') as HTMLButtonElement,
      skewt: viewGroup.querySelector('[data-view="skewt"]') as HTMLButtonElement,
    };

    this.applyViewMode();

    this.container.querySelector('.ap-panel-close')?.addEventListener('click', () => this.close());
    this.modelSel.addEventListener('change', () => {
      this.model = this.modelSel.value;
      this.onModelChange?.(this.model);
      if (this.currentRequest) this.load(this.currentRequest);
    });
    viewGroup.addEventListener('click', (e) => {
      const btn = (e.target as HTMLElement).closest('button');
      if (!btn) return;
      const v = btn.getAttribute('data-view') as ViewMode | null;
      if (!v) return;
      this.viewMode = v;
      saveViewMode(v);
      this.applyViewMode();
    });
  }

  /** Update the model from outside (e.g. for URL deep-linking). */
  setModel(model: string): void {
    if (!MODELS.includes(model as any)) return;
    this.model = model;
    this.modelSel.value = model;
  }

  /** Read the panel's current model — used by the host to round-trip
   *  the panel's selection through the URL state. */
  getModel(): string {
    return this.model;
  }

  /** Start (or restart) loading the profile for a new airport / hour. */
  load(req: AirportProfileRequest): void {
    this.currentRequest = req;
    this.snapshot = {
      meta: null, surface: [], levels: [], enriched: null, derived: [],
    };
    if (this.stream) { this.stream.abort(); this.stream = null; }
    this.titleEl.textContent = `${req.icao} — loading…`;
    this.statusEl.textContent = 'Connecting…';
    this.clearRenderers();

    this.stream = streamAirportProfile(
      { icao: req.icao, model: this.model, startHour: req.startHour, windowH: req.windowH },
      (phase, snapshot, raw) => this.onPhase(phase, snapshot, raw),
    );
  }

  private onPhase(phase: string, snapshot: AirportProfileSnapshot, raw: any): void {
    this.snapshot = snapshot;
    if (phase === 'meta') {
      const m = snapshot.meta!;
      const start = new Date(m.start_hour);
      const startStr = start.toISOString().slice(11, 16) + 'Z';
      this.titleEl.textContent = `${m.icao} · ${m.model.toUpperCase()} · ${startStr} +${m.window_h}h`;
      this.statusEl.textContent = 'Surface…';
      this.renderCross();
    } else if (phase === 'surface') {
      this.statusEl.textContent = 'Pressure levels…';
      this.renderCross();
    } else if (phase === 'levels') {
      this.statusEl.textContent = (raw && raw.error) ? `Levels failed (${raw.error})` : 'GRIB enrichment…';
      this.renderSkewT();
    } else if (phase === 'enriched') {
      this.statusEl.textContent = `Analyzing… ${formatEnrichmentBadge(snapshot.enriched)}`;
    } else if (phase === 'derived') {
      this.statusEl.textContent = formatEnrichmentBadge(snapshot.enriched);
      this.renderCross();
      this.renderSkewT();
    } else if (phase === 'complete') {
      this.statusEl.textContent = formatEnrichmentBadge(snapshot.enriched);
    } else if (phase === 'error') {
      this.statusEl.textContent = 'Stream error';
    }
  }

  private applyViewMode(): void {
    for (const v of ['both', 'cross', 'skewt'] as const) {
      this.viewBtns[v].classList.toggle('active', v === this.viewMode);
    }
    const showCross = this.viewMode === 'both' || this.viewMode === 'cross';
    const showSkewT = this.viewMode === 'both' || this.viewMode === 'skewt';
    this.crossEl.style.display = showCross ? 'block' : 'none';
    this.skewtEl.style.display = showSkewT ? 'block' : 'none';
    this.container.classList.toggle('view-both', this.viewMode === 'both');
    this.container.classList.toggle('view-cross', this.viewMode === 'cross');
    this.container.classList.toggle('view-skewt', this.viewMode === 'skewt');

    // Force a re-render so canvases pick up the new container size.
    setTimeout(() => {
      this.crossRenderer?.render();
      this.renderSkewT();
    }, 0);
  }

  private ensureCrossRenderer(): CrossSectionRenderer {
    if (!this.crossRenderer) {
      this.crossRenderer = new CrossSectionRenderer(this.crossEl);
      const layers = getAllLayers();
      const enabled = getDefaultEnabled();
      this.crossRenderer.setLayers(layers, enabled);
    }
    return this.crossRenderer;
  }

  private ensureSkewTRenderer(): SkewTRenderer {
    if (!this.skewtRenderer) {
      this.skewtRenderer = new SkewTRenderer(this.skewtEl);
    }
    return this.skewtRenderer;
  }

  private renderCross(): void {
    if (this.viewMode === 'skewt') return;
    const data = snapshotToVizData(this.snapshot);
    if (!data) return;
    const r = this.ensureCrossRenderer();
    r.setData(data);
    r.render();
  }

  private renderSkewT(): void {
    if (this.viewMode === 'cross') return;
    const data = snapshotToSkewtData(this.snapshot, 0);
    if (!data || data.levels.length === 0) {
      this.skewtRenderer?.clear();
      return;
    }
    const r = this.ensureSkewTRenderer();
    r.setData(data);
  }

  private clearRenderers(): void {
    this.crossRenderer?.destroy();
    this.crossRenderer = null;
    this.skewtRenderer?.destroy();
    this.skewtRenderer = null;
    this.crossEl.innerHTML = '';
    this.skewtEl.innerHTML = '';
  }

  /** Close the panel and notify the host. */
  close(): void {
    if (this.stream) { this.stream.abort(); this.stream = null; }
    this.clearRenderers();
    this.onClose?.();
  }

  /** Tear down everything (used when the host removes the panel). */
  destroy(): void {
    if (this.stream) { this.stream.abort(); this.stream = null; }
    this.clearRenderers();
    this.container.innerHTML = '';
    this.container.classList.remove('ap-panel', 'view-both', 'view-cross', 'view-skewt');
  }
}
