/** The Debrief section (#1024): drawn from the server's report as served,
 *  against the shared fixture `app/fixtures/api/debrief-report.json`
 *  (built by the #1023 `build_report` on the LELL-LEMI replay). */

import { describe, it, expect, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import en from '../../ts/i18n/locales/en.json';

vi.mock('../../ts/i18n/i18n', () => ({
  t: (key: string, params?: Record<string, string | number>) => {
    let msg = (en as Record<string, string>)[key] ?? key;
    for (const [k, v] of Object.entries(params ?? {})) msg = msg.split(`{${k}}`).join(String(v));
    return msg;
  },
}));
vi.mock('../../ts/adapters/debrief-adapter', () => ({
  fetchDebriefReport: vi.fn(), saveDebrief: vi.fn(), saveDebriefAnswers: vi.fn(),
}));

import type { DebriefReport, DebriefResponse } from '../../ts/store/types';
import { debriefSectionHtml, sectionDecision, type DebriefSectionState } from '../../ts/components/debrief-section';

const FIXTURE = resolve(__dirname, '../../../app/fixtures/api/debrief-report.json');
const report = (): DebriefReport => JSON.parse(readFileSync(FIXTURE, 'utf8')) as DebriefReport;

const debrief = (over: Partial<DebriefResponse> = {}): DebriefResponse => ({
  flight_id: 'f', decision: 'flown', reasons: [], outcomes: {}, note: null, form: 'questions',
  created_at: '2026-11-20T10:30:00+00:00', updated_at: '2026-11-20T10:30:00+00:00', ...over,
});

const state = (over: Partial<DebriefSectionState> = {}): DebriefSectionState => ({
  report: report(), debrief: null, pendingAvoided: {}, busy: false, error: null, ...over,
});

const count = (html: string, needle: string) => html.split(needle).length - 1;

describe('debrief section', () => {
  it('opens on the headline and the flown / cancelled tap, nothing else asked yet', () => {
    const html = debriefSectionHtml(state());
    expect(html).toContain('data-testid="debrief-headline">No radar along the route.<');
    expect(html).toContain('data-testid="debrief-flown"');
    expect(html).toContain('data-testid="debrief-cancelled"');
    expect(html).not.toContain('data-testid="debrief-question"');
    expect(html).not.toContain('data-testid="debrief-reason"');
    // The details fold carries the server's lines, closed.
    expect(html).toContain('<details class="debrief-details"');
    expect(html).not.toContain('<details class="debrief-details" open');
    expect(count(html, '<li data-kind=')).toBe(report().summary!.lines.length);
  });

  it('flown shows the server questions with their own options; the toggle only where avoidable', () => {
    const r = report();
    const html = debriefSectionHtml(state({ debrief: debrief() }));
    expect(count(html, 'data-testid="debrief-question"')).toBe(r.questions.length);
    for (const q of r.questions) {
      for (const o of q.options) expect(html).toContain(`data-question="${q.id}" data-answer="${o.id}"`);
    }
    const avoidable = r.questions.filter((q) => q.avoidable).length;
    expect(count(html, 'data-testid="debrief-avoided"')).toBe(avoidable);
    expect(avoidable).toBeLessThan(r.questions.length); // the cloud question has none
    expect(html).toContain('data-testid="debrief-note"');
  });

  it('marks a stored answer and its avoided toggle', () => {
    const r = report();
    const q = r.questions[0];
    q.answer = { kind: q.kind, answer: 'light', avoided: true, note: null,
      answered_at: '2026-11-20T11:00:00+00:00', rules_version: '1' };
    const html = debriefSectionHtml(state({ report: r }));
    // An answer means flown even before the decision row is read.
    expect(sectionDecision({ report: r, debrief: null })).toBe('flown');
    expect(html).toContain(`class="debrief-chip active" data-question="${q.id}" data-answer="light"`);
    expect(html).toMatch(new RegExp(`data-avoided="${q.id}" checked`));
  });

  it('cancelled shows the cancel reasons, no questions', () => {
    const html = debriefSectionHtml(state({ debrief: debrief({ decision: 'cancelled', reasons: ['ICE'] }) }));
    expect(html).not.toContain('data-testid="debrief-question"');
    expect(count(html, 'data-testid="debrief-reason"')).toBe(8);
    expect(html).toContain('class="debrief-chip active" data-reason="ICE"');
  });

  it('a monitoring flight shows neither tapped; no report draws nothing', () => {
    expect(sectionDecision({ report: report(), debrief: debrief({ decision: 'monitoring' }) })).toBeNull();
    expect(debriefSectionHtml(state({ report: null }))).toBe('');
  });

  it('never says go/no-go or verdict', () => {
    const html = debriefSectionHtml(state({ debrief: debrief() })).toLowerCase();
    expect(html).not.toContain('no-go');
    expect(html).not.toContain('verdict');
  });
});
