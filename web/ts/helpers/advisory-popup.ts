/** Shared advisory info popup renderer — used in both briefing and settings pages. */

import type { AdvisoryCatalogEntry, AdvisoryParameterDef } from '../types/advisories';
import { escapeHtml } from '../utils';
import { t } from '../i18n/i18n';

/**
 * Render advisory info popup HTML content.
 * @param entry - Advisory catalog entry with full metadata
 * @param paramsUsed - Parameter values to display (user-configured or defaults)
 */
export function renderAdvisoryPopup(entry: AdvisoryCatalogEntry, paramsUsed: Record<string, number>): string {
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

  return `
    <div class="popup-header"><h3>${escapeHtml(entry.name)}</h3></div>
    <p class="advisory-popup-category">${escapeHtml(entry.category)}</p>
    <p style="margin: 0.75rem 0;">${escapeHtml(entry.description)}</p>
    ${paramsHtml}
  `;
}
