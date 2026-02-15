/** Advisory dashboard renderer — compact grid of advisory cards with per-model badges. */

import type { RouteAdvisoriesManifest, RouteAdvisoryResult, AdvisoryStatus, ModelAdvisoryResult, AdvisoryCatalogEntry } from '../types/advisories';
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
  // Capitalize model name
  return model.toUpperCase();
}

function renderAdvisoryCard(adv: RouteAdvisoryResult, catalog: Map<string, AdvisoryCatalogEntry>): string {
  const entry = catalog.get(adv.advisory_id);
  const name = entry ? escapeHtml(entry.name) : escapeHtml(adv.advisory_id);
  const desc = entry ? escapeHtml(entry.short_description) : '';

  // Per-model badges
  const modelBadges = adv.per_model.map((m: ModelAdvisoryResult) =>
    `<span class="adv-model-badge ${statusBadgeClass(m.status)}" title="${escapeHtml(m.detail)}">${modelLabel(m.model)}</span>`
  ).join(' ');

  // Aggregate badge
  const aggClass = statusBadgeClass(adv.aggregate_status);

  return `
    <div class="advisory-card advisory-${adv.aggregate_status}" data-advisory="${escapeHtml(adv.advisory_id)}">
      <div class="advisory-card-header">
        <span class="badge ${aggClass}">${statusLabel(adv.aggregate_status)}</span>
        <span class="advisory-name">${name}</span>
      </div>
      <div class="advisory-models">${modelBadges}</div>
      <div class="advisory-detail">${escapeHtml(adv.aggregate_detail)}</div>
      ${desc ? `<div class="advisory-desc">${desc}</div>` : ''}
    </div>
  `;
}

/**
 * Render the advisory dashboard into the #advisories-section element.
 */
export function renderAdvisories(manifest: RouteAdvisoriesManifest | null): void {
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

  const cards = sorted.map(adv => renderAdvisoryCard(adv, catalog)).join('');

  el.innerHTML = `
    ${summary}
    <div class="advisory-grid">${cards}</div>
  `;
}
