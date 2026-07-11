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
import { renderLayerToggles } from './controls/panel';
import { renderSkewtOverlayControls } from './skewt/overlay-controls';
import { t } from '../i18n/i18n';
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
/** Per-panel layer-enable map. Key is independent from the main
 *  briefing's `wb_visibleLayers` so toggles in this panel don't bleed
 *  into the main /briefing.html view. */
const LAYERS_KEY = 'wb_apProfileLayers';

function loadViewMode(): ViewMode {
  const v = localStorage.getItem(VIEW_MODE_KEY);
  if (v === 'cross' || v === 'skewt' || v === 'both') return v;
  return 'both';
}
function saveViewMode(m: ViewMode): void {
  localStorage.setItem(VIEW_MODE_KEY, m);
}

function loadEnabledLayers(): Record<string, boolean> {
  const defaults = getDefaultEnabled('airport-profile');
  try {
    const raw = localStorage.getItem(LAYERS_KEY);
    if (!raw) return defaults;
    const saved = JSON.parse(raw) as Record<string, unknown>;
    // Merge: keep any layer the saved blob didn't know about at its
    // default. Coerce values to boolean so a malformed entry can't
    // poison the renderer's enable check.
    const merged: Record<string, boolean> = { ...defaults };
    for (const [k, v] of Object.entries(saved)) {
      if (typeof v === 'boolean') merged[k] = v;
    }
    return merged;
  } catch {
    return defaults;
  }
}
function saveEnabledLayers(m: Record<string, boolean>): void {
  try { localStorage.setItem(LAYERS_KEY, JSON.stringify(m)); } catch { /* quota */ }
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
  private settingsBtn: HTMLButtonElement;
  private drawerEl: HTMLDivElement;
  private drawerOpen = false;

  private crossRenderer: CrossSectionRenderer | null = null;
  private skewtRenderer: SkewTRenderer | null = null;
  /** Layer enable state for the cross-section. Persisted to localStorage
   *  so the user's toggles survive panel close + reopen. Applied to the
   *  renderer via `setLayers()` whenever it (re)mounts. */
  private enabledLayers: Record<string, boolean> = loadEnabledLayers();

  private snapshot: AirportProfileSnapshot = {
    meta: null, surface: [], levels: [], enriched: null, derived: [],
  };
  private stream: AirportProfileStreamHandle | null = null;
  private currentRequest: AirportProfileRequest | null = null;

  /** Per-(icao,startHour,model) cache of completed snapshots so model
   *  switching reuses prior fetches. Cleared whenever the request shape
   *  changes (different icao or startHour) since the previous entries
   *  no longer apply.
   *
   *  Bounded LRU: each entry holds full pressure-level arrays + sounding
   *  analysis (~hundreds of KB up to a few MB). Without a cap, a session
   *  hopping airports would accumulate unbounded snapshots. JS `Map`
   *  preserves insertion order, so we evict the oldest key on overflow. */
  private snapshotCache = new Map<string, AirportProfileSnapshot>();
  private static readonly SNAPSHOT_CACHE_MAX = 10;

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
        <button class="ap-panel-close" type="button">&times;</button>
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
        <button class="ap-settings-btn" type="button" title="Layers & overlays" aria-label="Settings" aria-expanded="false">⚙</button>
      </div>
      <div class="ap-panel-status" id="ap-status"></div>
      <div class="ap-panel-body">
        <div class="ap-cross" id="ap-cross"></div>
        <div class="ap-skewt" id="ap-skewt"></div>
      </div>
      <div class="ap-settings-drawer" hidden></div>
    `;

    this.titleEl = this.container.querySelector('.ap-panel-title') as HTMLDivElement;
    this.statusEl = this.container.querySelector('.ap-panel-status') as HTMLDivElement;
    this.crossEl = this.container.querySelector('.ap-cross') as HTMLDivElement;
    this.skewtEl = this.container.querySelector('.ap-skewt') as HTMLDivElement;
    this.modelSel = this.container.querySelector('.ap-model-sel') as HTMLSelectElement;
    this.modelSel.value = this.model;
    this.settingsBtn = this.container.querySelector('.ap-settings-btn') as HTMLButtonElement;
    this.drawerEl = this.container.querySelector('.ap-settings-drawer') as HTMLDivElement;

    const viewGroup = this.container.querySelector('.ap-view-group') as HTMLElement;
    this.viewBtns = {
      both: viewGroup.querySelector('[data-view="both"]') as HTMLButtonElement,
      cross: viewGroup.querySelector('[data-view="cross"]') as HTMLButtonElement,
      skewt: viewGroup.querySelector('[data-view="skewt"]') as HTMLButtonElement,
    };

    this.applyViewMode();

    const closeButton = this.container.querySelector('.ap-panel-close') as HTMLButtonElement;
    const closeLabel = t('advisories.focusClose');
    closeButton.setAttribute('aria-label', closeLabel);
    closeButton.title = closeLabel;
    closeButton.addEventListener('click', () => this.close());
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
      // Drawer contents depend on viewMode; re-render if open so a
      // section that just became visible shows its controls.
      if (this.drawerOpen) this.renderDrawer();
    });
    this.settingsBtn.addEventListener('click', () => this.toggleDrawer());
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

    // Cache hit: skip the SSE round-trip entirely. Bump the entry to
    // the most-recently-used slot (Map preserves insertion order, so
    // delete-then-set moves it to the end) — that way a still-actively-used
    // entry isn't evicted just because it was loaded first.
    const cacheKey = this.cacheKey(req, this.model);
    const cached = this.snapshotCache.get(cacheKey);
    if (cached) {
      this.snapshotCache.delete(cacheKey);
      this.snapshotCache.set(cacheKey, cached);
      this.snapshot = cached;
      this.applyTitle(cached);
      this.statusEl.innerHTML = esc(formatEnrichmentBadge(cached.enriched));
      this.clearRenderers();
      this.setLoading(false);
      this.renderCross();
      this.renderSkewT();
      if (this.drawerOpen) this.renderDrawer();
      return;
    }

    this.snapshot = {
      meta: null, surface: [], levels: [], enriched: null, derived: [],
    };
    this.titleEl.textContent = `${req.icao} — loading…`;
    this.statusEl.innerHTML = statusLoading('Connecting');
    this.clearRenderers();
    // Mark canvases as loading so the empty cross-section doesn't read
    // as "real-clear-skies forecast" while we wait for derived data.
    // Lifted in onPhase('derived') / 'complete' / 'error'.
    this.setLoading(true);
    if (this.drawerOpen) this.renderDrawer();

    this.stream = streamAirportProfile(
      { icao: req.icao, model: this.model, startHour: req.startHour, windowH: req.windowH },
      (phase, snapshot, raw) => this.onPhase(phase, snapshot, raw),
    );
  }

  /** Toggle the dimmed/grayscale "loading" state on the canvas areas.
   *  Driven by `is-loading` CSS class on `.ap-cross` and `.ap-skewt`
   *  (see css/style.css). */
  private setLoading(loading: boolean): void {
    this.crossEl.classList.toggle('is-loading', loading);
    this.skewtEl.classList.toggle('is-loading', loading);
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
      // Skew-T renderer just mounted — refresh drawer if open so
      // the previously-empty Skew-T section now has controls.
      if (this.drawerOpen) this.renderDrawer();
    } else if (phase === 'enriched') {
      this.statusEl.innerHTML = statusLoading(labelFromBackend ?? 'Adding cloud & icing detail');
    } else if (phase === 'derived') {
      this.statusEl.innerHTML = esc(formatEnrichmentBadge(snapshot.enriched));
      this.setLoading(false);
      this.renderCross();
      this.renderSkewT();
    } else if (phase === 'complete') {
      this.statusEl.innerHTML = esc(formatEnrichmentBadge(snapshot.enriched));
      // Successful completion: cache the snapshot for fast model-switch.
      if (this.currentRequest) {
        const key = this.cacheKey(this.currentRequest, this.model);
        // Re-insert overwrites + moves to most-recently-used slot.
        this.snapshotCache.delete(key);
        // Store a shallow clone so subsequent mutations of `snapshot`
        // (in onPhase callbacks for a follow-on request) don't bleed
        // back into the cached entry.
        this.snapshotCache.set(key, {
          meta: snapshot.meta,
          surface: snapshot.surface,
          levels: snapshot.levels,
          enriched: snapshot.enriched,
          derived: snapshot.derived,
        });
        // Evict oldest entries until we're back under the cap. JS Map
        // iteration order is insertion order, so the first key is the
        // least-recently-used.
        while (this.snapshotCache.size > AirportProfilePanel.SNAPSHOT_CACHE_MAX) {
          const oldest = this.snapshotCache.keys().next().value;
          if (oldest === undefined) break;
          this.snapshotCache.delete(oldest);
        }
      }
    } else if (phase === 'error') {
      // Backend-emitted structured error: {type, phase, message}. Falls
      // through to a generic message when the connection just dropped.
      const detail = raw && typeof raw === 'object' && raw.phase
        ? `${raw.phase}: ${raw.message ?? 'unknown error'}`
        : 'connection lost';
      this.statusEl.innerHTML = esc(`Error — ${detail}`);
      // Lift the loading dim on error too — the canvases now show
      // whatever last-known state they have (typically empty axes),
      // which is the correct signal alongside the error message.
      this.setLoading(false);
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
      this.crossRenderer.setLayers(getAllLayers(), this.enabledLayers);
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
    // TODO(#121-followup): Skew-T is locked to hour 0 of the window;
    // the cross-section above shows all 4 hours but there's no
    // affordance (click on a time tick, hour selector, etc.) to
    // inspect hours 1–3 here. Tracked separately from the v1 issue.
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

  // -------------------------------------------------------------------
  // Settings drawer (gear icon → slide-out panel that overlaps the map)
  //
  // The drawer is anchored to the panel's left edge via CSS (see
  // css/style.css: `.ap-settings-drawer { right: 100%; }`) so it grows
  // toward the map without resizing the panel itself. Contents are
  // rebuilt on every open + on viewMode change so toggling the View
  // segmented control swaps which section's controls are visible.
  // -------------------------------------------------------------------
  private toggleDrawer(): void {
    this.drawerOpen = !this.drawerOpen;
    this.drawerEl.hidden = !this.drawerOpen;
    this.container.classList.toggle('drawer-open', this.drawerOpen);
    this.settingsBtn.setAttribute('aria-expanded', String(this.drawerOpen));
    this.settingsBtn.classList.toggle('active', this.drawerOpen);
    if (this.drawerOpen) this.renderDrawer();
    else this.drawerEl.innerHTML = '';
  }

  private renderDrawer(): void {
    const showCross = this.viewMode !== 'skewt';
    const showSkewT = this.viewMode !== 'cross';

    // Build section shells; populate each with the appropriate
    // sub-control via the dedicated render helpers.
    let html = '<div class="ap-drawer-header">';
    html += '<span class="ap-drawer-title">Layers & overlays</span>';
    html += '<button class="ap-drawer-close" type="button">×</button>';
    html += '</div>';
    if (showCross) {
      html += '<section class="ap-drawer-section" data-section="cross">';
      html += '<h4 class="ap-drawer-section-title">Cross-section</h4>';
      html += '<div class="ap-drawer-cross-host"></div>';
      html += '</section>';
    }
    if (showSkewT) {
      html += '<section class="ap-drawer-section" data-section="skewt">';
      html += '<h4 class="ap-drawer-section-title">Skew-T</h4>';
      html += '<div class="ap-drawer-skewt-host"></div>';
      html += '</section>';
    }
    this.drawerEl.innerHTML = html;
    const closeButton = this.drawerEl.querySelector('.ap-drawer-close') as HTMLButtonElement;
    const closeLabel = t('advisories.focusClose');
    closeButton.setAttribute('aria-label', closeLabel);
    closeButton.title = closeLabel;
    closeButton.addEventListener('click', () => this.toggleDrawer());

    if (showCross) {
      const host = this.drawerEl.querySelector('.ap-drawer-cross-host') as HTMLElement;
      // Toggles work even before the cross-section renderer mounts —
      // state is owned by the panel and applied via setLayers() once
      // the renderer exists (see ensureCrossRenderer).
      // The current-conditions overlay is route-only (distance X axis); the
      // airport-profile drawer is time-axis, so drop that group's toggle.
      renderLayerToggles(host, this.enabledLayers, (layerId) => this.onLayerToggle(layerId), {
        hiddenGroups: new Set(['conditions']),
      });
    }
    if (showSkewT) {
      const host = this.drawerEl.querySelector('.ap-drawer-skewt-host') as HTMLElement;
      // Skew-T overlay controls read state from the renderer, so they
      // need it to exist. When the levels phase hasn't completed yet,
      // show a placeholder; renderDrawer is re-called on phase events
      // (see onPhase) so the controls light up once data lands.
      if (this.skewtRenderer) {
        renderSkewtOverlayControls(host, this.skewtRenderer);
      } else {
        host.innerHTML = '<div class="ap-drawer-empty">Available once data loads</div>';
      }
    }
  }

  private onLayerToggle(layerId: string): void {
    // Default-enabled layers are stored as `true`; flipping a missing
    // key to `false` is the explicit "off" signal the renderer checks.
    const current = this.enabledLayers[layerId] !== false;
    this.enabledLayers = { ...this.enabledLayers, [layerId]: !current };
    saveEnabledLayers(this.enabledLayers);
    if (this.crossRenderer) {
      this.crossRenderer.setLayers(getAllLayers(), this.enabledLayers);
      this.crossRenderer.render();
    }
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
    this.drawerOpen = false;
    this.clearRenderers();
    this.container.innerHTML = '';
    this.container.classList.remove('ap-panel', 'view-both', 'view-cross', 'view-skewt', 'drawer-open');
  }
}
