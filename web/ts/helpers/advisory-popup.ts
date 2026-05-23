/** Shared advisory info popup renderer — used in both briefing and settings pages. */

import type { AdvisoryCatalogEntry, AdvisoryParameterDef, RouteAdvisoryResult } from '../types/advisories';
import { escapeHtml, modelLabel } from '../utils';
import { t } from '../i18n/i18n';

/**
 * Render advisory info popup HTML content.
 * @param entry - Advisory catalog entry with full metadata
 * @param paramsUsed - Parameter values to display (user-configured or defaults)
 * @param adv - Optional evaluated result; supplies per-model cross-check notes.
 *   Omitted on the settings page (no evaluation yet) — that path is unaffected.
 */
export function renderAdvisoryPopup(
  entry: AdvisoryCatalogEntry,
  paramsUsed: Record<string, number>,
  adv?: RouteAdvisoryResult,
): string {
  const paramsHtml = entry.parameters.length > 0
    ? `<table class="advisory-params-table">
        <thead><tr><th>${t('advisories.parameter')}</th><th>${t('advisories.value')}</th><th>${t('advisories.description')}</th></tr></thead>
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

  const crossChecks = (adv?.per_model ?? []).filter((m) => m.cross_check);
  const crossCheckHtml = crossChecks.length > 0
    ? `<p class="advisory-popup-crosscheck-title">${escapeHtml(t('advisories.crossCheck'))}</p>
       <ul class="advisory-popup-crosscheck">${crossChecks.map((m) =>
         `<li>${escapeHtml(modelLabel(m.model))}: ${escapeHtml(m.cross_check as string)}</li>`
       ).join('')}</ul>`
    : '';

  return `
    <div class="popup-header"><h3>${escapeHtml(entry.name)}</h3></div>
    <p class="advisory-popup-category">${escapeHtml(entry.category)}</p>
    <p style="margin: 0.75rem 0;">${escapeHtml(entry.description)}</p>
    ${crossCheckHtml}
    ${paramsHtml}
  `;
}
