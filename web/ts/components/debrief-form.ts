/** Debrief form — renders a hybrid chips + text input + per-category outcomes.
 *
 * One self-contained component; renders into a host element and reports
 * outcomes back via the supplied callbacks. Intentionally framework-free
 * (this codebase has no React/Vue) — the surface is straightforward
 * imperative DOM building.
 */

import type {
  ConditionTagId,
  DebriefDecision,
  DebriefResponse,
  OutcomeValue,
} from '../store/types';
import {
  ALL_TAGS,
  OUTCOME_CATEGORIES,
  TAG_DESCRIPTIONS,
  TAG_LABELS,
  matchTagsInText,
} from './debrief-taxonomy';
import { saveDebrief, deleteDebrief, type DebriefRequest } from '../adapters/debrief-adapter';
import { escapeHtml } from '../utils';
import { showPopupContent } from './info-popup';
import { t } from '../i18n/i18n';

const NOTE_MAX_LEN = 300;

export interface DebriefFormOptions {
  /** Existing debrief to seed the form (edit mode), or null for create. */
  existing: DebriefResponse | null;
  /** Categories the briefing flagged at the time — controls which outcome rows render.
   *  Empty array → outcome section shows "All consistent" only. */
  flaggedCategories: ConditionTagId[];
  /** Called after a successful save with the saved debrief. */
  onSaved: (d: DebriefResponse) => void;
  /** Called after a successful delete. */
  onDeleted?: () => void;
  /** Called when the user dismisses without saving. */
  onCancelled?: () => void;
}

interface FormState {
  decision: DebriefDecision;
  reasons: Set<ConditionTagId>;
  outcomes: Map<ConditionTagId, OutcomeValue>;
  note: string;
  flagged: ConditionTagId[];
}

/** Mount the form into ``host``. Returns a cleanup function. */
export function renderDebriefForm(host: HTMLElement, opts: DebriefFormOptions): () => void {
  const state: FormState = initialState(opts);

  const root = document.createElement('div');
  root.className = 'debrief-form';
  host.innerHTML = '';
  host.appendChild(root);

  const render = () => {
    root.innerHTML = template(state, opts);
    wireEvents(root, state, opts, render);
  };
  render();

  return () => { host.innerHTML = ''; };
}

function initialState(opts: DebriefFormOptions): FormState {
  const existing = opts.existing;
  const decision: DebriefDecision = existing?.decision ?? 'flown';
  const reasons = new Set<ConditionTagId>(existing?.reasons ?? []);
  const outcomes = new Map<ConditionTagId, OutcomeValue>();
  if (existing?.outcomes) {
    for (const [k, v] of Object.entries(existing.outcomes)) {
      if (v) outcomes.set(k as ConditionTagId, v);
    }
  }
  // For categories the briefing flagged but the pilot hasn't recorded yet,
  // default to consistent so the form's "exception only" flow is honest.
  // (When editing an old debrief, we trust whatever was stored — including
  // explicit consistents and any categories the briefing no longer flags.)
  const flagged = Array.from(new Set([...opts.flaggedCategories, ...outcomes.keys()]));
  for (const cat of opts.flaggedCategories) {
    if (!outcomes.has(cat)) outcomes.set(cat, 'consistent');
  }
  return {
    decision,
    reasons,
    outcomes,
    note: existing?.note ?? '',
    flagged,
  };
}

function decisionBody(s: FormState): string {
  switch (s.decision) {
    case 'cancelled': return cancelledBody(s);
    case 'flown':     return flownBody(s);
    case 'monitoring': return monitoringBody();
  }
}

function monitoringBody(): string {
  return `
    <div class="debrief-section">
      <div class="debrief-section-label">Monitor only</div>
      <div class="debrief-empty">This flight was set up to watch the weather, not to be flown. It won't appear in the recent-debrief nudge or count toward flown/cancelled stats. Add a note if you want to remember why.</div>
    </div>
  `;
}

function template(s: FormState, opts: DebriefFormOptions): string {
  const infoLabel = escapeHtml(t('debrief.infoBtn'));
  const decisionToggle = `
    <div class="debrief-decision">
      <button type="button" class="debrief-decision-btn ${s.decision === 'flown' ? 'active' : ''}" data-decision="flown">✓ Flown</button>
      <button type="button" class="debrief-decision-btn ${s.decision === 'cancelled' ? 'active' : ''}" data-decision="cancelled">✕ Cancelled</button>
      <button type="button" class="debrief-decision-btn ${s.decision === 'monitoring' ? 'active' : ''}" data-decision="monitoring">👁 Monitor only</button>
      <button type="button" class="metric-info-btn debrief-info-btn" title="${infoLabel}" aria-label="${infoLabel}">i</button>
    </div>
  `;

  const body = decisionBody(s);

  const noteCount = s.note.length;
  const noteRemaining = NOTE_MAX_LEN - noteCount;
  const noteTooLong = noteRemaining < 0;

  return `
    ${decisionToggle}
    ${body}
    <label class="debrief-note-label">
      <span>Note (optional)</span>
      <textarea class="debrief-note" rows="2" maxlength="${NOTE_MAX_LEN + 50}"
        placeholder="What happened? Mention any condition by name and we'll flag the tag.">${escapeHtml(s.note)}</textarea>
      <span class="debrief-note-count ${noteTooLong ? 'over' : ''}">${noteRemaining} chars left</span>
    </label>
    <div class="debrief-actions">
      <button type="button" class="btn-secondary debrief-cancel">Cancel</button>
      ${opts.existing ? `<button type="button" class="btn-secondary debrief-delete">Delete debrief</button>` : ''}
      <button type="button" class="btn-primary debrief-save" ${noteTooLong ? 'disabled' : ''}>Save</button>
    </div>
  `;
}

function infoCardContent(decision: DebriefDecision): string {
  if (decision === 'cancelled') {
    return `
      <h2>About this debrief — Cancelled</h2>
      <p>You're recording <strong>why</strong> you decided not to fly. This becomes a calibration sample we can use to tune the system's advisories — your "no-go" calls are the most valuable signal we collect.</p>
      <h3>Two ways to enter</h3>
      <ul>
        <li><strong>Tap the chips</strong> for the conditions that drove the decision (multi-select — pick all that apply).</li>
        <li><strong>Or type a note</strong>. As you type, words like <em>icing</em>, <em>gusty</em>, <em>fog</em> auto-select the matching chip — you can always tap to remove one if it's not what you meant.</li>
        <li>Use both if you want — chips are the structured signal, the note is your free-form context.</li>
      </ul>
      <h3>Tags</h3>
      <p><strong>OPS</strong> = non-weather (aircraft, NOTAM, fuel, personal). It's still recorded but excluded from per-category accuracy stats.</p>
      <p>Everything else (IMC, ICE, WIND, TS, TURB, FRZ, VIS) maps to a forecast category we can grade.</p>
    `;
  }
  if (decision === 'monitoring') {
    return `
      <h2>About this debrief — Monitor only</h2>
      <p>You set this flight up to <strong>watch the weather</strong>, not to actually fly it — research, what-if planning, or keeping an eye on a route you might fly later.</p>
      <p>Marking it <em>Monitor only</em> means:</p>
      <ul>
        <li>It <strong>disappears from the "please debrief" nudge</strong> — no pressure to record an outcome.</li>
        <li>It does <strong>not</strong> count toward your <em>flown</em> or <em>cancelled</em> stats. It also doesn't bias the per-category accuracy data — there was no real go/no-go decision to grade.</li>
        <li>It <em>does</em> show in your "Monitor only" tally so you can see how many briefings were research vs. real flights.</li>
      </ul>
      <p>The note is optional — handy for remembering why you set this one up.</p>
    `;
  }
  return `
    <h2>About this debrief — Flown</h2>
    <p>You're telling us <strong>how the forecast held up</strong> against what you actually flew. This is the calibration data — for every advisory the system raised, did reality match?</p>
    <h3>What you'll see</h3>
    <ul>
      <li>One row per <strong>advisory category that was flagged</strong> on this briefing (icing, turbulence, etc.). Categories the system didn't flag aren't shown — there's nothing to grade.</li>
      <li>Each row defaults to <strong>As forecast</strong>. Only flip the ones that were actually different.</li>
      <li><strong>↓ Better</strong> = the system was too pessimistic (forecast worse than reality).</li>
      <li><strong>↑ Worse</strong> = the system was too optimistic (forecast better than reality — the dangerous miss).</li>
    </ul>
    <p>Add a free-form note for anything not covered — surprises, deviations, gotchas the categories don't fit.</p>
    <p class="muted"><em>If no rows appear, the briefing didn't flag anything for this flight (all green) — just leave a note if anything stood out.</em></p>
  `;
}

function cancelledBody(s: FormState): string {
  const chips = ALL_TAGS.map((t) => {
    const active = s.reasons.has(t);
    return `<button type="button" class="debrief-chip ${active ? 'active' : ''}" data-tag="${t}" title="${escapeHtml(TAG_DESCRIPTIONS[t])}">${escapeHtml(TAG_LABELS[t])}</button>`;
  }).join('');
  return `
    <div class="debrief-section">
      <div class="debrief-section-label">Why cancelled? (tap all that apply)</div>
      <div class="debrief-chips">${chips}</div>
    </div>
  `;
}

function flownBody(s: FormState): string {
  if (s.flagged.length === 0) {
    return `
      <div class="debrief-section">
        <div class="debrief-section-label">Outcome</div>
        <div class="debrief-empty">No advisories were raised on this flight — nothing to grade. Add a note if anything stood out.</div>
      </div>
    `;
  }
  const rows = s.flagged.map((cat) => {
    const value = s.outcomes.get(cat) ?? 'consistent';
    const buttons = (['consistent', 'better', 'worse'] as OutcomeValue[]).map((v) => {
      const cls = `debrief-outcome-btn outcome-${v} ${value === v ? 'active' : ''}`;
      const label = v === 'consistent' ? '✓ As forecast' : v === 'better' ? '↓ Better' : '↑ Worse';
      return `<button type="button" class="${cls}" data-cat="${cat}" data-value="${v}">${label}</button>`;
    }).join('');
    return `
      <div class="debrief-outcome-row" data-cat="${cat}">
        <div class="debrief-outcome-cat">${escapeHtml(TAG_LABELS[cat])}</div>
        <div class="debrief-outcome-buttons">${buttons}</div>
      </div>
    `;
  }).join('');
  return `
    <div class="debrief-section">
      <div class="debrief-section-label">Outcome (only categories flagged by the briefing)</div>
      ${rows}
    </div>
  `;
}

function wireEvents(
  root: HTMLElement,
  s: FormState,
  opts: DebriefFormOptions,
  rerender: () => void,
): void {
  // Info button
  root.querySelector('.debrief-info-btn')?.addEventListener('click', () => {
    showPopupContent(infoCardContent(s.decision));
  });

  // Decision toggle
  root.querySelectorAll<HTMLButtonElement>('.debrief-decision-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const d = btn.dataset.decision as DebriefDecision;
      if (s.decision === d) return;
      s.decision = d;
      // Clear the inactive side's data so a wrong-shape submit can't happen.
      // Asymmetric on purpose: cancelled and monitoring both leave outcomes
      // empty, and we keep reasons across cancelled↔monitoring so the chips
      // round-trip if the user toggles between the two.
      if (d === 'flown') {
        s.reasons.clear();
      } else {
        s.outcomes.clear();
      }
      rerender();
    });
  });

  // Cancel-reason chips
  root.querySelectorAll<HTMLButtonElement>('.debrief-chip').forEach((chip) => {
    chip.addEventListener('click', () => {
      const tag = chip.dataset.tag as ConditionTagId;
      if (s.reasons.has(tag)) s.reasons.delete(tag);
      else s.reasons.add(tag);
      rerender();
    });
  });

  // Outcome buttons
  root.querySelectorAll<HTMLButtonElement>('.debrief-outcome-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      const cat = btn.dataset.cat as ConditionTagId;
      const value = btn.dataset.value as OutcomeValue;
      s.outcomes.set(cat, value);
      rerender();
    });
  });

  // Note input — auto-toggle matching chips on input.
  const noteEl = root.querySelector<HTMLTextAreaElement>('.debrief-note');
  if (noteEl) {
    noteEl.addEventListener('input', () => {
      s.note = noteEl.value;
      // Update char counter without re-render to keep cursor in place.
      const counter = root.querySelector<HTMLElement>('.debrief-note-count');
      if (counter) {
        const remaining = NOTE_MAX_LEN - s.note.length;
        counter.textContent = `${remaining} chars left`;
        counter.classList.toggle('over', remaining < 0);
      }
      // Auto-toggle chips on the cancel form.
      if (s.decision === 'cancelled') {
        const matched = matchTagsInText(s.note);
        let changed = false;
        for (const tag of matched) {
          if (!s.reasons.has(tag)) {
            s.reasons.add(tag);
            changed = true;
          }
        }
        if (changed) {
          // Lightweight chip refresh — only flip the .active class.
          root.querySelectorAll<HTMLButtonElement>('.debrief-chip').forEach((chip) => {
            const t = chip.dataset.tag as ConditionTagId;
            chip.classList.toggle('active', s.reasons.has(t));
          });
        }
      }
    });
  }

  // Cancel
  root.querySelector('.debrief-cancel')?.addEventListener('click', () => {
    opts.onCancelled?.();
  });

  // Delete
  root.querySelector('.debrief-delete')?.addEventListener('click', async () => {
    if (!opts.existing) return;
    if (!confirm('Delete this debrief? This cannot be undone.')) return;
    try {
      await deleteDebrief(opts.existing.flight_id);
      opts.onDeleted?.();
    } catch (e) {
      alert(`Could not delete: ${(e as Error).message}`);
    }
  });

  // Save
  root.querySelector('.debrief-save')?.addEventListener('click', async () => {
    const flightId = opts.existing?.flight_id ?? root.closest<HTMLElement>('[data-flight-id]')?.dataset.flightId;
    if (!flightId) {
      alert('Internal error: no flight id on container');
      return;
    }
    const body = buildRequest(s);
    try {
      const saved = await saveDebrief(flightId, body);
      opts.onSaved(saved);
    } catch (e) {
      alert(`Could not save: ${(e as Error).message}`);
    }
  });
}

function buildRequest(s: FormState): DebriefRequest {
  const note = s.note.trim() ? s.note.trim() : null;
  if (s.decision === 'cancelled') {
    return { decision: 'cancelled', reasons: Array.from(s.reasons), note };
  }
  if (s.decision === 'monitoring') {
    return { decision: 'monitoring', note };
  }
  // Flown — only emit outcomes for categories that were flagged.
  const outcomes: Partial<Record<ConditionTagId, OutcomeValue>> = {};
  for (const cat of s.flagged) {
    if (!OUTCOME_CATEGORIES.includes(cat)) continue;  // belt-and-braces
    outcomes[cat] = s.outcomes.get(cat) ?? 'consistent';
  }
  return { decision: 'flown', outcomes, note };
}
