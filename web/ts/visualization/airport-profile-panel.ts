/** Airport profile panel — click (or tap) an airport on /maps.html to
 *  inspect it. Defaults to the summary Card (rendered synchronously from the
 *  map's forecast data); the cross-section + Skew-T are behind the toggle and
 *  fetched on intent (dwell / pointer-enter) via an SSE stream.
 *
 *  Renders into a host element that the maps page sizes via CSS. The panel
 *  owns its own SSE stream lifecycle; the inputs from the host are the
 *  airport summary (for the card) + selected (day, hour, model).
 *
 *  View modes (segmented toggle, persisted to localStorage):
 *    'card'  — summary card, no network (default)
 *    'cross' — cross-section only
 *    'skewt' — Skew-T only
 */

import { CrossSectionRenderer } from './cross-section/renderer';
import { SkewTRenderer } from './skewt/renderer';
import { getAllLayers, getDefaultEnabled } from './cross-section/layer-registry';
import { renderLayerToggles } from './controls/panel';
import { renderSkewtOverlayControls } from './skewt/overlay-controls';
import {
  streamAirportProfile, snapshotToVizData, snapshotToSkewtData,
  type AirportProfileSnapshot, type AirportProfileStreamHandle,
  type AirportProfileEnriched,
} from '../adapters/airport-profile-adapter';
import { renderAirportSummaryCard } from './airport-summary-card';
import type { ForecastAirport } from '../adapters/maps-adapter';
import type { ConsensusMode } from './weather-map-consensus';
import type { ForecastMetric } from './weather-map-format';

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

export type ViewMode = 'card' | 'cross' | 'skewt';
// v2 key: the summary card is the new default. The old `wb_apProfileView`
// defaulted to the heavy stacked 'both' view; migrating to a fresh key
// lands every existing user on the card once, without a value-migration.
const VIEW_MODE_KEY = 'wb_apProfileView2';
/** Per-panel layer-enable map. Key is independent from the main
 *  briefing's `wb_visibleLayers` so toggles in this panel don't bleed
 *  into the main /briefing.html view. */
const LAYERS_KEY = 'wb_apProfileLayers';

function loadViewMode(): ViewMode {
  const v = localStorage.getItem(VIEW_MODE_KEY);
  if (v === 'cross' || v === 'skewt' || v === 'card') return v;
  return 'card';
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
  /** Override the persisted default view (e.g. from a deep-link's
   *  `fc.apView`). When omitted, the last-used view is restored. */
  initialView?: ViewMode;
  onClose?: () => void;
  /** Fired when the user picks a different model from the panel's
   *  dropdown — lets the host round-trip the selection through URL
   *  state without polling. */
  onModelChange?: (model: string) => void;
  /** Fired when the user clicks a metric row in the summary card — the
   *  host switches the map's color dropdown to that metric. */
  onMetricSelect?: (metric: ForecastMetric) => void;
  /** Fired when the view mode changes (Card / Cross-section / Skew-T) so
   *  the host can deep-link it. */
  onViewChange?: (view: ViewMode) => void;
}

/** Snapshot summary the host already holds (from the forecast map response)
 *  used to render the card synchronously, with no network round-trip. */
export interface AirportSummaryInput {
  airport: ForecastAirport;
  consensusMode: ConsensusMode;
  validTime: Date;
  modelInitTimes?: Record<string, string>;
}

export interface AirportProfileRequest {
  icao: string;
  startHour: string;  // ISO 8601 UTC, e.g. "2026-05-07T12:00:00Z"
  windowH?: number;
  /** Per-airport summary for the card. Optional so callers without it
   *  (e.g. a deep-link before the map data lands) still open the panel. */
  summary?: AirportSummaryInput;
}

const MODELS = ['gfs', 'icon', 'ecmwf'] as const;

export class AirportProfilePanel {
  private container: HTMLElement;
  private model: string;
  private viewMode: ViewMode;
  private onClose?: () => void;
  private onModelChange?: (model: string) => void;
  private onMetricSelect?: (metric: ForecastMetric) => void;
  private onViewChange?: (view: ViewMode) => void;

  private cardEl: HTMLDivElement;
  private crossEl: HTMLDivElement;
  private skewtEl: HTMLDivElement;
  private statusEl: HTMLDivElement;
  private titleEl: HTMLDivElement;
  private modelSel: HTMLSelectElement;
  private viewBtns: Record<ViewMode, HTMLButtonElement>;
  private settingsBtn: HTMLButtonElement;
  private drawerEl: HTMLDivElement;
  private drawerOpen = false;

  /** Per-airport summary for the card (from the forecast map response). */
  private summary: AirportSummaryInput | null = null;
  /** Dwell-prefetch timer + guard so the slow cross-section SSE fires on
   *  intent (dwell / pointer-enter / switching to a heavy view), not on
   *  every marker click. */
  private prefetchTimer: number | null = null;
  private streamStarted = false;
  private static readonly PREFETCH_DWELL_MS = 400;

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
  /** Set when a phase fails. Sticky for the rest of the stream so later
   *  progress labels / the GRIB badge can't overwrite the failure. */
  private streamError: string | null = null;

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
    this.viewMode = opts.initialView ?? loadViewMode();
    this.onClose = opts.onClose;
    this.onModelChange = opts.onModelChange;
    this.onMetricSelect = opts.onMetricSelect;
    this.onViewChange = opts.onViewChange;

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
          <button class="btn-toggle" data-view="card">Card</button>
          <button class="btn-toggle" data-view="cross">Cross-section</button>
          <button class="btn-toggle" data-view="skewt">Skew-T</button>
        </div>
        <button class="ap-settings-btn" type="button" title="Layers & overlays" aria-label="Settings" aria-expanded="false">⚙</button>
      </div>
      <div class="ap-panel-body">
        <div class="ap-card-host" id="ap-card"></div>
        <div class="ap-cross" id="ap-cross"></div>
        <div class="ap-skewt" id="ap-skewt"></div>
      </div>
      <!-- Status sits *below* the body: it appears mid-stream (progress
           labels → GRIB badge), and above the body every appearance would
           shove the card down under the user's cursor. -->
      <div class="ap-panel-status" id="ap-status"></div>
      <div class="ap-settings-drawer" hidden></div>
    `;

    this.titleEl = this.container.querySelector('.ap-panel-title') as HTMLDivElement;
    this.statusEl = this.container.querySelector('.ap-panel-status') as HTMLDivElement;
    this.cardEl = this.container.querySelector('.ap-card-host') as HTMLDivElement;
    this.crossEl = this.container.querySelector('.ap-cross') as HTMLDivElement;
    this.skewtEl = this.container.querySelector('.ap-skewt') as HTMLDivElement;
    this.modelSel = this.container.querySelector('.ap-model-sel') as HTMLSelectElement;
    this.modelSel.value = this.model;
    this.settingsBtn = this.container.querySelector('.ap-settings-btn') as HTMLButtonElement;
    this.drawerEl = this.container.querySelector('.ap-settings-drawer') as HTMLDivElement;

    const viewGroup = this.container.querySelector('.ap-view-group') as HTMLElement;
    this.viewBtns = {
      card: viewGroup.querySelector('[data-view="card"]') as HTMLButtonElement,
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
      this.onViewChange?.(v);
      // Switching to a heavy view is explicit intent — start the SSE now
      // if the dwell timer hasn't already fired.
      if (v === 'cross' || v === 'skewt') this.ensureStream();
      this.applyViewMode();
      // Card view has no layer/overlay controls (the gear is hidden), so
      // close the drawer instead of leaving orphaned Cross/Skew-T sections.
      // For cross/skewt, re-render so the just-shown section's controls appear.
      if (v === 'card') {
        if (this.drawerOpen) this.toggleDrawer();
      } else if (this.drawerOpen) {
        this.renderDrawer();
      }
    });
    this.settingsBtn.addEventListener('click', () => this.toggleDrawer());

    // Pointer entering the panel is an intent signal — warm the
    // cross-section in the background so a switch to it feels instant.
    this.container.addEventListener('pointerenter', () => {
      if (this.currentRequest && !this.streamStarted) this.ensureStream();
    });
  }

  /** Update the model from outside (e.g. for URL deep-linking). */
  setModel(model: string): void {
    if (!MODELS.includes(model as any)) return;
    this.model = model;
    this.modelSel.value = model;
    // The card's Windy link carries the model — keep it in step.
    if (this.summary) this.renderCard();
  }

  /** Restrict the model choices to those the selected day actually has data for.
   *
   *  The panel's surface trace is read from the forecast-snapshot table, and not
   *  every model reaches every day: ICON's ceiling GRIB stops at 120h, so from
   *  D+5 there are no ICON snapshots at all. Offering ICON there would draw a
   *  cross-section with an empty surface — absence rendered as if it were a
   *  forecast, which is the one thing this map must not do. Pass the models the
   *  server reports for the day (`/maps/forecast/days`).
   */
  setAvailableModels(models: string[]): void {
    if (!models.length) return;  // grid not known yet — leave the choices alone
    for (const opt of Array.from(this.modelSel.options)) {
      const has = models.includes(opt.value);
      opt.disabled = !has;
      opt.textContent = has
        ? opt.value.toUpperCase()
        : `${opt.value.toUpperCase()} — not at this range`;
    }
    if (!models.includes(this.model)) {
      const fallback = models.includes('ecmwf') ? 'ecmwf' : models[0];
      this.model = fallback;
      this.modelSel.value = fallback;
      this.onModelChange?.(fallback);
      if (this.summary) this.renderCard();
    }
  }

  /** Read the panel's current model — used by the host to round-trip
   *  the panel's selection through the URL state. */
  getModel(): string {
    return this.model;
  }

  /** Open the panel for a new airport / hour. Renders the summary card
   *  immediately from host-provided data; the slow cross-section SSE is
   *  deferred (see `ensureStream` / dwell prefetch) unless a heavy view is
   *  already selected. */
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
    if (req.summary) this.summary = req.summary;

    // Cancel any pending prefetch + in-flight stream from the prior request.
    this.cancelPrefetch();
    if (this.stream) { this.stream.abort(); this.stream = null; }
    this.streamStarted = false;
    this.streamError = null;

    // Reset to an empty snapshot and render the card — instant, no network.
    this.snapshot = {
      meta: null, surface: [], levels: [], enriched: null, derived: [],
    };
    this.titleEl.textContent = req.icao;
    this.statusEl.innerHTML = '';
    this.clearRenderers();
    this.renderCard();
    this.applyViewMode();

    // Heavy view already selected → fetch now; otherwise defer the slow
    // cross-section SSE until the user shows intent.
    if (this.viewMode === 'cross' || this.viewMode === 'skewt') {
      this.ensureStream();
    } else {
      this.armPrefetch();
    }
  }

  /** Kick off (or reuse) the cross-section SSE for the current request +
   *  model. Idempotent per request+model via `streamStarted`; serves a
   *  cached snapshot instantly when one exists. */
  private ensureStream(): void {
    if (this.streamStarted || !this.currentRequest) return;
    this.cancelPrefetch();
    const req = this.currentRequest;

    // Cache hit: skip the SSE round-trip entirely. Bump the entry to
    // the most-recently-used slot (Map preserves insertion order, so
    // delete-then-set moves it to the end) — that way a still-actively-used
    // entry isn't evicted just because it was loaded first.
    const cacheKey = this.cacheKey(req, this.model);
    const cached = this.snapshotCache.get(cacheKey);
    if (cached) {
      this.snapshotCache.delete(cacheKey);
      this.snapshotCache.set(cacheKey, cached);
      this.streamStarted = true;
      this.streamError = null;
      this.snapshot = cached;
      this.applyTitle(cached);
      this.statusEl.innerHTML = esc(formatEnrichmentBadge(cached.enriched));
      this.clearRenderers();
      this.setLoading(false);
      this.renderCross();
      this.renderSkewT();
      return;
    }

    this.streamStarted = true;
    this.streamError = null;
    this.snapshot = {
      meta: null, surface: [], levels: [], enriched: null, derived: [],
    };
    this.titleEl.textContent = `${req.icao} — loading…`;
    // Only shown until the first `progress` event lands (a few ms) — from
    // then on the server names the stage that is actually running.
    this.statusEl.innerHTML = statusLoading('Connecting');
    this.clearRenderers();
    // Mark canvases as loading so the empty cross-section doesn't read
    // as "real-clear-skies forecast" while we wait for derived data.
    // Lifted in onPhase('derived') / 'complete' / 'error'.
    this.setLoading(true);

    this.stream = streamAirportProfile(
      { icao: req.icao, model: this.model, startHour: req.startHour, windowH: req.windowH },
      (phase, snapshot, raw) => this.onPhase(phase, snapshot, raw),
    );
  }

  /** Arm the dwell timer — if the user lingers on the card, warm the
   *  cross-section so a later switch is instant. Cancelled on close,
   *  reload, or an earlier intent signal (pointer-enter / view switch). */
  private armPrefetch(): void {
    this.cancelPrefetch();
    this.prefetchTimer = window.setTimeout(() => {
      this.prefetchTimer = null;
      this.ensureStream();
    }, AirportProfilePanel.PREFETCH_DWELL_MS);
  }

  private cancelPrefetch(): void {
    if (this.prefetchTimer != null) {
      clearTimeout(this.prefetchTimer);
      this.prefetchTimer = null;
    }
  }

  /** Replace the card's summary and re-render just the card — used when the
   *  map's model/consensus mode changes (the card's consensus column follows
   *  it), without disturbing the cross-section stream. */
  updateSummary(summary: AirportSummaryInput): void {
    this.summary = summary;
    this.renderCard();
  }

  /** Render the summary card from the host-provided snapshot summary. */
  private renderCard(): void {
    if (!this.summary) {
      this.cardEl.innerHTML = '<div class="ap-card-empty">Summary unavailable</div>';
      return;
    }
    renderAirportSummaryCard(this.cardEl, {
      airport: this.summary.airport,
      consensusMode: this.summary.consensusMode,
      validTime: this.summary.validTime,
      modelInitTimes: this.summary.modelInitTimes,
      model: this.model,
      onMetricSelect: this.onMetricSelect,
    });
  }

  /** Toggle the dimmed/grayscale "loading" state on the canvas areas.
   *  Driven by `is-loading` CSS class on `.ap-cross` and `.ap-skewt`
   *  (see maps.html). */
  private setLoading(loading: boolean): void {
    this.crossEl.classList.toggle('is-loading', loading);
    this.skewtEl.classList.toggle('is-loading', loading);
  }

  private cacheKey(req: AirportProfileRequest, model: string): string {
    return `${req.icao}|${req.startHour}|${model}`;
  }

  /** End-of-stream status: which GRIB runs contributed — unless a phase
   *  failed, in which case the failure stays on screen. */
  private applyFinalStatus(snapshot: AirportProfileSnapshot): void {
    this.statusEl.innerHTML = this.streamError
      ? esc(this.streamError)
      : esc(formatEnrichmentBadge(snapshot.enriched));
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

    if (phase === 'meta') {
      this.applyTitle(snapshot);
      this.renderCross();
    } else if (phase === 'surface') {
      this.renderCross();
    } else if (phase === 'progress') {
      // The server announces each stage *before* running it (see
      // api/airport_profile.py `_progress`), so the row names the step the
      // user is waiting on. Data phases (`levels`, `enriched`) deliberately
      // don't touch the status: by the time they land, their stage is done.
      if (!this.streamError) {
        const label = (raw && typeof raw.label === 'string') ? raw.label as string : 'Loading';
        this.statusEl.innerHTML = statusLoading(label);
      }
    } else if (phase === 'levels') {
      if (raw && raw.error) {
        // Sticky: the following stages still run (on an empty profile), and
        // their progress labels must not paper over the failure.
        this.streamError = `Levels failed (${raw.error})`;
        this.statusEl.innerHTML = esc(this.streamError);
      }
      this.renderSkewT();
      // Skew-T renderer just mounted — refresh drawer if open so
      // the previously-empty Skew-T section now has controls.
      if (this.drawerOpen) this.renderDrawer();
    } else if (phase === 'derived') {
      this.applyFinalStatus(snapshot);
      this.setLoading(false);
      this.renderCross();
      this.renderSkewT();
    } else if (phase === 'complete') {
      this.applyFinalStatus(snapshot);
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
      this.streamError = `Error — ${detail}`;
      this.statusEl.innerHTML = esc(this.streamError);
      // Lift the loading dim on error too — the canvases now show
      // whatever last-known state they have (typically empty axes),
      // which is the correct signal alongside the error message.
      this.setLoading(false);
    }
  }

  private applyViewMode(): void {
    for (const v of ['card', 'cross', 'skewt'] as const) {
      this.viewBtns[v].classList.toggle('active', v === this.viewMode);
    }
    const showCard = this.viewMode === 'card';
    const showCross = this.viewMode === 'cross';
    const showSkewT = this.viewMode === 'skewt';
    this.cardEl.style.display = showCard ? 'block' : 'none';
    this.crossEl.style.display = showCross ? 'block' : 'none';
    this.skewtEl.style.display = showSkewT ? 'block' : 'none';
    // The gear (layers/overlays) only applies to the cross-section / Skew-T.
    this.settingsBtn.style.display = showCard ? 'none' : '';
    this.container.classList.toggle('view-card', showCard);
    this.container.classList.toggle('view-cross', showCross);
    this.container.classList.toggle('view-skewt', showSkewT);

    // Build/refresh the active view's renderer from the current snapshot
    // (also lets canvases pick up the new container size).
    setTimeout(() => {
      this.renderCross();
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
    if (this.viewMode !== 'cross') return;
    const data = snapshotToVizData(this.snapshot);
    if (!data) return;
    const r = this.ensureCrossRenderer();
    r.setData(data);
    r.render();
  }

  private renderSkewT(): void {
    if (this.viewMode !== 'skewt') return;
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
  // maps.html: `.ap-settings-drawer { right: 100%; }`) so it grows
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
    // Exact match — 'card' shows neither section (the drawer isn't reachable
    // in card view; this is a guard against orphaned controls if it were).
    const showCross = this.viewMode === 'cross';
    const showSkewT = this.viewMode === 'skewt';

    // Build section shells; populate each with the appropriate
    // sub-control via the dedicated render helpers.
    let html = '<div class="ap-drawer-header">';
    html += '<span class="ap-drawer-title">Layers & overlays</span>';
    html += '<button class="ap-drawer-close" type="button" title="Close" aria-label="Close">×</button>';
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
    this.drawerEl.querySelector('.ap-drawer-close')?.addEventListener(
      'click', () => this.toggleDrawer(),
    );

    if (showCross) {
      const host = this.drawerEl.querySelector('.ap-drawer-cross-host') as HTMLElement;
      // Toggles work even before the cross-section renderer mounts —
      // state is owned by the panel and applied via setLayers() once
      // the renderer exists (see ensureCrossRenderer).
      // The current-conditions overlay is route-only (distance X axis); the
      // airport-profile drawer is time-axis, so drop that group's toggle. The
      // advisory-highlight group is also dropped: this view has no advisory
      // context (snapshotToVizData hardcodes advisoryHighlights=null), so the
      // Highlight toggle could never do anything — a static exclusion here,
      // unlike the main view's dynamic gating (#373).
      renderLayerToggles(host, this.enabledLayers, (layerId) => this.onLayerToggle(layerId), {
        hiddenGroups: new Set(['conditions', 'highlight']),
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
    this.cancelPrefetch();
    if (this.stream) { this.stream.abort(); this.stream = null; }
    this.snapshotCache.clear();
    this.drawerOpen = false;
    this.clearRenderers();
    this.container.innerHTML = '';
    this.container.classList.remove('ap-panel', 'view-card', 'view-cross', 'view-skewt', 'drawer-open');
  }
}
