/** DOM management for the Briefing report page.
 *
 * Renders all sections: header, assessment, synopsis, GRAMET,
 * model comparison, and Skew-T route view.
 */

import type {
  AirportObservation,
  AltitudeAdvisories,
  ConvectiveAssessment,
  DataStatus,
  FlightResponse,
  ForecastSnapshot,
  ObservationComparison,
  PackMeta,
  RouteAnalysesManifest,
  RouteObservations,
  RoutePointAnalysis,
  SfipZone,
  SoundingAnalysis,
  ThermodynamicIndices,
  VerticalRegime,
  WeatherDigest,
  WindComponent,
} from '../store/types';
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
import { $, escapeHtml, formatAlt, formatDate, formatDepartureTime, modelLabel, buildWindyUrl, flightTitle, flightRoute } from '../utils';
import { t, getDateLocale } from '../i18n/i18n';

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
  const route = wps.length > 2 ? flightRoute(wps) : '';
  const dateStr = formatDate(flight.target_date);
  const timeStr = formatDepartureTime(flight.departure_time);
  const alt = formatAlt(flight.cruise_altitude_ft);

  const routeHtml = route
    ? `<span class="briefing-route">${escapeHtml(route)}</span>`
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

export function renderAssessment(pack: PackMeta | null, flight?: FlightResponse | null): void {
  const el = $('assessment-banner');
  if (!el) return;

  if (!pack || !pack.assessment) {
    el.className = 'assessment-banner assessment-none';
    el.textContent = t('briefing.noAssessment');
    return;
  }

  const level = pack.assessment.toUpperCase();

  // Show alt assessment alongside primary if available
  if (pack.alt_assessment && flight?.alt_departure_time) {
    const altLevel = pack.alt_assessment.toUpperCase();
    const primaryTime = formatDepartureTime(flight.departure_time);
    const altTime = formatDepartureTime(flight.alt_departure_time);
    const altReason = pack.alt_assessment_reason ? ` \u2014 ${escapeHtml(pack.alt_assessment_reason)}` : '';
    const primaryReason = pack.assessment_reason ? ` \u2014 ${escapeHtml(pack.assessment_reason)}` : '';

    el.className = `assessment-banner assessment-${level.toLowerCase()}`;
    el.innerHTML = `
      <span class="assessment-dual">
        <span class="assessment-primary">
          <strong>${primaryTime}: ${level}</strong>${primaryReason}
        </span>
        <span class="assessment-separator">\u2502</span>
        <span class="assessment-alt assessment-${altLevel.toLowerCase()}-text">
          <strong>${t('briefing.alt', { time: altTime })}: ${altLevel}</strong>${altReason}
        </span>
      </span>
    `;
  } else {
    el.className = `assessment-banner assessment-${level.toLowerCase()}`;
    el.innerHTML = `
      <strong>${level}</strong>${pack.assessment_reason ? ` \u2014 ${escapeHtml(pack.assessment_reason)}` : ''}
    `;
  }
}

// --- Freshness bar ---

function formatModelRunTime(initTime: number): string {
  const d = new Date(initTime * 1000);
  const h = d.getUTCHours().toString().padStart(2, '0');
  return `${h}Z`;
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
    el.innerHTML = `<span class="refresh-prefix">${t('freshness.inProgress')}</span> · ${label}${detailSuffix}<span class="dots-spinner"></span>`;
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

  // Model basis line from the pack's init times, with GRIB annotation when different
  const packTimes = pack?.model_init_times || {};
  const gribTimes = pack?.grib_init_times || {};
  const fetchedParts = Object.entries(packTimes)
    .map(([m, t]) => {
      const gribTs = gribTimes[m];
      if (gribTs && gribTs !== t) {
        return `${modelLabel(m)} ${formatModelRunTime(t)} (GRIB ${formatModelRunTime(gribTs)})`;
      }
      return `${modelLabel(m)} ${formatModelRunTime(t)}`;
    });
  const skippedParts = (pack?.models_skipped_region || [])
    .map(m => `${modelLabel(m)} <span class="model-skipped">${t('freshness.skipped')}</span>`);
  const basisParts = [...fetchedParts, ...skippedParts].join(', ');
  const basisLine = basisParts ? `<span class="freshness-basis">${t('freshness.basedOn')}${basisParts}</span>` : '';

  // Build diagnostics HTML (warn entries only — info is too noisy for the bar)
  const diagEntries = (pack?.diagnostics || []).filter(d => d.level === 'warn');
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

  if (freshness.fresh) {
    let nextInfo = '';
    if (freshness.next_expected_update && freshness.next_expected_model) {
      const timeStr = formatTimeUntil(freshness.next_expected_update);
      nextInfo = t('freshness.nextUpdate', { time: `${modelLabel(freshness.next_expected_model)} ${timeStr}` });
    }
    const checkLink = `<a href="#" class="freshness-link" id="freshness-check-again">${t('freshness.checkAgain')}</a>`;
    el.className = 'freshness-bar freshness-current';
    el.innerHTML = `<span>${t('freshness.upToDate')}${nextInfo} ${checkLink}${forceLink}</span>${elapsedBadge}${basisLine}${diagHtml}`;
  } else {
    const staleStr = freshness.stale_models.map((m) => modelLabel(m)).join(', ');
    el.className = 'freshness-bar freshness-stale';
    el.innerHTML = `<span>${t('freshness.updatesAvailable')}${staleStr}${forceLink}</span>${elapsedBadge}${basisLine}${diagHtml}`;
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
  const lower = cat.toLowerCase();
  const cls = lower === 'vfr' ? 'flight-cat-vfr'
    : lower === 'mvfr' ? 'flight-cat-mvfr'
    : lower === 'ifr' ? 'flight-cat-ifr'
    : lower === 'lifr' ? 'flight-cat-lifr'
    : '';
  return `<span class="flight-cat-badge ${cls}">${escapeHtml(cat.toUpperCase())}</span>`;
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

// --- Synopsis (structured digest) ---

const DIGEST_SECTIONS: Array<{ key: keyof WeatherDigest; labelKey: string; icon: string }> = [
  { key: 'synoptic', labelKey: 'digest.synoptic', icon: '\uD83C\uDF0D' },
  { key: 'specific_concerns', labelKey: 'digest.concerns', icon: '\u26A0\uFE0F' },
  { key: 'trend', labelKey: 'digest.trend', icon: '\uD83D\uDCC8' },
  { key: 'watch_items', labelKey: 'digest.watchItems', icon: '\uD83D\uDC41\uFE0F' },
];

/** Digest section keys shown in compact mode (synoptic overview + trend only). */
const COMPACT_DIGEST_KEYS: Set<keyof WeatherDigest> = new Set(['synoptic', 'trend']);

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
): void {
  const el = $('synopsis-section');
  if (!el) return;

  if (!flight || !pack) {
    el.innerHTML = `<p class="muted">${t('digest.noBriefing')}</p>`;
    return;
  }

  if (digest) {
    el.innerHTML = renderDigestHtml(digest, displayMode);
    return;
  }

  if (pack.has_digest) {
    el.innerHTML = `<p class="muted">${t('digest.loading')}</p>`;
    fetchAndRenderDigestJson(flight.id, pack.fetch_timestamp, el, displayMode);
    return;
  }

  el.innerHTML = `<p class="muted">${t('digest.notAvailable')}</p>`;
}

async function fetchAndRenderDigestJson(
  flightId: string, timestamp: string, el: HTMLElement, displayMode: DisplayMode,
): Promise<void> {
  try {
    const url = api.digestJsonUrl(flightId, timestamp);
    const resp = await fetch(url);
    if (!resp.ok) throw new Error(`${resp.status}`);
    const digest: WeatherDigest = await resp.json();
    el.innerHTML = renderDigestHtml(digest, displayMode);
  } catch {
    el.innerHTML = `<p class="muted">${t('digest.failed')}</p>`;
  }
}

// --- DWD Synoptic Overview ---

export function renderDWDOverview(
  flight: FlightResponse | null,
  pack: PackMeta | null,
): void {
  const wrapper = $('dwd-overview-wrapper');
  const el = $('dwd-overview-section');
  if (!el || !wrapper) return;

  if (!flight || !pack) {
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
            el.innerHTML =
              `<p class="muted">${t('gramet.noCreds')}</p>`;
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
    </div>
  `;
}

// --- Model Comparison ---

export function renderModelComparison(
  snapshot: ForecastSnapshot | null,
  routeAnalyses?: RouteAnalysesManifest | null,
  selectedPointIndex?: number,
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
  selectedPointIndex?: number,
  displayMode: DisplayMode = 'full',
  tierVisibility: Record<Tier, boolean> = { key: true, useful: true, advanced: false },
  enabledLayers?: Record<string, boolean>,
): void {
  const el = $('sounding-section');
  if (!el) return;

  // Route-point mode: show single selected point
  if (routeAnalyses && routeAnalyses.analyses.length > 0) {
    const idx = selectedPointIndex ?? 0;
    const point = routeAnalyses.analyses[idx];
    if (point) {
      el.innerHTML = renderSinglePointSounding(point, displayMode, tierVisibility, enabledLayers);
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
        ${renderAtmosphericProfile(a.sounding, a.altitude_advisories)}
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
      const variantBadge = `<span class="sfip-variant">${sfipMatch.variant.startsWith('full') || sfipMatch.variant.startsWith('interp') ? 'CLW' : 'proxy'}</span>`;
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
    const variantBadge = `<span class="sfip-variant">${sfipMatch.variant.startsWith('full') || sfipMatch.variant.startsWith('interp') ? 'CLW' : 'proxy'}</span>`;
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

function renderAtmosphericProfile(
  soundings: Record<string, SoundingAnalysis>,
  adv: AltitudeAdvisories | null,
  enabledLayers?: Record<string, boolean>,
): string {
  if (!adv) return '';

  const parts: string[] = [];

  // Cruise icing badge
  if (adv.cruise_in_icing) {
    parts.push(
      `<div class="cruise-icing-banner ${riskClass(adv.cruise_icing_risk)}">` +
      `${t('sounding.cruiseInIcing')}${adv.cruise_icing_risk.toUpperCase()}</div>`,
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

// --- Route Slider ---

export function renderRouteSlider(
  ra: RouteAnalysesManifest | null,
  selectedIndex: number,
  onSelect: (index: number) => void,
): void {
  const section = $('route-slider-section');
  const container = $('route-slider-container');
  if (!section || !container) return;

  if (!ra || ra.analyses.length === 0) {
    section.style.display = 'none';
    return;
  }

  section.style.display = '';
  const analyses = ra.analyses;
  const maxIdx = analyses.length - 1;
  const current = analyses[selectedIndex] || analyses[0];
  const totalDist = ra.total_distance_nm;

  // Build waypoint labels for the track
  const waypointLabels = analyses
    .filter((a) => a.waypoint_icao)
    .map((a) => {
      const pct = totalDist > 0 ? (a.distance_from_origin_nm / totalDist) * 100 : 0;
      return `<span class="slider-waypoint-label" style="left: ${pct}%">${escapeHtml(a.waypoint_icao!)}</span>`;
    })
    .join('');

  // Format time
  // Append 'Z' so JS parses as UTC (backend sends naive ISO strings that are UTC by convention)
  const timeIso = current.interpolated_time.endsWith('Z') ? current.interpolated_time : current.interpolated_time + 'Z';
  const time = new Date(timeIso);
  const timeStr = time.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: 'UTC' }) + 'Z';

  // Wind info for first model
  const modelKeys = Object.keys(current.wind_components);
  let windInfo = '';
  if (modelKeys.length > 0) {
    const wc: WindComponent = current.wind_components[modelKeys[0]];
    const hdTail = wc.headwind_kt >= 0 ? `HD ${wc.headwind_kt.toFixed(0)}` : `TL ${(-wc.headwind_kt).toFixed(0)}`;
    windInfo = `Wind ${wc.wind_direction_deg.toFixed(0)}\u00B0/${wc.wind_speed_kt.toFixed(0)}kt (${hdTail})`;
  }

  const pointLabel = current.waypoint_icao
    ? `${current.waypoint_icao} \u2014 ${current.waypoint_name || ''}`
    : `${current.lat.toFixed(2)}\u00B0N ${Math.abs(current.lon).toFixed(2)}\u00B0${current.lon >= 0 ? 'E' : 'W'}`;

  container.innerHTML = `
    <div class="route-slider-info">
      <span class="slider-point-label">${escapeHtml(pointLabel)}</span>
      <span class="slider-distance">${current.distance_from_origin_nm.toFixed(0)} nm</span>
      <span class="slider-time">${escapeHtml(timeStr)}</span>
      <span class="slider-wind">${escapeHtml(windInfo)}</span>
    </div>
    <div class="route-slider-track">
      <input type="range" id="route-slider" min="0" max="${maxIdx}" value="${selectedIndex}" class="route-slider-input">
      <div class="slider-waypoint-labels">${waypointLabels}</div>
    </div>
    <div class="slider-endpoints">
      <span>${escapeHtml(analyses[0].waypoint_icao || t('sounding.origin'))}</span>
      <span>${escapeHtml(analyses[maxIdx].waypoint_icao || t('sounding.destination'))}</span>
    </div>
  `;

  const slider = document.getElementById('route-slider') as HTMLInputElement;
  if (slider) {
    slider.addEventListener('input', () => {
      onSelect(parseInt(slider.value, 10));
    });
  }
}

// --- Route-point sounding (single point) ---

function renderSinglePointSounding(
  point: RoutePointAnalysis,
  displayMode: DisplayMode = 'full',
  tierVisibility: Record<Tier, boolean> = { key: true, useful: true, advanced: false },
  enabledLayers?: Record<string, boolean>,
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
      ${renderAtmosphericProfile(point.sounding, point.altitude_advisories, enabledLayers)}
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
  selectedPointIndex?: number,
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
    const idx = selectedPointIndex ?? 0;
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
    btn.textContent = refreshing ? t('btn.refreshing') : t('btn.refresh');
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
  selectedPointIndex: number,
  selectedModel: string,
): void {
  const container = document.getElementById('external-links') as HTMLElement | null;
  const link = document.getElementById('windy-link') as HTMLAnchorElement | null;
  if (!container || !link) return;

  const point = routeAnalyses?.analyses?.[selectedPointIndex];
  if (!point) {
    container.style.display = 'none';
    return;
  }

  link.href = buildWindyUrl(point.lat, point.lon, point.interpolated_time, selectedModel);
  container.style.display = '';
}
