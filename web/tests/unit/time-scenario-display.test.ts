/** Tests for the timing-scenario display helpers (#434/#435).
 *
 * Locks the partition rules (confirmed-never-hidden, old-artifact-not-worse)
 * and the advisory_status → per-model-dot inversion, which drive the show-all
 * split, the "N look smoother" headline, and the candidate detail column.
 */

import { describe, it, expect } from 'vitest';
import {
  isWorseCandidate,
  improvingCount,
  invertAdvisoryStatus,
} from '../../ts/helpers/time-scenario-display';
import type { TimeCandidateDTO, TimeConfirmationDTO } from '../../ts/adapters/api-adapter';

function cand(overrides: Partial<TimeCandidateDTO>): TimeCandidateDTO {
  return {
    departure_time: '2026-07-18T09:00:00Z',
    departure_shift_hours: 1,
    valid_times: [],
    assessment: 'AMBER',
    assessment_reason: '',
    models_used: ['ecmwf'],
    improves: [],
    worsens: [],
    margin: 0,
    disposition: 'neutral',
    advisory_status: {},
    confidence: 'ecmwf_only',
    is_baseline: false,
    is_alternate: false,
    confirmed: null,
    confirm_pending: false,
    ...overrides,
  };
}

const confirmation = (over: Partial<TimeConfirmationDTO> = {}): TimeConfirmationDTO => ({
  models_checked: ['ecmwf', 'gfs', 'icon'],
  assessment: 'AMBER',
  assessment_reason: '',
  better_than_baseline: false,
  improves: [],
  worsens: [],
  confirmed_at: '2026-07-18T09:00:00Z',
  ...over,
});

describe('isWorseCandidate', () => {
  it('hides an unconfirmed graded-worse sweep row', () => {
    expect(isWorseCandidate(cand({ disposition: 'worse' }))).toBe(true);
  });

  it('never hides a confirmed row (user paid for the check)', () => {
    expect(isWorseCandidate(cand({ disposition: 'worse', confirmed: confirmation() }))).toBe(false);
  });

  it('does not hide baseline / alternate / improving / neutral', () => {
    expect(isWorseCandidate(cand({ disposition: 'worse', is_baseline: true }))).toBe(false);
    expect(isWorseCandidate(cand({ disposition: 'worse', is_alternate: true }))).toBe(false);
    expect(isWorseCandidate(cand({ disposition: 'improving' }))).toBe(false);
    expect(isWorseCandidate(cand({ disposition: 'neutral' }))).toBe(false);
  });

  it('treats an old artifact (no disposition) as not-worse', () => {
    expect(isWorseCandidate(cand({ disposition: undefined }))).toBe(false);
  });
});

describe('improvingCount', () => {
  it('counts only improving swept candidates', () => {
    const list = [
      cand({ is_baseline: true, disposition: 'improving' }), // baseline excluded
      cand({ is_alternate: true, disposition: 'improving' }), // alternate excluded
      cand({ disposition: 'improving' }),
      cand({ disposition: 'improving' }),
      cand({ disposition: 'neutral' }),
      cand({ disposition: 'worse' }),
    ];
    expect(improvingCount(list)).toBe(2);
  });
});

describe('invertAdvisoryStatus', () => {
  it('inverts to per-model maps, keeping GREEN, scoped to models_used', () => {
    const as = {
      convective: { ecmwf: 'RED', gfs: 'GREEN', all: 'AMBER' },
      icing_escape: { ecmwf: 'AMBER' },
    };
    const out = invertAdvisoryStatus(as, ['ecmwf', 'gfs']);
    const byModel = Object.fromEntries(out.map((pm) => [pm.model, pm.map]));
    expect(Object.keys(byModel).sort()).toEqual(['ecmwf', 'gfs']); // "all" filtered out
    expect(byModel.ecmwf.get('convective')).toBe('RED');
    expect(byModel.ecmwf.get('icing_escape')).toBe('AMBER');
    expect(byModel.gfs.get('convective')).toBe('GREEN'); // GREEN preserved
  });

  it('returns empty for an absent map', () => {
    expect(invertAdvisoryStatus(undefined, ['ecmwf'])).toEqual([]);
  });
});
