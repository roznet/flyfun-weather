/** The Debrief section (#1024) at the top of the briefing page's Observed
 *  group, after the live window.
 *
 *  Reading order: the server's headline, the flown / cancelled tap, then
 *  either the report's questions (one-tap chips, the "I changed level or
 *  route" toggle, a short note) or the cancel reasons, and the report's
 *  detail lines folded. The recap and tick replay follow it on the page.
 *
 *  Server-drawn (#1023): every word of the headline, lines, questions and
 *  option labels is the server's. The client only lays it out and sends the
 *  taps back; it never decides which questions to ask or what they mean.
 *
 *  `debriefSectionHtml` is pure (unit-tested); `mountDebriefSection` wires
 *  the taps to the API and re-renders from what the server returns.
 */

import type { ConditionTagId, DebriefReport, DebriefResponse } from '../store/types';
import { ALL_TAGS, TAG_LABELS } from './debrief-taxonomy';
import {
  fetchDebrief,
  fetchDebriefReport,
  saveDebrief,
  saveDebriefAnswers,
  type DebriefAnswerRequest,
} from '../adapters/debrief-adapter';
import { escapeHtml } from '../utils';
import { t } from '../i18n/i18n';

export const NOTE_MAX_LEN = 300;

export type SectionDecision = 'flown' | 'cancelled' | null;

export interface DebriefSectionState {
  report: DebriefReport | null;
  debrief: DebriefResponse | null;
  /** "I changed level or route" ticked before a chip was picked: local
   *  until the answer is sent with it. */
  pendingAvoided: Record<string, boolean>;
  /** The note as typed, kept across re-renders until saved. */
  draftNote?: string | null;
  busy: boolean;
  error: string | null;
}

/** The decision the section shows: the stored debrief's, else flown when
 *  any question has an answer (answering stores the flight as flown). A
 *  monitoring flight shows neither tapped. */
export function sectionDecision(state: Pick<DebriefSectionState, 'report' | 'debrief'>): SectionDecision {
  const d = state.debrief?.decision;
  if (d === 'flown' || d === 'cancelled') return d;
  if (state.report?.questions?.some((q) => q.answer)) return 'flown';
  return null;
}

function avoidedFor(state: DebriefSectionState, qid: string): boolean {
  const q = state.report?.questions.find((x) => x.id === qid);
  if (q?.answer) return q.answer.avoided;
  return !!state.pendingAvoided[qid];
}

function questionsHtml(state: DebriefSectionState): string {
  const questions = state.report?.questions ?? [];
  if (!questions.length) {
    return `<p class="debrief-empty" data-testid="debrief-no-questions">${escapeHtml(t('debriefSection.noQuestions'))}</p>`;
  }
  return questions.map((q) => {
    const chosen = q.answer?.answer ?? null;
    const chips = q.options.map((o) => {
      const active = o.id === chosen;
      return `<button type="button" class="debrief-chip${active ? ' active' : ''}"`
        + ` data-question="${escapeHtml(q.id)}" data-answer="${escapeHtml(o.id)}"`
        + ` aria-pressed="${active}" data-testid="debrief-answer">${escapeHtml(o.label)}</button>`;
    }).join('');
    const avoid = q.avoidable
      ? `<label class="debrief-avoided"><input type="checkbox" data-avoided="${escapeHtml(q.id)}"`
        + `${avoidedFor(state, q.id) ? ' checked' : ''} data-testid="debrief-avoided">`
        + ` ${escapeHtml(t('debriefSection.avoided'))}</label>`
      : '';
    return `<div class="debrief-question" data-kind="${escapeHtml(q.kind)}" data-testid="debrief-question">`
      + `<p class="debrief-question-text">${escapeHtml(q.text)}</p>`
      + `<div class="debrief-chips">${chips}</div>${avoid}</div>`;
  }).join('');
}

function reasonsHtml(state: DebriefSectionState): string {
  const chosen = new Set<ConditionTagId>(state.debrief?.decision === 'cancelled' ? state.debrief.reasons : []);
  const chips = ALL_TAGS.map((tag) => {
    const active = chosen.has(tag);
    return `<button type="button" class="debrief-chip${active ? ' active' : ''}" data-reason="${tag}"`
      + ` aria-pressed="${active}" data-testid="debrief-reason">${escapeHtml(TAG_LABELS[tag])}</button>`;
  }).join('');
  return `<p class="debrief-question-text">${escapeHtml(t('debriefSection.reasonsPrompt'))}</p>`
    + `<div class="debrief-chips">${chips}</div>`;
}

function noteHtml(state: DebriefSectionState): string {
  const note = state.draftNote ?? state.debrief?.note ?? '';
  return `<label class="debrief-note-label"><span>${escapeHtml(t('debriefSection.note'))}</span>`
    + `<textarea class="debrief-note" maxlength="${NOTE_MAX_LEN}" rows="2" data-testid="debrief-note">${escapeHtml(note)}</textarea></label>`
    + `<button type="button" class="btn btn-secondary debrief-note-save" data-testid="debrief-note-save">${escapeHtml(t('debriefSection.saveNote'))}</button>`;
}

function detailsHtml(report: DebriefReport): string {
  const lines = report.summary?.lines ?? [];
  if (!lines.length) return '';
  const items = lines.map((l) => `<li data-kind="${escapeHtml(l.kind)}">${escapeHtml(l.text)}</li>`).join('');
  return `<details class="debrief-details" data-testid="debrief-details">`
    + `<summary>${escapeHtml(t('debriefSection.details'))}</summary><ul>${items}</ul></details>`;
}

/** The whole section as HTML. Empty string when there is no report. */
export function debriefSectionHtml(state: DebriefSectionState): string {
  const report = state.report;
  if (!report) return '';
  const decision = sectionDecision(state);
  const headline = report.summary?.headline || report.message || '';
  const btn = (d: 'flown' | 'cancelled', key: string) =>
    `<button type="button" class="debrief-decision-btn${decision === d ? ' active' : ''}" data-decision="${d}"`
    + ` aria-pressed="${decision === d}" data-testid="debrief-${d}">${escapeHtml(t(key))}</button>`;
  let body = '';
  if (decision === 'flown') body = questionsHtml(state) + noteHtml(state);
  else if (decision === 'cancelled') body = reasonsHtml(state) + noteHtml(state);
  const error = state.error
    ? `<p class="debrief-error" role="alert" data-testid="debrief-error">${escapeHtml(state.error)}</p>` : '';
  return `<div class="debrief-section${state.busy ? ' busy' : ''}" data-testid="debrief-section">`
    + (headline ? `<p class="debrief-headline" data-testid="debrief-headline">${escapeHtml(headline)}</p>` : '')
    + `<div class="debrief-decision">${btn('flown', 'debriefSection.flown')}${btn('cancelled', 'debriefSection.cancelled')}</div>`
    + body + error + detailsHtml(report) + `</div>`;
}

export interface DebriefSectionHandle {
  teardown: () => void;
}

/** Mount the section into ``host`` for ``flightId``. ``onChanged`` runs
 *  after any save (the page reloads the recap and the flight). */
export function mountDebriefSection(
  host: HTMLElement,
  flightId: string,
  initial: { report: DebriefReport; debrief: DebriefResponse | null },
  onChanged?: (debrief: DebriefResponse | null) => void,
): DebriefSectionHandle {
  const state: DebriefSectionState = {
    report: initial.report, debrief: initial.debrief, pendingAvoided: {}, draftNote: null, busy: false, error: null,
  };
  const render = () => { host.innerHTML = debriefSectionHtml(state); };

  const run = async (work: () => Promise<void>) => {
    if (state.busy) return;
    state.busy = true;
    state.error = null;
    render();
    try {
      await work();
    } catch (e) {
      console.warn('Debrief save failed', e);
      state.error = t('debriefSection.saveFailed');
    } finally {
      state.busy = false;
      render();
    }
  };

  const noteValue = (): string | null => {
    const v = host.querySelector<HTMLTextAreaElement>('.debrief-note')?.value.trim() ?? '';
    return v ? v.slice(0, NOTE_MAX_LEN) : null;
  };

  const saveDecision = (decision: 'flown' | 'cancelled', reasons: ConditionTagId[], note: string | null) =>
    run(async () => {
      state.debrief = await saveDebrief(flightId, { decision, reasons, note, form: 'questions' });
      state.draftNote = null;
      onChanged?.(state.debrief);
      // The report reads the decision (a cancelled flight asks nothing). The
      // decision is stored by now: a failed refetch keeps the old report
      // rather than reporting the save as failed.
      try {
        state.report = await fetchDebriefReport(flightId);
      } catch (e) {
        console.warn('Debrief report refetch failed', e);
      }
    });

  const answer = (item: DebriefAnswerRequest) => run(async () => {
    state.report = await saveDebriefAnswers(flightId, [item]);
    delete state.pendingAvoided[item.question_id];
    // The server folds the answers into the stored debrief: the first answer
    // stores the flight as flown, clearing the last one drops a derived
    // debrief with no note. Follow what it stored rather than guess.
    try {
      state.debrief = await fetchDebrief(flightId);
    } catch (e) {
      console.warn('Debrief refetch failed', e);
      if (state.debrief) state.debrief = { ...state.debrief, decision: 'flown', reasons: [], form: 'questions' };
    }
    onChanged?.(state.debrief);
  });

  const onClick = (ev: Event) => {
    const el = (ev.target as Element | null)?.closest<HTMLElement>('button');
    if (!el || state.busy) return;
    if (el.dataset.decision) {
      const d = el.dataset.decision as 'flown' | 'cancelled';
      if (d === sectionDecision(state)) return;
      // A note typed but not yet saved goes with the new decision.
      void saveDecision(d, [], noteValue() ?? state.debrief?.note ?? null);
    } else if (el.dataset.question && el.dataset.answer) {
      const qid = el.dataset.question;
      const q = state.report?.questions.find((x) => x.id === qid);
      const same = q?.answer?.answer === el.dataset.answer;
      void answer({ question_id: qid, answer: same ? null : el.dataset.answer, avoided: avoidedFor(state, qid) });
    } else if (el.dataset.reason) {
      const tag = el.dataset.reason as ConditionTagId;
      const current = new Set(state.debrief?.decision === 'cancelled' ? state.debrief.reasons : []);
      if (current.has(tag)) current.delete(tag); else current.add(tag);
      void saveDecision('cancelled', ALL_TAGS.filter((x) => current.has(x)), noteValue());
    } else if (el.classList.contains('debrief-note-save')) {
      const decision = sectionDecision(state);
      if (!decision) return;
      const reasons = decision === 'cancelled' ? (state.debrief?.reasons ?? []) : [];
      void saveDecision(decision, reasons, noteValue());
    }
  };

  const onInput = (ev: Event) => {
    const area = (ev.target as Element | null)?.closest<HTMLTextAreaElement>('.debrief-note');
    if (area) state.draftNote = area.value;
  };

  const onChange = (ev: Event) => {
    const box = (ev.target as Element | null)?.closest<HTMLInputElement>('input[data-avoided]');
    if (!box || state.busy) return;
    const qid = box.dataset.avoided!;
    const q = state.report?.questions.find((x) => x.id === qid);
    if (q?.answer) {
      void answer({ question_id: qid, answer: q.answer.answer, avoided: box.checked });
    } else {
      state.pendingAvoided[qid] = box.checked;
    }
  };

  host.addEventListener('click', onClick);
  host.addEventListener('change', onChange);
  host.addEventListener('input', onInput);
  render();
  return {
    teardown: () => {
      host.removeEventListener('click', onClick);
      host.removeEventListener('change', onChange);
      host.removeEventListener('input', onInput);
      host.innerHTML = '';
    },
  };
}
