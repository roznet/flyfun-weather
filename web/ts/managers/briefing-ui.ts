/** DOM management for the Briefing report page.
 *
 * Renders all sections: header, assessment, synopsis, GRAMET,
 * model comparison, and Skew-T route view.
 */

import type {
  AirportObservation,
  AlternateAirport,
  AltitudeAdvisories,
  ConvectiveAssessment,
  DataStatus,
  FlightResponse,
  ForecastSnapshot,
  IcingRisk,
  ModelSourceDetail,
  ObservationComparison,
  PackMeta,
  RouteAlternates,
  RouteAnalysesManifest,
  RouteObservations,
  RoutePointAnalysis,
  RouteSigmets,
  SfipZone,
  SoundingAnalysis,
  ThermodynamicIndices,
  VerticalRegime,
  WeatherDigest,
} from '../store/types';
import type { RouteAdvisoriesManifest, RouteAdvisoryResult, AdvisoryStatus } from '../types/advisories';
import type { DisplayMode, Tier } from '../types/metrics';
import {
  getDisplayConfig,
  getMetric,
  isMetricVisible,
  matchThreshold,
  renderAnnotationRow,
  renderInfoButton,
  riskCssClass,
  variableToMetricId,
} from '../helpers/metrics-helper';
import { showPopupContent } from '../components/info-popup';
import * as api from '../adapters/api-adapter';
import { $, escapeHtml, formatAlt, formatDate, formatDepartureTime, modelLabel, buildWindyUrl, flightTitle, flightRouteCompact } from '../utils';
import { t, getDateLocale } from '../i18n/i18n';
import { showRoutePopup } from '../components/route-interpret';

// --- Header ---

export function renderHeader(
  flight: FlightResponse | null,
  snapshot: ForecastSnapshot | null,
): void {
  const el = $('briefing-header');
  if (!el || !flight) return;

  // Build waypoint list from snapshot or flight data
  let wps: string[];
  if (snapshot?.route?.waypoints) {
    wps = snapshot.route.waypoints.map((w) => w.icao);
  } else if (flight.waypoints?.length) {
    wps = flight.waypoints;
  } else {
    wps = flight.route_name.split('_').map(w => w.toUpperCase());
  }

  const title = flightTitle(wps);
  const compactRoute = wps.length > 2 ? flightRouteCompact(wps) : null;
  const dateStr = formatDate(flight.target_date);
  const timeStr = formatDepartureTime(flight.departure_time);
  const alt = formatAlt(flight.cruise_altitude_ft);

  const routeHtml = compactRoute
    ? `<span class="briefing-route">
         <span class="route-clickable" role="button" tabindex="0" data-briefing-route="1" title="${escapeHtml(compactRoute.fullText)}" aria-label="Show full route on map">${compactRoute.html}<span class="route-info-icon" aria-hidden="true">ⓘ</span></span>
       </span>`
    : '';

  el.innerHTML = `
    <div class="briefing-header-lines">
      <div class="briefing-header-line1">
        <span class="route-summary">${escapeHtml(title)}</span>
        <span class="date-summary">${escapeHtml(dateStr)} ${escapeHtml(timeStr)}</span>
      </div>
      <div class="briefing-header-line2">
        ${routeHtml}
        <span class="alt-summary">${escapeHtml(alt)}</span>
      </div>
    </div>
  `;

  if (compactRoute) {
    const open = () => {
      void showRoutePopup(
        { waypoints: wps, rawRoute: flight.raw_route },
        (msg) => alert(msg),
      );
    };
    el.querySelectorAll('[data-briefing-route]').forEach((node) => {
      node.addEventListener('click', (e) => {
        e.stopPropagation();
        open();
      });
      node.addEventListener('keydown', (e) => {
        const ke = e as KeyboardEvent;
        if (ke.key === 'Enter' || ke.key === ' ') {
          ke.preventDefault();
          open();
        }
      });
    });
  }
}

// --- Stale-pack banner ---

import { computeStalePackBanner } from '../helpers/stale-pack-banner';

/** Render the stale-pack banner. Owner-only — non-owners can't trigger
 *  a refresh. Uses textContent (not innerHTML) so localized strings
 *  can't introduce markup. */
export function renderStalePackBanner(
  flight: FlightResponse | null,
  manifest: RouteAnalysesManifest | null,
  isOwner: boolean,
  onRefresh: () => void,
): void {
  const el = $('stale-pack-banner');
  if (!el) return;

  const state = computeStalePackBanner(flight, manifest, isOwner);
  if (state === null) {
    el.style.display = 'none';
    return;
  }

  el.style.display = 'block';
  el.replaceChildren();
  const messageSpan = document.createElement('span');
  messageSpan.textContent = state.message;
  el.appendChild(messageSpan);
  const btn = document.createElement('button');
  btn.className = 'btn btn-sm btn-primary';
  btn.id = 'stale-pack-refresh-btn';
  btn.style.marginLeft = '0.75rem';
  btn.textContent = t('stalePack.refreshBtn');
  btn.addEventListener('click', () => onRefresh());
  el.appendChild(btn);
}

// --- History dropdown ---

export function renderHistoryDropdown(
  packs: PackMeta[],
  currentTimestamp: string | null,
  onSelect: (ts: string) => void,
): void {
  const select = $('history-select') as HTMLSelectElement;
  if (!select) return;

  select.innerHTML = packs.length === 0
    ? `<option>${t('briefing.noBriefings')}</option>`
    : packs.map((p) => {
        const date = new Date(p.fetch_timestamp);
        const dLabel = p.days_out >= 0 ? `D-${p.days_out}` : `D${p.days_out}`;
        const label = `${dLabel} (${date.toLocaleDateString(getDateLocale(), { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' })} UTC)`;
        const selected = p.fetch_timestamp === currentTimestamp ? ' selected' : '';
        return `<option value="${p.fetch_timestamp}"${selected}>${label}</option>`;
      }).join('');

  // Wire change event (remove old listener by replacing element)
  const newSelect = select.cloneNode(true) as HTMLSelectElement;
  select.parentNode!.replaceChild(newSelect, select);
  newSelect.addEventListener('change', () => {
    onSelect(newSelect.value);
  });
}

// --- Assessment banner ---

const ASSESSMENT_BADGE_CLASS: Record<AdvisoryStatus, string> = {
  red: 'badge-red',
  amber: 'badge-amber',
  green: 'badge-green',
  unavailable: 'badge-muted',
};

const ASSESSMENT_BADGE_LETTER: Record<AdvisoryStatus, string> = {
  red: 'R',
  amber: 'A',
  green: 'G',
  unavailable: '?',
};

interface AdvisoryCounts {
  red: number;
  amber: number;
  green: number;
}

function countAdvisoryStatuses(manifest: RouteAdvisoriesManifest | null): AdvisoryCounts {
  const counts: AdvisoryCounts = { red: 0, amber: 0, green: 0 };
  if (!manifest) return counts;
  for (const a of manifest.advisories) {
    if (a.aggregate_status === 'red') counts.red++;
    else if (a.aggregate_status === 'amber') counts.amber++;
    else if (a.aggregate_status === 'green') counts.green++;
  }
  return counts;
}

/**
 * Compare two advisory tallies using the same rule as the altitude table:
 * fewer reds wins, ties broken by fewer ambers.
 */
function compareAdvisoryCounts(
  alt: AdvisoryCounts,
  primary: AdvisoryCounts,
): 'better' | 'same' | 'worse' {
  if (alt.red < primary.red) return 'better';
  if (alt.red > primary.red) return 'worse';
  if (alt.amber < primary.amber) return 'better';
  if (alt.amber > primary.amber) return 'worse';
  return 'same';
}

function renderAdvisoryChips(manifest: RouteAdvisoriesManifest): string {
  const catalog = new Map(manifest.catalog.map(e => [e.id, e.name]));
  const concerns = manifest.advisories
    .filter(a => a.aggregate_status === 'red' || a.aggregate_status === 'amber')
    .sort((a, b) => {
      if (a.aggregate_status === b.aggregate_status) return 0;
      return a.aggregate_status === 'red' ? -1 : 1;
    });

  if (concerns.length === 0) {
    const greenCount = manifest.advisories.filter(a => a.aggregate_status === 'green').length;
    if (greenCount === 0) return '';
    return `<span class="assessment-chip"><span class="badge badge-green">G</span> ${greenCount} clear</span>`;
  }

  return concerns.map((adv: RouteAdvisoryResult) => {
    const name = catalog.get(adv.advisory_id) ?? adv.advisory_id;
    const cls = ASSESSMENT_BADGE_CLASS[adv.aggregate_status];
    const letter = ASSESSMENT_BADGE_LETTER[adv.aggregate_status];
    return `<span class="assessment-chip"><span class="badge ${cls}">${letter}</span> ${escapeHtml(name)}</span>`;
  }).join(' ');
}

export function renderAssessment(
  pack: PackMeta | null,
  flight?: FlightResponse | null,
  routeAdvisories?: RouteAdvisoriesManifest | null,
  altAdvisories?: RouteAdvisoriesManifest | null,
  digestPending: boolean = false,
  onGenerateDigest?: () => void,
): void {
  const el = $('assessment-banner');
  if (el) {
    if (!pack || !pack.assessment) {
      el.className = 'assessment-banner assessment-none';
      el.textContent = t('briefing.noAssessment');
    } else {
      const level = pack.assessment.toUpperCase();
      el.className = `assessment-banner assessment-${level.toLowerCase()}`;
      // The AI summary status lives here, next to the level chip \u2014 this is the
      // line people actually read. Cases after the chip:
      //   has_digest        \u2192 dash + the digest's reason sentence (or nothing).
      //   digestPending     \u2192 still generating (refresh or on-demand) \u2014 spinner.
      //   AI off for pack   \u2192 "AI summary off" + a Generate button (on demand).
      //   requested+absent  \u2192 generation failed \u2192 say so + offer to retry.
      let suffixHtml: string;
      let showGenerate = false;
      if (pack.has_digest) {
        suffixHtml = pack.assessment_reason ? ` \u2014 ${escapeHtml(pack.assessment_reason)}` : '';
      } else if (digestPending) {
        suffixHtml = ` <span class="assessment-pending">${escapeHtml(t('digest.generating'))}</span>`;
      } else if (pack.llm_digest_requested === false) {
        suffixHtml = ` <span class="assessment-note">${escapeHtml(t('digest.disabled'))}</span>`;
        showGenerate = true;
      } else {
        suffixHtml = ` <span class="assessment-note">${escapeHtml(t('digest.failedShort'))}</span>`;
        showGenerate = true;
      }
      const btnHtml = (showGenerate && onGenerateDigest)
        ? ` <button class="assessment-generate-btn" id="generate-digest-btn">${escapeHtml(t('digest.generate'))}</button>`
        : '';
      el.innerHTML = `<strong>${level}</strong>${suffixHtml}${btnHtml}`;
      if (btnHtml) {
        document.getElementById('generate-digest-btn')
          ?.addEventListener('click', () => onGenerateDigest?.());
      }
    }
  }

  // Alternate departure time \u2014 separate banner outside the AI digest box
  const altEl = $('alt-assessment-banner');
  if (!altEl) return;

  if (!pack || !pack.alt_assessment || !flight?.alt_departure_time) {
    altEl.style.display = 'none';
    altEl.innerHTML = '';
    return;
  }

  const altLevel = pack.alt_assessment.toUpperCase();
  const altTime = formatDepartureTime(flight.alt_departure_time);
  const chipsHtml = altAdvisories
    ? renderAdvisoryChips(altAdvisories)
    : (pack.alt_assessment_reason ? escapeHtml(pack.alt_assessment_reason) : '');

  // Only show comparison when both manifests are available
  let cmpHtml = '';
  if (routeAdvisories && altAdvisories) {
    const cmp = compareAdvisoryCounts(
      countAdvisoryStatuses(altAdvisories),
      countAdvisoryStatuses(routeAdvisories),
    );
    const label = cmp === 'better' ? 'Better' : cmp === 'worse' ? 'Worse' : 'Same';
    cmpHtml = `<span class="alt-cmp alt-cmp-${cmp}">${label}</span>`;
  }

  altEl.style.display = '';
  altEl.className = 'alt-assessment-banner';
  altEl.innerHTML = `
    <div class="alt-assessment-header">
      <strong>Alternate Departure Time ${escapeHtml(altTime)}:</strong>
      <span class="alt-assessment-level assessment-${altLevel.toLowerCase()}-text">${altLevel}</span>
      ${cmpHtml}
    </div>
    <div class="alt-assessment-chips">${chipsHtml}</div>
  `;
}

// --- Freshness bar ---

function formatModelRunTime(initTime: number): string {
  const d = new Date(initTime * 1000);
  const h = d.getUTCHours().toString().padStart(2, '0');
  return `${h}Z`;
}

/** Build the per-model basis segments from the freshness `sources` list.
 *
 * For each model:
 * - if only one source contributed, render `MODEL RUNZ PROVIDER`
 * - if both contributed AND on the same run, collapse to the primary alone
 *   (no need to mention Open-Meteo redundantly)
 * - if both contributed AND runs differ, render `MODEL DIRECTRUNZ PROVIDER, OMRUNZ Open-Meteo`
 *
 * Falls back to the legacy pack-times path when the API hasn't sent the
 * `sources` field yet (older clients/responses).
 */
function buildBasisFromSources(
  sources: ModelSourceDetail[] | undefined,
  pack: PackMeta | null,
): string[] {
  if (!sources || sources.length === 0) {
    // Legacy fallback: use pack init times (no provider attribution).
    const packTimes = pack?.model_init_times || {};
    const gribTimes = pack?.grib_init_times || {};
    return Object.entries(packTimes).map(([m, t]) => {
      const gribTs = gribTimes[m];
      if (gribTs && gribTs !== t) {
        return `${modelLabel(m)} ${formatModelRunTime(gribTs)}, ${formatModelRunTime(t)} Open-Meteo`;
      }
      return `${modelLabel(m)} ${formatModelRunTime(t)}`;
    });
  }

  // Group sources by model, preserving insertion order.
  const byModel = new Map<string, ModelSourceDetail[]>();
  for (const s of sources) {
    const list = byModel.get(s.model) || [];
    list.push(s);
    byModel.set(s.model, list);
  }

  const out: string[] = [];
  for (const [model, rows] of byModel) {
    const primary = rows.find(r => r.role === 'primary') || rows[0];
    const base = rows.find(r => r !== primary);
    const label = modelLabel(model);
    // Drop the provider tag when it's already a token in the model label
    // (e.g. "DWD ICON 12Z DWD" → "DWD ICON 12Z", "ECMWF IFS 12Z ECMWF" →
    // "ECMWF IFS 12Z").  Avoids visible duplication for ICON/ECMWF where
    // the provider is part of how pilots refer to the model.
    const tag = (provider: string) =>
      labelHasProvider(label, provider) ? '' : ` ${provider}`;
    if (!base || base.init === primary.init) {
      out.push(`${label} ${formatModelRunTime(primary.init)}${tag(primary.provider)}`);
    } else {
      out.push(
        `${label} ${formatModelRunTime(primary.init)}${tag(primary.provider)}, ` +
        `${formatModelRunTime(base.init)}${tag(base.provider)}`,
      );
    }
  }
  return out;
}

/** Return true when the provider name appears as a whole-word token in the label. */
function labelHasProvider(label: string, provider: string): boolean {
  const tokens = label.toUpperCase().split(/\s+/);
  return tokens.includes(provider.toUpperCase());
}

/** Format an ISO datetime for the popup as `DD MMM HH:MM UTC`. */
function formatPopupTime(iso: string): string {
  const d = new Date(iso);
  const day = d.getUTCDate().toString().padStart(2, '0');
  const months = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'];
  const mon = months[d.getUTCMonth()];
  const h = d.getUTCHours().toString().padStart(2, '0');
  const m = d.getUTCMinutes().toString().padStart(2, '0');
  return `${day} ${mon} ${h}:${m} UTC`;
}

/** Build the per-source detail table shown in the (i) popover. */
function renderSourcesPopupContent(sources: ModelSourceDetail[]): string {
  // Group by model so the "primary + base" pairing is visually adjacent.
  const byModel = new Map<string, ModelSourceDetail[]>();
  for (const s of sources) {
    const list = byModel.get(s.model) || [];
    list.push(s);
    byModel.set(s.model, list);
  }

  const rows: string[] = [];
  for (const [model, modelRows] of byModel) {
    const primary = modelRows.find(r => r.role === 'primary') || modelRows[0];
    const ordered = [primary, ...modelRows.filter(r => r !== primary)];
    ordered.forEach((r, idx) => {
      const modelCell = idx === 0
        ? `<td class="freshness-popup-model">${escapeHtml(modelLabel(model))}</td>`
        : `<td></td>`;
      const published = r.published_at
        ? formatPopupTime(r.published_at)
        : t('freshness.publishedUnknown');
      rows.push(`
        <tr class="freshness-popup-row freshness-popup-${r.role}">
          ${modelCell}
          <td>${escapeHtml(r.provider)}</td>
          <td>${formatModelRunTime(r.init)}</td>
          <td>${escapeHtml(published)}</td>
          <td>${formatPopupTime(r.next_expected)}</td>
        </tr>
      `);
    });
  }

  return `
    <div class="freshness-popup">
      <h3>${t('freshness.sourcesTitle')}</h3>
      <p class="freshness-popup-intro">${t('freshness.sourcesIntro')}</p>
      <table class="freshness-popup-table">
        <thead>
          <tr>
            <th>${t('freshness.colModel')}</th>
            <th>${t('freshness.colProvider')}</th>
            <th>${t('freshness.colRun')}</th>
            <th>${t('freshness.colPublished')}</th>
            <th>${t('freshness.colNextUpdate')}</th>
          </tr>
        </thead>
        <tbody>
          ${rows.join('')}
        </tbody>
      </table>
    </div>
  `;
}

function formatTimeUntil(isoStr: string): string {
  const target = new Date(isoStr).getTime();
  const now = Date.now();
  const diffMs = target - now;
  if (diffMs <= 0) return 'soon';
  const hours = Math.floor(diffMs / 3600000);
  const mins = Math.floor((diffMs % 3600000) / 60000);
  if (hours > 0) return `${hours}h ${mins}m`;
  return `${mins}m`;
}

// Persisted expand/collapse state for the sidebar freshness disclosure. The
// bar re-renders (innerHTML) on every freshness poll, so the chosen state must
// survive in storage rather than the DOM. Collapsed by default.
const FRESHNESS_EXPANDED_KEY = 'wb_freshness_expanded';
function getFreshnessExpanded(): boolean {
  try { return localStorage.getItem(FRESHNESS_EXPANDED_KEY) === '1'; } catch { return false; }
}
function setFreshnessExpanded(v: boolean): void {
  try { localStorage.setItem(FRESHNESS_EXPANDED_KEY, v ? '1' : '0'); } catch { /* ignore */ }
}

export function renderFreshnessBar(
  freshness: DataStatus | null,
  freshnessLoading: boolean,
  pack: PackMeta | null,
  isAdmin: boolean,
  refreshing: boolean,
  refreshStatus: 'queued' | 'refreshing' | null,
  refreshStage: string | null,
  refreshDetail: string | null,
  onForceRefresh: () => void,
  onCheckAgain: () => void,
  refreshElapsed?: number | null,
  avgRefreshSeconds?: number | null,
  notifyEmail?: boolean,
  onNotifyEmailChange?: (checked: boolean) => void,
): void {
  const el = $('freshness-bar');
  if (!el) return;

  if (!pack && !refreshing) {
    el.style.display = 'none';
    return;
  }

  el.style.display = '';

  // Refreshing state takes priority — show pipeline progress
  if (refreshing) {
    el.className = 'freshness-bar freshness-refreshing';
    if (refreshStatus === 'queued') {
      el.innerHTML = `<span class="refresh-prefix">${t('freshness.queued')}</span> · ${t('freshness.queuedWaiting')}<span class="dots-spinner"></span>`;
      return;
    }
    const detailSuffix = refreshDetail ? ` (${escapeHtml(refreshDetail)})` : '';
    const label = refreshStage ? escapeHtml(refreshStage) : t('freshness.starting');

    // Build "you can leave" hint with average time
    let avgHint = '';
    if (avgRefreshSeconds && avgRefreshSeconds > 0) {
      const mins = Math.ceil(avgRefreshSeconds / 60);
      avgHint = t('freshness.avgTime', { mins: String(mins) });
    }
    const leaveHint = `<span class="freshness-leave-hint">${t('freshness.canLeave')}${avgHint ? ' ' + avgHint : ''}</span>`;

    // Read-only confirmation if email notification was opted in before refresh started
    const emailNote = notifyEmail
      ? `<span class="freshness-leave-hint">${t('freshness.notifyEmailActive')}</span>`
      : '';

    el.innerHTML = `<span class="refresh-prefix">${t('freshness.inProgress')}</span> · ${label}${detailSuffix}<span class="dots-spinner"></span>${leaveHint}${emailNote}`;
    return;
  }

  if (freshnessLoading && !freshness) {
    el.className = 'freshness-bar freshness-current';
    el.innerHTML = t('freshness.checking');
    return;
  }

  if (!freshness) {
    el.style.display = 'none';
    return;
  }

  // Model basis line — one segment per model, listing each contributing
  // provider with its run time.  When the primary (direct) and base (OM)
  // sources are on the same run, collapse to the primary alone — no
  // need to mention Open-Meteo redundantly.  Models are separated by `·`
  // so the within-model comma stays unambiguous.
  const fetchedParts = buildBasisFromSources(freshness.sources, pack);
  const skippedParts = (pack?.models_skipped_region || [])
    .map(m => `${modelLabel(m)} <span class="model-skipped">${t('freshness.skipped')}</span>`);
  const basisParts = [...fetchedParts, ...skippedParts].join(' · ');
  // Note: don't add `metric-info-btn` here — there's a global click
  // delegate (briefing-main.ts) that routes that class to showMetricInfo
  // and would override our handler with "no info for this metric".
  const infoBtn = (freshness.sources && freshness.sources.length > 0)
    ? ` <button type="button" class="freshness-info-btn" id="freshness-sources-info" aria-label="${t('freshness.infoLabel')}">i</button>`
    : '';
  const basisLine = basisParts
    ? `<span class="freshness-basis">${t('freshness.basedOn')}${basisParts}${infoBtn}</span>`
    : '';

  // Build diagnostics HTML — show warn (retryable issues) and error
  // (irrecoverable failures); info entries are persisted for debug only.
  // See models/diagnostic.py for the level convention.
  const diagEntries = (pack?.diagnostics || []).filter(
    d => d.level === 'warn' || d.level === 'error',
  );
  const diagHtml = diagEntries.length > 0
    ? `<span class="freshness-diagnostics">${diagEntries.map(d =>
        `<span class="diag-${d.level}">${escapeHtml(d.message)}</span>`
      ).join('')}</span>`
    : '';

  // "Refreshed in ..." badge (shown briefly after refresh completes)
  let elapsedBadge = '';
  if (refreshElapsed && refreshElapsed > 0) {
    const mins = Math.floor(refreshElapsed / 60);
    const secs = Math.round(refreshElapsed % 60);
    elapsedBadge = `<span class="freshness-elapsed">${t('freshness.refreshedIn', { m: mins, s: secs })}</span>`;
  }

  const forceLink = isAdmin
    ? ` <a href="#" class="freshness-link" id="freshness-force-refresh">${t('freshness.forceRefresh')}</a>`
    : '';

  // Email notification opt-in checkbox (shown before refresh starts)
  const emailCheckId = 'freshness-notify-email';
  const emailChecked = notifyEmail ? ' checked' : '';
  const emailToggle = `<label class="freshness-notify-label"><input type="checkbox" id="${emailCheckId}"${emailChecked}> ${t('freshness.notifyEmail')}</label>`;

  // Tiered refresh gate: show what pressing refresh will actually
  // do, so the bar agrees with the button. Only the `realtime` and gated-`none`
  // (some updates, but below the lead-time threshold) cases need special
  // wording; `full` and "nothing new yet" fall through to the fresh/stale logic.
  const gate = freshness.refresh_decision;
  const checkLinkHtml = `<a href="#" class="freshness-link" id="freshness-check-again">${t('freshness.checkAgain')}</a>`;

  // Each non-refreshing branch contributes a status sentence + its action
  // links; assembly happens once below (inline for the classic page, or a
  // collapsible disclosure in the sidebar layout where rail space is tight).
  let statusHtml = '';
  let linksHtml = '';

  if (gate && gate.mode === 'realtime') {
    el.className = 'freshness-bar freshness-current';
    statusHtml = t('freshness.gatedRealtime');
    linksHtml = `${checkLinkHtml}${forceLink}`;
  } else if (gate && gate.mode === 'none') {
    // Below the lead-time threshold — a press won't run a full pipeline yet.
    // One consistent "up to date" message at every stage: it answers *when* a
    // full refresh becomes worthwhile (eta = the model run that crosses the
    // threshold) and *what* we're still waiting on (the not-yet-updated models),
    // so the wording doesn't lurch as runs trickle in.
    el.className = 'freshness-bar freshness-current';
    const missing = (gate.pending_models ?? []).map((m) => modelLabel(m)).join(', ');
    if (gate.eta_useful && missing) {
      statusHtml = t('freshness.gatedWaiting', { time: formatTimeUntil(gate.eta_useful), models: missing });
    } else if (missing) {
      statusHtml = t('freshness.gatedWaitingNoEta', { models: missing });
    } else {
      statusHtml = t('freshness.upToDate');
    }
    linksHtml = `${checkLinkHtml}${forceLink}`;
  } else if (freshness.fresh) {
    let nextInfo = '';
    if (freshness.next_expected_update && freshness.next_expected_model) {
      const timeStr = formatTimeUntil(freshness.next_expected_update);
      nextInfo = t('freshness.nextUpdate', { time: `${modelLabel(freshness.next_expected_model)} ${timeStr}` });
    }
    el.className = 'freshness-bar freshness-current';
    statusHtml = `${t('freshness.upToDate')}${nextInfo}`;
    linksHtml = `${checkLinkHtml}${forceLink}`;
  } else {
    const staleStr = freshness.stale_models.map((m) => modelLabel(m)).join(', ');
    el.className = 'freshness-bar freshness-stale';
    statusHtml = `${t('freshness.updatesAvailable')}${staleStr}`;
    linksHtml = `${forceLink}`;
  }

  if (document.querySelector('.container.layout-sidebar')) {
    // Collapsible disclosure: the status sentence stays visible with a caret;
    // the action links, "Based on…" basis, diagnostics and the email opt-in are
    // tucked into the expandable details. Collapsed by default; state persisted.
    const expanded = getFreshnessExpanded();
    const actions = linksHtml ? `<div class="freshness-actions">${linksHtml}</div>` : '';
    const detailsHtml = `${actions}${elapsedBadge}${basisLine}${diagHtml}${emailToggle}`;
    el.innerHTML =
      `<button type="button" class="freshness-toggle" id="freshness-toggle" aria-expanded="${expanded}">`
      + `<span class="freshness-caret" aria-hidden="true">›</span>`
      + `<span class="freshness-status">${statusHtml}</span></button>`
      + `<div class="freshness-details" id="freshness-details"${expanded ? '' : ' hidden'}>${detailsHtml}</div>`;
  } else {
    el.innerHTML = `<span>${statusHtml} ${linksHtml}</span>${elapsedBadge}${basisLine}${diagHtml}${emailToggle}`;
  }

  // Wire event handlers
  const checkLink = document.getElementById('freshness-check-again');
  if (checkLink) {
    checkLink.addEventListener('click', (e) => { e.preventDefault(); onCheckAgain(); });
  }
  const forceEl = document.getElementById('freshness-force-refresh');
  if (forceEl) {
    forceEl.addEventListener('click', (e) => { e.preventDefault(); onForceRefresh(); });
  }
  const emailEl = document.getElementById(emailCheckId) as HTMLInputElement | null;
  if (emailEl && onNotifyEmailChange) {
    emailEl.addEventListener('change', () => onNotifyEmailChange(emailEl.checked));
  }
  const sourcesInfoBtn = document.getElementById('freshness-sources-info');
  if (sourcesInfoBtn && freshness.sources) {
    sourcesInfoBtn.addEventListener('click', (e) => {
      e.preventDefault();
      showPopupContent(renderSourcesPopupContent(freshness.sources!));
    });
  }

  // Sidebar layout: wire the collapsible disclosure toggle.
  const fToggle = document.getElementById('freshness-toggle');
  const fDetails = document.getElementById('freshness-details');
  if (fToggle && fDetails) {
    fToggle.addEventListener('click', () => {
      const next = !getFreshnessExpanded();
      setFreshnessExpanded(next);
      fDetails.hidden = !next;
      fToggle.setAttribute('aria-expanded', String(next));
    });
  }
}

// --- Sharing controls (briefing page) ---

export interface BriefingSharingHandlers {
  onSubscribe: () => void;
  onUnsubscribe: () => void;
  onCopyShareLink: () => void;
}

/** Show/hide toolbar controls based on role and wire the subscribe/share buttons.
 *  Called whenever the flight changes. */
export function renderBriefingSharing(
  flight: FlightResponse | null,
  handlers: BriefingSharingHandlers,
): void {
  const shareBtn = $('share-btn') as HTMLButtonElement | null;
  const subscribeControl = $('briefing-subscribe-control');
  const sharedByLine = $('briefing-shared-by');
  const refreshBtn = $('refresh-btn') as HTMLButtonElement | null;

  if (!flight) {
    if (shareBtn) shareBtn.style.display = 'none';
    if (subscribeControl) { subscribeControl.style.display = 'none'; subscribeControl.innerHTML = ''; }
    if (sharedByLine) { sharedByLine.style.display = 'none'; sharedByLine.innerHTML = ''; }
    return;
  }

  const isOwner = flight.role === 'owner';

  // Owner: show share icon (disabled when private). Subscriber: hide it.
  if (shareBtn) {
    if (isOwner) {
      shareBtn.style.display = '';
      shareBtn.disabled = flight.private;
      shareBtn.title = flight.private ? t('briefing.sharePrivateTitle') : t('briefing.shareTitle');
      shareBtn.setAttribute('aria-label', shareBtn.title);
      // Replace listener via clone to avoid duplicate handlers on re-render.
      const fresh = shareBtn.cloneNode(true) as HTMLButtonElement;
      shareBtn.parentNode?.replaceChild(fresh, shareBtn);
      fresh.addEventListener('click', handlers.onCopyShareLink);
    } else {
      shareBtn.style.display = 'none';
    }
  }

  // Refresh button visibility is role-based and owned entirely here, so it
  // stays correct across re-renders (subscribe/unsubscribe, role flip). The
  // disabled/title state for past flights is set separately in briefing-main.
  if (refreshBtn) {
    refreshBtn.style.display = isOwner ? '' : 'none';
  }

  // Subscribe / Unsubscribe button (subscriber only).
  if (subscribeControl) {
    if (isOwner) {
      subscribeControl.style.display = 'none';
      subscribeControl.innerHTML = '';
    } else {
      subscribeControl.style.display = '';
      const label = flight.is_subscribed
        ? t('flightDetail.btnUnsubscribe')
        : t('flightDetail.btnSubscribe');
      const cls = flight.is_subscribed ? 'btn-subscribe-briefing btn-outline' : 'btn-subscribe-briefing btn-primary';
      subscribeControl.innerHTML = `<button class="btn btn-sm ${cls}" type="button">${label}</button>`;
      const btn = subscribeControl.querySelector('button');
      btn?.addEventListener('click', () => {
        if (flight.is_subscribed) {
          if (confirm(t('flightDetail.unsubscribeConfirm'))) handlers.onUnsubscribe();
        } else {
          handlers.onSubscribe();
        }
      });
    }
  }

  // "Shared by Alice Pilot" line below the toolbar (subscriber only).
  if (sharedByLine) {
    if (isOwner) {
      sharedByLine.style.display = 'none';
      sharedByLine.innerHTML = '';
    } else {
      const ownerName = flight.owner_display_name || '';
      sharedByLine.style.display = '';
      sharedByLine.innerHTML = `
        <span class="badge badge-shared">${t('flights.sharedBadge')}</span>
        <span>${ownerName ? t('flightDetail.sharedBy', { owner: escapeHtml(ownerName) }) : t('flightDetail.sharedByUnknown')}</span>
      `;
    }
  }
}

// --- Privacy toggle ---

export function renderPrivacyToggle(
  flight: FlightResponse | null,
  currentUserId: string,
  onUpdate: (isPrivate: boolean) => void,
): void {
  const el = $('privacy-bar');
  if (!el) return;

  // Only show for flight owners
  if (!flight || flight.user_id !== currentUserId) {
    el.style.display = 'none';
    return;
  }

  el.style.display = '';
  const isPrivate = flight.private;

  el.innerHTML = `
    <label class="auto-refresh-toggle">
      <input type="checkbox" id="privacy-check" ${isPrivate ? 'checked' : ''}>
      <span>${t('privacy.label')}</span>
    </label>
  `;

  const checkbox = document.getElementById('privacy-check') as HTMLInputElement;
  checkbox.addEventListener('change', () => {
    onUpdate(checkbox.checked);
  });
}

// --- Auto-refresh bar ---

export function renderAutoRefreshBar(
  flight: FlightResponse | null,
  currentUserId: string,
  isPast: boolean,
  onUpdate: (autoRefresh: boolean, hour: number | null) => void,
): void {
  const el = $('auto-refresh-bar');
  if (!el) return;

  // Hide for non-owners or past flights
  if (!flight || flight.user_id !== currentUserId || isPast) {
    el.style.display = 'none';
    return;
  }

  el.style.display = '';
  const enabled = flight.auto_refresh;
  const defaultHour = ((flight.target_time_utc - 1) + 24) % 24;
  const effectiveHour = flight.auto_refresh_hour ?? defaultHour;

  // Build hour options
  const hourOptions = Array.from({ length: 24 }, (_, h) => {
    const label = `${h.toString().padStart(2, '0')}:00Z`;
    const isDefault = h === defaultHour && flight.auto_refresh_hour === null;
    const selected = h === effectiveHour ? ' selected' : '';
    const suffix = isDefault ? t('freshness.auto') : '';
    return `<option value="${h}"${selected}>${label}${suffix}</option>`;
  }).join('');

  el.innerHTML = `
    <label class="auto-refresh-toggle">
      <input type="checkbox" id="auto-refresh-check" ${enabled ? 'checked' : ''}>
      <span>${t('autoRefresh.label')}</span>
    </label>
    <span class="auto-refresh-hour-group" ${enabled ? '' : 'style="display:none;"'}>
      ${t('autoRefresh.at')}<select id="auto-refresh-hour">${hourOptions}</select>
    </span>
  `;

  // Wire events
  const checkbox = document.getElementById('auto-refresh-check') as HTMLInputElement;
  const hourSelect = document.getElementById('auto-refresh-hour') as HTMLSelectElement;
  const hourGroup = el.querySelector('.auto-refresh-hour-group') as HTMLElement;

  checkbox.addEventListener('change', () => {
    const isOn = checkbox.checked;
    hourGroup.style.display = isOn ? '' : 'none';
    const selectedHour = parseInt(hourSelect.value, 10);
    const hourVal = selectedHour === defaultHour ? null : selectedHour;
    onUpdate(isOn, isOn ? hourVal : null);
  });

  hourSelect.addEventListener('change', () => {
    const selectedHour = parseInt(hourSelect.value, 10);
    const hourVal = selectedHour === defaultHour ? null : selectedHour;
    onUpdate(true, hourVal);
  });
}

// --- Route Observations (METAR/TAF) ---

function flightCatBadge(cat: string | null): string {
  if (!cat) return '\u2014';
  const upper = cat.toUpperCase();
  return `<span class="flight-cat-badge flight-cat-${cat.toLowerCase()}">${escapeHtml(upper)}</span>`;
}

function matchIcon(match: string): string {
  switch (match) {
    case 'CONFIRMING': return '<span class="agree-good">&#10003;</span>';
    case 'SIGNIFICANT': return '<span class="agree-moderate">&#9888;</span>';
    case 'CONFLICTING': return '<span class="agree-poor">&#10007;</span>';
    default: return '\u2014';
  }
}

function windAdvisoryBadge(status: string | null, tooltip?: string): string {
  if (!status) return '\u2014';
  const cls = status === 'green' ? 'badge-green'
    : status === 'amber' ? 'badge-amber'
    : status === 'red' ? 'badge-red'
    : 'badge-muted';
  const label = status === 'green' ? 'G'
    : status === 'amber' ? 'A'
    : status === 'red' ? 'R'
    : '?';
  const titleAttr = tooltip ? ` title="${escapeHtml(tooltip)}"` : '';
  return `<span class="badge ${cls}"${titleAttr}>${label}</span>`;
}

function windTooltip(rwyId: string | null, crosswind: number | null): string {
  if (!rwyId || crosswind == null) return '';
  return `RW${rwyId} xwind ${Math.round(crosswind)}kt`;
}

function formatWindStr(dir: number | null, speed: number | null, gust: number | null): string {
  if (dir == null || speed == null) return '';
  const d = Math.round(dir / 10) * 10;
  const g = gust != null ? `G${Math.round(gust)}` : '';
  return `${String(d).padStart(3, '0')}@${Math.round(speed)}${g}`;
}

function renderObsPopup(apt: AirportObservation, comp: ObservationComparison | undefined): string {
  // METAR raw
  const metarBlock = apt.metar_raw
    ? `<h4>${t('observations.metar')}</h4><code class="obs-popup-metar">${escapeHtml(apt.metar_raw)}</code>`
    : `<h4>${t('observations.metar')}</h4><p class="muted">${t('observations.notAvailable')}</p>`;

  // TAF raw with applicable lines highlighted
  let tafBlock: string;
  if (apt.taf_raw) {
    const lines = apt.taf_raw.split('\n');
    const applicable = new Set(apt.taf_applicable_lines ?? []);
    const tafHtml = lines.map((line, i) => {
      const escaped = escapeHtml(line);
      return applicable.has(i) ? `<mark>${escaped}</mark>` : escaped;
    }).join('\n');
    tafBlock = `<h4>${t('observations.taf')}</h4><code class="obs-popup-taf">${tafHtml}</code>`;
  } else {
    tafBlock = `<h4>${t('observations.taf')}</h4><p class="muted">${t('observations.notAvailable')}</p>`;
  }

  // Wind summary
  const windLines: string[] = [];

  const metarWind = formatWindStr(apt.metar_wind_dir, apt.metar_wind_speed_kt, apt.metar_wind_gust_kt);
  if (metarWind) {
    const rwy = apt.metar_best_runway_id ? ` RW${apt.metar_best_runway_id}` : '';
    const xw = apt.metar_crosswind_kt != null ? ` xwind ${Math.round(apt.metar_crosswind_kt)}kt` : '';
    windLines.push(`METAR: ${metarWind}${rwy}${xw}`);
  }

  const tafWind = formatWindStr(apt.taf_wind_dir, apt.taf_wind_speed_kt, apt.taf_wind_gust_kt);
  if (tafWind) {
    const rwy = apt.taf_best_runway_id ? ` RW${apt.taf_best_runway_id}` : '';
    const xw = apt.taf_crosswind_kt != null ? ` xwind ${Math.round(apt.taf_crosswind_kt)}kt` : '';
    windLines.push(`TAF:   ${tafWind}${rwy}${xw}`);
  }

  if (comp) {
    const modelWind = formatWindStr(comp.model_wind_dir, comp.model_wind_speed_kt, comp.model_wind_gust_kt);
    if (modelWind) {
      const rwy = comp.model_best_runway_id ? ` RW${comp.model_best_runway_id}` : '';
      const xw = comp.model_crosswind_kt != null ? ` xwind ${Math.round(comp.model_crosswind_kt)}kt` : '';
      windLines.push(`Model: ${modelWind}${rwy}${xw}`);
    }
  }

  const windBlock = windLines.length > 0
    ? `<h4>${t('observations.windSummary')}</h4><pre class="obs-wind-summary">${windLines.join('\n')}</pre>`
    : '';

  return `
    <div class="popup-header"><h3>${escapeHtml(apt.icao)}${apt.name ? ' \u2014 ' + escapeHtml(apt.name) : ''}</h3></div>
    ${metarBlock}
    ${tafBlock}
    ${windBlock}
  `;
}

export function renderRouteObservations(
  snapshot: ForecastSnapshot | null,
  onRefresh?: () => Promise<void>,
): void {
  const el = $('observations-section');
  const wrapper = $('observations-wrapper');
  if (!el) return;

  const obs = snapshot?.route_observations;
  if (!obs || obs.airports.length === 0) {
    if (wrapper) wrapper.style.display = 'none';
    return;
  }

  if (wrapper) wrapper.style.display = '';

  // Build comparison lookup by ICAO
  const compMap = new Map<string, ObservationComparison>();
  for (const c of obs.comparisons) {
    compMap.set(c.icao, c);
  }

  // Fetch time label
  let fetchLabel = '';
  if (obs.fetch_time) {
    try {
      const d = new Date(obs.fetch_time);
      fetchLabel = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) + 'Z';
    } catch { /* ignore */ }
  }

  // Refresh button (D-0 only, when callback provided)
  const refreshBtn = (onRefresh && snapshot?.days_out === 0)
    ? `<button class="obs-refresh-btn" title="${t('observations.refreshTitle')}">${t('observations.refresh')}</button>`
    : '';

  // Summary header
  const worstBadge = obs.worst_metar_category
    ? `${t('observations.worst')}${flightCatBadge(obs.worst_metar_category)}`
    : '';
  const phenomena = obs.phenomena_along_route.length > 0
    ? `${t('observations.phenomena')}${escapeHtml(obs.phenomena_along_route.join(', '))}`
    : '';
  const fetchInfo = fetchLabel ? `<span class="obs-fetch-time">${t('observations.fetched')}${fetchLabel}</span>` : '';
  const summaryHtml = `<p class="obs-summary">${obs.airports_with_metar}${t('observations.metarCount')}${obs.airports_with_taf}${t('observations.tafCount')}${Math.round(obs.corridor_nm)}${t('observations.corridor')}${worstBadge}${phenomena} ${fetchInfo}${refreshBtn}</p>`;

  // Conflict banner
  const conflictHtml = obs.has_conflicts
    ? `<div class="obs-conflict-banner">${t('observations.conflictsDetected')}</div>`
    : '';

  // Table rows — only airports with METAR or TAF
  const rows = obs.airports
    .filter((apt) => apt.has_metar || apt.has_taf)
    .map((apt) => {
      const comp = compMap.get(apt.icao);
      const isConflict = comp?.category_match === 'CONFLICTING';
      const rowClass = isConflict ? ' class="obs-conflict-row"' : '';

      // Wind tooltips
      const mTip = windTooltip(apt.metar_best_runway_id, apt.metar_crosswind_kt);
      const tTip = windTooltip(apt.taf_best_runway_id, apt.taf_crosswind_kt);
      const mdlTip = windTooltip(comp?.model_best_runway_id ?? null, comp?.model_crosswind_kt ?? null);

      return `
        <tr${rowClass}>
          <td class="obs-icao">${escapeHtml(apt.icao)} <button class="obs-info-btn" data-icao="${escapeHtml(apt.icao)}" title="${t('observations.showDetails')}" aria-label="${t('observations.info')}">i</button></td>
          <td>${Math.round(apt.distance_from_route_nm)}nm</td>
          <td>${apt.eta_hour_offset != null ? `+${apt.eta_hour_offset}h` : '\u2014'}</td>
          <td class="obs-group-start">${flightCatBadge(apt.metar_flight_category)}</td>
          <td>${flightCatBadge(apt.taf_flight_category_at_eta)}</td>
          <td>${flightCatBadge(comp?.model_category ?? null)}</td>
          <td>${comp ? matchIcon(comp.category_match) : '\u2014'}</td>
          <td class="obs-group-start">${windAdvisoryBadge(apt.metar_wind_advisory, mTip)}</td>
          <td>${windAdvisoryBadge(apt.taf_wind_advisory, tTip)}</td>
          <td>${windAdvisoryBadge(comp?.model_wind_advisory ?? null, mdlTip)}</td>
          <td>${comp?.wind_advisory_match ? matchIcon(comp.wind_advisory_match) : '\u2014'}</td>
        </tr>
      `;
    })
    .join('');

  el.innerHTML = `
    ${summaryHtml}
    ${conflictHtml}
    <div class="table-scroll">
      <table class="band-table obs-table">
        <thead>
          <tr>
            <th rowspan="2" style="text-align:left;">${t('observations.tableIcao')}</th>
            <th rowspan="2">${t('observations.tableDist')}</th>
            <th rowspan="2">${t('observations.tableEta')}</th>
            <th colspan="4" class="obs-group-header">${t('observations.tableCondition')}</th>
            <th colspan="4" class="obs-group-header">${t('observations.tableWind')}</th>
          </tr>
          <tr>
            <th class="obs-group-start">${t('observations.tableMetar')}</th><th>${t('observations.tableTaf')}</th><th>${t('observations.tableModel')}</th><th></th>
            <th class="obs-group-start">${t('observations.tableMetar')}</th><th>${t('observations.tableTaf')}</th><th>${t('observations.tableModel')}</th><th></th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;

  // Wire click handlers via event delegation
  el.addEventListener('click', (e) => {
    const target = e.target as HTMLElement;

    // (i) info button
    const infoBtn = target.closest('.obs-info-btn') as HTMLElement | null;
    if (infoBtn) {
      const icao = infoBtn.dataset.icao;
      if (!icao) return;
      const apt = obs.airports.find((a) => a.icao === icao);
      if (!apt) return;
      const comp = compMap.get(icao);
      showPopupContent(renderObsPopup(apt, comp));
      return;
    }

    // Refresh button
    const refreshBtn = target.closest('.obs-refresh-btn') as HTMLButtonElement | null;
    if (refreshBtn && onRefresh) {
      refreshBtn.disabled = true;
      refreshBtn.textContent = t('observations.refreshing');
      onRefresh().finally(() => {
        // Re-render will replace the button, but just in case:
        refreshBtn.disabled = false;
        refreshBtn.textContent = t('observations.refresh');
      });
    }
  });
}

// --- Weather-based alternates (D-2 inward) ---

function detourLabel(apt: AlternateAirport): string {
  if (apt.detour_early_nm == null && apt.detour_late_nm == null) return '—';
  const early = apt.detour_early_nm != null ? `${apt.detour_early_nm >= 0 ? '+' : ''}${Math.round(apt.detour_early_nm)}` : '?';
  const late = apt.detour_late_nm != null ? `+${Math.round(apt.detour_late_nm)}` : '?';
  return `${early} / ${late}nm`;
}

function deltaTags(apt: AlternateAirport): string {
  const tags: string[] = [];
  if (apt.better_category) tags.push('<span class="alt-tag alt-tag-cat">cat</span>');
  if (apt.better_wind) tags.push('<span class="alt-tag alt-tag-wind">wind</span>');
  if (apt.better_crosswind) tags.push('<span class="alt-tag alt-tag-xw">xwind</span>');
  if (apt.dominates_destination) tags.push('<span class="alt-tag alt-tag-dom" title="Better than the destination on at least one weather axis, and no worse on any">Better</span>');
  return tags.join(' ') || '—';
}

function renderAltPopup(apt: AlternateAirport): string {
  const rows = Object.entries(apt.per_model).map(([model, d]) => {
    const cat = (d['flight_category'] as string) ?? '—';
    const wind = d['wind_speed_kt'] != null ? `${Math.round(d['wind_speed_kt'] as number)}kt` : '—';
    const xw = d['crosswind_kt'] != null ? `${Math.round(d['crosswind_kt'] as number)}kt` : '—';
    return `<tr><td>${escapeHtml(model.toUpperCase())}</td><td>${flightCatBadge(cat)}</td><td>${wind}</td><td>${xw}</td></tr>`;
  }).join('');
  const approach = apt.has_instrument_approach
    ? (apt.best_approach_type ? escapeHtml(apt.best_approach_type) : 'yes')
    : 'none';
  const rwy = apt.longest_runway_ft != null ? `${apt.longest_runway_ft}ft${apt.has_hard_runway ? ' hard' : ''}` : '—';
  const meta = [
    `Distance from dest: ${Math.round(apt.distance_from_dest_nm)}nm (${escapeHtml(apt.position)})`,
    `Detour early/late: ${detourLabel(apt)}`,
    `Approach: ${approach}`,
    `Longest runway: ${rwy}`,
  ].join('\n');
  return `
    <div class="popup-header"><h3>${escapeHtml(apt.icao)}${apt.name ? ' — ' + escapeHtml(apt.name) : ''}</h3></div>
    <pre class="obs-wind-summary">${meta}</pre>
    <h4>Per-model</h4>
    <table class="band-table"><thead><tr><th>Model</th><th>Cat</th><th>Wind</th><th>Xwind</th></tr></thead><tbody>${rows}</tbody></table>
  `;
}

export function renderRouteAlternates(snapshot: ForecastSnapshot | null): void {
  const el = $('alternates-section');
  const wrapper = $('alternates-wrapper');
  if (!el) return;

  const alt: RouteAlternates | null | undefined = snapshot?.alternates;
  // Hidden unless populated — only appears D-2 inward (the stage gate).
  if (!alt) {
    if (wrapper) wrapper.style.display = 'none';
    return;
  }
  if (wrapper) wrapper.style.display = '';

  const destXw = alt.destination_crosswind_kt != null
    ? `, ${Math.round(alt.destination_crosswind_kt)}kt xwind` : '';
  const header = `Destination ${escapeHtml(alt.destination_icao)}: ${flightCatBadge(alt.destination_category)}${destXw}`;

  // Nearest-improving picks ("nearest VFR: EGyy 22nm before")
  const axisLabel: Record<string, string> = {
    category: 'better category', wind: 'lower wind', crosswind: 'lower crosswind',
  };
  const picks = alt.nearest_improving
    .filter((p) => p.icao)
    .map((p) => {
      const pos = p.position ? ` ${escapeHtml(p.position)}` : '';
      return `<li>Nearest ${axisLabel[p.axis] ?? escapeHtml(p.axis)}: <strong>${escapeHtml(p.icao as string)}</strong> ${Math.round(p.distance_from_dest_nm as number)}nm${pos}</li>`;
    })
    .join('');
  const picksHtml = picks
    ? `<ul class="alt-picks">${picks}</ul>`
    : `<p class="muted">No weather alternate improves on the destination across the evaluated candidates.</p>`;

  const approachNote = alt.approach_filter_relaxed ? ', approach data unavailable' : '';
  const summaryHtml = `<p class="obs-summary">${header} <span class="obs-fetch-time">${alt.candidates_evaluated} candidates within ${Math.round(alt.radius_nm)}nm${approachNote}</span></p>`;
  const relaxedHtml = alt.approach_filter_relaxed
    ? `<p class="muted alt-caption">⚠️ No published-approach data is available for the candidates, so non-VFR fields could not be filtered by approach — confirm an approach independently.</p>`
    : '';

  const rows = alt.alternates.map((apt) => {
    const tip = windTooltip(apt.best_runway_id, apt.crosswind_kt);
    const windStr = apt.wind_speed_kt != null ? `${Math.round(apt.wind_speed_kt)}kt` : '—';
    const xwStr = apt.crosswind_kt != null
      ? `<span${tip ? ` title="${escapeHtml(tip)}"` : ''}>${Math.round(apt.crosswind_kt)}kt</span>` : '—';
    const approach = apt.has_instrument_approach
      ? (apt.best_approach_type ? escapeHtml(apt.best_approach_type) : '✓') : '—';
    const agreeCat = apt.agreement?.['flight_category'];
    const agreeBadge = agreeCat ? ` <span class="alt-agree alt-agree-${agreeCat}">${escapeHtml(agreeCat)}</span>` : '';
    return `
      <tr>
        <td class="obs-icao">${escapeHtml(apt.icao)} <button class="alt-info-btn" data-icao="${escapeHtml(apt.icao)}" title="Details" aria-label="info">i</button></td>
        <td>${Math.round(apt.distance_from_dest_nm)}nm</td>
        <td>${escapeHtml(apt.position)} <span class="muted">(${detourLabel(apt)})</span></td>
        <td>${flightCatBadge(apt.flight_category)}${agreeBadge}</td>
        <td>${windStr}</td>
        <td>${xwStr}</td>
        <td>${approach}</td>
        <td>${deltaTags(apt)}</td>
      </tr>
    `;
  }).join('');

  el.innerHTML = `
    ${summaryHtml}
    <p class="muted alt-caption">Planning-grade divert candidates that improve on the destination weather — not an operational alternate (no fuel, minima, NOTAM, customs or PPR check). Non-VFR fields are shown only with a published instrument approach.</p>
    ${relaxedHtml}
    ${picksHtml}
    <div class="table-scroll">
      <table class="band-table obs-table">
        <thead>
          <tr>
            <th style="text-align:left;">Weather alternate</th>
            <th>From dest</th>
            <th>Before/after (detour)</th>
            <th>Category</th>
            <th>Wind</th>
            <th>Xwind</th>
            <th>Approach</th>
            <th>vs dest</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;

  // Assign (not addEventListener) so repeated renders from the store
  // subscription replace the handler rather than stacking duplicates.
  el.onclick = (e) => {
    const target = e.target as HTMLElement;
    const infoBtn = target.closest('.alt-info-btn') as HTMLElement | null;
    if (infoBtn) {
      const icao = infoBtn.dataset.icao;
      if (!icao) return;
      const apt = alt.alternates.find((a) => a.icao === icao);
      if (apt) showPopupContent(renderAltPopup(apt));
    }
  };
}

// --- Route SIGMETs (area hazards) ---

function sigmetLevel(ft: number | null): string {
  if (ft == null) return '?';
  if (ft <= 0) return 'SFC';
  // floor to match the Python helper (ft // 100), so the same SIGMET shows
  // the same flight level in the web UI and the PDF report.
  return `FL${String(Math.floor(ft / 100)).padStart(3, '0')}`;
}

function sigmetBand(baseFt: number | null, topFt: number | null): string {
  if (baseFt == null && topFt == null) return '—';
  return `${sigmetLevel(baseFt)}–${sigmetLevel(topFt)}`;
}

function sigmetEnroute(s: RouteSigmets['sigmets'][number]): string {
  if (s.enroute_distance_from_nm != null && s.enroute_distance_to_nm != null) {
    return `${Math.round(s.enroute_distance_from_nm)}–${Math.round(s.enroute_distance_to_nm)}nm`;
  }
  if (s.min_distance_nm != null) {
    return `${Math.round(s.min_distance_nm)}nm ${t('sigmets.off')}`;
  }
  return '—';
}

function renderSigmetPopup(s: RouteSigmets['sigmets'][number]): string {
  const head = [s.qualifier, s.hazard].filter(Boolean).join(' ') || 'SIGMET';
  const move = s.direction && s.speed_kt ? `${escapeHtml(s.direction)} ${s.speed_kt}kt` : '—';
  const valid = (s.valid_from || s.valid_to)
    ? `${s.valid_from ?? '?'} → ${s.valid_to ?? '?'}`
    : '—';
  const meta = [
    `${t('sigmets.popupLevels')}: ${sigmetBand(s.base_ft, s.top_ft)}`,
    `${t('sigmets.popupMove')}: ${move}`,
    `${t('sigmets.popupValid')}: ${escapeHtml(valid)}`,
  ].join('\n');
  const raw = s.raw_text
    ? `<h4>${t('sigmets.popupRaw')}</h4><code class="obs-popup-metar">${escapeHtml(s.raw_text)}</code>`
    : '';
  return `
    <div class="popup-header"><h3>${escapeHtml(head)} — ${escapeHtml(s.fir_id)}${s.fir_name ? ' (' + escapeHtml(s.fir_name) + ')' : ''}</h3></div>
    <pre class="obs-wind-summary">${meta}</pre>
    ${raw}
  `;
}

export function renderRouteSigmets(snapshot: ForecastSnapshot | null): void {
  const el = $('sigmets-section');
  const wrapper = $('sigmets-wrapper');
  if (!el) return;

  const sig = snapshot?.route_sigmets;
  if (!sig || sig.sigmets.length === 0) {
    if (wrapper) wrapper.style.display = 'none';
    return;
  }
  if (wrapper) wrapper.style.display = '';

  let fetchLabel = '';
  if (sig.fetch_time) {
    try {
      const d = new Date(sig.fetch_time);
      fetchLabel = d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) + 'Z';
    } catch { /* ignore */ }
  }

  const hazards = sig.hazards.length > 0
    ? `${t('sigmets.hazards')}${escapeHtml(sig.hazards.join(', '))}`
    : '';
  const fetchInfo = fetchLabel ? `<span class="obs-fetch-time">${t('observations.fetched')}${fetchLabel}</span>` : '';
  const summaryHtml = `<p class="obs-summary">${sig.count}${t('sigmets.count')}${Math.round(sig.corridor_nm)}${t('sigmets.corridor')}${hazards} ${fetchInfo}</p>`;

  const severeHtml = sig.has_severe
    ? `<div class="obs-conflict-banner">${t('sigmets.severeBanner')}</div>`
    : '';

  const rows = sig.sigmets.map((s, i) => {
    const head = [s.qualifier, s.hazard].filter(Boolean).join(' ') || 'SIGMET';
    const isSevere = (s.qualifier ?? '').toUpperCase() === 'SEV';
    const rowClass = isSevere ? ' class="obs-conflict-row"' : '';
    const move = s.direction && s.speed_kt ? `${escapeHtml(s.direction)} ${s.speed_kt}kt` : '—';
    return `
      <tr${rowClass}>
        <td class="obs-icao">${escapeHtml(head)} <button class="sigmet-info-btn" data-idx="${i}" title="${t('sigmets.showDetails')}" aria-label="${t('sigmets.info')}">i</button></td>
        <td style="text-align:left; font-family:monospace;">${escapeHtml(s.fir_id)}</td>
        <td>${sigmetBand(s.base_ft, s.top_ft)}</td>
        <td>${sigmetEnroute(s)}</td>
        <td>${move}</td>
      </tr>
    `;
  }).join('');

  el.innerHTML = `
    ${summaryHtml}
    ${severeHtml}
    <div class="table-scroll">
      <table class="band-table obs-table">
        <thead>
          <tr>
            <th style="text-align:left;">${t('sigmets.tableHazard')}</th>
            <th>${t('sigmets.tableFir')}</th>
            <th>${t('sigmets.tableLevels')}</th>
            <th>${t('sigmets.tableEnroute')}</th>
            <th>${t('sigmets.tableMove')}</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;

  // Assign (not addEventListener) so repeated renders from the store
  // subscription replace the handler rather than stacking duplicates.
  el.onclick = (e) => {
    const target = e.target as HTMLElement;
    const infoBtn = target.closest('.sigmet-info-btn') as HTMLElement | null;
    if (infoBtn) {
      const idx = Number(infoBtn.dataset.idx);
      const s = sig.sigmets[idx];
      if (s) showPopupContent(renderSigmetPopup(s));
    }
  };
}

// Red warning triangle (text-presentation glyph can't be forced red on all
// platforms, so use an inline SVG: red triangle + white exclamation).
const RD_WARN_ICON =
  '<svg class="rd-icon" viewBox="0 0 24 24" width="15" height="15" aria-hidden="true">'
  + '<path fill="#c0392b" d="M12 3 22 20H2z"/>'
  + '<path fill="#fff" d="M11 9h2v5h-2zm0 6h2v2h-2z"/></svg>';
// Flight-category tokens to colorize inside delta messages (longest first so
// MVFR/LIFR win over VFR/IFR; \b boundaries keep MVFR from matching VFR).
const RD_CAT_RE = /\b(LIFR|MVFR|VFR|IFR)\b/g;

/**
 * Banner warning that conditions worsened since the last real-time refresh.
 * Only shown when something actually got worse; the digest is NOT regenerated
 * on a realtime refresh, so this is the user's signal that the AI text may be
 * behind. Hidden (and cleared) otherwise.
 */
export function renderRefreshDelta(snapshot: ForecastSnapshot | null): void {
  const el = $('refresh-delta-banner');
  if (!el) return;

  const delta = snapshot?.last_refresh_delta;
  if (!delta || !delta.worsened || delta.messages.length === 0) {
    el.style.display = 'none';
    el.innerHTML = '';
    return;
  }

  const items = delta.messages
    .map(m => `<li>${escapeHtml(m).replace(RD_CAT_RE, (cat) => flightCatBadge(cat))}</li>`)
    .join('');
  el.innerHTML = `
    <div class="rd-title">${RD_WARN_ICON}<span>${t('refreshDelta.title')}</span></div>
    <ul class="rd-list">${items}</ul>
  `;
  el.style.display = '';
}

// --- Synopsis (structured digest) ---

const DIGEST_SECTIONS: Array<{ key: keyof WeatherDigest; labelKey: string; icon: string }> = [
  { key: 'synoptic', labelKey: 'digest.synoptic', icon: '\uD83C\uDF0D' },
  { key: 'specific_concerns', labelKey: 'digest.concerns', icon: '\u26A0\uFE0F' },
  { key: 'trend', labelKey: 'digest.trend', icon: '\uD83D\uDCC8' },
  { key: 'watch_items', labelKey: 'digest.watchItems', icon: '\uD83D\uDC41\uFE0F' },
];

/** Digest section keys shown in compact mode (synoptic overview + trend only). */
const COMPACT_DIGEST_KEYS: Set<keyof WeatherDigest> = new Set(['synoptic', 'trend']);

/** Packs already thumb-rated in this page view (no read-back of prior ratings). */
const ratedDigests = new Set<string>();

function digestFeedbackHtml(flightId: string, packTimestamp: string): string {
  if (ratedDigests.has(`${flightId}|${packTimestamp}`)) {
    return `<div class="digest-feedback"><span class="digest-feedback-thanks">${t('feedback.thumbs.thanks')}</span></div>`;
  }
  return `
    <div class="digest-feedback" id="digest-feedback">
      <span class="digest-feedback-label">${t('feedback.thumbs.question')}</span>
      <button type="button" class="digest-thumb-btn" id="digest-thumb-up" aria-label="${t('feedback.thumbs.up')}" title="${t('feedback.thumbs.up')}">👍</button>
      <button type="button" class="digest-thumb-btn" id="digest-thumb-down" aria-label="${t('feedback.thumbs.down')}" title="${t('feedback.thumbs.down')}">👎</button>
      <div class="digest-feedback-form" id="digest-feedback-form" style="display:none;">
        <p class="digest-feedback-helper"><strong>${t('feedback.thumbs.helperTitle')}</strong><br>${t('feedback.thumbs.helperBody')}</p>
        <textarea id="digest-feedback-comment" rows="3" maxlength="2000" placeholder="${t('feedback.thumbs.placeholder')}"></textarea>
        <label class="digest-feedback-consent">
          <input type="checkbox" id="digest-feedback-contact-ok" checked> ${t('feedback.contactOk')}
        </label>
        <div class="digest-feedback-actions">
          <button type="button" class="btn" id="digest-feedback-cancel">${t('feedback.cancel')}</button>
          <button type="button" class="btn btn-primary" id="digest-feedback-send">${t('feedback.thumbs.send')}</button>
        </div>
      </div>
      <div class="digest-feedback-error" id="digest-feedback-error"></div>
    </div>`;
}

function attachDigestFeedback(el: HTMLElement, flightId: string, packTimestamp: string): void {
  const maybeWidget = el.querySelector<HTMLElement>('#digest-feedback');
  if (!maybeWidget) return;
  const widget = maybeWidget;

  const upBtn = widget.querySelector<HTMLButtonElement>('#digest-thumb-up')!;
  const downBtn = widget.querySelector<HTMLButtonElement>('#digest-thumb-down')!;
  const form = widget.querySelector<HTMLElement>('#digest-feedback-form')!;
  const errorEl = widget.querySelector<HTMLElement>('#digest-feedback-error')!;

  async function post(sentiment: 'up' | 'down', comment: string, contactOk: boolean): Promise<void> {
    errorEl.textContent = '';
    upBtn.disabled = true;
    downBtn.disabled = true;
    try {
      await api.submitFeedback({
        flight_id: flightId,
        pack_timestamp: packTimestamp,
        category: 'digest_rating',
        comment,
        sentiment,
        target: 'digest',
        contact_ok: contactOk,
      });
      ratedDigests.add(`${flightId}|${packTimestamp}`);
      widget.innerHTML = `<span class="digest-feedback-thanks">${t('feedback.thumbs.thanks')}</span>`;
    } catch (err) {
      errorEl.textContent = t('feedback.failedSubmit', { error: String(err) });
      upBtn.disabled = false;
      downBtn.disabled = false;
    }
  }

  upBtn.addEventListener('click', () => {
    void post('up', '', false);
  });

  downBtn.addEventListener('click', () => {
    downBtn.classList.add('active');
    upBtn.classList.remove('active');
    form.style.display = '';
    widget.querySelector<HTMLTextAreaElement>('#digest-feedback-comment')?.focus();
  });

  widget.querySelector<HTMLButtonElement>('#digest-feedback-cancel')!.addEventListener('click', () => {
    form.style.display = 'none';
    downBtn.classList.remove('active');
    errorEl.textContent = '';
  });

  widget.querySelector<HTMLButtonElement>('#digest-feedback-send')!.addEventListener('click', () => {
    const comment = widget.querySelector<HTMLTextAreaElement>('#digest-feedback-comment')!.value.trim();
    const contactOk = widget.querySelector<HTMLInputElement>('#digest-feedback-contact-ok')!.checked;
    void post('down', comment, contactOk);
  });
}

function renderDigestHtml(digest: WeatherDigest, displayMode: DisplayMode): string {
  const sections = displayMode === 'compact'
    ? DIGEST_SECTIONS.filter(s => COMPACT_DIGEST_KEYS.has(s.key))
    : DIGEST_SECTIONS;
  return sections.map(({ key, labelKey, icon }) => {
    const text = digest[key];
    if (!text) return '';
    return `
      <div class="digest-section">
        <h4>${icon} ${t(labelKey)}</h4>
        <p>${escapeHtml(text as string)}</p>
      </div>
    `;
  }).join('');
}

export function renderSynopsis(
  flight: FlightResponse | null,
  pack: PackMeta | null,
  digest: WeatherDigest | null,
  displayMode: DisplayMode = 'full',
  digestPending: boolean = false,
): void {
  const el = $('synopsis-section');
  if (!el) return;

  if (!flight || !pack) {
    el.innerHTML = `<p class="muted">${t('digest.noBriefing')}</p>`;
    return;
  }

  if (digest) {
    const warning = buildDigestProfileWarning(digest, flight.profile_id);
    const staleClass = warning ? ' digest-stale' : '';
    el.innerHTML = `${warning}<div class="${staleClass}">${renderDigestHtml(digest, displayMode)}</div>${digestFeedbackHtml(flight.id, pack.fetch_timestamp)}`;
    attachDigestFeedback(el, flight.id, pack.fetch_timestamp);
    return;
  }

  if (pack.has_digest) {
    el.innerHTML = `<p class="muted">${t('digest.loading')}</p>`;
    fetchAndRenderDigestJson(flight.id, pack.fetch_timestamp, el, displayMode, flight.profile_id);
    return;
  }

  // `digestPending` is set while the SSE refresh stream is between its
  // briefing_ready and complete events — or while an on-demand generation is
  // running. The visible briefing is up; the digest is being generated.
  if (digestPending) {
    el.innerHTML = `<p class="muted">${t('digest.generating')}</p>`;
    return;
  }

  // AI summary was intentionally off for this pack's profile (e.g. a "quick
  // refresh" profile). The actionable Generate button lives in the assessment
  // banner at the top; here we just explain the empty section.
  if (pack.llm_digest_requested === false) {
    el.innerHTML = `<p class="muted">${t('digest.disabled')}</p>`;
    return;
  }

  el.innerHTML = `<p class="muted">${t('digest.notAvailable')}</p>`;
}

/** Build a warning banner if the digest was generated with a different profile. */
function buildDigestProfileWarning(
  digest: WeatherDigest,
  currentProfileId: number | null | undefined,
): string {
  // No profile tracking in this digest (legacy) — no warning
  if (digest.profile_id === undefined || digest.profile_id === null) return '';
  // Profile matches — no warning
  if (digest.profile_id === currentProfileId) return '';
  // Profile mismatch
  const digestProfileName = digest.profile_name || `#${digest.profile_id}`;
  return `<div class="digest-profile-warning">${t('digest.profileMismatch', { name: escapeHtml(digestProfileName) })}</div>`;
}

async function fetchAndRenderDigestJson(
  flightId: string, timestamp: string, el: HTMLElement, displayMode: DisplayMode,
  currentProfileId?: number | null,
): Promise<void> {
  try {
    const url = api.digestJsonUrl(flightId, timestamp);
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`${resp.status}`);
    const digest: WeatherDigest = await resp.json();
    const warning = buildDigestProfileWarning(digest, currentProfileId ?? null);
    const staleClass = warning ? ' digest-stale' : '';
    el.innerHTML = `${warning}<div class="${staleClass}">${renderDigestHtml(digest, displayMode)}</div>${digestFeedbackHtml(flightId, timestamp)}`;
    attachDigestFeedback(el, flightId, timestamp);
  } catch {
    el.innerHTML = `<p class="muted">${t('digest.failed')}</p>`;
  }
}

// --- Surface Pressure & Fronts (DWD + Met Office) ---

const DWD_CHART_TABS: ReadonlyArray<{ id: string; label: string; offsetH: number }> = [
  { id: 'ana', label: 'Analysis', offsetH: 0 },
  { id: '036', label: '+36h', offsetH: 36 },
  { id: '048', label: '+48h', offsetH: 48 },
  { id: '060', label: '+60h', offsetH: 60 },
  { id: '084', label: '+84h', offsetH: 84 },
  { id: '108', label: '+108h', offsetH: 108 },
];

const METOFFICE_CHART_TABS: ReadonlyArray<{ id: string; label: string; offsetH: number }> = [
  { id: 'ana', label: 'Analysis', offsetH: 0 },
  { id: '012', label: '+12h', offsetH: 12 },
  { id: '024', label: '+24h', offsetH: 24 },
  { id: '036', label: '+36h', offsetH: 36 },
  { id: '048', label: '+48h', offsetH: 48 },
  { id: '060', label: '+60h', offsetH: 60 },
  { id: '072', label: '+72h', offsetH: 72 },
  { id: '096', label: '+96h', offsetH: 96 },
  { id: '120', label: '+120h', offsetH: 120 },
];

const DWD_PAGE_URL = 'https://www.dwd.de/DE/leistungen/hobbymet_wk_europa/hobbyeuropakarten.html';
const METOFFICE_PAGE_URL = 'https://weather.metoffice.gov.uk/maps-and-charts/surface-pressure';

/** Parse "2026-05-08T06Z" into a Date. Returns null on failure. */
function parseRunCycle(runCycle: string): Date | null {
  // "2026-05-08T06Z" → "2026-05-08T06:00:00Z"
  const m = runCycle.match(/^(\d{4}-\d{2}-\d{2})T(\d{2})Z$/);
  if (!m) return null;
  const d = new Date(`${m[1]}T${m[2]}:00:00Z`);
  return Number.isNaN(d.getTime()) ? null : d;
}

function formatHoursAgo(ms: number): string {
  const hours = Math.max(0, Math.round(ms / 3_600_000));
  if (hours < 1) return 'just now';
  if (hours === 1) return '1 hour ago';
  if (hours < 48) return `${hours} hours ago`;
  return `${Math.floor(hours / 24)} days ago`;
}

function formatUtcCaption(d: Date): string {
  const yyyy = d.getUTCFullYear();
  const mm = String(d.getUTCMonth() + 1).padStart(2, '0');
  const dd = String(d.getUTCDate()).padStart(2, '0');
  const hh = String(d.getUTCHours()).padStart(2, '0');
  return `${yyyy}-${mm}-${dd} ${hh}Z`;
}

interface DwdChartWaypoint {
  icao: string;
  lat: number;
  lon: number;
  x: number;
  y: number;
}

// Overlay JSON keyed by chart-type: DWD uses {analysis, icon}, Met Office
// uses {colour}. Source descriptors map a chart id to its key.
type ChartOverlay = Record<string, { native_size: [number, number]; waypoints: DwdChartWaypoint[] }>;

function renderRouteSvg(
  overlay: ChartOverlay,
  overlayKey: string,
): string {
  const data = overlay[overlayKey];
  if (!data || !data.waypoints.length) return '';
  const [w, h] = data.native_size;
  const wps = data.waypoints;
  const polyline = wps.map((p) => `${p.x},${p.y}`).join(' ');
  // Sizes scale with native chart resolution so they look the same on
  // the analysis (4389px wide) and icon (800px wide) charts.
  const dotR = w / 250;
  const labelFont = w / 35;
  const labelDistance = w / 60; // dot-center → label-anchor (≥ 5 × dotR)
  const haloW = w / 800;

  const dots = wps
    .map(
      (p) =>
        `<circle cx="${p.x}" cy="${p.y}" r="${dotR}" fill="#ff00ff" stroke="white" stroke-width="${haloW}" />`,
    )
    .join('');

  // Smart label placement: push each endpoint label OUTSIDE the route's
  // bounding box, in the direction from bbox-center → endpoint. This puts
  // the label past the endpoint in genuinely empty space — no overlap
  // with any segment of the route, regardless of how the route curves.
  const minX = Math.min(...wps.map((p) => p.x));
  const maxX = Math.max(...wps.map((p) => p.x));
  const minY = Math.min(...wps.map((p) => p.y));
  const maxY = Math.max(...wps.map((p) => p.y));
  const cx = (minX + maxX) / 2;
  const cy = (minY + maxY) / 2;

  const endpoints =
    wps.length >= 2 ? [wps[0], wps[wps.length - 1]] : wps.length === 1 ? [wps[0]] : [];

  const labels = endpoints
    .map((wp) => {
      // Fallback: if endpoint is exactly at the bbox center (degenerate
      // single-point or all-coincident routes), just put the label to
      // the right of the dot.
      let dx = wp.x - cx;
      let dy = wp.y - cy;
      const mag = Math.hypot(dx, dy);
      if (mag === 0) {
        dx = 1;
        dy = 0;
      }
      const ux = mag > 0 ? dx / mag : 1;
      const uy = mag > 0 ? dy / mag : 0;
      const lx = wp.x + ux * labelDistance;
      const ly = wp.y + uy * labelDistance;
      // Sign-based anchor/baseline — text always extends AWAY from the
      // dot, so it can't drift back over the endpoint.
      const anchor = ux > 0 ? 'start' : ux < 0 ? 'end' : 'middle';
      const baseline = uy > 0 ? 'hanging' : uy < 0 ? 'alphabetic' : 'middle';
      return `<text x="${lx}" y="${ly}" font-size="${labelFont}" font-family="sans-serif" font-weight="bold" text-anchor="${anchor}" dominant-baseline="${baseline}" fill="white" stroke="black" stroke-width="${haloW * 2}" paint-order="stroke" style="white-space:pre;">${escapeHtml(wp.icao)}</text>`;
    })
    .join('');

  // vector-effect keeps the polyline 2px wide regardless of viewBox
  // scaling, so it stays crisp at any zoom level.
  return `
    <svg class="dwd-route-overlay" viewBox="0 0 ${w} ${h}" preserveAspectRatio="none"
         style="position:absolute; top:0; left:0; width:100%; height:100%; pointer-events:none;">
      <polyline points="${polyline}" stroke="#ff00ff" stroke-width="2.5" fill="none"
                vector-effect="non-scaling-stroke" stroke-linejoin="round" stroke-linecap="round" />
      ${dots}
      ${labels}
    </svg>
  `;
}

interface ChartSource {
  key: 'dwd' | 'metoffice';
  label: string;
  tabs: ReadonlyArray<{ id: string; label: string; offsetH: number }>;
  runCycle: string;
  defaultId: string;
  chartUrl: (chartId: string) => string;
  overlayUrl: () => string;
  overlayKeyFor: (chartId: string) => string;
  attributionHtml: string;
}

function makeDwdSource(flight: FlightResponse, pack: PackMeta): ChartSource {
  return {
    key: 'dwd',
    label: 'DWD',
    tabs: DWD_CHART_TABS,
    runCycle: pack.dwd_charts_run_cycle as string,
    defaultId: pack.dwd_charts_default_id || 'ana',
    chartUrl: (id) => api.dwdChartUrl(flight.id, pack.fetch_timestamp, id),
    overlayUrl: () => api.dwdChartOverlayUrl(flight.id, pack.fetch_timestamp),
    overlayKeyFor: (id) => (id === 'ana' ? 'analysis' : 'icon'),
    attributionHtml: `Source: <a href="${DWD_PAGE_URL}" target="_blank" rel="noopener">Deutscher Wetterdienst (DWD)</a>, CC BY 4.0`,
  };
}

function makeMetofficeSource(flight: FlightResponse, pack: PackMeta): ChartSource {
  return {
    key: 'metoffice',
    label: 'Met Office',
    tabs: METOFFICE_CHART_TABS,
    runCycle: pack.metoffice_charts_run_cycle as string,
    defaultId: pack.metoffice_charts_default_id || 'ana',
    chartUrl: (id) => api.metofficeChartUrl(flight.id, pack.fetch_timestamp, id),
    overlayUrl: () => api.metofficeChartOverlayUrl(flight.id, pack.fetch_timestamp),
    overlayKeyFor: () => 'colour',
    attributionHtml: `Source: <a href="${METOFFICE_PAGE_URL}" target="_blank" rel="noopener">Met Office</a> · &copy; Crown copyright`,
  };
}

/**
 * Surface Pressure & Fronts panel. Hosts the DWD chart source and, when
 * available, the Met Office source behind a `[DWD | Met Office]` toggle.
 *
 * `metofficeAvailable` is `user.is_admin || pack.metoffice_charts_public`
 * — the Met Office source is admin-gated until Met Office authorise reuse.
 */
export function renderDwdCharts(
  flight: FlightResponse | null,
  pack: PackMeta | null,
  metofficeAvailable = false,
): void {
  const wrapper = $('dwd-charts-wrapper');
  const el = $('dwd-charts-section');
  if (!wrapper || !el) return;

  if (!flight || !pack) {
    wrapper.style.display = 'none';
    return;
  }

  const sources: ChartSource[] = [];
  if (pack.dwd_charts_in_coverage && pack.dwd_charts_within_horizon && pack.dwd_charts_run_cycle) {
    sources.push(makeDwdSource(flight, pack));
  }
  if (
    metofficeAvailable &&
    pack.metoffice_charts_in_coverage &&
    pack.metoffice_charts_within_horizon &&
    pack.metoffice_charts_run_cycle
  ) {
    sources.push(makeMetofficeSource(flight, pack));
  }

  const inCoverage = pack.dwd_charts_in_coverage || (metofficeAvailable && pack.metoffice_charts_in_coverage);
  if (sources.length === 0) {
    if (!inCoverage) {
      // Non-Europe route — section silently hidden (matches prior behaviour).
      wrapper.style.display = 'none';
      return;
    }
    wrapper.style.display = '';
    el.innerHTML = `<p class="muted">Surface charts unavailable for this briefing — flight is beyond the forecast horizon, or the chart refresh failed.</p>`;
    return;
  }

  wrapper.style.display = '';
  mountChartPanel(el, sources);
}

function mountChartPanel(el: HTMLElement, sources: ChartSource[]): void {
  let activeKey = sources[0].key;
  let routeVisible = true; // persists across source switches

  const render = (): void => {
    const src = sources.find((s) => s.key === activeKey) as ChartSource;
    const issuedDate = parseRunCycle(src.runCycle);

    const sourceToggleHtml =
      sources.length > 1
        ? `<div class="chart-source-toggle" role="tablist" style="display:flex; gap:6px; margin-bottom:8px;">
             ${sources
               .map(
                 (s) =>
                   `<button type="button" class="btn-toggle${s.key === activeKey ? ' active' : ''}" data-source="${s.key}">${escapeHtml(s.label)}</button>`,
               )
               .join('')}
           </div>`
        : '';

    const tabsHtml = src.tabs
      .map((tab) => {
        const cls = tab.id === src.defaultId ? 'btn-toggle active' : 'btn-toggle';
        return `<button type="button" class="${cls}" data-chart-id="${tab.id}">${escapeHtml(tab.label)}</button>`;
      })
      .join('');

    el.innerHTML = `
      ${sourceToggleHtml}
      <div class="dwd-charts-tabs" role="tablist" style="display:flex; flex-wrap:wrap; gap:6px; margin-bottom:8px; align-items:center;">
        ${tabsHtml}
        <label style="margin-left:auto; display:inline-flex; align-items:center; gap:6px; cursor:pointer; user-select:none;" title="Toggle route overlay">
          <input type="checkbox" id="dwd-charts-route-toggle" ${routeVisible ? 'checked' : ''} style="cursor:pointer;">
          <span>Show route</span>
        </label>
      </div>
      <div id="dwd-charts-image-wrap" style="text-align:center;">
        <div id="dwd-charts-image-container" style="position:relative; display:inline-block; max-width:100%;">
          <img id="dwd-charts-image" alt="${escapeHtml(src.label)} chart" style="display:block; max-width:100%; height:auto;">
          <div id="dwd-charts-overlay-slot" style="position:absolute; top:0; left:0; width:100%; height:100%; pointer-events:none;"></div>
        </div>
        <p id="dwd-charts-caption" class="header-meta" style="margin-top:6px;"></p>
      </div>
    `;

    let currentChartId = src.defaultId;
    let overlay: ChartOverlay | null = null;

    const updateOverlay = (): void => {
      const slot = document.getElementById('dwd-charts-overlay-slot');
      if (!slot) return;
      if (!overlay || !routeVisible) {
        slot.innerHTML = '';
        return;
      }
      slot.innerHTML = renderRouteSvg(overlay, src.overlayKeyFor(currentChartId));
    };

    // Fetch overlay once per source render. Failures degrade silently.
    fetch(src.overlayUrl())
      .then((r) => (r.ok ? (r.json() as Promise<ChartOverlay>) : null))
      .then((data) => {
        overlay = data;
        updateOverlay();
      })
      .catch(() => {
        overlay = null;
      });

    const showChart = (chartId: string): void => {
      currentChartId = chartId;
      const img = document.getElementById('dwd-charts-image') as HTMLImageElement | null;
      const caption = document.getElementById('dwd-charts-caption');
      if (!img || !caption) return;

      img.src = src.chartUrl(chartId);
      img.alt = `${src.label} chart ${chartId}`;
      img.onerror = () => {
        img.style.display = 'none';
        caption.innerHTML = `<span class="muted">Chart no longer available — the cached file has been evicted.</span>`;
        const slot = document.getElementById('dwd-charts-overlay-slot');
        if (slot) slot.innerHTML = '';
      };
      img.onload = () => {
        img.style.display = '';
        updateOverlay();
      };

      let issuedCaption = '';
      if (issuedDate) {
        const ago = formatHoursAgo(Date.now() - issuedDate.getTime());
        issuedCaption = `Issued ${ago} (${escapeHtml(formatUtcCaption(issuedDate))}) · `;
      }
      caption.innerHTML = `${issuedCaption}${src.attributionHtml}`;

      el.querySelectorAll('.dwd-charts-tabs .btn-toggle[data-chart-id]').forEach((btn) => {
        const b = btn as HTMLButtonElement;
        b.classList.toggle('active', b.dataset.chartId === chartId);
      });
    };

    el.querySelectorAll('.dwd-charts-tabs .btn-toggle[data-chart-id]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const id = (btn as HTMLButtonElement).dataset.chartId;
        if (id) showChart(id);
      });
    });

    el.querySelectorAll('.chart-source-toggle .btn-toggle[data-source]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const key = (btn as HTMLButtonElement).dataset.source as ChartSource['key'] | undefined;
        if (key && key !== activeKey) {
          activeKey = key;
          render();
        }
      });
    });

    const routeCheckbox = document.getElementById('dwd-charts-route-toggle') as HTMLInputElement | null;
    routeCheckbox?.addEventListener('change', () => {
      routeVisible = routeCheckbox.checked;
      updateOverlay();
    });

    showChart(src.defaultId);
  };

  render();
}

// --- DWD Synoptic Overview ---

export function renderDWDOverview(
  flight: FlightResponse | null,
  pack: PackMeta | null,
  isAdmin: boolean,
): void {
  const wrapper = $('dwd-overview-wrapper');
  const el = $('dwd-overview-section');
  if (!el || !wrapper) return;

  if (!isAdmin || !flight || !pack) {
    wrapper.style.display = 'none';
    return;
  }

  // Try to load the DWD overview (may not exist for non-EU routes or old packs)
  fetchAndRenderDWDOverview(flight.id, pack.fetch_timestamp, el, wrapper);
}

interface DWDOverviewEntry {
  day_name_de: string;
  date_iso: string | null;
  source: string;
  text_en: string;
  text_de: string;
}

interface DWDOverview {
  source: string;
  coverage: string;
  entries: DWDOverviewEntry[];
}

async function fetchAndRenderDWDOverview(
  flightId: string, timestamp: string, el: HTMLElement, wrapper: HTMLElement,
): Promise<void> {
  try {
    const url = api.dwdOverviewUrl(flightId, timestamp);
    const resp = await fetch(url);
    if (!resp.ok) {
      wrapper.style.display = 'none';
      return;
    }
    const overview: DWDOverview = await resp.json();
    if (!overview.entries || overview.entries.length === 0) {
      wrapper.style.display = 'none';
      return;
    }

    wrapper.style.display = '';
    const entriesHtml = overview.entries.map(entry => {
      const sourceTag = entry.source === 'kurzfrist' ? 'short-range' : 'medium-range';
      const dateLabel = entry.date_iso || '?';
      return `
        <div class="dwd-entry">
          <h5>${escapeHtml(entry.day_name_de)} (${escapeHtml(dateLabel)}) — ${sourceTag}</h5>
          <p>${escapeHtml(entry.text_en)}</p>
          <details class="dwd-original">
            <summary>${t('dwd.originalGerman')}</summary>
            <p class="muted">${escapeHtml(entry.text_de)}</p>
          </details>
        </div>
      `;
    }).join('');

    el.innerHTML = `
      <h4>🌍 ${t('dwd.header')}${escapeHtml(overview.coverage)}</h4>
      ${entriesHtml}
    `;
  } catch {
    wrapper.style.display = 'none';
  }
}

// --- GRAMET ---

export function renderGramet(
  flight: FlightResponse | null,
  pack: PackMeta | null,
): void {
  const el = $('gramet-section');
  if (!el) return;

  if (!flight || !pack || !pack.has_gramet) {
    el.innerHTML = '<p class="muted">Loading...</p>';
    import('../adapters/preferences-adapter').then(({ fetchPreferences }) =>
      fetchPreferences()
        .then((prefs) => {
          if (!prefs.has_autorouter_creds) {
            const key = prefs.autorouter_mode === 'password' ? 'gramet.noCredsDev' : 'gramet.noCreds';
            el.innerHTML =
              `<p class="muted">${t(key)}</p>`;
          } else {
            el.innerHTML = `<p class="muted">${t('gramet.notAvailable')}</p>`;
          }
        })
        .catch(() => {
          el.innerHTML = `<p class="muted">${t('gramet.notAvailable')}</p>`;
        }),
    );
    return;
  }

  const pngUrl = api.grametPngUrl(flight.id, pack.fetch_timestamp);
  const pdfUrl = api.grametUrl(flight.id, pack.fetch_timestamp);
  el.innerHTML = `
    <img src="${pngUrl}" alt="${t('gramet.altText')}" class="gramet-img" />
    <div class="gramet-actions">
      <a href="${pdfUrl}" download class="btn btn-sm">${t('gramet.downloadPdf')}</a>
      <a href="https://www.autorouter.aero" target="_blank" class="btn btn-sm btn-outline">autorouter.aero</a>
    </div>
  `;
}

// --- Model Comparison ---

export function renderModelComparison(
  snapshot: ForecastSnapshot | null,
  routeAnalyses?: RouteAnalysesManifest | null,
  selectedPointIndex?: number | null,
  displayMode: DisplayMode = 'full',
  tierVisibility: Record<Tier, boolean> = { key: true, useful: true, advanced: false },
): void {
  const el = $('comparison-section');
  if (!el) return;

  // Route-point mode: single point comparison
  if (routeAnalyses && routeAnalyses.analyses.length > 0) {
    const idx = selectedPointIndex ?? 0;
    const point = routeAnalyses.analyses[idx];
    if (point && point.model_divergence.length > 0) {
      const label = point.waypoint_icao
        ? `${point.waypoint_icao} \u2014 ${point.waypoint_name || ''}`
        : `Point ${point.point_index} (${point.distance_from_origin_nm.toFixed(0)} nm)`;
      el.innerHTML = renderComparisonTable(label, point.model_divergence, displayMode, tierVisibility);
      return;
    }
    el.innerHTML = `<p class="muted">${t('comparison.noDataPoint')}</p>`;
    return;
  }

  // Fallback: stacked waypoint view
  if (!snapshot || snapshot.analyses.length === 0) {
    el.innerHTML = `<p class="muted">${t('comparison.noDataAvailable')}</p>`;
    return;
  }

  el.innerHTML = snapshot.analyses.map((a) => {
    if (a.model_divergence.length === 0) return '';
    return renderComparisonTable(
      `${a.waypoint.icao} \u2014 ${a.waypoint.name}`,
      a.model_divergence,
      displayMode,
      tierVisibility,
    );
  }).join('');
}

function renderComparisonTable(
  label: string,
  divergences: Array<{ variable: string; model_values: Record<string, number>; mean: number; spread: number; agreement: string }>,
  displayMode: DisplayMode = 'full',
  tierVisibility: Record<Tier, boolean> = { key: true, useful: true, advanced: false },
): string {
  const models = Object.keys(divergences[0]?.model_values || {});
  const headerCells = models.map((m) => `<th>${modelLabel(m)}</th>`).join('');
  const colSpan = models.length + 3; // var-name + models + spread + agree

  const rows = divergences.map((d) => {
    const metricId = variableToMetricId(d.variable);

    // Apply tier filtering
    if (metricId && !isMetricVisible('comparison', metricId, tierVisibility)) return '';

    const metric = metricId ? getMetric(metricId) : null;
    const varLabel = metric?.name ?? formatVarName(d.variable);

    const valueCells = models.map((m) => {
      const val = d.model_values[m];
      return `<td>${val !== undefined ? val.toFixed(1) : '\u2014'}</td>`;
    }).join('');
    const agreeIcon = d.agreement === 'good' ? '&#10003;'
      : d.agreement === 'moderate' ? '&#9888;' : '&#10007;';
    const agreeClass = `agree-${d.agreement}`;

    const infoBtn = metricId && displayMode === 'full'
      ? ` ${renderInfoButton(metricId, d.mean)}`
      : '';

    const annotation = metricId
      ? renderAnnotationRow(metricId, d.mean, displayMode, colSpan)
      : '';

    return `
      <tr>
        <td class="var-name">${varLabel}${infoBtn}</td>
        ${valueCells}
        <td>${d.spread.toFixed(1)}</td>
        <td class="${agreeClass}">${agreeIcon}</td>
      </tr>
      ${annotation}
    `;
  }).join('');

  const tierBtn = renderTierToggle('comparison', tierVisibility);

  return `
    <div class="comparison-waypoint">
      <h4>${escapeHtml(label)}</h4>
      <table class="comparison-table">
        <thead>
          <tr>
            <th>${t('comparison.variable')}</th>
            ${headerCells}
            <th>${t('comparison.spread')}</th>
            <th>${t('comparison.agree')}</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
      ${tierBtn}
    </div>
  `;
}

function formatVarName(name: string): string {
  const labelKeys: Record<string, string> = {
    'temperature_c': 'comparison.temp',
    'wind_speed_kt': 'comparison.windSpeed',
    'wind_direction_deg': 'comparison.windDir',
    'cloud_cover_pct': 'comparison.cloud',
    'precipitation_mm': 'comparison.precip',
    'freezing_level_m': 'comparison.freezingM',
    'freezing_level_ft': 'comparison.freezingFt',
    'cape_surface_jkg': 'comparison.cape',
    'lcl_altitude_ft': 'comparison.lcl',
    'k_index': 'comparison.kIndex',
    'total_totals': 'comparison.totalTotals',
    'precipitable_water_mm': 'comparison.pw',
    'lifted_index': 'comparison.liftedIndex',
    'bulk_shear_0_6km_kt': 'comparison.shear',
    'max_omega_pa_s': 'comparison.maxOmega',
  };
  const key = labelKeys[name];
  return key ? t(key) : name;
}

// --- Sounding Analysis ---

const RISK_COLORS: Record<string, string> = {
  none: '',
  marginal: 'risk-marginal',
  weak: 'risk-light',
  light: 'risk-light',
  low: 'risk-light',
  moderate: 'risk-moderate',
  strong: 'risk-high',
  high: 'risk-high',
  severe: 'risk-severe',
  extreme: 'risk-severe',
};

function riskClass(risk: string): string {
  return RISK_COLORS[risk] || '';
}

function roundAlt(ft: number): number {
  return Math.round(ft / 500) * 500;
}

export function renderSoundingAnalysis(
  snapshot: ForecastSnapshot | null,
  routeAnalyses?: RouteAnalysesManifest | null,
  selectedPointIndex?: number | null,
  displayMode: DisplayMode = 'full',
  tierVisibility: Record<Tier, boolean> = { key: true, useful: true, advanced: false },
  enabledLayers?: Record<string, boolean>,
  effectiveCruiseAltitudeFt?: number | null,
): void {
  const el = $('sounding-section');
  if (!el) return;

  // Route-point mode: show single selected point
  if (routeAnalyses && routeAnalyses.analyses.length > 0) {
    const idx = selectedPointIndex ?? 0;
    const point = routeAnalyses.analyses[idx];
    if (point) {
      el.innerHTML = renderSinglePointSounding(point, displayMode, tierVisibility, enabledLayers, effectiveCruiseAltitudeFt);
      return;
    }
  }

  // Fallback: stacked waypoint view
  if (!snapshot || snapshot.analyses.length === 0) {
    el.innerHTML = `<p class="muted">${t('sounding.noAnalysis')}</p>`;
    return;
  }

  const hasSounding = snapshot.analyses.some(
    (a) => a.sounding && Object.keys(a.sounding).length > 0,
  );
  if (!hasSounding) {
    el.innerHTML = `<p class="muted">${t('sounding.notAvailable')}</p>`;
    return;
  }

  el.innerHTML = snapshot.analyses.map((a) => {
    if (!a.sounding || Object.keys(a.sounding).length === 0) return '';
    return `
      <div class="sounding-waypoint">
        <h4>${a.waypoint.icao} \u2014 ${a.waypoint.name}</h4>
        ${renderConvectiveBanner(a.sounding, displayMode, tierVisibility)}
        ${renderVerticalMotion(a.sounding, displayMode)}
        ${renderAltitudeMarkers(a.sounding, displayMode, tierVisibility)}
        ${renderAtmosphericProfile(a.sounding, a.altitude_advisories, undefined, effectiveCruiseAltitudeFt)}
        ${renderAdvisoriesTable(a.altitude_advisories)}
      </div>
    `;
  }).join('');
}

function renderConvectiveBanner(
  soundings: Record<string, SoundingAnalysis>,
  displayMode: DisplayMode = 'full',
  tierVisibility: Record<Tier, boolean> = { key: true, useful: true, advanced: false },
): string {
  const models = Object.keys(soundings);
  const hasConvective = models.some(
    (m) => soundings[m].convective && soundings[m].convective!.risk_level !== 'none',
  );
  if (!hasConvective) return '';

  const headerCells = models.map((m) => `<th>${modelLabel(m)}</th>`).join('');
  const colSpan = models.length + 1;
  const config = getDisplayConfig().sections.convective;

  // Build row specs from display config
  const rows = config.metrics.map((mc) => {
    if (!isMetricVisible('convective', mc.id, tierVisibility)) return '';

    const metric = getMetric(mc.id);
    const label = metric?.name ?? mc.id;

    // Special case: Risk row
    if (mc.id === 'convective_risk') {
      const cells = models.map((m) => {
        const c = soundings[m].convective;
        if (!c || c.risk_level === 'none') return '<td>\u2014</td>';
        return `<td class="${riskClass(c.risk_level)}">${c.risk_level.toUpperCase()}</td>`;
      }).join('');
      return `<tr><td class="var-name">${label}</td>${cells}</tr>`;
    }

    // Get the first non-null value for annotation
    let firstValue: number | null = null;
    const cells = models.map((m) => {
      const v = getSoundingField(soundings[m], mc.field!, mc.source!);
      if (v != null && firstValue === null) firstValue = v;
      if (v == null) return '<td>\u2014</td>';
      return `<td>${formatMetricValue(mc.id, v)}</td>`;
    }).join('');

    const annotation = renderAnnotationRow(mc.id, firstValue, displayMode, colSpan);
    return `<tr><td class="var-name">${label}${metric?.unit ? ' (' + metric.unit + ')' : ''}</td>${cells}</tr>${annotation}`;
  }).join('');

  // Modifiers summary (outside table to avoid forcing columns wide)
  const allMods = new Set<string>();
  for (const m of models) {
    for (const mod of soundings[m].convective?.severe_modifiers ?? []) {
      allMods.add(mod);
    }
  }
  const modsSummary = allMods.size > 0
    ? `<p class="convective-modifiers"><strong>${t('sounding.severeModifiers')}</strong> ${escapeHtml([...allMods].join(', '))}</p>`
    : '';

  // Tier toggle button
  const tierBtn = renderTierToggle('convective', tierVisibility);

  return `
    <div class="convective-section">
      <h5>${t('sounding.convective')}</h5>
      <table class="band-table">
        <thead><tr><th></th>${headerCells}</tr></thead>
        <tbody>${rows}</tbody>
      </table>
      ${modsSummary}
      ${tierBtn}
    </div>
  `;
}

/** Extract a value from ConvectiveAssessment or ThermodynamicIndices. */
function getSoundingField(
  sounding: SoundingAnalysis,
  field: string,
  source: string,
): number | null {
  if (source === 'convective' && sounding.convective) {
    const val = sounding.convective[field as keyof typeof sounding.convective];
    return typeof val === 'number' ? val : null;
  }
  if (source === 'indices' && sounding.indices) {
    const val = sounding.indices[field as keyof typeof sounding.indices];
    return typeof val === 'number' ? val : null;
  }
  return null;
}

/** Format a metric value with appropriate precision. */
function formatMetricValue(metricId: string, value: number): string {
  // IDs that should show 1 decimal
  if (metricId === 'lifted_index' || metricId === 'showalter_index') {
    return value.toFixed(1);
  }
  // IDs that show integer with comma formatting
  if (metricId === 'cape_surface_jkg' && Math.abs(value) >= 1000) {
    return value.toLocaleString('en', { maximumFractionDigits: 0 });
  }
  return value.toFixed(0);
}

/** Render tier toggle button for a section. */
function renderTierToggle(
  sectionId: string,
  tierVisibility: Record<Tier, boolean>,
): string {
  const config = getDisplayConfig().sections[sectionId];
  if (!config) return '';

  const hasAdvanced = config.metrics.some((m) => m.tier === 'advanced');
  if (!hasAdvanced) return '';

  const label = tierVisibility.advanced ? t('sounding.hideAdvanced') : t('sounding.showAdvanced');
  return `<button class="tier-toggle-btn" data-section="${sectionId}" data-tier="advanced">${label}</button>`;
}

function formatClassification(cls: string): string {
  if (cls === 'unavailable') return t('sounding.na');
  // Look up display label from metrics catalog thresholds
  const metric = getMetric('vertical_motion_class');
  if (metric) {
    // Match enum value to threshold label (e.g., "synoptic_ascent" → "Synoptic Ascent")
    const normalized = cls.replace(/_/g, ' ');
    const match = metric.thresholds.find((t) => t.label.toLowerCase() === normalized);
    if (match) return match.label;
  }
  return cls;
}

function renderVerticalMotion(soundings: Record<string, SoundingAnalysis>, displayMode: DisplayMode = 'full'): string {
  const models = Object.keys(soundings);
  const hasVerticalMotion = models.some(
    (m) => soundings[m].vertical_motion && soundings[m].vertical_motion!.classification !== 'unavailable',
  );
  if (!hasVerticalMotion) return '';

  const headerCells = models.map((m) => `<th>${modelLabel(m)}</th>`).join('');
  const colSpan = models.length + 1;

  // Summary rows
  const rowSpecs: Array<{ label: string; metricId?: string; render: (m: string) => string }> = [
    {
      label: t('sounding.classification'),
      metricId: 'vertical_motion_class',
      render: (m) => {
        const vm = soundings[m].vertical_motion;
        if (!vm || vm.classification === 'unavailable') return `<td class="muted">${t('sounding.na')}</td>`;
        const cls = vm.classification === 'convective' ? 'risk-severe'
          : vm.classification === 'synoptic_ascent' ? 'risk-moderate'
          : '';
        return `<td class="${cls}">${formatClassification(vm.classification)}</td>`;
      },
    },
    {
      label: t('sounding.maxW'),
      render: (m) => {
        const vm = soundings[m].vertical_motion;
        if (!vm || vm.max_w_fpm == null) return '<td>\u2014</td>';
        const sign = vm.max_w_fpm > 0 ? '+' : '';
        const alt = vm.max_w_level_ft != null ? ` @ ${vm.max_w_level_ft.toFixed(0)}ft` : '';
        return `<td>${sign}${vm.max_w_fpm.toFixed(0)}${alt}</td>`;
      },
    },
  ];

  // Add contamination row only if any model flags it
  const hasContamination = models.some(
    (m) => soundings[m].vertical_motion?.convective_contamination,
  );
  if (hasContamination) {
    rowSpecs.push({
      label: t('sounding.contamination'),
      render: (m) => {
        const vm = soundings[m].vertical_motion;
        if (!vm) return '<td>\u2014</td>';
        return vm.convective_contamination
          ? `<td class="risk-moderate">${t('sounding.midLevelConvective')}</td>`
          : `<td>${t('sounding.contaminationNone')}</td>`;
      },
    });
  }

  const summaryRows = rowSpecs.map(({ label, metricId, render }) => {
    const cells = models.map(render).join('');
    const infoBtn = metricId ? ` ${renderInfoButton(metricId)}` : '';
    const row = `<tr><td class="var-name">${label}${infoBtn}</td>${cells}</tr>`;

    // Add annotation for classification row in annotated mode
    if (metricId && displayMode === 'full') {
      const metric = getMetric(metricId);
      if (metric && metric.thresholds.length > 0) {
        // For enum-style metrics (like vertical_motion_class), match formatted label to threshold
        const firstCls = models.map((m) => soundings[m].vertical_motion?.classification).find((c) => c && c !== 'unavailable');
        if (firstCls) {
          const formatted = formatClassification(firstCls);
          const match = metric.thresholds.find((t) => t.label === formatted);
          if (match?.meaning) {
            return row + `<tr class="metric-annotation-row"><td class="metric-annotation" colspan="${colSpan}">${match.meaning}</td></tr>`;
          }
        }
      }
    }
    return row;
  }).join('');

  // CAT risk layers section
  let catSection = '';
  const hasCat = models.some(
    (m) => (soundings[m].vertical_motion?.cat_risk_layers?.length ?? 0) > 0,
  );
  if (hasCat) {
    const allAlts = new Set<number>();
    for (const m of models) {
      const layers = soundings[m].vertical_motion?.cat_risk_layers || [];
      for (const l of layers) {
        allAlts.add(roundAlt(l.base_ft));
        allAlts.add(roundAlt(l.top_ft));
      }
    }
    const sortedAlts = [...allAlts].sort((a, b) => b - a);

    if (sortedAlts.length >= 2) {
      const catRows = sortedAlts.slice(0, -1).map((alt, i) => {
        const nextAlt = sortedAlts[i + 1];
        const midpoint = (alt + nextAlt) / 2;

        let anyHit = false;
        const cells = models.map((m) => {
          const layer = (soundings[m].vertical_motion?.cat_risk_layers || []).find(
            (l) => l.base_ft <= midpoint && l.top_ft >= midpoint,
          );
          if (!layer) return '<td>\u2014</td>';
          anyHit = true;
          const ri = layer.richardson_number != null ? ` Ri=${layer.richardson_number.toFixed(2)}` : '';
          return `<td class="${riskClass(layer.risk)}">${layer.risk.toUpperCase()}${ri}</td>`;
        }).join('');

        if (!anyHit) return '';
        return `<tr><td class="var-name">${nextAlt}-${alt}ft</td>${cells}</tr>`;
      }).join('');

      const catInfoBtn = renderInfoButton('cat_risk');
      catSection = `
        <h6>${t('sounding.catRiskLayers')}<span class="section-info-btn">${catInfoBtn}</span></h6>
        <table class="band-table">
          <thead><tr><th>${t('sounding.altitude')}</th>${headerCells}</tr></thead>
          <tbody>${catRows}</tbody>
        </table>
      `;
    }
  }

  const vmInfoBtn = renderInfoButton('vertical_motion_class');
  return `
    <div class="vertical-motion-section">
      <h5>Vertical Motion <span class="section-info-btn">${vmInfoBtn}</span></h5>
      <table class="band-table">
        <thead><tr><th></th>${headerCells}</tr></thead>
        <tbody>${summaryRows}</tbody>
      </table>
      ${catSection}
    </div>
  `;
}

function renderAltitudeMarkers(
  soundings: Record<string, SoundingAnalysis>,
  displayMode: DisplayMode = 'full',
  tierVisibility: Record<Tier, boolean> = { key: true, useful: true, advanced: false },
): string {
  const models = Object.keys(soundings);
  const hasIndices = models.some((m) => soundings[m].indices != null);
  if (!hasIndices) return '';

  const headerCells = models.map((m) => `<th>${modelLabel(m)}</th>`).join('');
  const colSpan = models.length + 1;
  const config = getDisplayConfig().sections.altitudes;

  const rows = config.metrics.map((mc) => {
    if (!isMetricVisible('altitudes', mc.id, tierVisibility)) return '';

    const metric = getMetric(mc.id);
    const label = metric?.name ?? mc.id;
    const field = mc.field as keyof ThermodynamicIndices;

    let firstValue: number | null = null;
    const cells = models.map((m) => {
      const v = soundings[m].indices?.[field] as number | null;
      if (v != null && firstValue === null) firstValue = v;
      if (v == null) return '<td>\u2014</td>';
      // Altitude metrics get 'ft' suffix, PW gets 'mm'
      const suffix = mc.id === 'precipitable_water_mm' ? 'mm' : 'ft';
      return `<td>${v.toFixed(0)}${suffix}</td>`;
    }).join('');

    const annotation = renderAnnotationRow(mc.id, firstValue, displayMode, colSpan);
    return `<tr><td class="var-name">${label}</td>${cells}</tr>${annotation}`;
  }).join('');

  const tierBtn = renderTierToggle('altitudes', tierVisibility);

  return `
    <div class="altitude-markers">
      <h5>${t('sounding.keyAltitudes')}</h5>
      <table class="band-table">
        <thead><tr><th></th>${headerCells}</tr></thead>
        <tbody>${rows}</tbody>
      </table>
      ${tierBtn}
    </div>
  `;
}

function inversionStrengthLabel(strengthC: number): string {
  if (strengthC >= 3) return t('sounding.inversionStrong');
  if (strengthC >= 1) return t('sounding.inversionModerate');
  return t('sounding.inversionWeak');
}

const COVERAGE_OKTAS: Record<string, string> = {
  sct: '3\u20134/8',
  bkn: '5\u20137/8',
  ovc: '8/8',
};

/** Find the SFIP zone that best overlaps a given altitude band. */
function findOverlappingSfip(sfipZones: SfipZone[], floorFt: number, ceilingFt: number): SfipZone | null {
  let best: SfipZone | null = null;
  let bestOverlap = 0;
  for (const sz of sfipZones) {
    const overlap = Math.min(sz.top_ft, ceilingFt) - Math.max(sz.base_ft, floorFt);
    if (overlap > bestOverlap) { bestOverlap = overlap; best = sz; }
  }
  return best;
}

/** Build the multi-line HTML content for a single regime cell. */
/** Map SFIP variant string to a display badge: "CLW" when actual cloud liquid water data was used, "proxy" otherwise. */
function sfipVariantBadge(variant: string): string {
  return variant.startsWith('full') || variant.startsWith('interp') ? 'CLW' : 'proxy';
}

function regimeCellContent(regime: VerticalRegime, sfipZones?: SfipZone[], enabledLayers?: Record<string, boolean>): string {
  const layerOn = (id: string) => !enabledLayers || enabledLayers[id] !== false;
  const lines: string[] = [];

  // SFIP zone lookup (used by both icing paths)
  const sfipMatch = sfipZones ? findOverlappingSfip(sfipZones, regime.floor_ft, regime.ceiling_ft) : null;

  // Cloud: headline + params subtitle
  if (regime.in_cloud && layerOn('cloud-bands')) {
    if (regime.cloud_coverage) {
      const cov = regime.cloud_coverage.toUpperCase();
      const oktas = COVERAGE_OKTAS[regime.cloud_coverage] ?? '';
      const oktasStr = oktas ? ` (${oktas})` : '';
      const infoBtn = renderInfoButton('cloud_coverage', regime.cloud_coverage);
      lines.push(`<div class="regime-cloud">${cov}${oktasStr} ${infoBtn}</div>`);
      const params: string[] = [];
      if (regime.mean_dewpoint_depression_c != null)
        params.push(`DD=${regime.mean_dewpoint_depression_c.toFixed(1)}\u00b0C`);
      if (regime.mean_temperature_c != null)
        params.push(`T=${regime.mean_temperature_c.toFixed(0)}\u00b0C`);
      if (regime.cloud_cover_pct != null && layerOn('nwp-cloud-bands'))
        params.push(`${t('sounding.nwpLabel')}${regime.cloud_cover_pct.toFixed(0)}%`);
      if (params.length > 0)
        lines.push(`<div class="regime-params">${params.join(' ')}</div>`);
    } else {
      // Old data: no coverage detail
      lines.push(`<div class="regime-cloud">${t('sounding.inCloud')}</div>`);
    }
  } else if (!layerOn('cloud-bands') && layerOn('nwp-cloud-bands') && regime.cloud_cover_pct != null && regime.cloud_cover_pct > 0) {
    // Cloud-bands OFF but NWP ON: show minimal NWP line
    lines.push(`<div class="regime-cloud">${t('sounding.nwpLabel')}${regime.cloud_cover_pct.toFixed(0)}%</div>`);
  }

  // Icing: headline + params subtitle
  if (regime.icing_risk !== 'none' && layerOn('icing-bands')) {
    const risk = regime.icing_risk.toUpperCase();
    const type = regime.icing_type !== 'none' ? ` ${regime.icing_type}` : '';
    const sld = regime.sld_risk ? ' <span class="sld-badge">SLD</span>' : '';
    const infoBtn = renderInfoButton('icing_risk', regime.icing_risk);
    lines.push(`<div class="regime-icing">${risk}${type}${sld} ${infoBtn}</div>`);
    const params: string[] = [];
    if (regime.mean_wet_bulb_c != null)
      params.push(`Tw=${regime.mean_wet_bulb_c.toFixed(0)}\u00b0C`);
    if (regime.mean_icing_index != null)
      params.push(`Ix=${regime.mean_icing_index.toFixed(0)} ${renderInfoButton('ogimet_index', regime.mean_icing_index)}`);
    if (layerOn('sfip-bands') && sfipMatch && sfipMatch.mean_sfip_100 != null) {
      const variantBadge = `<span class="sfip-variant">${sfipVariantBadge(sfipMatch.variant)}</span>`;
      params.push(`SFIP=${sfipMatch.mean_sfip_100.toFixed(0)} ${renderInfoButton('sfip_risk', sfipMatch.mean_sfip_100)} ${variantBadge}`);
    }
    if (regime.mean_rh_pct != null)
      params.push(`RH=${regime.mean_rh_pct.toFixed(0)}%`);
    if (params.length > 0)
      lines.push(`<div class="regime-params">${params.join(' ')}</div>`);
  } else if (!layerOn('icing-bands') && layerOn('sfip-bands') && sfipMatch && sfipMatch.mean_sfip_100 != null && sfipMatch.risk !== 'none') {
    // Icing-bands OFF but SFIP ON: show SFIP-only icing data
    const risk = sfipMatch.risk.toUpperCase();
    const infoBtn = renderInfoButton('sfip_risk', sfipMatch.mean_sfip_100);
    lines.push(`<div class="regime-icing">${risk} ${infoBtn}</div>`);
    const variantBadge = `<span class="sfip-variant">${sfipVariantBadge(sfipMatch.variant)}</span>`;
    lines.push(`<div class="regime-params">SFIP=${sfipMatch.mean_sfip_100.toFixed(0)} ${renderInfoButton('sfip_risk', sfipMatch.mean_sfip_100)} ${variantBadge}</div>`);
  }

  // Inversion line
  if (regime.inversion && regime.inversion_strength_c != null && layerOn('inversion-bands')) {
    const label = inversionStrengthLabel(regime.inversion_strength_c).toUpperCase();
    const sfc = regime.inversion_surface_based ? ' SFC' : '';
    lines.push(`<div class="regime-inversion">INV ${label} +${regime.inversion_strength_c.toFixed(1)}\u00b0C${sfc}</div>`);
  }

  // CAT line
  if (regime.cat_risk && layerOn('cat-bands')) {
    lines.push(`<div class="regime-cat">CAT ${regime.cat_risk.toUpperCase()}</div>`);
  }

  // Strong motion line
  if (regime.strong_vertical_motion) {
    lines.push(`<div class="regime-motion">Strong motion</div>`);
  }

  // Clear band
  if (lines.length === 0) {
    const nwp = layerOn('nwp-cloud-bands') && regime.cloud_cover_pct != null && regime.cloud_cover_pct > 0
      ? ` <span class="regime-nwp">${t('sounding.nwpLabel')}${regime.cloud_cover_pct.toFixed(0)}%</span>` : '';
    lines.push(`<span class="regime-clear">${t('sounding.clear')}${nwp}</span>`);
  }

  return lines.join('');
}

/** Cell CSS class based on worst active condition. */
function regimeCellClass(regime: VerticalRegime): string {
  if (regime.icing_risk !== 'none') return riskClass(regime.icing_risk);
  if (regime.cat_risk) return riskClass(regime.cat_risk);
  return '';
}

/** Recompute cruise_in_icing/cruise_icing_risk at *effectiveAltFt*, the
 *  current effective cruise altitude. Mirrors the server-side
 *  `_cruise_icing_status` logic: scan each model's icing_zones for a
 *  zone that contains the altitude; keep the worst risk found.
 *  Returns null when no model has the altitude in icing.
 */
function recomputeCruiseIcing(
  soundings: Record<string, SoundingAnalysis>,
  effectiveAltFt: number,
): { inIcing: boolean; risk: IcingRisk } {
  // Order must mirror server-side `_ICING_ORDER` in
  // src/weatherbrief/analysis/sounding/advisories.py.
  const order: IcingRisk[] = ['none', 'light', 'moderate', 'severe'];
  let worstIdx = 0;
  let inIcing = false;
  for (const sounding of Object.values(soundings)) {
    const zones = sounding?.icing_zones ?? [];
    for (const z of zones) {
      if (z.base_ft <= effectiveAltFt && effectiveAltFt <= z.top_ft) {
        inIcing = true;
        const idx = order.indexOf(z.risk);
        if (idx > worstIdx) worstIdx = idx;
      }
    }
  }
  return { inIcing, risk: order[worstIdx] };
}

function renderAtmosphericProfile(
  soundings: Record<string, SoundingAnalysis>,
  adv: AltitudeAdvisories | null,
  enabledLayers?: Record<string, boolean>,
  effectiveCruiseAltitudeFt?: number | null,
): string {
  if (!adv) return '';

  const parts: string[] = [];

  // Cruise icing badge — if an override altitude is active and differs
  // from the manifest's baked value, recompute the flag client-side from
  // each model's icing_zones so the banner tracks the user's probe.
  let cruiseInIcing = adv.cruise_in_icing;
  let cruiseIcingRisk = adv.cruise_icing_risk;
  if (effectiveCruiseAltitudeFt != null) {
    const recomp = recomputeCruiseIcing(soundings, effectiveCruiseAltitudeFt);
    cruiseInIcing = recomp.inIcing;
    cruiseIcingRisk = recomp.risk;
  }
  if (cruiseInIcing) {
    parts.push(
      `<div class="cruise-icing-banner ${riskClass(cruiseIcingRisk)}">` +
      `${t('sounding.cruiseInIcing')}${cruiseIcingRisk.toUpperCase()}</div>`,
    );
  }

  // Per-model vertical regimes as columns
  const models = Object.keys(adv.regimes);
  if (models.length > 0) {
    const headerCells = models.map((m) => `<th>${modelLabel(m)}</th>`).join('');

    // Collect all unique altitudes to build rows
    const allAlts = new Set<number>();
    for (const regimes of Object.values(adv.regimes)) {
      for (const r of regimes) {
        allAlts.add(r.floor_ft);
        allAlts.add(r.ceiling_ft);
      }
    }
    const sortedAlts = [...allAlts].sort((a, b) => b - a); // top-down

    // Build regime rows: for each altitude pair, show each model's regime
    const rows = sortedAlts.slice(0, -1).map((alt, i) => {
      const nextAlt = sortedAlts[i + 1];
      const midpoint = (alt + nextAlt) / 2;

      const cells = models.map((m) => {
        const regime = adv.regimes[m].find(
          (r) => r.floor_ft <= midpoint && r.ceiling_ft >= midpoint,
        );
        if (!regime) return '<td>\u2014</td>';
        const cls = regimeCellClass(regime);
        const modelSfipZones = soundings[m]?.sfip_zones ?? [];
        return `<td class="regime-cell ${cls}">${regimeCellContent(regime, modelSfipZones, enabledLayers)}</td>`;
      }).join('');

      return `<tr><td class="var-name">${nextAlt.toFixed(0)}-${alt.toFixed(0)}ft</td>${cells}</tr>`;
    }).join('');

    parts.push(`
      <div class="atmospheric-profile">
        <h5>${t('sounding.atmosphericProfile')}</h5>
        <div class="table-scroll">
          <table class="band-table">
            <thead><tr><th>${t('sounding.altitude')}</th>${headerCells}</tr></thead>
            <tbody>${rows}</tbody>
          </table>
        </div>
      </div>
    `);
  }

  return parts.join('');
}

function renderAdvisoriesTable(adv: AltitudeAdvisories | null): string {
  if (!adv || adv.advisories.length === 0) return '';

  const advModels = Object.keys(adv.regimes);
  const advHeaderCells = advModels.map((m) => `<th>${modelLabel(m)}</th>`).join('');

  const advisoryRows = adv.advisories.map((a) => {
    const isDescentToZero = a.advisory_type === 'descend_below_icing' && a.altitude_ft === 0;
    const infeasibleBadge = !a.feasible ? ' <span class="advisory-badge">INFEASIBLE</span>' : '';

    let label: string;
    if (isDescentToZero) {
      label = t('sounding.unableDescend') + infeasibleBadge;
    } else {
      label = escapeHtml(a.reason) + infeasibleBadge;
    }

    const cells = advModels.map((m) => {
      const v = a.per_model_ft[m];
      if (v == null) return '<td>\u2014</td>';
      if (a.advisory_type === 'descend_below_icing' && v === 0) {
        return `<td>${t('sounding.surface')}</td>`;
      }
      return `<td>${v.toFixed(0)}ft</td>`;
    }).join('');

    const rowCls = !a.feasible ? ' class="advisory-infeasible"' : '';
    return `<tr${rowCls}><td class="var-name">${label}</td>${cells}</tr>`;
  }).join('');

  return `
    <div class="advisories-section">
      <h5>${t('sounding.advisories')}</h5>
      <table class="band-table">
        <thead><tr><th></th>${advHeaderCells}</tr></thead>
        <tbody>${advisoryRows}</tbody>
      </table>
    </div>
  `;
}

// --- Route-point sounding (single point) ---

function renderSinglePointSounding(
  point: RoutePointAnalysis,
  displayMode: DisplayMode = 'full',
  tierVisibility: Record<Tier, boolean> = { key: true, useful: true, advanced: false },
  enabledLayers?: Record<string, boolean>,
  effectiveCruiseAltitudeFt?: number | null,
): string {
  if (!point.sounding || Object.keys(point.sounding).length === 0) {
    return `<p class="muted">${t('sounding.noDataPoint')}</p>`;
  }

  const label = point.waypoint_icao
    ? `${point.waypoint_icao} \u2014 ${point.waypoint_name || ''}`
    : `Point ${point.point_index} (${point.distance_from_origin_nm.toFixed(0)} nm)`;

  return `
    <div class="sounding-waypoint">
      <h4>${escapeHtml(label)}</h4>
      ${renderConvectiveBanner(point.sounding, displayMode, tierVisibility)}
      ${renderVerticalMotion(point.sounding, displayMode)}
      ${renderAltitudeMarkers(point.sounding, displayMode, tierVisibility)}
      ${renderAtmosphericProfile(point.sounding, point.altitude_advisories, enabledLayers, effectiveCruiseAltitudeFt)}
      ${renderAdvisoriesTable(point.altitude_advisories)}
    </div>
  `;
}

// --- Skew-T ---

/** Update the Skew-T model label to show the currently selected model. */
function updateSkewtModelLabel(selectedModel: string): void {
  const el = document.getElementById('skewt-model-name');
  if (el) el.textContent = modelLabel(selectedModel);
}

export function renderSkewTs(
  flight: FlightResponse | null,
  pack: PackMeta | null,
  snapshot: ForecastSnapshot | null,
  selectedModel: string,
  routeAnalyses?: RouteAnalysesManifest | null,
  selectedPointIndex?: number | null,
): void {
  const el = $('skewt-section');
  if (!el) return;

  if (!flight || !pack) {
    el.innerHTML = `<p class="muted">${t('skewt.notAvailable')}</p>`;
    return;
  }

  // Update model label in Skew-T section
  updateSkewtModelLabel(selectedModel);

  // Route-point mode: single Skew-T + Hodograph pair
  if (routeAnalyses && routeAnalyses.analyses.length > 0) {
    if (selectedPointIndex == null) {
      el.innerHTML = `<p class="muted">${t('skewt.selectPoint')}</p>`;
      return;
    }
    const idx = selectedPointIndex;
    const point = routeAnalyses.analyses[idx];
    if (point) {
      const label = point.waypoint_icao || `Point ${point.point_index}`;
      const skewtUrlStr = api.routeSkewtUrl(flight.id, pack.fetch_timestamp, point.point_index, selectedModel);
      const hodoUrlStr = api.routeHodographUrl(flight.id, pack.fetch_timestamp, point.point_index, selectedModel);
      el.innerHTML = `
        <div class="skewt-gallery">
          <div class="skewt-card skewt-card-large">
            <h4>${label} \u2014 ${modelLabel(selectedModel)}</h4>
            <div class="skewt-pair">
              <img src="${skewtUrlStr}" alt="Skew-T ${label} ${selectedModel}"
                   class="skewt-img" loading="lazy"
                   onerror="this.closest('.skewt-card').classList.add('skewt-unavailable')">
              <img src="${hodoUrlStr}" alt="Hodograph ${label} ${selectedModel}"
                   class="skewt-hodo-img" loading="lazy">
            </div>
            <div class="skewt-fallback">${t('skewt.tabNotAvailable')}</div>
          </div>
        </div>
      `;
      return;
    }
  }

  // Fallback: waypoint gallery
  if (!pack.has_skewt || !snapshot) {
    el.innerHTML = `<p class="muted">${t('skewt.notAvailable')}</p>`;
    return;
  }

  const waypoints = snapshot.route.waypoints;
  el.innerHTML = `
    <div class="skewt-gallery">
      ${waypoints.map((wp) => {
        const skewtUrlStr = api.skewtUrl(flight.id, pack.fetch_timestamp, wp.icao, selectedModel);
        const hodoUrlStr = api.hodographUrl(flight.id, pack.fetch_timestamp, wp.icao, selectedModel);
        return `
          <div class="skewt-card">
            <h4>${wp.icao}</h4>
            <div class="skewt-pair">
              <img src="${skewtUrlStr}" alt="Skew-T ${wp.icao} ${selectedModel}"
                   class="skewt-img" loading="lazy"
                   onerror="this.closest('.skewt-card').classList.add('skewt-unavailable')">
              <img src="${hodoUrlStr}" alt="Hodograph ${wp.icao} ${selectedModel}"
                   class="skewt-hodo-img" loading="lazy">
            </div>
            <div class="skewt-fallback">${t('skewt.tabNotAvailable')}</div>
          </div>
        `;
      }).join('')}
    </div>
  `;
}

// --- Loading / Error ---

export function renderLoading(loading: boolean): void {
  const el = $('loading-overlay');
  if (el) el.style.display = loading ? 'flex' : 'none';
}

export function renderRefreshing(refreshing: boolean): void {
  const btn = $('refresh-btn') as HTMLButtonElement;
  if (btn) {
    btn.disabled = refreshing;
    if (refreshing) {
      // Drop any trailing ellipsis from the label and append an animated
      // dots-spinner so the button visibly shows work in progress.
      const label = t('btn.refreshing').replace(/[.…]+\s*$/, '');
      btn.innerHTML = `${escapeHtml(label)}<span class="dots-spinner"></span>`;
    } else {
      btn.textContent = t('btn.refresh');
    }
  }
}

export function renderEmailing(emailing: boolean): void {
  const btn = $('email-btn') as HTMLButtonElement;
  if (btn) {
    btn.disabled = emailing;
    btn.textContent = emailing ? t('btn.sending') : t('btn.sendEmail');
  }
}

export function renderError(error: string | null): void {
  const el = $('error-message');
  if (el) {
    el.textContent = error || '';
    el.style.display = error ? 'block' : 'none';
  }
}

// --- Windy link ---

/** Update the Windy link to reflect the currently selected point and model. */
export function updateWindyLink(
  routeAnalyses: RouteAnalysesManifest | null,
  selectedPointIndex: number | null,
  selectedModel: string,
): void {
  const container = document.getElementById('external-links') as HTMLElement | null;
  const link = document.getElementById('windy-link') as HTMLAnchorElement | null;
  if (!container || !link) return;

  const point = selectedPointIndex != null ? routeAnalyses?.analyses?.[selectedPointIndex] : undefined;
  if (!point) {
    container.style.display = 'none';
    return;
  }

  link.href = buildWindyUrl(point.lat, point.lon, point.interpolated_time, selectedModel);
  container.style.display = '';
}
