/** Advisory dashboard renderer — compact grid of advisory cards with per-model badges. */

import type { RouteAdvisoriesManifest, RouteAdvisoryResult, AdvisoryStatus, ModelAdvisoryResult, AdvisoryCatalogEntry, AirportConditions, AirportConditionsSummary, AirportModelCondition, FlightCategory, RunwayWind, AltitudeTableResult } from '../types/advisories';
import type { DisplayMode } from '../types/metrics';
import { showPopupContent } from '../components/info-popup';
import { renderAdvisoryPopup } from '../helpers/advisory-popup';
import { $, escapeHtml, formatAlt, modelLabel } from '../utils';
import { t } from '../i18n/i18n';

/** Advisory categories hidden in compact mode (informational, not actionable). */
const COMPACT_HIDDEN_CATEGORIES = new Set(['model']);

const STATUS_ORDER: AdvisoryStatus[] = ['red', 'amber', 'green', 'unavailable'];

function statusBadgeClass(status: AdvisoryStatus): string {
  switch (status) {
    case 'green': return 'badge-green';
    case 'amber': return 'badge-amber';
    case 'red': return 'badge-red';
    default: return 'badge-muted';
  }
}

function statusLabel(status: AdvisoryStatus): string {
  switch (status) {
    case 'green': return 'G';
    case 'amber': return 'A';
    case 'red': return 'R';
    default: return '?';
  }
}

function flightCatBadgeClass(cat: FlightCategory): string {
  switch (cat) {
    case 'vfr': return 'flight-cat-vfr';
    case 'mvfr': return 'flight-cat-mvfr';
    case 'ifr': return 'flight-cat-ifr';
    case 'lifr': return 'flight-cat-lifr';
  }
}

function formatRunwayPopup(allRunways: RunwayWind[]): string {
  if (allRunways.length === 0) return '';
  const rows = allRunways.map(r =>
    `<tr><td>${escapeHtml(r.runway_id)}</td><td>${r.heading_deg.toFixed(0)}&deg;</td>` +
    `<td>${r.crosswind_kt.toFixed(0)}kt</td><td>${r.headwind_kt > 0 ? '+' : ''}${r.headwind_kt.toFixed(0)}kt</td></tr>`
  ).join('');
  return `
    <div class="popup-header"><h3>${t('advisories.allRunways')}</h3></div>
    <table class="advisory-params-table">
      <thead><tr><th>${t('advisories.runway')}</th><th>${t('advisories.heading')}</th><th>${t('advisories.crosswind')}</th><th>${t('advisories.headwind')}</th></tr></thead>
      <tbody>${rows}</tbody>
    </table>
  `;
}

function roundWind(deg: number): string {
  return String(Math.round(deg / 10) * 10).padStart(3, '0');
}

function formatWind(cond: AirportModelCondition): string {
  if (cond.wind_speed_kt == null || cond.wind_direction_deg == null) return '\u2014';

  const dir = roundWind(cond.wind_direction_deg);
  const spd = cond.wind_speed_kt.toFixed(0);
  const gust = cond.wind_gust_kt != null ? `G${cond.wind_gust_kt.toFixed(0)}` : '';
  return `${dir}@${spd}${gust}`;
}

function formatRunwayComponents(rwy: RunwayWind, windDir: number): string {
  // Headwind: \u2193 down = headwind, \u2191 up = tailwind
  const hwArrow = rwy.headwind_kt >= 0 ? '\u2193' : '\u2191';
  const hwVal = Math.abs(rwy.headwind_kt).toFixed(0);

  // Crosswind direction from wind relative to runway
  // sin(wind_dir - heading) < 0 → wind from left → drifts right → \u2192
  // sin(wind_dir - heading) > 0 → wind from right → drifts left → \u2190
  const rel = (windDir - rwy.heading_deg) * Math.PI / 180;
  const xwArrow = Math.sin(rel) >= 0 ? '\u2190' : '\u2192';
  const xwVal = rwy.crosswind_kt.toFixed(0);

  return `RW${rwy.runway_id} ${hwArrow}${hwVal} ${xwArrow}${xwVal}`;
}

/** Severity rank for flight categories — higher = worse. */
const FLIGHT_CAT_SEVERITY: Record<FlightCategory, number> = {
  vfr: 0, mvfr: 1, ifr: 2, lifr: 3,
};

/**
 * Compute a summary condition from per-model conditions using the user's aggregation mode.
 *
 * - **majority**: find the most common flight category (ties broken by worst),
 *   then pick the worst ceiling/vis/wind from that winning group.
 * - **worst**: pick the worst category and worst values across all models.
 */
function computeSummaryCondition(
  conditions: AirportModelCondition[],
  aggregation: 'worst' | 'majority',
): AirportModelCondition | null {
  if (conditions.length === 0) return null;

  let winningCat: FlightCategory;
  let pool: AirportModelCondition[];

  if (aggregation === 'majority') {
    // Count votes per category
    const counts = new Map<FlightCategory, number>();
    for (const c of conditions) {
      counts.set(c.flight_category, (counts.get(c.flight_category) ?? 0) + 1);
    }
    // Pick category with most votes; ties broken by worst severity
    let bestCount = 0;
    let bestSeverity = -1;
    winningCat = conditions[0].flight_category;
    for (const [cat, count] of counts) {
      const sev = FLIGHT_CAT_SEVERITY[cat];
      if (count > bestCount || (count === bestCount && sev > bestSeverity)) {
        bestCount = count;
        bestSeverity = sev;
        winningCat = cat;
      }
    }
    pool = conditions.filter(c => c.flight_category === winningCat);
  } else {
    // Worst: pick the worst category across all
    winningCat = conditions[0].flight_category;
    for (const c of conditions) {
      if (FLIGHT_CAT_SEVERITY[c.flight_category] > FLIGHT_CAT_SEVERITY[winningCat]) {
        winningCat = c.flight_category;
      }
    }
    pool = conditions;
  }

  // Pick worst values from the pool (lowest ceiling/vis, highest wind)
  let worstCeiling: number | null = null;
  let worstVis: number | null = null;
  let worstWindSpd: number | null = null;
  let worstWindDir: number | null = null;
  let worstGust: number | null = null;
  let worstRwy: RunwayWind | null = null;
  let allRwysCombined: RunwayWind[] = [];

  for (const c of pool) {
    if (c.ceiling_ft !== null) {
      worstCeiling = worstCeiling === null ? c.ceiling_ft : Math.min(worstCeiling, c.ceiling_ft);
    }
    if (c.visibility_sm !== null) {
      worstVis = worstVis === null ? c.visibility_sm : Math.min(worstVis, c.visibility_sm);
    }
    if (c.wind_speed_kt !== null) {
      if (worstWindSpd === null || c.wind_speed_kt > worstWindSpd) {
        worstWindSpd = c.wind_speed_kt;
        worstWindDir = c.wind_direction_deg;
        worstGust = c.wind_gust_kt;
        worstRwy = c.best_runway;
      }
    }
    allRwysCombined = allRwysCombined.concat(c.all_runways);
  }

  return {
    model: 'Summary',
    flight_category: winningCat,
    ceiling_ft: worstCeiling,
    visibility_sm: worstVis,
    wind_speed_kt: worstWindSpd,
    wind_direction_deg: worstWindDir,
    wind_gust_kt: worstGust,
    best_runway: worstRwy,
    all_runways: allRwysCombined,
  };
}

function renderSummaryRow(cond: AirportModelCondition): string {
  const catLabel = cond.flight_category.toUpperCase();
  const catClass = flightCatBadgeClass(cond.flight_category);
  const vis = cond.visibility_sm !== null ? `vis ${cond.visibility_sm}sm` : '';
  const ceil = cond.ceiling_ft !== null ? `ceil ${cond.ceiling_ft}ft` : 'CLR';
  const wind = formatWind(cond);
  const rwyComp = cond.best_runway && cond.wind_direction_deg != null
    ? formatRunwayComponents(cond.best_runway, cond.wind_direction_deg)
    : '';

  const parts = [ceil, vis, wind !== '\u2014' ? wind : '', rwyComp].filter(Boolean).join(' \u00b7 ');

  return `
    <div class="airport-summary">
      <span class="flight-cat-badge ${catClass}">${catLabel}</span>
      <span class="airport-summary-detail">${parts}</span>
    </div>
  `;
}

function renderConditionRow(cond: AirportModelCondition): string {
  const catLabel = cond.flight_category.toUpperCase();
  const catClass = flightCatBadgeClass(cond.flight_category);
  const vis = cond.visibility_sm !== null ? `${cond.visibility_sm}sm` : 'N/A';
  const ceil = cond.ceiling_ft !== null ? `${cond.ceiling_ft}ft` : 'CLR';

  const wind = formatWind(cond);
  const rwyComp = cond.best_runway && cond.wind_direction_deg != null
    ? formatRunwayComponents(cond.best_runway, cond.wind_direction_deg)
    : '';
  const windCell = rwyComp ? `${wind} ${rwyComp}` : wind;

  return `
    <tr class="airport-condition-row">
      <td class="airport-model">${modelLabel(cond.model)}</td>
      <td><span class="flight-cat-badge ${catClass}">${catLabel}</span></td>
      <td>vis ${vis}</td>
      <td>ceil ${ceil}</td>
      <td class="airport-rwy-cell" data-model="${escapeHtml(cond.model)}">${windCell}</td>
    </tr>
  `;
}

function renderAirportCard(
  summary: AirportConditionsSummary,
  role: string,
  aggregation: 'worst' | 'majority',
): string {
  if (summary.conditions.length === 0) return '';

  const summaryCond = computeSummaryCondition(summary.conditions, aggregation);
  const summaryHtml = summaryCond ? renderSummaryRow(summaryCond) : '';
  const rows = summary.conditions.map(renderConditionRow).join('');
  return `
    <div class="airport-card">
      <div class="airport-card-header">${role}: ${escapeHtml(summary.icao)}</div>
      ${summaryHtml}
      <table class="airport-conditions-table">
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
}

function renderAirportConditions(conditions: AirportConditions, aggregation: 'worst' | 'majority'): string {
  const dep = renderAirportCard(conditions.departure, t('advisories.departure'), aggregation);
  const arr = renderAirportCard(conditions.arrival, t('advisories.arrival'), aggregation);
  if (!dep && !arr) return '';
  return `<div class="airport-cards">${dep}${arr}</div>`;
}


function renderAdvisoryCard(adv: RouteAdvisoryResult, catalog: Map<string, AdvisoryCatalogEntry>): string {
  const entry = catalog.get(adv.advisory_id);
  const name = entry ? escapeHtml(entry.name) : escapeHtml(adv.advisory_id);
  const desc = entry ? escapeHtml(entry.short_description) : '';

  const modelBadges = adv.per_model.map((m: ModelAdvisoryResult) =>
    `<span class="adv-model-badge ${statusBadgeClass(m.status)}" title="${escapeHtml(m.detail)}">${modelLabel(m.model)}</span>`
  ).join(' ');

  const aggClass = statusBadgeClass(adv.aggregate_status);
  const infoBtn = entry
    ? `<button class="metric-info-btn advisory-info-btn" data-advisory-id="${escapeHtml(adv.advisory_id)}" title="${t('advisories.advisoryInfo')}" aria-label="${t('advisories.advisoryInfo')}">i</button>`
    : '';

  return `
    <div class="advisory-card advisory-${adv.aggregate_status}" data-advisory="${escapeHtml(adv.advisory_id)}">
      <div class="advisory-card-header">
        <span class="badge ${aggClass}">${statusLabel(adv.aggregate_status)}</span>
        <span class="advisory-name">${name}</span>
        ${infoBtn}
      </div>
      <div class="advisory-models">${modelBadges}</div>
      <div class="advisory-detail">${escapeHtml(adv.aggregate_detail)}</div>
      ${desc ? `<div class="advisory-desc">${desc}</div>` : ''}
    </div>
  `;
}

/** Render the altitude table popup HTML from an AltitudeTableResult. */
export function renderAltitudeTablePopup(result: AltitudeTableResult): string {
  const { rows, advisory_ids, advisory_names, cruise_altitude_ft, best_below_cruise, best_above_cruise } = result;

  if (rows.length === 0) {
    return `<div class="popup-header"><h3>${t('advisories.altTableTitle')}</h3></div><p class="muted">${t('advisories.altTableEmpty')}</p>`;
  }

  // Column headers: abbreviated advisory names
  const headerCells = advisory_ids.map(id => {
    const name = advisory_names[id] || id;
    // Abbreviate: take first word or first 10 chars
    const abbrev = name.length > 10 ? name.split(/[\s(]/)[0].slice(0, 10) : name;
    return `<th class="alt-table-col-header" title="${escapeHtml(name)}">${escapeHtml(abbrev)}</th>`;
  }).join('');

  const bodyRows = rows.map(row => {
    const isCruise = row.altitude_ft === cruise_altitude_ft;
    const isBestBelow = row.altitude_ft === best_below_cruise;
    const isBestAbove = row.altitude_ft === best_above_cruise;

    let rowClass = '';
    if (isCruise) rowClass += ' alt-table-cruise';
    if (isBestBelow || isBestAbove) rowClass += ' alt-table-best';

    const altLabel = formatAlt(row.altitude_ft);
    const cruiseMarker = isCruise ? ' <span class="alt-table-cruise-marker">\u2190</span>' : '';
    const bestMarker = (isBestBelow || isBestAbove) ? ' <span class="alt-table-best-marker">\u2605</span>' : '';

    const statusCells = advisory_ids.map(id => {
      const status = row.statuses[id] || 'unavailable';
      return `<td><span class="badge ${statusBadgeClass(status)}">${statusLabel(status)}</span></td>`;
    }).join('');

    return `<tr class="${rowClass}">
      <td class="alt-table-alt">${altLabel}${cruiseMarker}${bestMarker}</td>
      ${statusCells}
      <td class="alt-table-score">${row.red_count}R ${row.amber_count}A</td>
    </tr>`;
  }).join('');

  return `
    <div class="popup-header"><h3>${t('advisories.altTableTitle')}</h3></div>
    <p class="muted" style="margin:0 0 0.5rem;font-size:0.8rem;">
      ${t('advisories.altTableDesc', { ceiling: String(Math.round(result.flight_ceiling_ft / 100)), step: String(result.step_ft) })}
      <span class="alt-table-cruise-marker">\u2190</span> ${t('advisories.altTableCruise')},
      <span class="alt-table-best-marker">\u2605</span> ${t('advisories.altTableBest')}.
    </p>
    <div class="alt-table-scroll">
      <table class="alt-table">
        <thead>
          <tr>
            <th class="alt-table-alt-header">${t('advisories.altTableAlt')}</th>
            ${headerCells}
            <th>${t('advisories.altTableScore')}</th>
          </tr>
        </thead>
        <tbody>${bodyRows}</tbody>
      </table>
    </div>
  `;
}

export interface AltitudeOverrideConfig {
  currentAlt: number;    // current slider value or flight default
  defaultAlt: number;    // flight's cruise_altitude_ft
  ceilingFt: number;     // flight_ceiling_ft for slider max
  onChange: (alt: number) => void;
}

export interface AltTimeToggleConfig {
  primaryLabel: string;   // e.g. "09:00Z"
  altLabel: string;       // e.g. "15:00Z"
  showingAlt: boolean;
  onToggle: () => void;
}

export interface ProfileSelectorConfig {
  profiles: { id: number; name: string }[];
  currentProfileId: number | null;
  isOwner: boolean;
  onChange: (profileId: number) => void;
}

/**
 * Render the advisory dashboard into the #advisories-section element.
 * onRecalculate callback wires up the recalculate button.
 */
export function renderAdvisories(
  manifest: RouteAdvisoriesManifest | null,
  onRecalculate?: () => void,
  displayMode: DisplayMode = 'full',
  altitudeOverride?: AltitudeOverrideConfig,
  onAltitudeTable?: () => Promise<void>,
  altTimeToggle?: AltTimeToggleConfig,
  profileSelector?: ProfileSelectorConfig,
): void {
  const el = $('advisories-section');
  const section = $('advisories-wrapper');
  if (!el) return;

  if (!manifest || manifest.advisories.length === 0) {
    el.innerHTML = `<p class="muted">${t('advisories.noAdvisories')}</p>`;
    if (section) section.style.display = 'none';
    return;
  }

  if (section) section.style.display = '';

  // Build catalog lookup
  const catalog = new Map<string, AdvisoryCatalogEntry>();
  for (const entry of manifest.catalog) {
    catalog.set(entry.id, entry);
  }

  // In compact mode, filter out secondary advisories (e.g. model confidence)
  const advisories = displayMode === 'compact'
    ? manifest.advisories.filter(adv => {
        const entry = catalog.get(adv.advisory_id);
        return !entry || !COMPACT_HIDDEN_CATEGORIES.has(entry.category);
      })
    : manifest.advisories;

  // Sort: RED first, then AMBER, then GREEN, then UNAVAILABLE
  const sorted = [...advisories].sort((a, b) => {
    return STATUS_ORDER.indexOf(a.aggregate_status) - STATUS_ORDER.indexOf(b.aggregate_status);
  });

  // Count by status for summary
  const counts = { green: 0, amber: 0, red: 0, unavailable: 0 };
  for (const adv of sorted) {
    counts[adv.aggregate_status]++;
  }

  const summaryParts: string[] = [];
  if (counts.red > 0) summaryParts.push(`<span class="badge badge-red">${counts.red} RED</span>`);
  if (counts.amber > 0) summaryParts.push(`<span class="badge badge-amber">${counts.amber} AMBER</span>`);
  if (counts.green > 0) summaryParts.push(`<span class="badge badge-green">${counts.green} GREEN</span>`);

  const summary = summaryParts.length > 0
    ? `<div class="advisory-summary">${summaryParts.join(' ')}</div>`
    : '';

  const recalcBtn = onRecalculate
    ? `<button class="btn btn-secondary btn-sm" id="recalc-advisories-btn">${t('advisories.recalculate')}</button>`
    : '';

  const altTableBtn = onAltitudeTable
    ? `<button class="btn btn-secondary btn-sm" id="alt-table-btn">${t('advisories.altitudeTable')}</button>`
    : '';

  // Profile selector dropdown
  let profileHtml = '';
  if (profileSelector && profileSelector.isOwner && profileSelector.profiles.length > 0) {
    const options = profileSelector.profiles.map(p => {
      const selected = p.id === profileSelector.currentProfileId ? ' selected' : '';
      return `<option value="${p.id}"${selected}>${escapeHtml(p.name)}</option>`;
    }).join('');
    profileHtml = `
      <div class="advisory-profile-selector">
        <label class="profile-selector-label">${t('advisories.profile')}</label>
        <select id="advisory-profile-select" class="input-select input-select-sm">${options}</select>
      </div>`;
  }

  // Altitude slider
  let sliderHtml = '';
  if (altitudeOverride) {
    const { currentAlt, defaultAlt, ceilingFt } = altitudeOverride;
    const isOverridden = currentAlt !== defaultAlt;
    const labelClass = isOverridden ? 'alt-label-overridden' : '';
    const resetBtn = isOverridden
      ? `<button class="btn btn-sm alt-reset-btn" id="advisory-alt-reset" title="${t('advisories.resetTitle')}">${t('advisories.reset')}</button>`
      : '';
    sliderHtml = `
      <div class="advisory-altitude-slider">
        <label class="alt-slider-label ${labelClass}" id="advisory-alt-label">${formatAlt(currentAlt)}</label>
        <input type="range" id="advisory-alt-slider" min="2000" max="${ceilingFt}" step="1000" value="${currentAlt}">
        ${resetBtn}
      </div>`;
  }

  // Airport conditions cards (above advisory grid)
  const aggregation = manifest.aggregation ?? 'majority';
  const airportHtml = manifest.airport_conditions
    ? renderAirportConditions(manifest.airport_conditions, aggregation)
    : '';

  // Alt time toggle
  let toggleHtml = '';
  if (altTimeToggle) {
    const primaryActive = !altTimeToggle.showingAlt ? ' active' : '';
    const altActive = altTimeToggle.showingAlt ? ' active' : '';
    toggleHtml = `
      <div class="advisory-time-toggle">
        <button class="btn btn-sm btn-toggle${primaryActive}" data-alt-toggle="primary">${escapeHtml(altTimeToggle.primaryLabel)}</button>
        <button class="btn btn-sm btn-toggle${altActive}" data-alt-toggle="alt">Alt ${escapeHtml(altTimeToggle.altLabel)}</button>
      </div>`;
  }

  const cards = sorted.map(adv => renderAdvisoryCard(adv, catalog)).join('');

  el.innerHTML = `
    ${toggleHtml}
    <div class="advisory-toolbar">
      ${summary}
      ${profileHtml}
      ${sliderHtml}
      ${recalcBtn}
      ${altTableBtn}
    </div>
    ${airportHtml}
    <div class="advisory-grid">${cards}</div>
  `;

  // Wire profile selector
  if (profileSelector && profileSelector.isOwner) {
    const selectEl = document.getElementById('advisory-profile-select') as HTMLSelectElement | null;
    if (selectEl) {
      selectEl.addEventListener('change', () => {
        const newId = parseInt(selectEl.value, 10);
        if (!isNaN(newId)) {
          selectEl.setAttribute('disabled', 'true');
          profileSelector.onChange(newId);
        }
      });
    }
  }

  // Wire recalculate button
  if (onRecalculate) {
    const btn = $('recalc-advisories-btn');
    if (btn) {
      btn.addEventListener('click', () => {
        btn.setAttribute('disabled', 'true');
        btn.textContent = t('advisories.recalculating');
        onRecalculate();
      });
    }
  }

  // Wire altitude table button
  if (onAltitudeTable) {
    const altBtn = $('alt-table-btn');
    if (altBtn) {
      altBtn.addEventListener('click', async () => {
        altBtn.setAttribute('disabled', 'true');
        altBtn.textContent = t('advisories.loading');
        try {
          await onAltitudeTable();
        } finally {
          altBtn.removeAttribute('disabled');
          altBtn.textContent = t('advisories.altitudeTable');
        }
      });
    }
  }

  // Wire altitude slider
  if (altitudeOverride) {
    const slider = document.getElementById('advisory-alt-slider') as HTMLInputElement | null;
    const label = document.getElementById('advisory-alt-label');
    const resetBtn = document.getElementById('advisory-alt-reset');
    if (slider) {
      slider.addEventListener('input', () => {
        const val = parseInt(slider.value, 10);
        if (label) {
          label.textContent = formatAlt(val);
          label.classList.toggle('alt-label-overridden', val !== altitudeOverride.defaultAlt);
        }
        altitudeOverride.onChange(val);
      });
    }
    if (resetBtn) {
      resetBtn.addEventListener('click', () => {
        altitudeOverride.onChange(altitudeOverride.defaultAlt);
      });
    }
  }

  // Wire alt time toggle
  if (altTimeToggle) {
    el.querySelectorAll('[data-alt-toggle]').forEach((btn) => {
      btn.addEventListener('click', () => altTimeToggle.onToggle());
    });
  }

  // Wire advisory info buttons (event delegation)
  el.addEventListener('click', (e) => {
    const btn = (e.target as HTMLElement).closest('.advisory-info-btn') as HTMLElement | null;
    if (!btn) return;
    const advId = btn.dataset.advisoryId;
    if (!advId) return;
    const entry = catalog.get(advId);
    if (!entry) return;
    const adv = manifest.advisories.find(a => a.advisory_id === advId);
    showPopupContent(renderAdvisoryPopup(entry, adv?.parameters_used ?? {}));
  });

  // Wire runway info popups (event delegation)
  if (manifest.airport_conditions) {
    const ac = manifest.airport_conditions;
    el.addEventListener('click', (e) => {
      const cell = (e.target as HTMLElement).closest('.airport-rwy-cell') as HTMLElement | null;
      if (!cell) return;
      const model = cell.dataset.model;
      if (!model) return;

      // Find which airport card this is in
      const card = cell.closest('.airport-card');
      if (!card) return;
      const header = card.querySelector('.airport-card-header')?.textContent || '';
      const isDep = header.startsWith(t('advisories.departure'));
      const summary = isDep ? ac.departure : ac.arrival;
      const cond = summary.conditions.find(c => c.model === model);
      if (cond && cond.all_runways.length > 0) {
        showPopupContent(formatRunwayPopup(cond.all_runways));
      }
    });
  }
}
