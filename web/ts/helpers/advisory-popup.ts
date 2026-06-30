/** Shared advisory info popup renderer — used in both briefing and settings pages. */

import type { AdvisoryCatalogEntry, AdvisoryParameterDef, Mitigation, RouteAdvisoryResult } from '../types/advisories';
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
         `<li>${escapeHtml(modelLabel(m.model))}: ${escapeHtml(m.cross_check!)}</li>`
       ).join('')}</ul>`
    : '';

  // Mitigations (#330): advice-only tips that never change the grade. Rendered in
  // neutral "tip" chrome (blue-gray, lightbulb) — deliberately distinct from the
  // diagnostic cross_check (info-circle). Shared with the standalone lightbulb
  // popup (`renderMitigationPopup`) so both surfaces stay identical.
  const mitigationHtml = renderMitigationBlock(adv?.aggregate_mitigations);

  return `
    <div class="popup-header"><h3>${escapeHtml(entry.name)}</h3></div>
    <p class="advisory-popup-category">${escapeHtml(entry.category)}</p>
    <p style="margin: 0.75rem 0;">${escapeHtml(entry.description)}</p>
    ${crossCheckHtml}
    ${mitigationHtml}
    ${paramsHtml}
  `;
}

/**
 * Render the neutral "Options to improve" tip block for a list of mitigations,
 * or '' when the list is empty. Shared across every surface that shows
 * mitigations — the aggregate (i) popup, the standalone lightbulb popup, and the
 * per-model badge popup — so they never drift.
 *
 * `detail` is already localized; `addresses` is a machine tag, never displayed.
 */
export function renderMitigationBlock(mitigations?: Mitigation[]): string {
  if (!mitigations || mitigations.length === 0) return '';
  return `<div class="advisory-mitigation" role="note">
       <p class="advisory-mitigation-title"><span class="advisory-mitigation-icon" aria-hidden="true">\u{1F4A1}</span>${escapeHtml(t('advisories.mitigationTitle'))}</p>
       <ul class="advisory-mitigation-list">${mitigations.map((m) =>
         `<li>${escapeHtml(m.detail)}</li>`
       ).join('')}</ul>
     </div>`;
}

/**
 * Popup shown when the card's mitigation lightbulb is tapped: the advisory name
 * plus the "Options to improve" list. Focused on the advice — distinct from the
 * full (i) popup, which also carries the description, cross-checks, and params.
 */
export function renderMitigationPopup(
  entry: AdvisoryCatalogEntry,
  adv: RouteAdvisoryResult,
): string {
  return `
    <div class="popup-header"><h3>${escapeHtml(entry.name)}</h3></div>
    ${renderMitigationBlock(adv.aggregate_mitigations)}
  `;
}
