/** Debrief report block (prototype, debrief brainstorm 2026-11-17): the top of
 *  the Observed tab once the flight is over.
 *
 *  - one headline: how the flight went against the forecast, judged only on
 *    what the instruments could see (radar en route, airport METARs);
 *  - one to three questions on what they could not (icing, cloud, turbulence
 *    most models had at the flown level), answered with one tap;
 *  - "Details": the METAR next to the TAF at each end, radar, the forecast at
 *    your level — collapsed.
 *
 *  The recap ribbon and its replay below it are unchanged: this block reads
 *  the flight, they show it. English only while it is a prototype (the
 *  headline and questions come from the server in English). */

import type { DebriefReport, DebriefReportQuestion } from '../store/types';
import { escapeHtml } from '../utils';

const COUNT_LABEL = ['', 'One quick question', 'Two quick questions', 'Three quick questions'];

function questionHtml(q: DebriefReportQuestion): string {
  const a = q.answer;
  const chips = q.options.map((o) =>
    `<button type="button" class="chip debrief-q-chip" data-q="${escapeHtml(q.id)}" data-a="${escapeHtml(o.id)}"`
    + ` aria-pressed="${a?.answer === o.id}">${escapeHtml(o.label)}</button>`).join('');
  const avoid = q.avoidable
    ? `<label class="debrief-q-avoid"><input type="checkbox" data-q-avoid="${escapeHtml(q.id)}"`
      + `${a?.avoided ? ' checked' : ''}> I changed level or route to stay out of it</label>`
    : '';
  return `<div class="debrief-q" data-testid="debrief-q-${escapeHtml(q.kind)}">`
    + `<p>${escapeHtml(q.text)}</p><div class="debrief-q-chips">${chips}</div>${avoid}</div>`;
}

/** The block's HTML, or '' when there is nothing to say (no report, or no
 *  recording to build one from). */
export function debriefReportHtml(report: DebriefReport | null): string {
  if (!report || report.status !== 'ok' || !report.summary) return '';
  const { summary } = report;
  const qs = report.questions ?? [];
  const lines = summary.lines.map((l) =>
    `<li class="debrief-line debrief-line-${escapeHtml(l.kind)}">${escapeHtml(l.text)}</li>`).join('');
  return `<div class="debrief-report" data-testid="debrief-report">`
    + `<p class="debrief-headline${summary.differs_from_forecast ? ' debrief-headline-differs' : ''}"`
    + ` data-testid="debrief-headline">${escapeHtml(summary.headline)}</p>`
    + (qs.length
      ? `<div class="debrief-questions"><div class="debrief-q-count">${COUNT_LABEL[Math.min(qs.length, 3)]}</div>`
        + qs.map(questionHtml).join('') + '</div>'
      : '')
    + `<details class="debrief-details" data-testid="debrief-details"><summary>Details</summary>`
    + `<ul class="debrief-lines">${lines}</ul></details>`
    + '</div>';
}

export interface DebriefReportHandlers {
  /** Store an answer (null clears it). */
  onAnswer: (questionId: string, answer: string | null, avoided: boolean) => void;
}

/** Draw the block into `el` and wire the chips (tap again to clear) and the
 *  "I changed level or route" box (re-sends the current answer). The
 *  "Details" open state survives a redraw after an answer. */
export function mountDebriefReport(el: HTMLElement, report: DebriefReport | null, handlers: DebriefReportHandlers): void {
  const wasOpen = el.querySelector<HTMLDetailsElement>('.debrief-details')?.open ?? false;
  el.innerHTML = debriefReportHtml(report);
  const details = el.querySelector<HTMLDetailsElement>('.debrief-details');
  if (details) details.open = wasOpen;
  const avoided = (qid: string) =>
    !!el.querySelector<HTMLInputElement>(`[data-q-avoid="${CSS.escape(qid)}"]`)?.checked;
  el.onclick = (ev) => {
    const chip = (ev.target as Element | null)?.closest<HTMLElement>('.debrief-q-chip');
    if (!chip?.dataset.q) return;
    const pressed = chip.getAttribute('aria-pressed') === 'true';
    handlers.onAnswer(chip.dataset.q, pressed ? null : chip.dataset.a ?? null, avoided(chip.dataset.q));
  };
  el.onchange = (ev) => {
    const box = ev.target as HTMLInputElement;
    const qid = box.dataset?.qAvoid;
    if (!qid) return;
    const current = el.querySelector<HTMLElement>(`.debrief-q-chip[data-q="${CSS.escape(qid)}"][aria-pressed="true"]`);
    if (current?.dataset.a) handlers.onAnswer(qid, current.dataset.a, box.checked);
  };
}
