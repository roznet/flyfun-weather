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
import { $, escapeHtml, formatAlt, formatDate, formatTime, modelLabel, buildWindyUrl } from '../utils';

// --- Header ---

export function renderHeader(
  flight: FlightResponse | null,
  snapshot: ForecastSnapshot | null,
): void {
  const el = $('briefing-header');
  if (!el || !flight) return;

  // Use snapshot waypoints if available, otherwise derive from route name
  let routeStr: string;
  if (snapshot?.route?.waypoints) {
    routeStr = snapshot.route.waypoints.map((w) => w.icao).join(' \u2192 ');
  } else if (flight.waypoints?.length) {
    routeStr = flight.waypoints.join(' \u2192 ');
  } else {
    routeStr = flight.route_name.replace(/_/g, ' \u2192 ').toUpperCase();
  }

  const dateStr = formatDate(flight.target_date);
  const timeStr = formatTime(flight.target_time_utc);
  const alt = formatAlt(flight.cruise_altitude_ft);

  el.innerHTML = `
    <span class="route-summary">${escapeHtml(routeStr)}</span>
    <span class="date-summary">${escapeHtml(dateStr)} ${escapeHtml(timeStr)}</span>
    <span class="alt-summary">${escapeHtml(alt)}</span>
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
    ? '<option>No briefings yet</option>'
    : packs.map((p) => {
        const date = new Date(p.fetch_timestamp);
        const label = `D-${p.days_out} (${date.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', timeZone: 'UTC' })} UTC)`;
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

export function renderAssessment(pack: PackMeta | null): void {
  const el = $('assessment-banner');
  if (!el) return;

  if (!pack || !pack.assessment) {
    el.className = 'assessment-banner assessment-none';
    el.textContent = 'No assessment available';
    return;
  }

  const level = pack.assessment.toUpperCase();
  el.className = `assessment-banner assessment-${level.toLowerCase()}`;
  el.innerHTML = `
    <strong>${level}</strong>${pack.assessment_reason ? ` \u2014 ${escapeHtml(pack.assessment_reason)}` : ''}
  `;
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
      el.innerHTML = `<span class="refresh-prefix">Queued</span> · Waiting for other refreshes to complete<span class="dots-spinner"></span>`;
      return;
    }
    const detailSuffix = refreshDetail ? ` (${escapeHtml(refreshDetail)})` : '';
    const label = refreshStage ? escapeHtml(refreshStage) : 'Starting refresh';
    el.innerHTML = `<span class="refresh-prefix">Refreshing (may take a minute)</span> · ${label}${detailSuffix}<span class="dots-spinner"></span>`;
    return;
  }

  if (freshnessLoading && !freshness) {
    el.className = 'freshness-bar freshness-current';
    el.innerHTML = 'Checking for updates...';
    return;
  }

  if (!freshness) {
    el.style.display = 'none';
    return;
  }

  // Model basis line from the pack's init times, with GRIB annotation when different
  const packTimes = pack?.model_init_times || {};
  const gribTimes = pack?.grib_init_times || {};
  const basisParts = Object.entries(packTimes)
    .map(([m, t]) => {
      const gribTs = gribTimes[m];
      if (gribTs && gribTs !== t) {
        return `${modelLabel(m)} ${formatModelRunTime(t)} (GRIB ${formatModelRunTime(gribTs)})`;
      }
      return `${modelLabel(m)} ${formatModelRunTime(t)}`;
    })
    .join(', ');
  const basisLine = basisParts ? `<span class="freshness-basis">Based on ${basisParts}</span>` : '';

  const forceLink = isAdmin
    ? ' <a href="#" class="freshness-link" id="freshness-force-refresh">Force refresh</a>'
    : '';

  if (freshness.fresh) {
    let nextInfo = '';
    if (freshness.next_expected_update && freshness.next_expected_model) {
      const timeStr = formatTimeUntil(freshness.next_expected_update);
      nextInfo = `, next update ${modelLabel(freshness.next_expected_model)} in ~${timeStr}`;
    }
    const checkLink = `<a href="#" class="freshness-link" id="freshness-check-again">Check again</a>`;
    el.className = 'freshness-bar freshness-current';
    el.innerHTML = `<span>Up to date${nextInfo} ${checkLink}${forceLink}</span>${basisLine}`;
  } else {
    const staleStr = freshness.stale_models.map((m) => modelLabel(m)).join(', ');
    el.className = 'freshness-bar freshness-stale';
    el.innerHTML = `<span>Updates available: ${staleStr}${forceLink}</span>${basisLine}`;
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
      <span>Private</span>
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
    const suffix = isDefault ? ' (auto)' : '';
    return `<option value="${h}"${selected}>${label}${suffix}</option>`;
  }).join('');

  el.innerHTML = `
    <label class="auto-refresh-toggle">
      <input type="checkbox" id="auto-refresh-check" ${enabled ? 'checked' : ''}>
      <span>Auto-refresh</span>
    </label>
    <span class="auto-refresh-hour-group" ${enabled ? '' : 'style="display:none;"'}>
      at <select id="auto-refresh-hour">${hourOptions}</select>
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
    ? `<h4>METAR</h4><code class="obs-popup-metar">${escapeHtml(apt.metar_raw)}</code>`
    : '<h4>METAR</h4><p class="muted">Not available</p>';

  // TAF raw with applicable lines highlighted
  let tafBlock: string;
  if (apt.taf_raw) {
    const lines = apt.taf_raw.split('\n');
    const applicable = new Set(apt.taf_applicable_lines ?? []);
    const tafHtml = lines.map((line, i) => {
      const escaped = escapeHtml(line);
      return applicable.has(i) ? `<mark>${escaped}</mark>` : escaped;
    }).join('\n');
    tafBlock = `<h4>TAF</h4><code class="obs-popup-taf">${tafHtml}</code>`;
  } else {
    tafBlock = '<h4>TAF</h4><p class="muted">Not available</p>';
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
    ? `<h4>Wind Summary</h4><pre class="obs-wind-summary">${windLines.join('\n')}</pre>`
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
    ? `<button class="obs-refresh-btn" title="Re-fetch METAR/TAF observations">Refresh</button>`
    : '';

  // Summary header
  const worstBadge = obs.worst_metar_category
    ? ` \u2014 Worst: ${flightCatBadge(obs.worst_metar_category)}`
    : '';
  const phenomena = obs.phenomena_along_route.length > 0
    ? ` \u2014 Phenomena: ${escapeHtml(obs.phenomena_along_route.join(', '))}`
    : '';
  const fetchInfo = fetchLabel ? `<span class="obs-fetch-time">Fetched ${fetchLabel}</span>` : '';
  const summaryHtml = `<p class="obs-summary">${obs.airports_with_metar} METAR, ${obs.airports_with_taf} TAF within ${Math.round(obs.corridor_nm)}nm corridor${worstBadge}${phenomena} ${fetchInfo}${refreshBtn}</p>`;

  // Conflict banner
  const conflictHtml = obs.has_conflicts
    ? '<div class="obs-conflict-banner">Observation/model conflicts detected</div>'
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
          <td class="obs-icao">${escapeHtml(apt.icao)} <button class="obs-info-btn" data-icao="${escapeHtml(apt.icao)}" title="Show METAR/TAF details" aria-label="Info">i</button></td>
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
            <th rowspan="2" style="text-align:left;">ICAO</th>
            <th rowspan="2">Dist</th>
            <th rowspan="2">ETA</th>
            <th colspan="4" class="obs-group-header">Condition</th>
            <th colspan="4" class="obs-group-header">Wind</th>
          </tr>
          <tr>
            <th class="obs-group-start">METAR</th><th>TAF</th><th>Model</th><th></th>
            <th class="obs-group-start">METAR</th><th>TAF</th><th>Model</th><th></th>
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
      refreshBtn.textContent = 'Refreshing...';
      onRefresh().finally(() => {
        // Re-render will replace the button, but just in case:
        refreshBtn.disabled = false;
        refreshBtn.textContent = 'Refresh';
      });
    }
  });
}

// --- Synopsis (structured digest) ---

const DIGEST_SECTIONS: Array<{ key: keyof WeatherDigest; label: string; icon: string }> = [
  { key: 'synoptic', label: 'Synoptic', icon: '\uD83C\uDF0D' },
  { key: 'winds', label: 'Winds', icon: '\uD83D\uDCA8' },
  { key: 'cloud_visibility', label: 'Cloud & Visibility', icon: '\u2601\uFE0F' },
  { key: 'precipitation_convection', label: 'Precipitation & Convection', icon: '\uD83C\uDF27\uFE0F' },
  { key: 'icing', label: 'Icing', icon: '\u2744\uFE0F' },
  { key: 'specific_concerns', label: 'Specific Concerns', icon: '\u26A0\uFE0F' },
  { key: 'model_agreement', label: 'Model Agreement', icon: '\uD83D\uDCCA' },
  { key: 'trend', label: 'Trend', icon: '\uD83D\uDCC8' },
  { key: 'watch_items', label: 'Watch Items', icon: '\uD83D\uDC41\uFE0F' },
];

/** Digest section keys shown in compact mode (synoptic overview + trend only). */
const COMPACT_DIGEST_KEYS: Set<keyof WeatherDigest> = new Set(['synoptic', 'trend']);

function renderDigestHtml(digest: WeatherDigest, displayMode: DisplayMode): string {
  const sections = displayMode === 'compact'
    ? DIGEST_SECTIONS.filter(s => COMPACT_DIGEST_KEYS.has(s.key))
    : DIGEST_SECTIONS;
  return sections.map(({ key, label, icon }) => {
    const text = digest[key];
    if (!text) return '';
    return `
      <div class="digest-section">
        <h4>${icon} ${label}</h4>
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
    el.innerHTML = '<p class="muted">No briefing loaded.</p>';
    return;
  }

  if (digest) {
    el.innerHTML = renderDigestHtml(digest, displayMode);
    return;
  }

  if (pack.has_digest) {
    el.innerHTML = '<p class="muted">Loading digest...</p>';
    fetchAndRenderDigestJson(flight.id, pack.fetch_timestamp, el, displayMode);
    return;
  }

  el.innerHTML = '<p class="muted">Synopsis not available. Trigger a refresh to generate.</p>';
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
    el.innerHTML = '<p class="muted">Failed to load digest.</p>';
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
              '<p class="muted">No GRAMET available. To enable, enter your ' +
              '<a href="https://www.autorouter.aero" target="_blank">autorouter.aero</a> ' +
              'credentials in <a href="settings.html">Account Settings</a>.</p>';
          } else {
            el.innerHTML = '<p class="muted">GRAMET not available for this briefing.</p>';
          }
        })
        .catch(() => {
          el.innerHTML = '<p class="muted">GRAMET not available for this briefing.</p>';
        }),
    );
    return;
  }

  const pngUrl = api.grametPngUrl(flight.id, pack.fetch_timestamp);
  const pdfUrl = api.grametUrl(flight.id, pack.fetch_timestamp);
  el.innerHTML = `
    <img src="${pngUrl}" alt="GRAMET Cross-Section" class="gramet-img" />
    <div class="gramet-actions">
      <a href="${pdfUrl}" download class="btn btn-sm">Download GRAMET PDF</a>
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
    el.innerHTML = '<p class="muted">No model comparison data for this point.</p>';
    return;
  }

  // Fallback: stacked waypoint view
  if (!snapshot || snapshot.analyses.length === 0) {
    el.innerHTML = '<p class="muted">No model comparison data available.</p>';
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
            <th>Variable</th>
            ${headerCells}
            <th>Spread</th>
            <th>Agree</th>
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
      ${tierBtn}
    </div>
  `;
}

function formatVarName(name: string): string {
  const labels: Record<string, string> = {
    'temperature_c': 'Temp (\u00B0C)',
    'wind_speed_kt': 'Wind (kt)',
    'wind_direction_deg': 'Wind dir (\u00B0)',
    'cloud_cover_pct': 'Cloud (%)',
    'precipitation_mm': 'Precip (mm)',
    'freezing_level_m': 'Freezing (m)',
    'freezing_level_ft': 'Freezing (ft)',
    'cape_surface_jkg': 'CAPE (J/kg)',
    'lcl_altitude_ft': 'LCL (ft)',
    'k_index': 'K-index',
    'total_totals': 'Total Totals',
    'precipitable_water_mm': 'PW (mm)',
    'lifted_index': 'Lifted Index',
    'bulk_shear_0_6km_kt': 'Shear 0-6km (kt)',
    'max_omega_pa_s': 'Max Omega (Pa/s)',
  };
  return labels[name] || name;
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
    el.innerHTML = '<p class="muted">No sounding analysis available.</p>';
    return;
  }

  const hasSounding = snapshot.analyses.some(
    (a) => a.sounding && Object.keys(a.sounding).length > 0,
  );
  if (!hasSounding) {
    el.innerHTML = '<p class="muted">Sounding analysis not available for this briefing.</p>';
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
    ? `<p class="convective-modifiers"><strong>Severe modifiers:</strong> ${escapeHtml([...allMods].join(', '))}</p>`
    : '';

  // Tier toggle button
  const tierBtn = renderTierToggle('convective', tierVisibility);

  return `
    <div class="convective-section">
      <h5>Convective</h5>
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

  const label = tierVisibility.advanced ? 'Hide advanced' : 'Show advanced';
  return `<button class="tier-toggle-btn" data-section="${sectionId}" data-tier="advanced">${label}</button>`;
}

function formatClassification(cls: string): string {
  if (cls === 'unavailable') return 'N/A';
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
      label: 'Classification',
      metricId: 'vertical_motion_class',
      render: (m) => {
        const vm = soundings[m].vertical_motion;
        if (!vm || vm.classification === 'unavailable') return '<td class="muted">N/A</td>';
        const cls = vm.classification === 'convective' ? 'risk-severe'
          : vm.classification === 'synoptic_ascent' ? 'risk-moderate'
          : '';
        return `<td class="${cls}">${formatClassification(vm.classification)}</td>`;
      },
    },
    {
      label: 'Max W (ft/min)',
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
      label: 'Contamination',
      render: (m) => {
        const vm = soundings[m].vertical_motion;
        if (!vm) return '<td>\u2014</td>';
        return vm.convective_contamination
          ? '<td class="risk-moderate">Mid-level convective</td>'
          : '<td>None</td>';
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
        <h6>CAT Risk Layers <span class="section-info-btn">${catInfoBtn}</span></h6>
        <table class="band-table">
          <thead><tr><th>Altitude</th>${headerCells}</tr></thead>
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
      <h5>Key Altitudes</h5>
      <table class="band-table">
        <thead><tr><th></th>${headerCells}</tr></thead>
        <tbody>${rows}</tbody>
      </table>
      ${tierBtn}
    </div>
  `;
}

function inversionStrengthLabel(strengthC: number): string {
  if (strengthC >= 3) return 'strong';
  if (strengthC >= 1) return 'moderate';
  return 'weak';
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
        params.push(`NWP\u00a0${regime.cloud_cover_pct.toFixed(0)}%`);
      if (params.length > 0)
        lines.push(`<div class="regime-params">${params.join(' ')}</div>`);
    } else {
      // Old data: no coverage detail
      lines.push(`<div class="regime-cloud">In cloud</div>`);
    }
  } else if (!layerOn('cloud-bands') && layerOn('nwp-cloud-bands') && regime.cloud_cover_pct != null && regime.cloud_cover_pct > 0) {
    // Cloud-bands OFF but NWP ON: show minimal NWP line
    lines.push(`<div class="regime-cloud">NWP\u00a0${regime.cloud_cover_pct.toFixed(0)}%</div>`);
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
      const variantBadge = `<span class="sfip-variant">${sfipMatch.variant === 'full' ? 'CLW' : 'proxy'}</span>`;
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
    const variantBadge = `<span class="sfip-variant">${sfipMatch.variant === 'full' ? 'CLW' : 'proxy'}</span>`;
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
      ? ` <span class="regime-nwp">NWP\u00a0${regime.cloud_cover_pct.toFixed(0)}%</span>` : '';
    lines.push(`<span class="regime-clear">Clear${nwp}</span>`);
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
      `Cruise in icing: ${adv.cruise_icing_risk.toUpperCase()}</div>`,
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
        <h5>Atmospheric Profile</h5>
        <div class="table-scroll">
          <table class="band-table">
            <thead><tr><th>Altitude</th>${headerCells}</tr></thead>
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
      label = 'Unable to descend below freezing' + infeasibleBadge;
    } else {
      label = escapeHtml(a.reason) + infeasibleBadge;
    }

    const cells = advModels.map((m) => {
      const v = a.per_model_ft[m];
      if (v == null) return '<td>\u2014</td>';
      if (a.advisory_type === 'descend_below_icing' && v === 0) {
        return '<td>SFC</td>';
      }
      return `<td>${v.toFixed(0)}ft</td>`;
    }).join('');

    const rowCls = !a.feasible ? ' class="advisory-infeasible"' : '';
    return `<tr${rowCls}><td class="var-name">${label}</td>${cells}</tr>`;
  }).join('');

  return `
    <div class="advisories-section">
      <h5>Advisories</h5>
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
      <span>${escapeHtml(analyses[0].waypoint_icao || 'Origin')}</span>
      <span>${escapeHtml(analyses[maxIdx].waypoint_icao || 'Destination')}</span>
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
    return '<p class="muted">No sounding data for this point.</p>';
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
    el.innerHTML = '<p class="muted">Skew-T diagrams not available.</p>';
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
            <div class="skewt-fallback">Not available</div>
          </div>
        </div>
      `;
      return;
    }
  }

  // Fallback: waypoint gallery
  if (!pack.has_skewt || !snapshot) {
    el.innerHTML = '<p class="muted">Skew-T diagrams not available.</p>';
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
            <div class="skewt-fallback">Not available</div>
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
    btn.textContent = refreshing ? 'Refreshing...' : 'Refresh';
  }
}

export function renderEmailing(emailing: boolean): void {
  const btn = $('email-btn') as HTMLButtonElement;
  if (btn) {
    btn.disabled = emailing;
    btn.textContent = emailing ? 'Sending...' : 'Send Email';
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
