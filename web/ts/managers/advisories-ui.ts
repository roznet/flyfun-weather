/** Advisory dashboard renderer — compact grid of advisory cards with per-model badges. */

import type { RouteAdvisoriesManifest, RouteAdvisoryResult, AdvisoryStatus, ModelAdvisoryResult, AdvisoryCatalogEntry, AirportConditions, AirportConditionsSummary, AirportModelCondition, FlightCategory, RunwayWind, AltitudeTableResult } from '../types/advisories';
import type { DisplayMode } from '../types/metrics';
import { showPopupContent } from '../components/info-popup';
import { renderAdvisoryPopup } from '../helpers/advisory-popup';
import { renderFrontsInfo } from '../helpers/fronts-info';
import { advisoryBand, isCompactBand, compareAdvisories } from '../helpers/advisory-order';
import { formatAltitudeDeltaNote } from '../helpers/altitude-diff';
import { $, escapeHtml, formatAlt, modelLabel } from '../utils';
import { t } from '../i18n/i18n';
import { formatVisibility } from '../units';
import { ADVISORY_TO_PRESET } from '../visualization/cross-section/advisory-presets';

/** Live advisory catalog (names / descriptions / parameter defs) fetched from
 *  `/advisories/catalog`, preferred over the pack-baked copy so the (i) popups
 *  reflect current code rather than whatever was frozen into the pack at
 *  generation time. Set once by briefing-main; null until it arrives, then we
 *  fall back to the pack catalog. Grades/results stay pack-sourced. */
let liveCatalog: AdvisoryCatalogEntry[] | null = null;
export function setLiveAdvisoryCatalog(entries: AdvisoryCatalogEntry[]): void {
  liveCatalog = entries;
}

/** Visibility for an airport condition, region-aware.
 *  Prefers raw meters (visibility_m); falls back to legacy SM for old packs. */
function formatCondVis(cond: AirportModelCondition): string | null {
  // != null (not !==): visibility_m is optional, so this also catches `undefined`
  // from old packs serialized before the field existed, falling through to SM.
  if (cond.visibility_m != null) return formatVisibility(cond.visibility_m);
  if (cond.visibility_sm !== null) return `${cond.visibility_sm} SM`;
  return null;
}

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
  return `flight-cat-${cat.toLowerCase()}`;
}

/** Popup for a single per-model advisory badge: "ECMWF · Fronts" + status + detail. */
function formatModelDetailPopup(advName: string, m: ModelAdvisoryResult): string {
  return `
    <div class="popup-header"><h3>${escapeHtml(modelLabel(m.model))} &middot; ${escapeHtml(advName)}</h3></div>
    <p><span class="badge ${statusBadgeClass(m.status)}">${statusLabel(m.status)}</span></p>
    <p class="advisory-detail">${escapeHtml(m.detail)}</p>
  `;
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
  VFR: 0, MVFR: 1, IFR: 2, LIFR: 3,
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
  let worstVisM: number | null = null;
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
    if (c.visibility_m != null) {
      worstVisM = worstVisM === null ? c.visibility_m : Math.min(worstVisM, c.visibility_m);
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
    visibility_m: worstVisM,
    visibility_sm: worstVis,
    wind_speed_kt: worstWindSpd,
    wind_direction_deg: worstWindDir,
    wind_gust_kt: worstGust,
    best_runway: worstRwy,
    all_runways: allRwysCombined,
  };
}

function renderSummaryRow(cond: AirportModelCondition): string {
  const catLabel = cond.flight_category;
  const catClass = flightCatBadgeClass(cond.flight_category);
  const visStr = formatCondVis(cond);
  const vis = visStr ? `vis ${visStr}` : '';
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
  const catLabel = cond.flight_category;
  const catClass = flightCatBadgeClass(cond.flight_category);
  const vis = formatCondVis(cond) ?? 'N/A';
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


function renderAdvisoryCard(
  adv: RouteAdvisoryResult,
  catalog: Map<string, AdvisoryCatalogEntry>,
  chipsEnabled: boolean,
  compact = false,
): string {
  const entry = catalog.get(adv.advisory_id);
  const name = entry ? escapeHtml(entry.name) : escapeHtml(adv.advisory_id);
  const desc = entry ? escapeHtml(entry.short_description) : '';

  // Per-model badges are tappable (#fronts-clarity): native `title` only shows on
  // desktop hover and never on touch (the iOS app), so the per-model reason was
  // effectively invisible. A tap opens a popup with that model's detail; the
  // `title` stays as a desktop hover bonus. `--tappable` carries the cursor +
  // dotted-underline affordance hint.
  const modelBadges = adv.per_model.map((m: ModelAdvisoryResult) =>
    `<span class="adv-model-badge adv-model-badge--tappable ${statusBadgeClass(m.status)}"` +
    ` data-advisory-id="${escapeHtml(adv.advisory_id)}" data-model="${escapeHtml(m.model)}"` +
    ` role="button" tabindex="0" title="${escapeHtml(m.detail)}">${modelLabel(m.model)}</span>`
  ).join(' ');

  const aggClass = statusBadgeClass(adv.aggregate_status);
  const infoBtn = entry
    ? `<button class="metric-info-btn advisory-info-btn" data-advisory-id="${escapeHtml(adv.advisory_id)}" title="${t('advisories.advisoryInfo')}" aria-label="${t('advisories.advisoryInfo')}">i</button>`
    : '';

  // Cross-section preset chip (#219): only for advisories with a mapped preset.
  // Reuses .metric-info-btn styling; its own delegated handler fires onAdvisoryChip.
  const chip = chipsEnabled && ADVISORY_TO_PRESET[adv.advisory_id]
    ? `<button class="metric-info-btn advisory-view-btn" data-advisory-id="${escapeHtml(adv.advisory_id)}" title="${t('advisories.showOnCrossSection')}" aria-label="${t('advisories.showOnCrossSection')}">\u{1F4C8}</button>`
    : '';

  // Compact (two-line) variant for the all-clear band: header + the detail line,
  // dropping the per-model badge row and the description to save space.
  if (compact) {
    return `
    <div class="advisory-card advisory-card--compact advisory-${adv.aggregate_status}" data-advisory="${escapeHtml(adv.advisory_id)}">
      <div class="advisory-card-header">
        <span class="badge ${aggClass}">${statusLabel(adv.aggregate_status)}</span>
        <span class="advisory-name">${name}</span>
        ${chip}
        ${infoBtn}
      </div>
      <div class="advisory-detail">${escapeHtml(adv.aggregate_detail)}</div>
    </div>
  `;
  }

  return `
    <div class="advisory-card advisory-${adv.aggregate_status}" data-advisory="${escapeHtml(adv.advisory_id)}">
      <div class="advisory-card-header">
        <span class="badge ${aggClass}">${statusLabel(adv.aggregate_status)}</span>
        <span class="advisory-name">${name}</span>
        ${chip}
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
  onChange: (alt: number) => void;       // continuous (drag): cheap local update
  onProbe?: (alt: number) => void;       // release: table-indexed statuses + guarded wind overlay (#259)
  plannedAlt?: number;                   // digest's planned altitude, for the delta note
  table?: AltitudeTableResult | null;    // cached altitude table for instant indexing + delta note
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
  onAdvisoryChip?: (advisoryId: string) => void,
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

  // Build catalog lookup: pack copy first, then override by id with the live
  // catalog so descriptions / parameter defs reflect current code. Per-advisory
  // results (grades, params_used) come from `manifest.advisories`, untouched.
  const catalog = new Map<string, AdvisoryCatalogEntry>();
  for (const entry of manifest.catalog) {
    catalog.set(entry.id, entry);
  }
  if (liveCatalog) {
    for (const entry of liveCatalog) {
      catalog.set(entry.id, entry);
    }
  }

  // In compact mode, filter out secondary advisories (e.g. model confidence)
  const advisories = displayMode === 'compact'
    ? manifest.advisories.filter(adv => {
        const entry = catalog.get(adv.advisory_id);
        return !entry || !COMPACT_HIDDEN_CATEGORIES.has(entry.category);
      })
    : manifest.advisories;

  // Sort into actionability bands: RED → AMBER → green-mixed (a model dissents)
  // → green-clear (all models green) → UNAVAILABLE. green-mixed is ordered
  // most-severe-dissent first. See helpers/advisory-order.
  const sorted = [...advisories].sort(compareAdvisories);

  // Count by status for summary
  const counts = { green: 0, amber: 0, red: 0, unavailable: 0 };
  let mixedGreen = 0;
  for (const adv of sorted) {
    counts[adv.aggregate_status]++;
    if (advisoryBand(adv) === 'green-mixed') mixedGreen++;
  }

  const summaryParts: string[] = [];
  if (counts.red > 0) summaryParts.push(`<span class="badge badge-red">${counts.red} RED</span>`);
  if (counts.amber > 0) summaryParts.push(`<span class="badge badge-amber">${counts.amber} AMBER</span>`);
  if (counts.green > 0) {
    // Flag how many "green" cards actually hide model disagreement.
    const mixedNote = mixedGreen > 0 ? ` (${mixedGreen} mixed)` : '';
    summaryParts.push(`<span class="badge badge-green">${counts.green} GREEN${mixedNote}</span>`);
  }

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
    // Deterministic delta note vs the digest's planned altitude (#259).
    const plannedAlt = altitudeOverride.plannedAlt ?? defaultAlt;
    const deltaNote = (altitudeOverride.table && isOverridden)
      ? (formatAltitudeDeltaNote(altitudeOverride.table, currentAlt, plannedAlt) ?? '')
      : '';
    sliderHtml = `
      <div class="advisory-altitude-slider">
        <label class="alt-slider-label ${labelClass}" id="advisory-alt-label">${formatAlt(currentAlt)}</label>
        <input type="range" id="advisory-alt-slider" min="2000" max="${ceilingFt}" step="1000" value="${currentAlt}">
        ${resetBtn}
        <span class="alt-delta-note" id="advisory-alt-delta">${escapeHtml(deltaNote)}</span>
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

  // Full cards for the actionable bands (red/amber/green-mixed); the boring
  // bands (unanimous green, then unavailable) collapse into a compact pill grid.
  const fullCards: RouteAdvisoryResult[] = [];
  const compactCards: RouteAdvisoryResult[] = [];
  for (const adv of sorted) {
    (isCompactBand(advisoryBand(adv)) ? compactCards : fullCards).push(adv);
  }
  const cards = fullCards.map(adv => renderAdvisoryCard(adv, catalog, !!onAdvisoryChip)).join('');
  const compact = compactCards.length > 0
    ? `<div class="advisory-allclear">`
      + `<div class="advisory-allclear-label">${t('advisories.allClear')}</div>`
      + `<div class="advisory-grid advisory-grid--compact">`
      + compactCards.map(adv => renderAdvisoryCard(adv, catalog, !!onAdvisoryChip, true)).join('')
      + `</div></div>`
    : '';

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
    ${compact}
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
    const deltaEl = document.getElementById('advisory-alt-delta');
    const resetBtn = document.getElementById('advisory-alt-reset');
    const plannedAlt = altitudeOverride.plannedAlt ?? altitudeOverride.defaultAlt;
    // Live, deterministic delta note from the cached table — instant, no server.
    const updateDelta = (val: number): void => {
      if (!deltaEl) return;
      deltaEl.textContent = (altitudeOverride.table && val !== altitudeOverride.defaultAlt)
        ? (formatAltitudeDeltaNote(altitudeOverride.table, val, plannedAlt) ?? '')
        : '';
    };
    // The lever indexes the cached table client-side (#259); fall back to the
    // legacy full recalc only when no table is available (old packs).
    const onRelease = altitudeOverride.onProbe ?? (onRecalculate ? () => onRecalculate() : undefined);
    if (slider) {
      // `input` fires continuously during drag — keep this cheap (local state
      // + cross-section repaint). Cross-section cruise line follows live.
      slider.addEventListener('input', () => {
        const val = parseInt(slider.value, 10);
        if (label) {
          label.textContent = formatAlt(val);
          label.classList.toggle('alt-label-overridden', val !== altitudeOverride.defaultAlt);
        }
        updateDelta(val);
        altitudeOverride.onChange(val);
      });
      // `change` fires once on release — index the table for instant statuses
      // and kick the debounced, stale-guarded wind-overlay update.
      if (onRelease) {
        slider.addEventListener('change', () => onRelease(parseInt(slider.value, 10)));
      }
    }
    if (resetBtn) {
      resetBtn.addEventListener('click', () => {
        altitudeOverride.onChange(altitudeOverride.defaultAlt);
        updateDelta(altitudeOverride.defaultAlt);
        if (onRelease) onRelease(altitudeOverride.defaultAlt);
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
    let html = renderAdvisoryPopup(entry, adv?.parameters_used ?? {}, adv);
    // The Fronts advisory grades air-mass boundaries; cross-link to the full
    // detection write-up (the rich layer (i) content) rather than duplicating it.
    if (advId === 'fronts') {
      // English-only to match the linked content (fronts-info is English-only).
      html += `<p class="popup-section popup-learn-more">`
        + `You can read more on how they are computed and their impact `
        + `<a href="#" data-show-fronts-info>here →</a></p>`;
    }
    showPopupContent(html);
    if (advId === 'fronts') {
      const link = document.querySelector('[data-show-fronts-info]') as HTMLElement | null;
      link?.addEventListener('click', (ev) => {
        ev.preventDefault();
        showPopupContent(renderFrontsInfo());
      });
    }
  });

  // Wire per-model badge popups (event delegation). Tap/click — or Enter/Space
  // while focused — shows that model's detail, reliable on touch where the
  // native `title` tooltip never fires.
  const showModelDetail = (badge: HTMLElement): void => {
    const advId = badge.dataset.advisoryId;
    const model = badge.dataset.model;
    if (!advId || !model) return;
    const adv = manifest.advisories.find(a => a.advisory_id === advId);
    const m = adv?.per_model.find(pm => pm.model === model);
    if (!adv || !m) return;
    const advName = catalog.get(advId)?.name ?? advId;
    showPopupContent(formatModelDetailPopup(advName, m));
  };
  el.addEventListener('click', (e) => {
    const badge = (e.target as HTMLElement).closest('.adv-model-badge--tappable') as HTMLElement | null;
    if (badge) showModelDetail(badge);
  });

  el.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' && e.key !== ' ') return;
    const badge = (e.target as HTMLElement).closest('.adv-model-badge--tappable') as HTMLElement | null;
    if (!badge) return;
    e.preventDefault();
    showModelDetail(badge);
  });

  // Wire advisory cross-section chips (#219, event delegation). Configures the
  // cross-section for the advisory's preset and jumps to it.
  if (onAdvisoryChip) {
    el.addEventListener('click', (e) => {
      const btn = (e.target as HTMLElement).closest('.advisory-view-btn') as HTMLElement | null;
      if (!btn) return;
      const advId = btn.dataset.advisoryId;
      if (advId) onAdvisoryChip(advId);
    });
  }

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
