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

/** HTML-escape user-visible strings before injecting into the status
 *  bar (we use innerHTML there so the dots-spinner span renders). */
function esc(s: string): string {
  const d = document.createElement('div');
  d.textContent = s;
  return d.innerHTML;
}

/** Status row template that matches briefing-ui's "Refreshing · <stage>…"
 *  pattern: phase label followed by an animated dots-spinner. */
function statusLoading(label: string): string {
  return `${esc(label)}<span class="dots-spinner"></span>`;
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

  /** Per-(icao,startHour,model) cache of completed snapshots so model
   *  switching reuses prior fetches. Cleared whenever the request shape
   *  changes (different icao or startHour) since the previous entries
   *  no longer apply. */
  private snapshotCache = new Map<string, AirportProfileSnapshot>();

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
    // Drop the cache on icao or startHour change — the cached snapshots
    // are scoped to the previous request shape and no longer apply.
    if (
      this.currentRequest
      && (this.currentRequest.icao !== req.icao
          || this.currentRequest.startHour !== req.startHour)
    ) {
      this.snapshotCache.clear();
    }
    this.currentRequest = req;
    if (this.stream) { this.stream.abort(); this.stream = null; }

    // Cache hit: skip the SSE round-trip entirely.
    const cached = this.snapshotCache.get(this.cacheKey(req, this.model));
    if (cached) {
      this.snapshot = cached;
      this.applyTitle(cached);
      this.statusEl.innerHTML = esc(formatEnrichmentBadge(cached.enriched));
      this.clearRenderers();
      this.renderCross();
      this.renderSkewT();
      return;
    }

    this.snapshot = {
      meta: null, surface: [], levels: [], enriched: null, derived: [],
    };
    this.titleEl.textContent = `${req.icao} — loading…`;
    this.statusEl.innerHTML = statusLoading('Connecting');
    this.clearRenderers();

    this.stream = streamAirportProfile(
      { icao: req.icao, model: this.model, startHour: req.startHour, windowH: req.windowH },
      (phase, snapshot, raw) => this.onPhase(phase, snapshot, raw),
    );
  }

  private cacheKey(req: AirportProfileRequest, model: string): string {
    return `${req.icao}|${req.startHour}|${model}`;
  }

  /** Render the panel header from the snapshot's meta. */
  private applyTitle(snapshot: AirportProfileSnapshot): void {
    if (!snapshot.meta) return;
    const m = snapshot.meta;
    const startStr = new Date(m.start_hour).toISOString().slice(11, 16) + 'Z';
    this.titleEl.textContent = `${m.icao} · ${m.model.toUpperCase()} · ${startStr} +${m.window_h}h`;
  }

  private onPhase(phase: string, snapshot: AirportProfileSnapshot, raw: any): void {
    this.snapshot = snapshot;
    // Backend emits a `label` field on phases that map to a stage in the
    // briefing pipeline (see api/airport_profile.py:_PHASE_TO_STAGE). For
    // phases without a label (`meta`, `surface`) we use a local string
    // since the briefing pipeline has no analogue.
    const labelFromBackend = (raw && typeof raw === 'object' && typeof raw.label === 'string')
      ? raw.label as string
      : null;

    if (phase === 'meta') {
      this.applyTitle(snapshot);
      this.statusEl.innerHTML = statusLoading('Loading');
      this.renderCross();
    } else if (phase === 'surface') {
      this.statusEl.innerHTML = statusLoading(labelFromBackend ?? 'Loading');
      this.renderCross();
    } else if (phase === 'levels') {
      const text = (raw && raw.error)
        ? `Levels failed (${raw.error})`
        : (labelFromBackend ?? 'Fetching forecasts');
      this.statusEl.innerHTML = (raw && raw.error) ? esc(text) : statusLoading(text);
      this.renderSkewT();
    } else if (phase === 'enriched') {
      this.statusEl.innerHTML = statusLoading(labelFromBackend ?? 'Adding cloud & icing detail');
    } else if (phase === 'derived') {
      this.statusEl.innerHTML = esc(formatEnrichmentBadge(snapshot.enriched));
      this.renderCross();
      this.renderSkewT();
    } else if (phase === 'complete') {
      this.statusEl.innerHTML = esc(formatEnrichmentBadge(snapshot.enriched));
      // Successful completion: cache the snapshot for fast model-switch.
      if (this.currentRequest) {
        this.snapshotCache.set(
          this.cacheKey(this.currentRequest, this.model),
          // Store a shallow clone so subsequent mutations of `snapshot`
          // (in onPhase callbacks for a follow-on request) don't bleed
          // back into the cached entry.
          {
            meta: snapshot.meta,
            surface: snapshot.surface,
            levels: snapshot.levels,
            enriched: snapshot.enriched,
            derived: snapshot.derived,
          },
        );
      }
    } else if (phase === 'error') {
      // Backend-emitted structured error: {type, phase, message}. Falls
      // through to a generic message when the connection just dropped.
      const detail = raw && typeof raw === 'object' && raw.phase
        ? `${raw.phase}: ${raw.message ?? 'unknown error'}`
        : 'connection lost';
      this.statusEl.innerHTML = esc(`Error — ${detail}`);
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

  /** User clicked the ✕ button. Notifies the host so it can decide
   *  what to do with the panel container — the host is then expected
   *  to call `destroy()`. We don't tear anything down here; that keeps
   *  the lifecycle linear (one teardown path) instead of having
   *  `close()` and `destroy()` partially overlap. */
  close(): void {
    this.onClose?.();
  }

  /** Single teardown path: abort the SSE stream, dispose renderers,
   *  empty the container. Idempotent. */
  destroy(): void {
    if (this.stream) { this.stream.abort(); this.stream = null; }
    this.snapshotCache.clear();
    this.clearRenderers();
    this.container.innerHTML = '';
    this.container.classList.remove('ap-panel', 'view-both', 'view-cross', 'view-skewt');
  }
}
