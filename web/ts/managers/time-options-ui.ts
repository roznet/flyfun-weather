/** Timing-scenario panel renderer — a neutral soft hook surfacing calmer
 *  departure windows. Attention-director, never a verdict: no green/red framing
 *  on the hook itself, never auto-switches the plan. See
 *  `designs/plans/timing-scenario-plan.md`.
 *
 *  Suppression rule (against noise): the whole panel is hidden unless there is
 *  at least one *improving* candidate (not baseline, not just the pinned
 *  preferred time). Shown: the baseline reference row + the pinned preferred row
 *  (if present) + the improving windows, ranked best-first, capped at ~3. */

import type { TimeCandidate, TimeConfirmation, TimeWindowScan } from '../types/time-scan';
import { escapeHtml, formatDepartureTime } from '../utils';

/** Max improving windows to surface — decision D (cap ~3). */
const MAX_IMPROVING = 3;

export interface TimeOptionsCallbacks {
  /** "Check all models" for one candidate. Resolves once the store has merged
   *  the confirmation (which re-renders this panel). */
  onConfirm: (departureTime: string) => Promise<void>;
  /** Map an advisory id to its display name (reuses the advisory catalog). */
  resolveName: (advisoryId: string) => string;
}

// The delegated click handler is bound once per container; it reads the current
// callbacks from module scope so re-renders don't stack duplicate listeners.
let _onConfirm: TimeOptionsCallbacks['onConfirm'] | null = null;

/** True when a candidate is a genuinely better window (improves something and
 *  isn't just the baseline or the pinned preferred time). Drives suppression. */
function isImproving(c: TimeCandidate): boolean {
  return !c.is_baseline && !c.is_preferred && (c.improves.length > 0 || c.margin > 0);
}

function assessmentClass(assessment: string): string {
  switch (assessment.toUpperCase()) {
    case 'GREEN': return 'badge-green';
    case 'AMBER': return 'badge-amber';
    case 'RED': return 'badge-red';
    default: return 'badge-muted';
  }
}

/** Signed departure shift, e.g. "+1h", "−2h", "+1.5h"; empty for baseline. */
function shiftLabel(hours: number): string {
  if (!hours) return '';
  const abs = Math.abs(hours);
  const val = Number.isInteger(abs) ? String(abs) : abs.toFixed(1);
  return `${hours > 0 ? '+' : '−'}${val}h`;
}

/** "today" / "the day before" / "the day after" from the window's day_flex. */
function dayFlexLabel(dayFlex: string): string {
  if (dayFlex === 'prev') return 'the day before';
  if (dayFlex === 'next') return 'the day after';
  return 'today';
}

/** improves / worsens line, mapped to display names. Empty when neither side
 *  has anything. */
function diffLine(
  improves: string[],
  worsens: string[],
  resolveName: (id: string) => string,
): string {
  const parts: string[] = [];
  if (improves.length > 0) {
    parts.push(`<span class="time-option-improves">improves: ${escapeHtml(improves.map(resolveName).join(', '))}</span>`);
  }
  if (worsens.length > 0) {
    parts.push(`<span class="time-option-worsens">worsens: ${escapeHtml(worsens.map(resolveName).join(', '))}</span>`);
  }
  return parts.length ? `<div class="time-option-diff">${parts.join(' · ')}</div>` : '';
}

/** The confirmed multi-model outcome — including the on-brand downgrade case. */
function confirmedLine(
  confirmed: TimeConfirmation,
  resolveName: (id: string) => string,
): string {
  const label = confirmed.better_than_baseline
    ? 'All models checked: still better'
    : 'All models checked: actually not better';
  const cls = confirmed.better_than_baseline ? 'time-option-confirmed--better' : 'time-option-confirmed--downgrade';
  // Prefer the server's human phrasing; fall back to the mapped worsens list.
  const detail = confirmed.detail
    || (confirmed.worsens.length ? confirmed.worsens.map(resolveName).join(', ') : '');
  return `
    <div class="time-option-confirmed ${cls}">
      <span class="time-option-confirmed-label">${escapeHtml(label)}</span>
      ${detail ? `<span class="time-option-confirmed-detail">${escapeHtml(detail)}</span>` : ''}
    </div>`;
}

function renderRow(c: TimeCandidate, cb: TimeOptionsCallbacks): string {
  const time = formatDepartureTime(c.departure_time);
  const shift = shiftLabel(c.departure_shift_hours);

  let tag = '';
  if (c.is_baseline) tag = '<span class="time-option-tag">as planned</span>';
  else if (c.is_preferred) tag = '<span class="time-option-tag time-option-tag--preferred">★ your preferred time</span>';

  const assessment = c.confirmed?.assessment ?? c.ecmwf_assessment;
  const badge = `<span class="badge ${assessmentClass(assessment)}">${escapeHtml(String(assessment).toUpperCase())}</span>`;

  // improves/worsens: use the confirmed diff once available, else the ECMWF one.
  const improves = c.confirmed?.improves ?? c.improves;
  const worsens = c.confirmed?.worsens ?? c.worsens;
  const diff = c.is_baseline ? '' : diffLine(improves, worsens, cb.resolveName);

  // Honesty ladder: provisional (ECMWF-only) until confirmed. The baseline row
  // is never a "check all models" target — it's the reference, not a candidate.
  let ladder = '';
  if (!c.is_baseline) {
    if (c.confidence === 'confirmed' && c.confirmed) {
      ladder = confirmedLine(c.confirmed, cb.resolveName);
    } else {
      ladder = `
        <div class="time-option-provisional">
          <span class="time-option-provisional-label">ECMWF only</span>
          <button type="button" class="time-option-confirm-btn"
                  data-departure-time="${escapeHtml(c.departure_time)}">tap to check all models →</button>
        </div>`;
    }
  }

  return `
    <div class="time-option-row${c.is_baseline ? ' time-option-row--baseline' : ''}">
      <div class="time-option-head">
        <span class="time-option-time">${escapeHtml(time)}${shift ? ` <span class="time-option-shift">${escapeHtml(shift)}</span>` : ''}</span>
        ${badge}
        ${tag}
      </div>
      ${diff}
      ${ladder}
    </div>`;
}

/**
 * Render the timing-scenario panel into `container`. Suppresses (hides) the
 * container entirely when there is nothing better to show. Wires the per-row
 * "check all models" buttons via event delegation.
 */
export function renderTimeOptions(
  container: HTMLElement | null,
  timeOptions: TimeWindowScan | null,
  callbacks: TimeOptionsCallbacks,
): void {
  if (!container) return;
  _onConfirm = callbacks.onConfirm;

  const improving = (timeOptions?.candidates ?? []).filter(isImproving);
  // Suppress entirely unless at least one improving candidate cleared the margin.
  if (!timeOptions || improving.length === 0) {
    container.style.display = 'none';
    container.innerHTML = '';
    return;
  }
  container.style.display = '';

  const shown = improving.slice(0, MAX_IMPROVING);
  const baseline = timeOptions.candidates.find(c => c.is_baseline) ?? null;
  const preferred = timeOptions.candidates.find(c => c.is_preferred) ?? null;

  // Rows: baseline reference (context) → pinned preferred (if any) → improving.
  const rows: TimeCandidate[] = [];
  if (baseline) rows.push(baseline);
  if (preferred && preferred !== baseline) rows.push(preferred);
  rows.push(...shown);

  const n = improving.length;
  const header = `\u{1F550} ECMWF found ${n} better window${n === 1 ? '' : 's'} ${dayFlexLabel(timeOptions.window.day_flex)}`;

  const horizonNote = timeOptions.window.horizon_clipped
    ? `<p class="time-option-note">The search stops at the ECMWF forecast horizon.</p>`
    : '';

  container.innerHTML = `
    <div class="time-options-panel">
      <div class="time-options-header">${escapeHtml(header)}</div>
      <div class="time-options-rows">
        ${rows.map(c => renderRow(c, callbacks)).join('')}
      </div>
      ${horizonNote}
      <p class="time-option-footnote">A neutral heads-up, not a recommendation — your plan is unchanged.</p>
    </div>`;

  // Bind the delegated confirm handler once per container (re-renders replace
  // the innerHTML but keep the container, so a fresh listener each render would
  // stack). The handler reads the current onConfirm from module scope.
  if (!container.dataset.timeOptionsBound) {
    container.dataset.timeOptionsBound = '1';
    container.addEventListener('click', async (e) => {
      const btn = (e.target as HTMLElement).closest('.time-option-confirm-btn') as HTMLButtonElement | null;
      if (!btn || !_onConfirm) return;
      const dt = btn.dataset.departureTime;
      if (!dt) return;
      btn.disabled = true;
      btn.textContent = 'checking all models…';
      try {
        await _onConfirm(dt);
        // Success re-renders the panel (store change) — nothing to restore.
      } catch {
        btn.disabled = false;
        btn.textContent = 'tap to check all models →';
      }
    });
  }
}
