/** Read-only summary card for an existing debrief. */

import type { ConditionTagId, DebriefResponse, OutcomeValue } from '../store/types';
import { OUTCOME_LABELS, TAG_LABELS } from './debrief-taxonomy';
import { escapeHtml } from '../utils';

export function renderDebriefSummary(d: DebriefResponse): string {
  let decisionPill: string;
  if (d.decision === 'cancelled') {
    decisionPill = `<span class="debrief-pill debrief-pill-cancelled">✕ Cancelled</span>`;
  } else if (d.decision === 'monitoring') {
    decisionPill = `<span class="debrief-pill debrief-pill-monitoring">👁 Monitor only</span>`;
  } else {
    decisionPill = `<span class="debrief-pill debrief-pill-flown">✓ Flown</span>`;
  }

  let body = '';
  if (d.decision === 'monitoring') {
    body = `<div class="debrief-summary-empty">Watching weather only — not a real go/no-go decision.</div>`;
  } else if (d.decision === 'cancelled') {
    if (d.reasons.length === 0) {
      body = `<div class="debrief-summary-empty">No reason recorded.</div>`;
    } else {
      const tags = d.reasons.map((t) =>
        `<span class="debrief-tag" title="${escapeHtml(TAG_LABELS[t])}">${escapeHtml(t)}</span>`,
      ).join('');
      body = `<div class="debrief-summary-tags">${tags}</div>`;
    }
  } else {
    // Flown — show categories where the outcome is NOT consistent (the
    // signal). If everything is consistent, show a single confirmation line.
    const flagged = Object.entries(d.outcomes).filter(([, v]) => v !== 'consistent');
    if (flagged.length === 0) {
      const queriedCount = Object.keys(d.outcomes).length;
      body = queriedCount
        ? `<div class="debrief-summary-empty">All ${queriedCount} flagged ${queriedCount === 1 ? 'category' : 'categories'} as forecast.</div>`
        : `<div class="debrief-summary-empty">No advisories were graded.</div>`;
    } else {
      const items = flagged.map(([cat, val]) => {
        const v = val as OutcomeValue;
        const arrow = v === 'better' ? '↓' : '↑';
        return `<span class="debrief-outcome-tag debrief-outcome-${v}" title="${escapeHtml(OUTCOME_LABELS[v])}">${arrow} ${escapeHtml(TAG_LABELS[cat as ConditionTagId])}</span>`;
      }).join('');
      body = `<div class="debrief-summary-tags">${items}</div>`;
    }
  }

  const note = d.note
    ? `<div class="debrief-summary-note">“${escapeHtml(d.note)}”</div>`
    : '';

  return `
    <div class="debrief-summary">
      <div class="debrief-summary-header">${decisionPill}</div>
      ${body}
      ${note}
    </div>
  `;
}

/** Compact one-line pill for use in list rows. */
export function renderDebriefPill(d: DebriefResponse | null | undefined): string {
  if (!d) return `<span class="debrief-pill debrief-pill-pending" title="No debrief recorded yet">◼ Debrief</span>`;
  if (d.decision === 'monitoring') {
    return `<span class="debrief-pill debrief-pill-monitoring" title="Monitor only — not a real flight">👁</span>`;
  }
  if (d.decision === 'cancelled') {
    const reasons = d.reasons.length ? ` ${d.reasons.join(' · ')}` : '';
    return `<span class="debrief-pill debrief-pill-cancelled">✕${escapeHtml(reasons)}</span>`;
  }
  // Flown — surface "worse" / "better" if any, else green check.
  const worseCount = Object.values(d.outcomes).filter((v) => v === 'worse').length;
  const betterCount = Object.values(d.outcomes).filter((v) => v === 'better').length;
  if (worseCount > 0) {
    return `<span class="debrief-pill debrief-pill-worse">↑ Worse · ${worseCount}</span>`;
  }
  if (betterCount > 0) {
    return `<span class="debrief-pill debrief-pill-better">↓ Better · ${betterCount}</span>`;
  }
  return `<span class="debrief-pill debrief-pill-flown">✓ As forecast</span>`;
}
