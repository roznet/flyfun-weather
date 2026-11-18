/** Debrief report block (prototype): headline first, questions, details collapsed. */

import { describe, it, expect } from 'vitest';
import { debriefReportHtml } from '../../ts/components/debrief-report';
import type { DebriefReport } from '../../ts/store/types';

const base: DebriefReport = {
  v: 'phase0-1',
  flight_id: 'f',
  status: 'ok',
  summary: {
    headline: 'Clear, as forecast. The fog at EGTF lifted before you left.',
    differs_from_forecast: false,
    lines: [{ kind: 'departure', text: 'Departure EGTF (via EGLL): 10:50Z VFR, 9000 NCD.' }],
  },
  questions: [],
};

describe('debriefReportHtml', () => {
  it('leads with the headline and keeps the details collapsed', () => {
    const html = debriefReportHtml(base);
    expect(html.indexOf('debrief-headline')).toBeLessThan(html.indexOf('debrief-details'));
    expect(html).toContain('Clear, as forecast.');
    expect(html).toMatch(/<details class="debrief-details"[^>]*>/);
    expect(html).not.toMatch(/<details[^>]* open/);
    expect(html).not.toContain('debrief-questions');
  });

  it('marks the headline when the flight differed and draws the questions', () => {
    const html = debriefReportHtml({
      ...base,
      summary: { ...base.summary!, differs_from_forecast: true },
      questions: [{
        id: 'turbulence-15-37', kind: 'turbulence', text: 'How was it?', avoidable: true,
        options: [{ id: 'none', label: 'Smooth' }, { id: 'light', label: 'Light' }],
        answer: { answer: 'light', avoided: false, note: null, answered_at: '2026-11-18T00:00:00Z' },
      }],
    });
    expect(html).toContain('debrief-headline-differs');
    expect(html).toContain('One quick question');
    expect(html).toContain('data-a="light" aria-pressed="true"');
    expect(html).toContain('data-a="none" aria-pressed="false"');
    expect(html).toContain('I changed level or route');
  });

  it('draws nothing without a report to show', () => {
    expect(debriefReportHtml(null)).toBe('');
    expect(debriefReportHtml({ ...base, status: 'no_live_window', summary: null })).toBe('');
  });
});
