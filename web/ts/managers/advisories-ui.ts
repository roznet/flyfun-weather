/** Advisory dashboard renderer — compact grid of advisory cards with per-model badges. */

import type { RouteAdvisoriesManifest, RouteAdvisoryResult, AdvisoryStatus, ModelAdvisoryResult, AdvisoryCatalogEntry, AdvisoryParameterDef, AirportConditions, AirportConditionsSummary, AirportModelCondition, FlightCategory, RunwayWind } from '../types/advisories';
import { showPopupContent } from '../components/info-popup';
import { escapeHtml } from '../utils';

const $ = (id: string) => document.getElementById(id);

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

function modelLabel(model: string): string {
  return model.toUpperCase();
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
    <div class="popup-header"><h3>All Runways</h3></div>
    <table class="advisory-params-table">
      <thead><tr><th>Runway</th><th>Heading</th><th>Crosswind</th><th>Headwind</th></tr></thead>
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

function renderAirportCard(summary: AirportConditionsSummary, role: 'Departure' | 'Arrival'): string {
  if (summary.conditions.length === 0) return '';

  const rows = summary.conditions.map(renderConditionRow).join('');
  return `
    <div class="airport-card">
      <div class="airport-card-header">${role}: ${escapeHtml(summary.icao)}</div>
      <table class="airport-conditions-table">
        <tbody>${rows}</tbody>
      </table>
    </div>
  `;
}

function renderAirportConditions(conditions: AirportConditions): string {
  const dep = renderAirportCard(conditions.departure, 'Departure');
  const arr = renderAirportCard(conditions.arrival, 'Arrival');
  if (!dep && !arr) return '';
  return `<div class="airport-cards">${dep}${arr}</div>`;
}

function renderAdvisoryPopup(entry: AdvisoryCatalogEntry, paramsUsed: Record<string, number>): string {
  const paramsHtml = entry.parameters.length > 0
    ? `<table class="advisory-params-table">
        <thead><tr><th>Parameter</th><th>Value</th><th>Description</th></tr></thead>
        <tbody>${entry.parameters.map((p: AdvisoryParameterDef) => {
          const val = paramsUsed[p.key] ?? p.default;
          return `<tr>
            <td>${escapeHtml(p.label)}</td>
            <td><strong>${val}${p.unit ? ' ' + escapeHtml(p.unit) : ''}</strong></td>
            <td class="text-muted">${escapeHtml(p.description)}</td>
          </tr>`;
        }).join('')}</tbody>
      </table>`
    : '';

  return `
    <div class="popup-header"><h3>${escapeHtml(entry.name)}</h3></div>
    <p class="advisory-popup-category">${escapeHtml(entry.category)}</p>
    <p style="margin: 0.75rem 0;">${escapeHtml(entry.description)}</p>
    ${paramsHtml}
  `;
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
    ? `<button class="metric-info-btn advisory-info-btn" data-advisory-id="${escapeHtml(adv.advisory_id)}" title="Advisory info" aria-label="Advisory info">i</button>`
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

/**
 * Render the advisory dashboard into the #advisories-section element.
 * onRecalculate callback wires up the recalculate button.
 */
export function renderAdvisories(
  manifest: RouteAdvisoriesManifest | null,
  onRecalculate?: () => void,
): void {
  const el = $('advisories-section');
  const section = $('advisories-wrapper');
  if (!el) return;

  if (!manifest || manifest.advisories.length === 0) {
    el.innerHTML = '<p class="muted">No advisories available</p>';
    if (section) section.style.display = 'none';
    return;
  }

  if (section) section.style.display = '';

  // Build catalog lookup
  const catalog = new Map<string, AdvisoryCatalogEntry>();
  for (const entry of manifest.catalog) {
    catalog.set(entry.id, entry);
  }

  // Sort: RED first, then AMBER, then GREEN, then UNAVAILABLE
  const sorted = [...manifest.advisories].sort((a, b) => {
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
    ? '<button class="btn btn-secondary btn-sm" id="recalc-advisories-btn">Recalculate</button>'
    : '';

  // Airport conditions cards (above advisory grid)
  const airportHtml = manifest.airport_conditions
    ? renderAirportConditions(manifest.airport_conditions)
    : '';

  const cards = sorted.map(adv => renderAdvisoryCard(adv, catalog)).join('');

  el.innerHTML = `
    <div class="advisory-toolbar">
      ${summary}
      ${recalcBtn}
    </div>
    ${airportHtml}
    <div class="advisory-grid">${cards}</div>
  `;

  // Wire recalculate button
  if (onRecalculate) {
    const btn = $('recalc-advisories-btn');
    if (btn) {
      btn.addEventListener('click', () => {
        btn.setAttribute('disabled', 'true');
        btn.textContent = 'Recalculating...';
        onRecalculate();
      });
    }
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
      const isDep = header.startsWith('Departure');
      const summary = isDep ? ac.departure : ac.arrival;
      const cond = summary.conditions.find(c => c.model === model);
      if (cond && cond.all_runways.length > 0) {
        showPopupContent(formatRunwayPopup(cond.all_runways));
      }
    });
  }
}
